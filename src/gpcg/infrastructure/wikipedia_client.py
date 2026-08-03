"""Wikipedia client — fetch game article description and text (V2).

Uses the Wikipedia REST API to fetch article summary and full text
by QID (from Wikidata) or by title. The text is used to generate
lore_summary via LLM.

See ARCHITECTURE_V2.md §6.5 (Enriquecimento).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import httpx

from gpcg.logging import get_logger

log = get_logger(__name__)

WIKIPEDIA_API_URL = "https://en.wikipedia.org/w/api.php"
WIKIPEDIA_REST_URL = "https://en.wikipedia.org/api/rest_v1"
USER_AGENT = "GPCG/2.0 (gameplay-content-generator; brunointegrationsgaming@gmail.com)"
REQUEST_TIMEOUT = 30.0


@dataclass
class WikipediaArticle:
    """Article data from Wikipedia."""
    title: str
    description: Optional[str] = None  # short description (first paragraph)
    extract: Optional[str] = None  # full text extract (for lore generation)
    url: Optional[str] = None  # article URL
    qid: Optional[str] = None  # Wikidata QID if known


class WikipediaClient:
    """Client for Wikipedia REST + Action API.

    Stateless — each call is an independent HTTP request.
    Caching is handled by the caller (enrich_game stores results in
    Game.metadata_json.enrichment_cache).
    """

    def __init__(self, *, timeout: float = REQUEST_TIMEOUT):
        self.timeout = timeout

    def _headers(self) -> dict:
        return {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }

    def get_summary_by_qid(self, qid: str) -> Optional[WikipediaArticle]:
        """Fetch article summary by Wikidata QID.

        Uses the REST API: /page/summary/{title}, but first resolves
        the QID to a Wikipedia title via the Action API.
        """
        title = self._resolve_qid_to_title(qid)
        if not title:
            log.info(f"Wikipedia: no article found for QID {qid}")
            return None
        return self.get_summary_by_title(title)

    def get_summary_by_title(self, title: str) -> Optional[WikipediaArticle]:
        """Fetch article summary by exact title."""
        url = f"{WIKIPEDIA_REST_URL}/page/summary/{title}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(url, headers=self._headers())
                if resp.status_code == 404:
                    log.info(f"Wikipedia: article '{title}' not found")
                    return None
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, Exception) as e:
            log.error(f"Wikipedia summary fetch failed for '{title}': {e}")
            return None

        return WikipediaArticle(
            title=data.get("title", title),
            description=data.get("description"),
            extract=data.get("extract"),
            url=data.get("content_urls", {}).get("desktop", {}).get("page"),
        )

    def get_full_text(self, title: str, *, max_chars: int = 8000) -> Optional[str]:
        """Fetch the full text extract of an article (for lore generation).

        Uses the Action API with explaintext to get plain text (not HTML).
        Limited to max_chars to avoid sending huge texts to the LLM.
        """
        params = {
            "action": "query",
            "titles": title,
            "prop": "extracts",
            "explaintext": "1",
            "exsectionformat": "plain",
            "format": "json",
            "redirects": "1",
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(WIKIPEDIA_API_URL, params=params, headers=self._headers())
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, Exception) as e:
            log.error(f"Wikipedia full text fetch failed for '{title}': {e}")
            return None

        pages = data.get("query", {}).get("pages", {})
        if not pages:
            return None

        # pages is a dict keyed by pageid
        page = next(iter(pages.values()))
        extract = page.get("extract")
        if not extract:
            return None

        # Truncate to max_chars (keep complete sentences if possible)
        if len(extract) > max_chars:
            # Try to cut at a sentence boundary near max_chars
            cut = extract[:max_chars]
            last_period = cut.rfind(". ")
            if last_period > max_chars * 0.7:
                extract = cut[: last_period + 1]
            else:
                extract = cut + "..."

        return extract

    def search(self, query: str, *, limit: int = 5) -> list[dict]:
        """Search Wikipedia for articles matching a query.

        Returns list of {title, snippet} dicts.
        """
        params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": str(limit),
            "format": "json",
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(WIKIPEDIA_API_URL, params=params, headers=self._headers())
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, Exception) as e:
            log.error(f"Wikipedia search failed for '{query}': {e}")
            return []

        results = []
        for item in data.get("query", {}).get("search", []):
            results.append({
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
            })
        return results

    def _resolve_qid_to_title(self, qid: str) -> Optional[str]:
        """Resolve a Wikidata QID to a Wikipedia article title.

        Uses the Action API with wbgetentities to find the sitelink.
        """
        # Use Wikidata API to get the English Wikipedia sitelink
        from gpcg.infrastructure.wikidata_client import WikidataClient
        wd = WikidataClient(timeout=self.timeout)
        entity = wd.get_entity(qid)
        if not entity:
            return None
        sitelinks = entity.get("sitelinks", {})
        enwiki = sitelinks.get("enwiki", {})
        return enwiki.get("title")

    def get_article_for_game(self, game_name: str, qid: Optional[str] = None) -> Optional[WikipediaArticle]:
        """Get a Wikipedia article for a game, by QID (preferred) or by name search.

        If qid is provided, tries to resolve via QID first.
        Falls back to name search if QID resolution fails.
        """
        if qid:
            article = self.get_summary_by_qid(qid)
            if article:
                # Also fetch full text for lore generation
                full_text = self.get_full_text(article.title)
                if full_text:
                    article.extract = full_text
                return article

        # Fallback: search by name
        results = self.search(f"{game_name} video game", limit=5)
        if not results:
            results = self.search(game_name, limit=5)
        if not results:
            return None

        # Pick the best result (first one, or one that mentions "game" in snippet)
        best = results[0]
        for r in results:
            if "game" in r.get("snippet", "").lower():
                best = r
                break

        article = self.get_summary_by_title(best["title"])
        if article:
            full_text = self.get_full_text(article.title)
            if full_text:
                article.extract = full_text
        return article

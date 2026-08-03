"""Wikidata client — fetch canonical game metadata via SPARQL (V2).

Uses the Wikidata SPARQL endpoint to resolve game identity by name,
then extracts structured fields (developer, publisher, franchise, genres,
release_date, aliases, external_ids).

See ARCHITECTURE_V2.md §6.5 (Enriquecimento).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import httpx

from gpcg.logging import get_logger

log = get_logger(__name__)

WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"
WIKIDATA_API_URL = "https://www.wikidata.org/w/api.php"
WIKIDATA_REST_URL = "https://www.wikidata.org/wiki/Special:EntityData"
USER_AGENT = "GPCG/2.0 (gameplay-content-generator; brunointegrationsgaming@gmail.com)"
REQUEST_TIMEOUT = 30.0


@dataclass
class WikidataGameInfo:
    """Structured data extracted from Wikidata for a game."""
    qid: Optional[str] = None  # Q123456
    label: Optional[str] = None  # canonical label
    description: Optional[str] = None  # short description
    developer: Optional[str] = None
    publisher: Optional[str] = None
    franchise: Optional[str] = None
    genres: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)
    release_date: Optional[datetime] = None
    aliases: list[str] = field(default_factory=list)
    steam_app_id: Optional[str] = None
    raw: dict = field(default_factory=dict)  # raw entity data for debugging


class WikidataClient:
    """Client for Wikidata SPARQL + REST API.

    Stateless — each call is an independent HTTP request.
    Caching is handled by the caller (enrich_game stores results in
    Game.metadata_json.enrichment_cache).
    """

    def __init__(self, *, timeout: float = REQUEST_TIMEOUT):
        self.timeout = timeout

    def _headers(self) -> dict:
        return {
            "User-Agent": USER_AGENT,
            "Accept": "application/sparql-results+json",
        }

    def _api_headers(self) -> dict:
        return {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }

    def search_entities(self, name: str, *, limit: int = 5) -> list[dict]:
        """Search Wikidata for entities matching a name.

        Returns list of {qid, label, description, concept_uri} dicts.
        Uses the wbsearchentities API (faster than SPARQL for name search).
        """
        params = {
            "action": "wbsearchentities",
            "search": name,
            "language": "en",
            "format": "json",
            "limit": str(limit),
            "type": "item",
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(WIKIDATA_API_URL, params=params, headers=self._api_headers())
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, Exception) as e:
            log.error(f"Wikidata search failed for '{name}': {e}")
            return []

        results = []
        for item in data.get("search", []):
            results.append({
                "qid": item.get("id", ""),
                "label": item.get("label", ""),
                "description": item.get("description", ""),
                "concept_uri": item.get("concepturi", ""),
            })
        return results

    def get_entity(self, qid: str) -> Optional[dict]:
        """Fetch full entity data for a QID via Special:EntityData REST endpoint.

        Returns the parsed JSON entity data, or None on failure.
        """
        url = f"{WIKIDATA_REST_URL}/{qid}.json"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(url, headers=self._api_headers())
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, Exception) as e:
            log.error(f"Wikidata entity fetch failed for {qid}: {e}")
            return None

        # Entity data is under entities.{qid}
        entities = data.get("entities", {})
        return entities.get(qid)

    def resolve_game(self, name: str) -> Optional[WikidataGameInfo]:
        """Resolve a game name to a WikidataGameInfo.

        Steps:
        1. Search entities by name
        2. Filter to likely game entities (has P31=Q7889 or description mentions "game")
        3. Fetch full entity data for the best candidate
        4. Extract structured fields
        """
        candidates = self.search_entities(name, limit=10)
        if not candidates:
            log.info(f"Wikidata: no candidates for '{name}'")
            return None

        # Filter: prefer entities whose description mentions "game" or "video game"
        game_candidates = [
            c for c in candidates
            if "game" in (c.get("description") or "").lower()
            or "video game" in (c.get("description") or "").lower()
        ]
        if not game_candidates:
            # Fall back to first candidate (might still be a game)
            game_candidates = candidates[:1]

        best = game_candidates[0]
        qid = best["qid"]
        log.info(f"Wikidata: best candidate for '{name}' is {qid} ({best.get('label')})")

        entity = self.get_entity(qid)
        if not entity:
            return None

        return self._extract_game_info(qid, entity)

    def _extract_game_info(self, qid: str, entity: dict) -> WikidataInfo:
        """Extract structured game info from a Wikidata entity dict."""
        info = WikidataGameInfo(qid=qid)
        info.raw = entity

        # Labels (canonical name) — use _extract_label for language fallback
        info.label = self._extract_label(entity)

        # Description
        descriptions = entity.get("descriptions", {})
        info.description = (
            descriptions.get("en", {}).get("value")
            or descriptions.get("pt", {}).get("value")
            or descriptions.get("es", {}).get("value")
            or descriptions.get("fr", {}).get("value")
        )

        # Aliases (alternative names) — collect from multiple languages
        aliases = entity.get("aliases", {})
        all_aliases = []
        for lang in ("en", "pt", "es", "fr"):
            all_aliases.extend(a["value"] for a in aliases.get(lang, []))
        info.aliases = list(set(all_aliases))

        # Claims (structured properties)
        claims = entity.get("claims", {})

        # P1592: Steam application ID
        steam_claims = claims.get("P1592", [])
        if steam_claims:
            steam_value = steam_claims[0].get("mainsnak", {}).get("datavalue", {}).get("value")
            if steam_value:
                info.steam_app_id = str(steam_value)

        # P123: publisher
        info.publisher = self._get_entity_label_from_claim(claims, "P123")

        # P178: developer
        info.developer = self._get_entity_label_from_claim(claims, "P178")

        # P179: series/franchise (part of the series)
        info.franchise = self._get_entity_label_from_claim(claims, "P179")

        # P136: genre
        info.genres = self._get_entity_labels_from_claim_list(claims, "P136")

        # P400: platform (for video game platforms)
        # P50: platform is not standard; P400 is "platform for video game"
        info.platforms = self._get_entity_labels_from_claim_list(claims, "P400")

        # P577: publication date / release date
        release_claims = claims.get("P577", [])
        if release_claims:
            date_value = release_claims[0].get("mainsnak", {}).get("datavalue", {}).get("value", {})
            if isinstance(date_value, dict):
                time_str = date_value.get("time", "")
                if time_str:
                    info.release_date = self._parse_wikidata_time(time_str)

        return info

    def _get_entity_label_from_claim(self, claims: dict, prop: str) -> Optional[str]:
        """Get the label of the entity referenced in a single-value claim."""
        claim_list = claims.get(prop, [])
        if not claim_list:
            return None
        qid = claim_list[0].get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("id")
        if not qid:
            return None
        # Fetch the label for this entity
        entity = self.get_entity(qid)
        if entity:
            return self._extract_label(entity)
        return qid  # fallback to QID if label fetch fails

    def _get_entity_labels_from_claim_list(self, claims: dict, prop: str) -> list[str]:
        """Get labels of entities referenced in a multi-value claim."""
        claim_list = claims.get(prop, [])
        if not claim_list:
            return []
        results = []
        for claim in claim_list:
            qid = claim.get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("id")
            if qid:
                entity = self.get_entity(qid)
                if entity:
                    label = self._extract_label(entity)
                    if label:
                        results.append(label)
                else:
                    results.append(qid)
        return results

    def _extract_label(self, entity: dict) -> Optional[str]:
        """Extract the best available label from a Wikidata entity.

        Priority: en label > pt label > enwiki sitelink title > ptwiki sitelink
        > es/fr/de labels > any first available label.

        Some entities don't have English labels (Wikidata data quality issue),
        so we fall back to the English Wikipedia article title from sitelinks,
        then to any available language label.
        """
        labels = entity.get("labels", {})
        # Priority languages for labels
        for lang in ("en", "pt", "es", "fr", "de"):
            if lang in labels and labels[lang].get("value"):
                return labels[lang]["value"]
        # Fallback: use English Wikipedia sitelink title (usually the canonical name)
        sitelinks = entity.get("sitelinks", {})
        for wiki in ("enwiki", "ptwiki", "eswiki", "frwiki", "dewiki"):
            if wiki in sitelinks and sitelinks[wiki].get("title"):
                return sitelinks[wiki]["title"]
        # Last resort: any available label
        for lang, label_data in labels.items():
            if label_data.get("value"):
                return label_data["value"]
        return None

    def _parse_wikidata_time(self, time_str: str) -> Optional[datetime]:
        """Parse a Wikidata time string like '+1996-03-22T00:00:00Z'."""
        # Format: +YYYY-MM-DDT00:00:00Z
        try:
            clean = time_str.lstrip("+")
            # Take just the date part
            date_part = clean.split("T")[0]
            return datetime.fromisoformat(date_part)
        except (ValueError, IndexError):
            return None


# Convenience: keep backward compat with the type alias used above
WikidataInfo = WikidataGameInfo

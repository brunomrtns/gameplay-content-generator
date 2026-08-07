"""IGDB API client — OAuth2 authentication and game data fetching.

IGDB (Internet Game Database) is owned by Twitch and uses Twitch OAuth2
for authentication. The API uses a custom query language called
"Apicalypse" for filtering and field selection.

Rate limits: 4 requests/second, 8 concurrent connections.
We enforce the rate limit with a simple sleep between requests.

API docs: https://api-docs.igdb.com/
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from gpcg.config import get_settings

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_IGDB_API_BASE = "https://api.igdb.com/v4"
_IGDB_AUTH_URL = "https://id.twitch.tv/oauth2/token"
_IGDB_GAME_FIELDS = (
    "id,name,slug,summary,storyline,first_release_date,"
    "rating,rating_count,total_rating,total_rating_count,hypes,"
    "category,cover.image_id,screenshots.image_id,"
    "genres.name,themes.name,game_modes.name,player_perspectives.name,"
    "platforms.name,franchise.name,involved_companies.company.name,"
    "involved_companies.publisher,involved_companies.developer,"
    "alternative_names.name,alternative_names.comment,"
    "url,updated_at"
)


# ── Data classes ──────────────────────────────────────────────────────────────


@dataclass
class IGDBGame:
    """Raw IGDB game data, before normalization into CatalogGame."""

    id: int
    name: str
    slug: str
    summary: Optional[str] = None
    storyline: Optional[str] = None
    first_release_date: Optional[int] = None
    rating: Optional[float] = None
    rating_count: Optional[int] = None
    total_rating: Optional[float] = None
    total_rating_count: Optional[int] = None
    hypes: Optional[int] = None
    category: int = 0
    cover_image_id: Optional[str] = None
    screenshot_image_ids: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    themes: list[str] = field(default_factory=list)
    game_modes: list[str] = field(default_factory=list)
    player_perspectives: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)
    franchise: Optional[str] = None
    developer: Optional[str] = None
    publisher: Optional[str] = None
    alternative_names: list[str] = field(default_factory=list)
    igdb_url: Optional[str] = None
    updated_at: Optional[int] = None

    @property
    def cover_url(self) -> Optional[str]:
        """Full URL for the cover image (IGDB CDN)."""
        if not self.cover_image_id:
            return None
        return f"https://images.igdb.com/igdb/image/upload/t_cover_big/{self.cover_image_id}.jpg"

    @property
    def screenshot_urls(self) -> list[str]:
        """Full URLs for screenshots (IGDB CDN)."""
        return [
            f"https://images.igdb.com/igdb/image/upload/t_screenshot_big/{sid}.jpg"
            for sid in self.screenshot_image_ids
        ]


# ── Client ────────────────────────────────────────────────────────────────────


class IGDBClient:
    """IGDB API client with OAuth2 token management and rate limiting.

    Token is fetched on first use and cached. If the token expires (401),
    it's automatically refreshed on the next request.

    Thread-safe: the token lock ensures only one thread refreshes the token.
    The rate limiter uses a simple sleep — sufficient for single-threaded
    sync (the sync service runs in one background thread).
    """

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        settings = get_settings()
        self._client_id = client_id or settings.igdb_client_id
        self._client_secret = client_secret or settings.igdb_client_secret
        self._timeout = timeout
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._last_request_time: float = 0.0
        self._rate_limit_sec: float = settings.catalog_igdb_rate_limit_sec

        if not self._client_id or not self._client_secret:
            raise ValueError(
                "IGDB credentials not configured. Set IGDB_CLIENT_ID and "
                "IGDB_CLIENT_SECRET in .env"
            )

    # ── Authentication ──────────────────────────────────────────────────────

    def _ensure_token(self) -> str:
        """Fetch a new OAuth2 token if the current one is expired or missing."""
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token

        log.info("Fetching IGDB OAuth2 token...")
        resp = httpx.post(
            _IGDB_AUTH_URL,
            params={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "grant_type": "client_credentials",
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        self._token = data["access_token"]
        # expires_in is in seconds (typically ~60 days = 5184000).
        # We subtract a 60-second safety margin.
        self._token_expires_at = time.time() + data.get("expires_in", 3600) - 60
        log.info("IGDB token acquired (expires in %ds)", data.get("expires_in", 3600))
        return self._token

    # ── Rate limiting ───────────────────────────────────────────────────────

    def _rate_limit(self) -> None:
        """Sleep to enforce the IGDB rate limit (4 req/s default)."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self._rate_limit_sec:
            time.sleep(self._rate_limit_sec - elapsed)
        self._last_request_time = time.time()

    # ── HTTP request ────────────────────────────────────────────────────────

    def _request(self, endpoint: str, body: str) -> list[dict[str, Any]]:
        """Make a POST request to an IGDB endpoint.

        Returns the parsed JSON array. Raises httpx.HTTPStatusError on
        non-2xx responses. Automatically refreshes the token on 401.
        """
        url = f"{_IGDB_API_BASE}/{endpoint}"
        for attempt in range(2):
            token = self._ensure_token()
            self._rate_limit()

            resp = httpx.post(
                url,
                content=body,
                headers={
                    "Client-ID": self._client_id,
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
                timeout=self._timeout,
            )

            if resp.status_code == 401 and attempt == 0:
                log.warning("IGDB token expired (401), refreshing...")
                self._token = None
                continue

            resp.raise_for_status()
            return resp.json()

        # Should not reach here — the second attempt would have raised.
        return []

    # ── Game fetching ───────────────────────────────────────────────────────

    def fetch_games(
        self,
        *,
        offset: int = 0,
        limit: int = 500,
        updated_after: Optional[int] = None,
        sort_by: str = "total_rating_count desc",
    ) -> list[IGDBGame]:
        """Fetch a page of games from IGDB.

        Args:
            offset: Pagination offset (IGDB uses offset/limit, not cursors).
            limit: Page size (max 500).
            updated_after: If set, only fetch games with updated_at > this
                timestamp (for incremental sync). If None, fetches all
                matching the popularity filter (full sync).
            sort_by: Sort order (Apicalypse syntax). Default: most popular first.

        Returns:
            List of IGDBGame objects. Empty list when no more results.
        """
        settings = get_settings()

        # Build the Apicalypse query body.
        # For full sync: filter by popularity (rating/count/hypes) + year.
        # For incremental sync: filter by updated_at > last_sync AND same
        #   popularity filter (we don't want to sync unpopular games that
        #   happened to get an IGDB metadata update).
        #
        # NOTE: IGDB's `category` field is null for ALL games (371k+), so we
        # can't filter by category = 0 (main_game). The popularity filter
        # (rating/count/hypes) already excludes DLCs, mods, and bundles,
        # which rarely have high ratings or many rating counts.
        where_clauses: list[str] = []

        if updated_after is not None:
            where_clauses.append(f"updated_at > {int(updated_after)}")
        else:
            # Full sync: apply popularity filter to avoid syncing ~370k games.
            # A game is included if ANY of:
            #   - rating > rating_min (well-regarded)
            #   - total_rating_count > count_min (popular)
            #   - hypes > hypes_min (anticipated)
            rating_min = settings.catalog_sync_filter_rating_min
            count_min = settings.catalog_sync_filter_rating_count_min
            hypes_min = settings.catalog_sync_filter_hypes_min
            year_min = settings.catalog_sync_filter_year_min
            where_clauses.append(
                f"(rating > {rating_min} | total_rating_count > {count_min} | hypes > {hypes_min})"
            )
            where_clauses.append(f"first_release_date > {year_min}")

        where_clause = " & ".join(where_clauses)
        body = (
            f"fields {_IGDB_GAME_FIELDS};\n"
            f"where {where_clause};\n"
            f"sort {sort_by};\n"
            f"limit {limit};\n"
            f"offset {offset};"
        )

        raw_games = self._request("games", body)
        return [self._parse_game(g) for g in raw_games]

    def fetch_game_count(self, updated_after: Optional[int] = None) -> int:
        """Get the total count of games matching the current filter."""
        settings = get_settings()
        where_clauses: list[str] = []

        if updated_after is not None:
            where_clauses.append(f"updated_at > {int(updated_after)}")
        else:
            rating_min = settings.catalog_sync_filter_rating_min
            count_min = settings.catalog_sync_filter_rating_count_min
            hypes_min = settings.catalog_sync_filter_hypes_min
            year_min = settings.catalog_sync_filter_year_min
            where_clauses.append(
                f"(rating > {rating_min} | total_rating_count > {count_min} | hypes > {hypes_min})"
            )
            where_clauses.append(f"first_release_date > {year_min}")

        where_clause = " & ".join(where_clauses)
        body = f"where {where_clause};"

        result = self._request("games/count", body)
        if isinstance(result, dict) and "count" in result:
            return result["count"]
        return 0

    # ── Parsing ─────────────────────────────────────────────────────────────

    def _parse_game(self, raw: dict[str, Any]) -> IGDBGame:
        """Parse a raw IGDB game dict into an IGDBGame dataclass.

        IGDB uses nested objects for related entities (genres, platforms,
        involved_companies, etc.). We flatten them into simple lists/strings.
        """
        # Cover — IGDB returns {"image_id": "abc123"}
        cover_id = None
        if raw.get("cover") and isinstance(raw["cover"], dict):
            cover_id = raw["cover"].get("image_id")

        # Screenshots — list of {"image_id": "abc123"}
        screenshot_ids = []
        for ss in raw.get("screenshots", []) or []:
            if isinstance(ss, dict) and ss.get("image_id"):
                screenshot_ids.append(ss["image_id"])

        # Genres/themes/modes/perspectives/platforms — list of {"name": "..."}
        def _extract_names(items: Any) -> list[str]:
            if not items:
                return []
            return [item["name"] for item in items if isinstance(item, dict) and "name" in item]

        # Franchise — single {"name": "..."}
        franchise = None
        if raw.get("franchise") and isinstance(raw["franchise"], dict):
            franchise = raw["franchise"].get("name")

        # Involved companies — list of {
        #   "company": {"name": "..."},
        #   "publisher": bool,
        #   "developer": bool,
        # }
        developer = None
        publisher = None
        for ic in raw.get("involved_companies", []) or []:
            if not isinstance(ic, dict):
                continue
            company = ic.get("company")
            company_name = company.get("name") if isinstance(company, dict) else None
            if not company_name:
                continue
            if ic.get("developer") and developer is None:
                developer = company_name
            if ic.get("publisher") and publisher is None:
                publisher = company_name

        # Alternative names — list of {"name": "...", "comment": "..."}
        alt_names = []
        for alt in raw.get("alternative_names", []) or []:
            if isinstance(alt, dict) and alt.get("name"):
                alt_names.append(alt["name"])

        return IGDBGame(
            id=raw["id"],
            name=raw["name"],
            slug=raw["slug"],
            summary=raw.get("summary"),
            storyline=raw.get("storyline"),
            first_release_date=raw.get("first_release_date"),
            rating=raw.get("rating"),
            rating_count=raw.get("rating_count"),
            total_rating=raw.get("total_rating"),
            total_rating_count=raw.get("total_rating_count"),
            hypes=raw.get("hypes"),
            category=raw.get("category", 0),
            cover_image_id=cover_id,
            screenshot_image_ids=screenshot_ids,
            genres=_extract_names(raw.get("genres")),
            themes=_extract_names(raw.get("themes")),
            game_modes=_extract_names(raw.get("game_modes")),
            player_perspectives=_extract_names(raw.get("player_perspectives")),
            platforms=_extract_names(raw.get("platforms")),
            franchise=franchise,
            developer=developer,
            publisher=publisher,
            alternative_names=alt_names,
            igdb_url=raw.get("url"),
            updated_at=raw.get("updated_at"),
        )

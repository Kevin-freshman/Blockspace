"""Small read-only client for public Polymarket endpoints."""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import requests


class PolymarketApiError(RuntimeError):
    pass


class PolymarketClient:
    DATA_API = "https://data-api.polymarket.com"
    CLOB_API = "https://clob.polymarket.com"

    def __init__(
        self,
        timeout_seconds: int = 15,
        max_retries: int = 3,
        min_request_interval: float = 0.1,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.min_request_interval = max(0.0, float(min_request_interval))
        self._local = threading.local()
        self._rate_lock = threading.Lock()
        self._next_request_at = 0.0

    def leaderboard(
        self,
        time_period: str,
        order_by: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        while len(rows) < limit:
            page_size = min(50, limit - len(rows))
            data = self._get(
                self.DATA_API + "/v1/leaderboard",
                {
                    "category": "CRYPTO",
                    "timePeriod": time_period,
                    "orderBy": order_by,
                    "limit": page_size,
                    "offset": len(rows),
                },
            )
            if not isinstance(data, list):
                raise PolymarketApiError("leaderboard response was not a list")
            rows.extend(data)
            if len(data) < page_size:
                break
        deduplicated: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            address = str(row.get("proxyWallet") or "").lower()
            if address and address not in deduplicated:
                deduplicated[address] = row
        return list(deduplicated.values())[:limit]

    def leaderboard_user(
        self,
        time_period: str,
        order_by: str,
        address: str,
    ) -> Optional[Dict[str, Any]]:
        """Return the official rank for one address, including far outside top 1,000."""
        data = self._get(
            self.DATA_API + "/v1/leaderboard",
            {
                "category": "CRYPTO",
                "timePeriod": time_period,
                "orderBy": order_by,
                "user": address,
                "limit": 1,
                "offset": 0,
            },
        )
        if not isinstance(data, list):
            raise PolymarketApiError("leaderboard user response was not a list")
        return data[0] if data else None

    def recent_activity(
        self,
        address: str,
        cutoff_timestamp: int,
        page_size: int,
        max_pages: int,
        end_timestamp: Optional[int] = None,
    ) -> Tuple[List[Dict[str, Any]], bool]:
        rows: List[Dict[str, Any]] = []
        truncated = False
        for page in range(max_pages):
            offset = page * page_size
            params: Dict[str, Any] = {
                "user": address,
                "type": "TRADE",
                "start": cutoff_timestamp,
                "sortBy": "TIMESTAMP",
                "sortDirection": "DESC",
                "limit": page_size,
                "offset": offset,
            }
            if end_timestamp is not None:
                params["end"] = max(0, int(end_timestamp))
            data = self._get(
                self.DATA_API + "/activity",
                params,
            )
            if not isinstance(data, list):
                raise PolymarketApiError("activity response was not a list")
            rows.extend(data)
            if len(data) < page_size:
                break
            if page == max_pages - 1:
                truncated = True
        return rows, truncated

    def live_activity(
        self,
        address: str,
        start_timestamp: int,
        limit: int,
    ) -> List[Dict[str, Any]]:
        data = self._get(
            self.DATA_API + "/activity",
            {
                "user": address,
                "type": "TRADE",
                "start": max(0, start_timestamp),
                "sortBy": "TIMESTAMP",
                "sortDirection": "DESC",
                "limit": limit,
                "offset": 0,
            },
        )
        if not isinstance(data, list):
            raise PolymarketApiError("live activity response was not a list")
        return data

    def positions(self, address: str) -> List[Dict[str, Any]]:
        data = self._get(
            self.DATA_API + "/positions",
            {
                "user": address,
                "sizeThreshold": 0,
                "limit": 500,
                "offset": 0,
                "sortBy": "CURRENT",
                "sortDirection": "DESC",
            },
        )
        if not isinstance(data, list):
            raise PolymarketApiError("positions response was not a list")
        return data

    def market(self, condition_id: str) -> Optional[Dict[str, Any]]:
        try:
            data = self._get(self.CLOB_API + "/markets/" + condition_id, None)
        except PolymarketApiError as exc:
            if "status 404" in str(exc):
                return None
            raise
        if not isinstance(data, dict):
            return None
        return {
            "condition_id": str(data.get("condition_id") or condition_id).lower(),
            "question": data.get("question") or "",
            "market_slug": data.get("market_slug") or "",
            "end_date_iso": data.get("end_date_iso"),
            "closed": bool(data.get("closed")),
            "active": bool(data.get("active")),
        }

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update(
                {
                    "Accept": "application/json",
                    "User-Agent": "blockspace-polymarket-filter/1.0",
                }
            )
            self._local.session = session
        return session

    def _get(self, url: str, params: Optional[Dict[str, Any]]) -> Any:
        last_error: Optional[BaseException] = None
        for attempt in range(self.max_retries):
            try:
                self._wait_for_request_slot()
                response = self._session().get(
                    url,
                    params=params,
                    timeout=self.timeout_seconds,
                )
                if response.status_code == 404:
                    raise PolymarketApiError("status 404 for " + url)
                if response.status_code == 429 or response.status_code >= 500:
                    raise requests.HTTPError(
                        "retryable status %s" % response.status_code,
                        response=response,
                    )
                response.raise_for_status()
                return response.json()
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt + 1 < self.max_retries:
                    retry_after = None
                    response = getattr(exc, "response", None)
                    if response is not None:
                        retry_after = response.headers.get("Retry-After")
                    try:
                        delay = float(retry_after) if retry_after is not None else None
                    except (TypeError, ValueError):
                        delay = None
                    if delay is None:
                        delay = 0.5 * (2 ** attempt)
                    time.sleep(max(0.0, min(delay, 60.0)))
        raise PolymarketApiError("request failed for %s: %s" % (url, last_error))

    def _wait_for_request_slot(self) -> None:
        if self.min_request_interval <= 0:
            return
        with self._rate_lock:
            now = time.monotonic()
            delay = self._next_request_at - now
            if delay > 0:
                time.sleep(delay)
                now = time.monotonic()
            self._next_request_at = now + self.min_request_interval

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

    def __init__(self, timeout_seconds: int = 15, max_retries: int = 3) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._local = threading.local()

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

    def recent_activity(
        self,
        address: str,
        cutoff_timestamp: int,
        page_size: int,
        max_pages: int,
    ) -> Tuple[List[Dict[str, Any]], bool]:
        rows: List[Dict[str, Any]] = []
        truncated = False
        for page in range(max_pages):
            offset = page * page_size
            data = self._get(
                self.DATA_API + "/activity",
                {
                    "user": address,
                    "type": "TRADE",
                    "start": cutoff_timestamp,
                    "sortBy": "TIMESTAMP",
                    "sortDirection": "DESC",
                    "limit": page_size,
                    "offset": offset,
                },
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
                    time.sleep(0.5 * (2 ** attempt))
        raise PolymarketApiError("request failed for %s: %s" % (url, last_error))

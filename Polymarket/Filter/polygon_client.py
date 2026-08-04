"""Bounded, read-only Polygon JSON-RPC client."""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Iterable, List, Optional

import requests


class PolygonRpcError(RuntimeError):
    pass


class PolygonRpcClient:
    def __init__(
        self,
        rpc_url: str,
        expected_chain_id: int = 137,
        timeout_seconds: int = 20,
        max_retries: int = 3,
    ) -> None:
        self.rpc_url = rpc_url
        self.expected_chain_id = expected_chain_id
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._local = threading.local()

    def verify_chain(self) -> int:
        response = self._rpc_call("eth_chainId", [])
        try:
            chain_id = int(response, 16)
        except (TypeError, ValueError):
            raise PolygonRpcError("Polygon RPC returned an invalid chain ID")
        if chain_id != self.expected_chain_id:
            raise PolygonRpcError(
                "expected Polygon chain ID %d, received %d"
                % (self.expected_chain_id, chain_id)
            )
        return chain_id

    def transaction_receipts(
        self, transaction_hashes: Iterable[str], batch_size: int = 20
    ) -> Dict[str, Optional[Dict[str, Any]]]:
        hashes = list(dict.fromkeys(str(value).lower() for value in transaction_hashes))
        result: Dict[str, Optional[Dict[str, Any]]] = {}
        for start in range(0, len(hashes), batch_size):
            chunk = hashes[start : start + batch_size]
            result.update(self._receipt_batch(chunk))
        return result

    def _receipt_batch(
        self, transaction_hashes: List[str]
    ) -> Dict[str, Optional[Dict[str, Any]]]:
        if not transaction_hashes:
            return {}
        payload = [
            {
                "jsonrpc": "2.0",
                "method": "eth_getTransactionReceipt",
                "params": [transaction_hash],
                "id": index + 1,
            }
            for index, transaction_hash in enumerate(transaction_hashes)
        ]
        try:
            data = self._post(payload)
            if not isinstance(data, list):
                raise PolygonRpcError("Polygon RPC batch response was not a list")
            by_id = {item.get("id"): item for item in data if isinstance(item, dict)}
            result = {}
            for index, transaction_hash in enumerate(transaction_hashes):
                item = by_id.get(index + 1, {})
                if item.get("error"):
                    raise PolygonRpcError(str(item["error"]))
                receipt = item.get("result")
                result[transaction_hash] = normalize_receipt(receipt)
            return result
        except PolygonRpcError:
            # Some public RPC providers reject JSON-RPC batches. Fall back to
            # bounded individual receipt lookups without changing semantics.
            return {
                transaction_hash: normalize_receipt(
                    self._rpc_call("eth_getTransactionReceipt", [transaction_hash])
                )
                for transaction_hash in transaction_hashes
            }

    def _rpc_call(self, method: str, params: List[Any]) -> Any:
        data = self._post(
            {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
        )
        if not isinstance(data, dict):
            raise PolygonRpcError("Polygon RPC response was not an object")
        if data.get("error"):
            raise PolygonRpcError(str(data["error"]))
        return data.get("result")

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update(
                {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "blockspace-polymarket-filter/2.0",
                }
            )
            self._local.session = session
        return session

    def _post(self, payload: Any) -> Any:
        last_error: Optional[BaseException] = None
        for attempt in range(self.max_retries):
            try:
                response = self._session().post(
                    self.rpc_url,
                    json=payload,
                    timeout=self.timeout_seconds,
                )
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
        raise PolygonRpcError("Polygon RPC request failed: %s" % last_error)


def normalize_receipt(receipt: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(receipt, dict):
        return None
    transaction_hash = str(receipt.get("transactionHash") or "").lower()
    logs = receipt.get("logs") or []
    return {
        "transaction_hash": transaction_hash,
        "status": _hex_int(receipt.get("status")),
        "block_number": _hex_int(receipt.get("blockNumber")),
        "to": str(receipt.get("to") or "").lower(),
        "log_count": len(logs),
        "log_addresses": sorted(
            {
                str(log.get("address") or "").lower()
                for log in logs
                if isinstance(log, dict) and log.get("address")
            }
        ),
    }


def _hex_int(value: Any) -> Optional[int]:
    try:
        return int(str(value), 16)
    except (TypeError, ValueError):
        return None

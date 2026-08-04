"""Dependency-light HTTP server for the Polymarket Filter dashboard."""

from __future__ import annotations

import argparse
import json
import mimetypes
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse

from service import FilterService


BASE_DIR = Path(__file__).resolve().parent
STATIC_FILES = {
    "/": BASE_DIR / "static" / "index.html",
    "/index.html": BASE_DIR / "static" / "index.html",
    "/app.js": BASE_DIR / "static" / "app.js",
    "/styles.css": BASE_DIR / "static" / "styles.css",
}


class FilterHandler(BaseHTTPRequestHandler):
    service: FilterService
    server_version = "PolymarketFilter/1.0"

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/health":
            snapshot = self.service.snapshot()
            self._json(
                200,
                {
                    "ok": True,
                    "status": snapshot["status"],
                    "candidates": len(snapshot["addresses"]),
                    "filtered": len(snapshot["filtered_addresses"]),
                    "live_events": len(snapshot["live_trades"]),
                },
            )
            return
        if path == "/api/state":
            self._json(200, self.service.public_snapshot())
            return
        if path == "/api/export/addresses.csv":
            self._download(
                self.service.export_addresses_csv(),
                "text/csv; charset=utf-8",
                "polymarket-filter-addresses.csv",
            )
            return
        if path == "/api/export/trades.csv":
            self._download(
                self.service.export_trades_csv(),
                "text/csv; charset=utf-8",
                "polymarket-filter-live-trades.csv",
            )
            return
        if path == "/api/export/snapshot.json":
            payload = json.dumps(
                self.service.snapshot(), ensure_ascii=False, indent=2
            ).encode("utf-8")
            self._download(payload, "application/json", "polymarket-filter-snapshot.json")
            return
        if path in STATIC_FILES:
            self._static(STATIC_FILES[path])
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path != "/api/scan":
            self._json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 65536:
                raise ValueError("request body is too large")
            raw = self.rfile.read(length) if length else b"{}"
            payload: Dict[str, Any] = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("request body must be an object")
            started, message = self.service.start_scan(payload)
            if not started:
                self._json(409, {"error": message})
                return
            self._json(202, {"scan_id": message, "status": "scanning"})
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})

    def log_message(self, format_string: str, *args: Any) -> None:
        print("%s - %s" % (self.address_string(), format_string % args))

    def _static(self, path: Path) -> None:
        try:
            payload = path.read_bytes()
        except OSError:
            self._json(404, {"error": "static file not found"})
            return
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' https: data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, value: Any) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def _download(self, payload: bytes, content_type: str, filename: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Content-Disposition", 'attachment; filename="%s"' % filename)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)


def load_config(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Polymarket CRYPTO leaderboard filter")
    parser.add_argument("--config", type=Path, default=BASE_DIR / "config.json")
    parser.add_argument("--host", help="override configured bind host")
    parser.add_argument("--port", type=int, help="override configured port")
    args = parser.parse_args()

    config = load_config(args.config)
    host = args.host or config["http"]["host"]
    port = args.port or int(config["http"]["port"])
    service = FilterService(BASE_DIR, config)
    FilterHandler.service = service
    server = ThreadingHTTPServer((host, port), FilterHandler)

    def stop_server(_signum: int, _frame: Any) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop_server)
    print("Polymarket Filter listening on http://%s:%d" % (host, port))
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        service.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()

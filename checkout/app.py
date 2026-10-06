"""The checkout service: POST /pay, GET /metrics and GET /healthz.

Each request writes one JSON line to standard output. An error line holds the exception and the
stack frame where it was raised.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import prometheus_client
from prometheus_client import CollectorRegistry, Counter

from checkout import orders, store

REVISION = os.environ.get("APP_REVISION", "dev")
PORT = int(os.environ.get("PORT", "8080"))
MAX_BODY = 16384
SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

prometheus_client.disable_created_metrics()
REGISTRY = CollectorRegistry()
REQUESTS = Counter(
    "http_requests",
    "Requests of POST /pay by status code and code revision.",
    ["code", "revision"],
    registry=REGISTRY,
)
for _code in ("200", "400", "500"):
    REQUESTS.labels(code=_code, revision=REVISION)

_out = threading.Lock()


def log(**fields: object) -> None:
    now = time.time()
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + f".{int(now % 1 * 1000):03d}Z"
    line = json.dumps({"ts": stamp, **fields}, separators=(",", ":"), default=str)
    with _out:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def _source(path: str) -> str:
    if path.startswith(SOURCE_ROOT + os.sep):
        return path[len(SOURCE_ROOT) + 1 :]
    return path


def error_fields(exc: BaseException) -> dict:
    """The exception and its stack frames, innermost last."""
    stack = [f"{_source(f.filename)}:{f.lineno} in {f.name}" for f in traceback.extract_tb(exc.__traceback__)]
    return {
        "exception": "".join(traceback.format_exception_only(exc)).strip(),
        "frame": stack[-1] if stack else "",
        "stack": stack,
    }


def _text(body: dict, name: str) -> str:
    value = body.get(name)
    return value[: orders.MAX_ID_LENGTH] if isinstance(value, str) else ""


class Handler(BaseHTTPRequestHandler):
    server_version = "checkout"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        """The service writes its own JSON line for each request."""

    def _send(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload, separators=(",", ":")).encode())

    def do_GET(self) -> None:
        started = time.monotonic()
        if self.path == "/healthz":
            self._json(200, {"status": "ok"})
        elif self.path == "/metrics":
            self._send(200, prometheus_client.generate_latest(REGISTRY), prometheus_client.CONTENT_TYPE_LATEST)
        else:
            self._json(404, {"error": "not_found"})
        self._log_request(200 if self.path in ("/healthz", "/metrics") else 404, started)

    def do_POST(self) -> None:
        started = time.monotonic()
        if self.path != "/pay":
            self._json(404, {"error": "not_found"})
            self._log_request(404, started)
            return
        status, payload, extra = self._pay()
        REQUESTS.labels(code=str(status), revision=REVISION).inc()
        self._json(status, payload)
        self._log_request(status, started, **extra)

    def _pay(self) -> tuple[int, dict, dict]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if not 0 < length <= MAX_BODY:
            return 400, {"error": "invalid_request", "message": "a JSON body is required"}, {}
        body: dict = {}
        try:
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise orders.OrderError("the body must be a JSON object")
            order = orders.parse_order(body)
        except ValueError as exc:
            return 400, {"error": "invalid_request", "message": str(exc)}, {}
        except Exception as exc:  # noqa: BLE001 (an unexpected error answers 500)
            ids = {"order_id": _text(body, "order_id"), "client_id": _text(body, "client_id")}
            attempt = body.get("attempt", 1)
            attempt = attempt if attempt in range(1, orders.MAX_ATTEMPT + 1) else 1
            try:
                store.record(ids["order_id"], attempt, ids["client_id"], "error", REVISION)
            except Exception as store_exc:  # noqa: BLE001
                return 500, {"error": "internal_error"}, {**ids, **error_fields(store_exc)}
            return 500, {"error": "internal_error"}, {**ids, "attempt": attempt, **error_fields(exc)}
        try:
            store.record(order.order_id, order.attempt, order.client_id, "ok", REVISION)
        except Exception as exc:  # noqa: BLE001
            return 500, {"error": "internal_error"}, {"order_id": order.order_id, **error_fields(exc)}
        extra = {"order_id": order.order_id, "attempt": order.attempt}
        return 200, {"status": "paid", "order_id": order.order_id}, extra

    def _log_request(self, status: int, started: float, **extra: object) -> None:
        log(
            level="error" if status >= 500 else "info",
            method=self.command,
            path=self.path,
            status=status,
            revision=REVISION,
            duration_ms=round((time.monotonic() - started) * 1000, 1),
            **extra,
        )


def make_server(port: int = PORT) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.daemon_threads = False
    return server


def main() -> None:
    server = make_server()

    def stop(signum: int, frame: object) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log(level="info", event="start", revision=REVISION, port=PORT)
    server.serve_forever()
    server.server_close()
    store.close()
    log(level="info", event="stop", revision=REVISION)


if __name__ == "__main__":
    main()

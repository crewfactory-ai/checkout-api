import http.client
import json
import threading
import time

import pytest

from checkout import app, orders, store


@pytest.fixture
def server(monkeypatch):
    rows = []
    monkeypatch.setattr(store, "record", lambda *row: rows.append(row))
    srv = app.make_server(0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv.server_address[1], rows
    srv.shutdown()
    srv.server_close()


def call(port, method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    data = None if body is None else json.dumps(body).encode()
    conn.request(method, path, body=data, headers={"Content-Type": "application/json", "Connection": "close"})
    resp = conn.getresponse()
    text = resp.read().decode()
    conn.close()
    return resp.status, text


def log_lines(capsys, wanted):
    """The JSON log lines that match: the server writes each line just after its answer."""
    seen = []
    for _ in range(100):
        seen += [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.startswith("{")]
        hits = [x for x in seen if wanted(x)]
        if hits:
            return hits
        time.sleep(0.02)
    raise AssertionError(f"no matching log line in {seen}")


def test_health(server):
    port, _ = server
    assert call(port, "GET", "/healthz") == (200, '{"status":"ok"}')


def test_a_payment_writes_one_row_and_counts_it(server):
    port, rows = server
    status, _ = call(port, "POST", "/pay", {"order_id": "o1", "client_id": "c1", "amount": "5", "currency_code": "EUR"})
    assert status == 200
    assert rows == [("o1", 1, "c1", "ok", app.REVISION)]
    _, metrics = call(port, "GET", "/metrics")
    assert f'http_requests_total{{code="200",revision="{app.REVISION}"}} 1.0' in metrics
    assert f'http_requests_total{{code="500",revision="{app.REVISION}"}} 0.0' in metrics


def test_an_invalid_request_writes_no_row(server):
    port, rows = server
    status, _ = call(port, "POST", "/pay", {"order_id": "o1", "currency_code": "EUR"})
    assert status == 400
    assert rows == []


def test_an_unexpected_error_answers_500_and_writes_an_error_row(server, monkeypatch, capsys):
    port, rows = server

    def broken(body):
        raise RuntimeError("boom")

    monkeypatch.setattr(orders, "parse_order", broken)
    status, _ = call(port, "POST", "/pay", {"order_id": "o2", "client_id": "c2", "amount": "5", "attempt": 2})
    assert status == 500
    assert rows == [("o2", 2, "c2", "error", app.REVISION)]
    line = log_lines(capsys, lambda x: x.get("status") == 500)[-1]
    assert line["exception"] == "RuntimeError: boom"
    assert line["frame"].startswith("tests/test_app.py:")

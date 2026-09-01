# checkout-api

A sample codebase: a small payments service in Python.

## Endpoints

| Method and path | What it does |
| --- | --- |
| `POST /pay` | Takes `{"order_id", "client_id", "amount", "currency_code"}` and an optional `attempt`. Writes one row for the attempt. Answers `200`, `400` for an invalid request, or `500` on an error. |
| `GET /metrics` | The counter `http_requests_total{code, revision}` of `POST /pay` in the Prometheus text format. |
| `GET /healthz` | `200` while the process runs. |

Each request writes one JSON line to standard output. An error line holds the exception and the
stack frame.

## Layout

| Path | Content |
| --- | --- |
| `checkout/app.py` | The HTTP server |
| `checkout/orders.py` | The checks of one payment request |
| `checkout/store.py` | The database writes |
| `checkout/loadgen.py` | A load generator: `python -m checkout.loadgen` |
| `tests/` | The tests: `python -m pytest -q` |

## Settings

| Variable | Use |
| --- | --- |
| `APP_REVISION` | The code revision, the label `revision` of the counter |
| `PORT` | The listen port, 8080 by default |
| `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`, `PGSSLMODE` | The PostgreSQL connection |

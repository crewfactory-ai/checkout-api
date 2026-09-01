"""The store: one row in payment_attempts for each attempt.

The service connects to the primary as payments_app, which may only insert. libpq reads the
connection from the environment: PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD and PGSSLMODE.
"""

from __future__ import annotations

import os
import queue
import threading

import psycopg

POOL_SIZE = int(os.environ.get("PG_POOL_SIZE", "4"))
WAIT_SECONDS = 5

INSERT = (
    "INSERT INTO payment_attempts (order_id, attempt, client_id, outcome, revision) "
    "VALUES (%s, %s, %s, %s, %s)"
)

_idle: queue.LifoQueue[psycopg.Connection] = queue.LifoQueue()
_lock = threading.Lock()
_open = 0


def _connect() -> psycopg.Connection:
    return psycopg.connect(autocommit=True, connect_timeout=WAIT_SECONDS, application_name="checkout-api")


def _take() -> psycopg.Connection:
    global _open
    try:
        return _idle.get_nowait()
    except queue.Empty:
        pass
    with _lock:
        grow = _open < POOL_SIZE
        if grow:
            _open += 1
    if grow:
        try:
            return _connect()
        except Exception:
            with _lock:
                _open -= 1
            raise
    return _idle.get(timeout=WAIT_SECONDS)


def _drop(conn: psycopg.Connection) -> None:
    global _open
    conn.close()
    with _lock:
        _open -= 1


def record(order_id: str, attempt: int, client_id: str, outcome: str, revision: str) -> None:
    """Write one attempt. Raise when the database refuses it or cannot be reached."""
    conn = _take()
    try:
        conn.execute(INSERT, (order_id, attempt, client_id, outcome, revision))
    except Exception:
        _drop(conn)
        raise
    _idle.put(conn)


def close() -> None:
    while True:
        try:
            _drop(_idle.get_nowait())
        except queue.Empty:
            return

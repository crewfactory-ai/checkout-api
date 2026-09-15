"""The load generator: new payments at a fixed rate, and the client's retry after an error.

Each second it sends RATE new orders from CLIENTS pseudonymous client IDs. A share of the orders
(NO_CURRENCY_SHARE) holds no currency_code, as an older mobile client sends them. After a 500 the
client sends the same order again after RETRY_DELAY seconds, with currency_code EUR, in a share
of the cases (RETRY_SHARE). It writes one JSON line of counts every 10 seconds.
"""

from __future__ import annotations

import heapq
import http.client
import json
import os
import random
import signal
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

CURRENCIES = ["EUR"] * 6 + ["SEK", "NOK", "DKK", "PLN"]


@dataclass
class Settings:
    target_url: str = "http://checkout-api/pay"
    rate: float = 20.0
    clients: int = 50000
    no_currency_share: float = 0.30
    retry_share: float = 0.85
    retry_delay: float = 5.0
    workers: int = 48
    seed: int | None = None

    @classmethod
    def from_env(cls, env: dict[str, str]) -> Settings:
        s = cls()
        s.target_url = env.get("TARGET_URL", s.target_url)
        s.rate = float(env.get("RATE", s.rate))
        s.clients = int(env.get("CLIENTS", s.clients))
        s.no_currency_share = float(env.get("NO_CURRENCY_SHARE", s.no_currency_share))
        s.retry_share = float(env.get("RETRY_SHARE", s.retry_share))
        s.retry_delay = float(env.get("RETRY_DELAY", s.retry_delay))
        s.workers = int(env.get("WORKERS", s.workers))
        s.seed = int(env["SEED"]) if env.get("SEED") else None
        return s


def new_order(rng: random.Random, settings: Settings) -> dict:
    order = {
        "order_id": f"ord-{rng.getrandbits(64):016x}",
        "client_id": f"cl-{rng.randrange(settings.clients):05d}",
        "amount": f"{rng.randrange(500, 50000) / 100:.2f}",
    }
    if rng.random() >= settings.no_currency_share:
        order["currency_code"] = rng.choice(CURRENCIES)
    return order


def retry_of(order: dict) -> dict:
    """The client's second attempt of a failed order: the same order, in euro."""
    return {**order, "currency_code": "EUR", "attempt": order.get("attempt", 1) + 1}


@dataclass
class Counts:
    sent: int = 0
    ok: int = 0
    errors: int = 0
    refused: int = 0
    retries: int = 0
    unreachable: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, **changes: int) -> None:
        with self.lock:
            for name, value in changes.items():
                setattr(self, name, getattr(self, name) + value)

    def snapshot(self) -> dict:
        with self.lock:
            return {k: getattr(self, k) for k in ("sent", "ok", "errors", "refused", "retries", "unreachable")}


class LoadGenerator:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.url = urllib.parse.urlsplit(settings.target_url)
        self.rng = random.Random(settings.seed)
        self.rng_lock = threading.Lock()
        self.counts = Counts()
        self.due: list[tuple[float, int, dict, int]] = []
        self.due_lock = threading.Lock()
        self.sequence = 0
        self.stopping = threading.Event()

    def _random(self) -> float:
        with self.rng_lock:
            return self.rng.random()

    def _later(self, delay: float, order: dict, tries: int = 0) -> None:
        with self.due_lock:
            self.sequence += 1
            heapq.heappush(self.due, (time.monotonic() + delay, self.sequence, order, tries))

    def post(self, order: dict) -> int:
        """Send one order. Return the HTTP status, or 0 when the service cannot be reached."""
        body = json.dumps(order, separators=(",", ":")).encode()
        conn = http.client.HTTPConnection(self.url.hostname, self.url.port or 80, timeout=10)
        try:
            conn.request("POST", self.url.path or "/", body=body,
                         headers={"Content-Type": "application/json", "Connection": "close"})
            resp = conn.getresponse()
            resp.read()
            return resp.status
        except OSError:
            return 0
        finally:
            conn.close()

    def handle(self, order: dict, tries: int) -> None:
        status = self.post(order)
        if status == 0:
            # No answer is no payment attempt: send the same request again after 1 s, 3 times at most.
            self.counts.add(unreachable=1)
            if tries < 3:
                self._later(1.0, order, tries + 1)
            return
        self.counts.add(sent=1)
        if status == 200:
            self.counts.add(ok=1)
        elif status >= 500:
            self.counts.add(errors=1)
            if order.get("attempt", 1) == 1 and self._random() < self.settings.retry_share:
                self.counts.add(retries=1)
                self._later(self.settings.retry_delay, retry_of(order))
        else:
            self.counts.add(refused=1)

    def run(self, duration: float | None = None) -> None:
        interval = 1.0 / self.settings.rate
        started = time.monotonic()
        next_new = started
        next_report = started + 10
        with ThreadPoolExecutor(max_workers=self.settings.workers) as pool:
            while not self.stopping.is_set():
                now = time.monotonic()
                if duration is not None and now - started >= duration:
                    break
                while next_new <= now:
                    with self.rng_lock:
                        order = new_order(self.rng, self.settings)
                    pool.submit(self.handle, order, 0)
                    next_new += interval
                with self.due_lock:
                    ready = []
                    while self.due and self.due[0][0] <= now:
                        ready.append(heapq.heappop(self.due))
                for _, _, order, tries in ready:
                    pool.submit(self.handle, order, tries)
                if now >= next_report:
                    print(json.dumps({"event": "load", **self.counts.snapshot()}), flush=True)
                    next_report += 10
                time.sleep(max(0.0, min(next_new, now + 0.05) - time.monotonic()))


def main() -> None:
    settings = Settings.from_env(dict(os.environ))
    generator = LoadGenerator(settings)
    signal.signal(signal.SIGTERM, lambda signum, frame: generator.stopping.set())
    print(json.dumps({"event": "start", "target_url": settings.target_url, "rate": settings.rate}), flush=True)
    generator.run()
    print(json.dumps({"event": "stop", **generator.counts.snapshot()}), flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()

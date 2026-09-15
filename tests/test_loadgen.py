import random

from checkout import loadgen


def test_settings_from_the_environment():
    s = loadgen.Settings.from_env({"RATE": "5", "TARGET_URL": "http://svc:8080/pay", "SEED": "7"})
    assert (s.rate, s.target_url, s.seed, s.clients) == (5.0, "http://svc:8080/pay", 7, 50000)


def test_the_share_of_orders_with_no_currency():
    settings = loadgen.Settings()
    rng = random.Random(1)
    sample = [loadgen.new_order(rng, settings) for _ in range(20000)]
    share = sum("currency_code" not in o for o in sample) / len(sample)
    assert 0.28 < share < 0.32
    assert all(o["client_id"].startswith("cl-") and len(o["client_id"]) == 8 for o in sample)
    assert len({o["order_id"] for o in sample}) == len(sample)


def test_a_retry_is_the_same_order_in_euro():
    order = {"order_id": "ord-1", "client_id": "cl-00001", "amount": "9.90"}
    assert loadgen.retry_of(order) == {**order, "currency_code": "EUR", "attempt": 2}


def test_a_500_is_retried_once_in_most_cases(monkeypatch):
    gen = loadgen.LoadGenerator(loadgen.Settings(seed=3))
    monkeypatch.setattr(gen, "post", lambda order: 500)
    for _ in range(1000):
        gen.handle({"order_id": "o", "client_id": "c", "amount": "1"}, 0)
    assert 800 < gen.counts.retries < 900
    first_retry = gen.due[0][2]
    gen.due.clear()
    gen.handle(first_retry, 0)
    assert gen.due == []

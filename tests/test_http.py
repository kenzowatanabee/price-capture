import threading

import pytest
from curl_cffi import requests as cffi_requests

from price_capture import http


class FakeResponse:
    def __init__(self, status_code=200, headers=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = ""


@pytest.fixture
def no_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr(http.time, "sleep", lambda s: sleeps.append(s))
    return sleeps


def record_sessions(monkeypatch):
    seen = []

    def fake_get(self, url, **kwargs):
        seen.append((id(self), url))
        return FakeResponse()

    monkeypatch.setattr(cffi_requests.Session, "get", fake_get)
    return seen


def test_sessions_are_reused_per_domain_within_a_thread(monkeypatch):
    seen = record_sessions(monkeypatch)
    http.fetch("https://a.test/1")
    http.fetch("https://a.test/2")
    http.fetch("https://b.test/1")
    assert seen[0][0] == seen[1][0]
    assert seen[0][0] != seen[2][0]


def test_sessions_are_not_shared_between_threads(monkeypatch):
    seen = record_sessions(monkeypatch)
    http.fetch("https://shared.test/1")
    t = threading.Thread(target=http.fetch, args=("https://shared.test/2",))
    t.start()
    t.join()
    assert seen[0][0] != seen[1][0]


def test_concurrency_per_domain_is_capped(monkeypatch):
    import time as real_time

    monkeypatch.setattr(http, "PER_STORE_CONCURRENCY", 2)
    state = {"now": 0, "max": 0}
    lock = threading.Lock()

    def slow_get(self, url, **kwargs):
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        real_time.sleep(0.05)
        with lock:
            state["now"] -= 1
        return FakeResponse()

    monkeypatch.setattr(cffi_requests.Session, "get", slow_get)
    threads = [threading.Thread(target=http.fetch, args=("https://capped.test/x",)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert state["max"] == 2


def test_different_domains_do_not_share_a_cap(monkeypatch):
    import time as real_time

    monkeypatch.setattr(http, "PER_STORE_CONCURRENCY", 1)
    barrier = threading.Barrier(2, timeout=5)

    def get(self, url, **kwargs):
        barrier.wait()  # only passes if both domains are in flight at once
        return FakeResponse()

    monkeypatch.setattr(cffi_requests.Session, "get", get)
    threads = [threading.Thread(target=http.fetch, args=(f"https://own{i}.test/x",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not barrier.broken


def test_jitter_sleeps_a_random_amount_before_each_request(monkeypatch, no_sleep):
    monkeypatch.setattr(http, "REQUEST_JITTER", 0.5)
    monkeypatch.setattr(http.random, "uniform", lambda a, b: b)  # worst case
    record_sessions(monkeypatch)
    http.fetch("https://jitter.test/x")
    assert no_sleep == [0.5]

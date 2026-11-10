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

import pytest

from price_capture import http


@pytest.fixture(autouse=True)
def no_request_jitter(monkeypatch):
    """Keep tests fast: no random delay before requests (tests that need it set it themselves)."""
    monkeypatch.setattr(http, "REQUEST_JITTER", 0)

import pytest

from price_capture import http
from price_capture.platforms import base


@pytest.fixture(autouse=True)
def no_request_jitter(monkeypatch):
    """Keep tests fast: no random delay before requests (tests that need it set it themselves)."""
    monkeypatch.setattr(http, "REQUEST_JITTER", 0)


@pytest.fixture(autouse=True)
def no_cart_check(monkeypatch):
    """The VTEX cart availability check makes a POST; tests opt in explicitly so nothing hits the network."""
    monkeypatch.setattr(base, "CART_CHECK_CEP", "")

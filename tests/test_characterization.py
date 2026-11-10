"""Behaviour that must not change during the refactor."""
import json

from curl_cffi import requests as cffi_requests

from price_capture import scrape


class FakeResponse:
    def __init__(self, status_code=200, text="", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self):
        return self._payload


PRODUCT = {"ean": "7509546060781", "desc_sku": "CREME DENTAL TESTE 50g"}


def patch_get(monkeypatch, handler):
    monkeypatch.setattr(cffi_requests.Session, "get", lambda self, url, **kwargs: handler(url, **kwargs))


def test_vtex_io_html_takes_lowest_positive_offer_and_product_name(monkeypatch):
    state = {
        "Product:1": {"__typename": "Product", "productName": "Creme VTEX IO"},
        "Offer:1": {"__typename": "CommertialOffer", "Price": 0},
        "Offer:2": {"__typename": "CommertialOffer", "Price": 14.5},
        "Offer:3": {"__typename": "CommertialOffer", "Price": 12.9},
    }
    html = (
        '<template data-type="json" data-varname="__STATE__"><script>'
        + json.dumps(state)
        + "</script></template>"
    )
    patch_get(monkeypatch, lambda *a, **k: FakeResponse(text=html))
    res = scrape.scrape_store_product(
        {"name": "S", "type": "VTEX_IO_HTML", "domain": "https://x.test"}, PRODUCT
    )
    assert res == {"status": "Success", "product_name": "Creme VTEX IO", "price_brl": 12.9}


def test_vtex_io_html_without_state_is_not_found(monkeypatch):
    patch_get(monkeypatch, lambda *a, **k: FakeResponse(text="<html>sem estado</html>"))
    res = scrape.scrape_store_product(
        {"name": "S", "type": "VTEX_IO_HTML", "domain": "https://x.test"}, PRODUCT
    )
    assert res["status"] == "Not Found"
    assert res["message"].startswith("NO_STATE:")


def test_unknown_store_type_is_a_config_error_and_makes_no_request(monkeypatch):
    def handler(*a, **k):
        raise AssertionError("no request expected")

    patch_get(monkeypatch, handler)
    res = scrape.scrape_store_product(
        {"name": "S", "type": "SOMETHING_NEW", "domain": "https://x.test"}, PRODUCT
    )
    assert res == {"status": "Error", "message": "CONFIG: unknown store type 'SOMETHING_NEW'"}


def test_missing_domain_is_an_error():
    res = scrape.scrape_store_product({"name": "S", "type": "B2C_REST", "domain": ""}, PRODUCT)
    assert res == {"status": "Error", "message": "CONFIG: domain URL not configured"}


def test_request_urls_per_platform(monkeypatch):
    seen = []

    def handler(url, **k):
        seen.append(url)
        return FakeResponse(status_code=500)

    patch_get(monkeypatch, handler)
    for t in ["B2C_REST", "RD_NEXTJS", "VTEX_IO_HTML", "CARREFOUR_INTELLIGENT_SEARCH"]:
        scrape.scrape_store_product({"name": "S", "type": t, "domain": "https://x.test"}, PRODUCT)
    assert seen == [
        "https://x.test/api/catalog_system/pub/products/search?fq=alternateIds_Ean:7509546060781",
        "https://x.test/search?w=7509546060781&search-type=direct",
        "https://x.test/busca?q=7509546060781&lang=pt_BR",
        "https://x.test/_v/api/intelligent-search/product_search/v2/?query=7509546060781&sc=1&page=1&count=1&sort=&hideUnavailableItems=false",
    ]

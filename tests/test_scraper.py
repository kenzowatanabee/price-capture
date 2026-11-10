import json
import re

import pytest
from curl_cffi import requests as cffi_requests

import scraper
from price_capture import config
from price_capture.platforms import embedded_state


class FakeResponse:
    def __init__(self, status_code=200, text="", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self):
        return self._payload


def make_store(store_type, name="Store"):
    return {"name": name, "type": store_type, "domain": "https://example.test"}


PRODUCT = {"ean": "7509546060781", "desc_sku": "CREME DENTAL TESTE 50g"}


def next_html(products):
    state = {"props": {"pageProps": {"data": {"products": products}}}}
    return (
        '<html><script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(state)
        + "</script></html>"
    )


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(scraper.time, "sleep", lambda _s: None)


def patch_get(monkeypatch, handler):
    monkeypatch.setattr(scraper.requests, "get", handler)


# ---------- helpers ----------

def test_clean_ean_strips_spaces_and_dashes():
    assert config.clean_ean(" 7509-5460 60781 ") == "7509546060781"


def test_find_true_products_array_skips_lists_without_sku():
    data = {
        "products": [{"name": "banner"}],
        "nested": {"products": [{"sku": "1", "name": "real"}]},
    }
    assert embedded_state.find_true_products_array(data) == [{"sku": "1", "name": "real"}]


def test_find_true_products_array_none_when_missing():
    assert embedded_state.find_true_products_array({"a": [1, 2, {"b": 3}]}) is None


def test_extract_state_next_js():
    html = next_html([{"sku": "1"}])
    state, kind = embedded_state.extract_state_from_html(html)
    assert kind == "NEXT_JS"
    assert state["props"]["pageProps"]["data"]["products"] == [{"sku": "1"}]


def test_extract_state_vtex_template():
    html = (
        '<template data-type="json" data-varname="__STATE__">'
        '<script>{"a": 1}</script></template>'
    )
    assert embedded_state.extract_state_from_html(html) == ({"a": 1}, "VTEX_IO")


def test_extract_state_vtex_window_state():
    html = '<script>window.__STATE__ = {"a": 2};</script>'
    assert embedded_state.extract_state_from_html(html) == ({"a": 2}, "VTEX_IO")


def test_extract_state_none():
    assert embedded_state.extract_state_from_html("<html></html>") == (None, None)


# ---------- csv loaders ----------

def test_load_products_cleans_and_skips_empty(tmp_path):
    f = tmp_path / "products.csv"
    f.write_text("ean,desc_sku\n7509-546 060781, Creme \n,sem ean\n", encoding="utf-8")
    assert config.load_products(str(f)) == [
        {"ean": "7509546060781", "desc_sku": "Creme"}
    ]


def test_load_stores_respects_enabled_flag(tmp_path):
    f = tmp_path / "stores.csv"
    f.write_text(
        "store_id,type,domain,enabled\n"
        "A,B2C_REST,https://a.test,true\n"
        "B,B2C_REST,https://b.test,false\n"
        "C,B2C_REST,https://c.test,yes\n",
        encoding="utf-8",
    )
    assert [s["name"] for s in config.load_stores(str(f))] == ["A", "C"]


def test_load_stores_enabled_defaults_to_true_without_column(tmp_path):
    f = tmp_path / "stores.csv"
    f.write_text("store_id,type,domain\nA,B2C_REST,https://a.test\n", encoding="utf-8")
    assert len(config.load_stores(str(f))) == 1


# ---------- scrape_store_product ----------

def vtex_payload(price, name="Produto X"):
    return [{"productName": name, "items": [{"sellers": [{"commertialOffer": {"Price": price}}]}]}]


def test_b2c_rest_success(monkeypatch):
    patch_get(monkeypatch, lambda *a, **k: FakeResponse(payload=vtex_payload(12.9)))
    res = scraper.scrape_store_product(make_store("B2C_REST"), PRODUCT)
    assert res == {"status": "Success", "product_name": "Produto X", "price_brl": 12.9}


def test_b2c_rest_empty_result_is_not_found(monkeypatch):
    patch_get(monkeypatch, lambda *a, **k: FakeResponse(payload=[]))
    res = scraper.scrape_store_product(make_store("B2C_REST"), PRODUCT)
    assert res["status"] == "Not Found"


def test_carrefour_intelligent_search_success(monkeypatch):
    payload = {"products": vtex_payload(8.5, "Carrefour item")}
    patch_get(monkeypatch, lambda *a, **k: FakeResponse(payload=payload))
    res = scraper.scrape_store_product(make_store("CARREFOUR_INTELLIGENT_SEARCH"), PRODUCT)
    assert (res["status"], res["price_brl"]) == ("Success", 8.5)


def test_rd_nextjs_prefers_first_party_seller(monkeypatch):
    html = next_html(
        [
            {"sku": "1", "name": "Marketplace", "priceService": 20.0, "is1P": False},
            {"sku": "2", "name": "Loja propria", "priceService": 15.0, "is1P": True},
        ]
    )
    patch_get(monkeypatch, lambda *a, **k: FakeResponse(text=html))
    res = scraper.scrape_store_product(make_store("RD_NEXTJS"), PRODUCT)
    assert (res["product_name"], res["price_brl"]) == ("Loja propria", 15.0)


def test_http_error_status(monkeypatch):
    patch_get(monkeypatch, lambda *a, **k: FakeResponse(status_code=403))
    res = scraper.scrape_store_product(make_store("B2C_REST"), PRODUCT)
    assert res == {"status": "Error", "message": "HTTP 403"}


def test_timeout_is_retried_then_succeeds(monkeypatch, no_sleep):
    calls = {"n": 0}

    def handler(*a, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise cffi_requests.errors.RequestsError("timed out", 28)
        return FakeResponse(payload=vtex_payload(5.0))

    patch_get(monkeypatch, handler)
    res = scraper.scrape_store_product(make_store("B2C_REST"), PRODUCT)
    assert res["status"] == "Success"
    assert calls["n"] == 3


def test_timeout_gives_up_after_retries(monkeypatch, no_sleep):
    def handler(*a, **k):
        raise cffi_requests.errors.RequestsError("timed out", 28)

    patch_get(monkeypatch, handler)
    res = scraper.scrape_store_product(make_store("B2C_REST"), PRODUCT)
    assert res == {"status": "Error", "message": "Connection Timed Out (curl 28)"}


def test_non_timeout_curl_error_is_not_retried_and_reports_code(monkeypatch, no_sleep):
    calls = {"n": 0}

    def handler(*a, **k):
        calls["n"] += 1
        raise cffi_requests.errors.RequestsError("SSL certificate problem", 60)

    patch_get(monkeypatch, handler)
    res = scraper.scrape_store_product(make_store("B2C_REST"), PRODUCT)
    assert calls["n"] == 1
    assert res["status"] == "Error"
    assert "curl 60" in res["message"]


def test_waf_block_detected_for_vtex_io_html(monkeypatch):
    patch_get(monkeypatch, lambda *a, **k: FakeResponse(text="<html>Just a moment...</html>"))
    res = scraper.scrape_store_product(make_store("VTEX_IO_HTML"), PRODUCT)
    assert res == {"status": "Error", "message": "Blocked by WAF / Cloudflare"}


# ---------- process_task / summary ----------

def test_process_task_record_shape_and_timestamp(monkeypatch):
    monkeypatch.setattr(
        scraper,
        "scrape_store_product",
        lambda store, product: {"status": "Success", "product_name": "X", "price_brl": 9.9},
    )
    rec = scraper.process_task((make_store("B2C_REST", "Loja"), PRODUCT))
    assert set(rec) == set(scraper.CSV_COLUMNS)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", rec["fetched_at"])


def test_summarize_results_rate(capsys):
    results = [
        {"store": "A", "status": "Success"},
        {"store": "A", "status": "Error"},
        {"store": "B", "status": "Success"},
        {"store": "B", "status": "Not Found"},
    ]
    assert scraper.summarize_results(results, 4) == 0.5
    assert "A" in capsys.readouterr().out


def test_summarize_counts_missing_results_as_failures():
    # 1 success out of 4 expected requests (3 workers crashed) -> 25%
    assert scraper.summarize_results([{"store": "A", "status": "Success"}], 4) == 0.25

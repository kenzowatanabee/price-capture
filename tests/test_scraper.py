import json
import re
import time

import pytest
from curl_cffi import requests as cffi_requests

from price_capture import config, runner, scrape
from price_capture.platforms import embedded_state
from price_capture.schema import CSV_COLUMNS


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
    monkeypatch.setattr(time, "sleep", lambda _s: None)


def patch_get(monkeypatch, handler):
    monkeypatch.setattr(cffi_requests.Session, "get", lambda self, url, **kwargs: handler(url, **kwargs))


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


def test_load_stores_rejects_unknown_type(tmp_path):
    f = tmp_path / "stores.csv"
    f.write_text("store_id,type,domain\nA,B2C_RST,https://a.test\n", encoding="utf-8")
    with pytest.raises(config.ConfigError, match="B2C_RST"):
        config.load_stores(str(f))


def test_load_stores_ignores_unknown_type_when_disabled(tmp_path):
    f = tmp_path / "stores.csv"
    f.write_text("store_id,type,domain,enabled\nA,WIP,https://a.test,false\n", encoding="utf-8")
    assert config.load_stores(str(f)) == []


def test_load_stores_enabled_defaults_to_true_without_column(tmp_path):
    f = tmp_path / "stores.csv"
    f.write_text("store_id,type,domain\nA,B2C_REST,https://a.test\n", encoding="utf-8")
    assert len(config.load_stores(str(f))) == 1


# ---------- scrape_store_product ----------

def vtex_payload(price, name="Produto X"):
    return [{"productName": name, "items": [{"sellers": [{"commertialOffer": {"Price": price}}]}]}]


def test_b2c_rest_success(monkeypatch):
    patch_get(monkeypatch, lambda *a, **k: FakeResponse(payload=vtex_payload(12.9)))
    res = scrape.scrape_store_product(make_store("B2C_REST"), PRODUCT)
    assert res == {"status": "Success", "product_name": "Produto X", "price_brl": 12.9}


def test_b2c_rest_empty_result_is_not_listed(monkeypatch):
    patch_get(monkeypatch, lambda *a, **k: FakeResponse(payload=[]))
    res = scrape.scrape_store_product(make_store("B2C_REST"), PRODUCT)
    assert res["status"] == "Not Found"
    assert res["message"].startswith("NOT_LISTED:")


def test_carrefour_intelligent_search_success(monkeypatch):
    payload = {"products": vtex_payload(8.5, "Carrefour item")}
    patch_get(monkeypatch, lambda *a, **k: FakeResponse(payload=payload))
    res = scrape.scrape_store_product(make_store("CARREFOUR_INTELLIGENT_SEARCH"), PRODUCT)
    assert (res["status"], res["price_brl"]) == ("Success", 8.5)


def test_rd_nextjs_prefers_first_party_seller(monkeypatch):
    html = next_html(
        [
            {"sku": "1", "name": "Marketplace", "priceService": 20.0, "is1P": False},
            {"sku": "2", "name": "Loja propria", "priceService": 15.0, "is1P": True},
        ]
    )
    patch_get(monkeypatch, lambda *a, **k: FakeResponse(text=html))
    res = scrape.scrape_store_product(make_store("RD_NEXTJS"), PRODUCT)
    assert (res["product_name"], res["price_brl"]) == ("Loja propria", 15.0)


def test_http_error_status(monkeypatch):
    patch_get(monkeypatch, lambda *a, **k: FakeResponse(status_code=403))
    res = scrape.scrape_store_product(make_store("B2C_REST"), PRODUCT)
    assert res == {"status": "Error", "message": "HTTP_ERROR: 403"}


def test_timeout_is_retried_then_succeeds(monkeypatch, no_sleep):
    calls = {"n": 0}

    def handler(*a, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise cffi_requests.errors.RequestsError("timed out", 28)
        return FakeResponse(payload=vtex_payload(5.0))

    patch_get(monkeypatch, handler)
    res = scrape.scrape_store_product(make_store("B2C_REST"), PRODUCT)
    assert res["status"] == "Success"
    assert calls["n"] == 3


def test_timeout_gives_up_after_retries(monkeypatch, no_sleep):
    def handler(*a, **k):
        raise cffi_requests.errors.RequestsError("timed out", 28)

    patch_get(monkeypatch, handler)
    res = scrape.scrape_store_product(make_store("B2C_REST"), PRODUCT)
    assert res == {"status": "Error", "message": "TIMEOUT: connection timed out (curl 28)"}


def test_non_timeout_curl_error_is_not_retried_and_reports_code(monkeypatch, no_sleep):
    calls = {"n": 0}

    def handler(*a, **k):
        calls["n"] += 1
        raise cffi_requests.errors.RequestsError("SSL certificate problem", 60)

    patch_get(monkeypatch, handler)
    res = scrape.scrape_store_product(make_store("B2C_REST"), PRODUCT)
    assert calls["n"] == 1
    assert res["status"] == "Error"
    assert res["message"].startswith("NETWORK: curl 60")


def test_waf_block_detected_for_vtex_io_html(monkeypatch):
    patch_get(monkeypatch, lambda *a, **k: FakeResponse(text="<html>Just a moment...</html>"))
    res = scrape.scrape_store_product(make_store("VTEX_IO_HTML"), PRODUCT)
    assert res == {"status": "Error", "message": "BLOCKED: WAF / Cloudflare challenge"}


# ---------- process_task / summary ----------

def test_process_task_record_shape_and_timestamp(monkeypatch):
    monkeypatch.setattr(
        runner,
        "scrape_store_product",
        lambda store, product: {"status": "Success", "product_name": "X", "price_brl": 9.9},
    )
    rec = runner.process_task((make_store("B2C_REST", "Loja"), PRODUCT))
    assert set(rec) == set(CSV_COLUMNS)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", rec["fetched_at"])


def test_summarize_results_rate(capsys):
    results = [
        {"store": "A", "status": "Success"},
        {"store": "A", "status": "Error"},
        {"store": "B", "status": "Success"},
        {"store": "B", "status": "Not Found"},
    ]
    assert runner.summarize_results(results, 4) == 0.5
    assert "A" in capsys.readouterr().out


def test_summarize_counts_missing_results_as_failures():
    # 1 success out of 4 expected requests (3 workers crashed) -> 25%
    assert runner.summarize_results([{"store": "A", "status": "Success"}], 4) == 0.25


# ---------- zero / negative prices are not real prices ----------

@pytest.mark.parametrize("bad_price", [0, 0.0, -1])
def test_b2c_rest_zero_price_is_not_a_success(monkeypatch, bad_price):
    patch_get(monkeypatch, lambda *a, **k: FakeResponse(payload=vtex_payload(bad_price)))
    res = scrape.scrape_store_product(make_store("B2C_REST"), PRODUCT)
    assert res["status"] == "Not Found"
    assert "price_brl" not in res


def test_carrefour_zero_price_is_not_a_success(monkeypatch):
    payload = {"products": vtex_payload(0)}
    patch_get(monkeypatch, lambda *a, **k: FakeResponse(payload=payload))
    res = scrape.scrape_store_product(make_store("CARREFOUR_INTELLIGENT_SEARCH"), PRODUCT)
    assert res["status"] == "Not Found"


def test_rd_nextjs_zero_price_is_not_a_success(monkeypatch):
    html = next_html([{"sku": "1", "name": "Loja propria", "priceService": 0, "is1P": True}])
    patch_get(monkeypatch, lambda *a, **k: FakeResponse(text=html))
    res = scrape.scrape_store_product(make_store("RD_NEXTJS"), PRODUCT)
    assert res["status"] == "Not Found"


# ---------- VTEX seller choice and availability ----------

def vtex_sellers_payload(*sellers):
    return [{"productName": "Produto X", "items": [{"sellers": list(sellers)}]}]


def seller(price, default=None, **offer_extra):
    s = {"commertialOffer": {"Price": price, **offer_extra}}
    if default is not None:
        s["sellerDefault"] = default
    return s


def test_vtex_prefers_the_default_seller(monkeypatch):
    payload = vtex_sellers_payload(seller(20.0, default=False), seller(15.0, default=True))
    patch_get(monkeypatch, lambda *a, **k: FakeResponse(payload=payload))
    res = scrape.scrape_store_product(make_store("B2C_REST"), PRODUCT)
    assert res["price_brl"] == 15.0


def test_vtex_falls_back_to_first_seller_without_default_flag(monkeypatch):
    payload = vtex_sellers_payload(seller(20.0), seller(15.0))
    patch_get(monkeypatch, lambda *a, **k: FakeResponse(payload=payload))
    res = scrape.scrape_store_product(make_store("B2C_REST"), PRODUCT)
    assert res["price_brl"] == 20.0


@pytest.mark.parametrize("extra", [{"AvailableQuantity": 0}, {"IsAvailable": False}])
def test_vtex_out_of_stock_offer_is_not_a_success(monkeypatch, extra):
    payload = vtex_sellers_payload(seller(15.0, default=True, **extra))
    patch_get(monkeypatch, lambda *a, **k: FakeResponse(payload=payload))
    res = scrape.scrape_store_product(make_store("B2C_REST"), PRODUCT)
    assert res["status"] == "Not Found"
    assert "price_brl" not in res


def test_vtex_in_stock_offer_is_a_success(monkeypatch):
    payload = vtex_sellers_payload(seller(15.0, default=True, AvailableQuantity=5, IsAvailable=True))
    patch_get(monkeypatch, lambda *a, **k: FakeResponse(payload=payload))
    assert scrape.scrape_store_product(make_store("B2C_REST"), PRODUCT)["status"] == "Success"


# ---------- reason codes ----------

def message_code(res):
    return res["message"].split(":", 1)[0]


def test_carrefour_empty_result_is_not_listed(monkeypatch):
    patch_get(monkeypatch, lambda *a, **k: FakeResponse(payload={"products": []}))
    res = scrape.scrape_store_product(make_store("CARREFOUR_INTELLIGENT_SEARCH"), PRODUCT)
    assert (res["status"], message_code(res)) == ("Not Found", "NOT_LISTED")


def test_rd_page_without_products_is_not_listed(monkeypatch):
    state = {"props": {"pageProps": {"data": {"products": []}}}}
    html = '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(state) + "</script>"
    patch_get(monkeypatch, lambda *a, **k: FakeResponse(text=html))
    res = scrape.scrape_store_product(make_store("RD_NEXTJS"), PRODUCT)
    assert (res["status"], message_code(res)) == ("Not Found", "NOT_LISTED")


def test_rd_page_without_embedded_state_is_flagged(monkeypatch):
    patch_get(monkeypatch, lambda *a, **k: FakeResponse(text="<html>layout changed</html>"))
    res = scrape.scrape_store_product(make_store("RD_NEXTJS"), PRODUCT)
    assert (res["status"], message_code(res)) == ("Not Found", "NO_STATE")


@pytest.mark.parametrize(
    "payload, code",
    [
        (vtex_payload(0), "ZERO_PRICE"),
        ([{"productName": "X", "items": []}], "NO_PRICE"),
        (vtex_sellers_payload(seller(9.0, AvailableQuantity=0)), "OUT_OF_STOCK"),
    ],
)
def test_not_found_reason_codes(monkeypatch, payload, code):
    patch_get(monkeypatch, lambda *a, **k: FakeResponse(payload=payload))
    res = scrape.scrape_store_product(make_store("B2C_REST"), PRODUCT)
    assert (res["status"], message_code(res)) == ("Not Found", code)


def test_unparseable_json_is_a_parse_error(monkeypatch):
    def bad_json():
        raise ValueError("Expecting value")

    resp = FakeResponse(text="<html>blocked</html>")
    resp.json = bad_json
    patch_get(monkeypatch, lambda *a, **k: resp)
    res = scrape.scrape_store_product(make_store("B2C_REST"), PRODUCT)
    assert (res["status"], message_code(res)) == ("Error", "PARSE_ERROR")


# ---------- run health gate ----------

def rows(success=0, not_found=0, error=0, store="A"):
    return (
        [{"store": store, "status": "Success", "message": None}] * success
        + [{"store": store, "status": "Not Found", "message": "NOT_LISTED: x"}] * not_found
        + [{"store": store, "status": "Error", "message": "HTTP_ERROR: 403"}] * error
    )


def test_gate_passes_when_many_skus_are_legitimately_not_listed():
    # 40% found, 60% not listed, no errors: a coverage gap, not a blocked scraper.
    results = rows(success=40, not_found=60)
    assert runner.evaluate_run(results, 100, min_success_rate=0.2, max_error_rate=0.5) == []


def test_gate_fails_on_high_error_rate():
    results = rows(success=30, not_found=10, error=60)
    problems = runner.evaluate_run(results, 100, min_success_rate=0.2, max_error_rate=0.5)
    assert len(problems) == 1 and "error rate 60%" in problems[0]


def test_gate_fails_when_almost_nothing_is_found_even_without_errors():
    # A silently broken parser shows up as everything "Not Found".
    results = rows(success=5, not_found=95)
    problems = runner.evaluate_run(results, 100, min_success_rate=0.2, max_error_rate=0.5)
    assert len(problems) == 1 and "success rate 5%" in problems[0]


def test_gate_counts_crashed_workers_as_errors():
    results = rows(success=40, not_found=10)  # 50 of 100 expected results never arrived
    problems = runner.evaluate_run(results, 100, min_success_rate=0.2, max_error_rate=0.4)
    assert len(problems) == 1 and "error rate 50%" in problems[0]


def test_gate_fails_with_no_requests():
    assert runner.evaluate_run([], 0, 0.2, 0.5) != []


def test_summary_lists_reasons_and_flags_stores_with_no_successes(capsys, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    results = rows(success=3, store="Good") + rows(not_found=2, error=1, store="Bad")
    runner.summarize_results(results, 6)
    out = capsys.readouterr().out
    assert "NOT_LISTED: 2" in out and "HTTP_ERROR: 1" in out
    assert "::warning::Bad" in out
    assert "::warning::Good" not in out


# ---------- extra columns (VTEX payloads) ----------

def rich_payload(**offer_extra):
    s = {
        "sellerName": "Drogaria Teste",
        "sellerDefault": True,
        "commertialOffer": {"Price": 12.9, "ListPrice": 15.9, **offer_extra},
    }
    return [{"productName": "Produto X", "link": "https://loja.test/produto-x/p", "items": [{"sellers": [s]}]}]


def test_vtex_success_includes_list_price_seller_url_and_availability(monkeypatch):
    patch_get(monkeypatch, lambda *a, **k: FakeResponse(payload=rich_payload(AvailableQuantity=7)))
    res = scrape.scrape_store_product(make_store("B2C_REST"), PRODUCT)
    assert res == {
        "status": "Success",
        "product_name": "Produto X",
        "price_brl": 12.9,
        "list_price": 15.9,
        "available": "true",
        "seller": "Drogaria Teste",
        "url": "https://loja.test/produto-x/p",
    }


def test_vtex_availability_is_left_empty_when_the_payload_does_not_say(monkeypatch):
    patch_get(monkeypatch, lambda *a, **k: FakeResponse(payload=rich_payload()))
    res = scrape.scrape_store_product(make_store("B2C_REST"), PRODUCT)
    assert res["status"] == "Success" and "available" not in res


def test_out_of_stock_row_keeps_the_descriptive_columns_but_no_price(monkeypatch):
    patch_get(monkeypatch, lambda *a, **k: FakeResponse(payload=rich_payload(AvailableQuantity=0)))
    res = scrape.scrape_store_product(make_store("B2C_REST"), PRODUCT)
    assert res["status"] == "Not Found" and "price_brl" not in res
    assert (res["available"], res["seller"], res["list_price"]) == ("false", "Drogaria Teste", 15.9)


def test_list_price_of_zero_is_dropped(monkeypatch):
    payload = rich_payload()
    payload[0]["items"][0]["sellers"][0]["commertialOffer"]["ListPrice"] = 0
    patch_get(monkeypatch, lambda *a, **k: FakeResponse(payload=payload))
    assert "list_price" not in scrape.scrape_store_product(make_store("B2C_REST"), PRODUCT)


def test_relative_product_link_is_resolved_against_the_request_url(monkeypatch):
    payload = {"products": rich_payload()}
    payload["products"][0]["link"] = "/produto-x/p"
    resp = FakeResponse(payload=payload)
    resp.url = "https://carrefour.test/_v/api/intelligent-search/product_search/v2/?query=1"
    patch_get(monkeypatch, lambda *a, **k: resp)
    res = scrape.scrape_store_product(make_store("CARREFOUR_INTELLIGENT_SEARCH"), PRODUCT)
    assert res["url"] == "https://carrefour.test/produto-x/p"

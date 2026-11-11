import csv
from collections import Counter
import time

from price_capture import runner
from price_capture.schema import CSV_COLUMNS


def setup_inputs(tmp_path, monkeypatch, n_products=5):
    (tmp_path / "p.csv").write_text(
        "ean,desc_sku\n" + "".join(f"750954606{i:04d},PROD {i}\n" for i in range(n_products)),
        encoding="utf-8",
    )
    (tmp_path / "s.csv").write_text(
        "store_id,type,domain,enabled\nLoja,B2C_REST,https://loja.test,true\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PRODUCTS_FILE", "p.csv")
    monkeypatch.setenv("STORES_FILE", "s.csv")
    monkeypatch.setenv("MAX_WORKERS", "1")


def fake_record(store, product):
    return {c: "" for c in CSV_COLUMNS} | {
        "ean": product["ean"], "store": store["name"], "status": "Success", "price_brl": 9.9,
    }


def test_results_csv_is_written_while_the_run_is_in_progress(tmp_path, monkeypatch):
    setup_inputs(tmp_path, monkeypatch)
    seen_mid_run = {}
    calls = {"n": 0}

    def fake_process_task(args, *rest):
        store, product = args[0], args[1]
        calls["n"] += 1
        if calls["n"] == 4:
            time.sleep(0.3)  # let the main thread write the earlier rows
            with open("results.csv", encoding="utf-8") as f:
                seen_mid_run["rows"] = list(csv.DictReader(f))
        return fake_record(store, product)

    monkeypatch.setattr(runner, "process_task", fake_process_task)
    runner.main()
    assert len(seen_mid_run["rows"]) >= 2  # rows were already on disk before the run finished

    with open("results.csv", encoding="utf-8") as f:
        assert len(list(csv.DictReader(f))) == 5


def test_partial_csv_survives_a_crash(tmp_path, monkeypatch):
    setup_inputs(tmp_path, monkeypatch)
    calls = {"n": 0}

    def fake_process_task(args, *rest):
        calls["n"] += 1
        if calls["n"] == 4:
            time.sleep(0.3)
            raise KeyboardInterrupt  # simulates the run dying part-way through
        return fake_record(args[0], args[1])

    monkeypatch.setattr(runner, "process_task", fake_process_task)
    try:
        runner.main()
    except BaseException:
        pass
    with open("results.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) >= 2


def test_make_run_id_prefers_explicit_then_github_then_timestamp(monkeypatch):
    monkeypatch.delenv("RUN_ID", raising=False)
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    monkeypatch.delenv("GITHUB_RUN_ATTEMPT", raising=False)
    assert runner.make_run_id().startswith("local-")
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    assert runner.make_run_id() == "gh-123-1"
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    assert runner.make_run_id() == "gh-123-2"
    monkeypatch.setenv("RUN_ID", "manual-7")
    assert runner.make_run_id() == "manual-7"


def test_every_row_of_a_run_shares_one_run_id_and_csv_has_all_columns(tmp_path, monkeypatch):
    setup_inputs(tmp_path, monkeypatch, n_products=3)
    monkeypatch.setenv("RUN_ID", "test-run")
    monkeypatch.setattr(
        runner, "scrape_store_product",
        lambda store, product: {"status": "Success", "product_name": "X", "price_brl": 9.9,
                                "list_price": 12.0, "available": "true", "seller": "S", "url": "https://x.test/p"},
    )
    runner.main()
    with open("results.csv", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        assert reader.fieldnames == CSV_COLUMNS
    assert {r["run_id"] for r in rows} == {"test-run"}
    assert rows[0]["list_price"] == "12.0" and rows[0]["url"] == "https://x.test/p"


def test_fetched_at_is_utc_for_domo_and_fetched_at_brt_is_brasilia_clock(monkeypatch):
    # Domo reads DATETIME values as UTC and converts them to the company time zone for display,
    # so fetched_at must be the real UTC instant; fetched_at_brt is plain text Domo leaves alone.
    from datetime import datetime as real_datetime, timezone

    class FrozenDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            moment = real_datetime(2026, 11, 11, 11, 45, 0, tzinfo=timezone.utc)
            return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)

    monkeypatch.setattr(runner, "datetime", FrozenDatetime)
    monkeypatch.setattr(runner, "scrape_store_product", lambda s, p: {"status": "Success", "price_brl": 1.0})
    store = {"name": "S", "type": "B2C_REST", "domain": "https://s.test"}
    rec = runner.process_task((store, {"ean": "1", "desc_sku": "d"}, "r"))
    assert rec["fetched_at"] == "2026-11-11 11:45:00"
    assert rec["fetched_at_brt"] == "2026-11-11 08:45:00"


# ---------- circuit breaker for unreachable stores ----------

NETWORK_ERR = {"status": "Error", "message": "NETWORK: curl 7: Connection refused"}
OK = {"status": "Success", "price_brl": 1.0}


def test_breaker_opens_after_consecutive_network_failures():
    b = runner.StoreBreaker(limit=3)
    for _ in range(2):
        b.record("Dead", NETWORK_ERR)
    assert not b.is_open("Dead")
    b.record("Dead", NETWORK_ERR)
    assert b.is_open("Dead") and not b.is_open("Other")


def test_breaker_resets_on_success_and_ignores_fast_failures():
    b = runner.StoreBreaker(limit=2)
    b.record("S", NETWORK_ERR)
    b.record("S", OK)
    b.record("S", NETWORK_ERR)
    assert not b.is_open("S")
    b.record("S", {"status": "Error", "message": "HTTP_ERROR: 403"})  # fast, not a reason to stop
    assert not b.is_open("S")


def test_breaker_disabled_with_limit_zero():
    b = runner.StoreBreaker(limit=0)
    for _ in range(50):
        b.record("S", NETWORK_ERR)
    assert not b.is_open("S")


def test_open_breaker_skips_the_request(monkeypatch):
    b = runner.StoreBreaker(limit=1)
    b.record("Dead", NETWORK_ERR)

    def must_not_scrape(store, product):
        raise AssertionError("no request expected")

    monkeypatch.setattr(runner, "scrape_store_product", must_not_scrape)
    rec = runner.process_task(({"name": "Dead", "type": "B2C_REST", "domain": "d"}, {"ean": "1", "desc_sku": "d"}, "r"), b)
    assert rec["status"] == "Error" and rec["message"].startswith("SKIPPED:")


def test_run_stops_calling_a_dead_store_but_still_writes_every_row(tmp_path, monkeypatch):
    setup_inputs(tmp_path, monkeypatch, n_products=10)
    (tmp_path / "s.csv").write_text(
        "store_id,type,domain,enabled\nDead,B2C_REST,https://dead.test,true\nAlive,B2C_REST,https://alive.test,true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("STORE_FAILURE_LIMIT", "3")
    monkeypatch.setenv("MIN_SUCCESS_RATE", "0")
    monkeypatch.setenv("MAX_ERROR_RATE", "1")
    calls = Counter()

    def fake_scrape(store, product):
        calls[store["name"]] += 1
        return NETWORK_ERR if store["name"] == "Dead" else OK

    monkeypatch.setattr(runner, "scrape_store_product", fake_scrape)
    runner.main()
    assert calls == {"Dead": 3, "Alive": 10}
    with open("results.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 20
    assert sum(r["message"].startswith("SKIPPED:") for r in rows) == 7


# ---------- a slow store must not hold up the others ----------

def test_fast_stores_are_not_held_up_by_a_slow_store(tmp_path, monkeypatch):
    import time as real_time
    from curl_cffi import requests as cffi_requests

    # More products than workers (as in production: 75 x 10), otherwise every worker blocks on a
    # slow task only after all fast tasks are already done and the problem never shows.
    setup_inputs(tmp_path, monkeypatch, n_products=30)
    stores = "".join(f"S{i},B2C_REST,https://{'slow' if i == 0 else 'fast'}{i}.test,true\n" for i in range(10))
    (tmp_path / "s.csv").write_text("store_id,type,domain,enabled\n" + stores, encoding="utf-8")
    monkeypatch.setenv("MAX_WORKERS", "10")

    class R:
        status_code = 200
        text = ""

        def json(self):
            return [{"productName": "P", "items": [{"sellers": [{"commertialOffer": {"Price": 1.0}}]}]}]

    fast_done = []

    def fake_get(self, url, **kwargs):
        if "slow" in url:
            real_time.sleep(0.1)
        else:
            fast_done.append(real_time.monotonic())
        return R()

    monkeypatch.setattr(cffi_requests.Session, "get", fake_get)
    start = real_time.monotonic()
    runner.main()
    total = real_time.monotonic() - start

    # The slow store alone needs ~30 x 0.1s / 2 slots = 1.5s; the 270 fast requests should not wait for it.
    assert len(fast_done) == 270
    assert max(fast_done) - start < 0.4, f"fast stores finished at {max(fast_done) - start:.2f}s of {total:.2f}s"


def test_max_workers_still_caps_total_requests_in_flight(tmp_path, monkeypatch):
    import threading
    import time as real_time

    setup_inputs(tmp_path, monkeypatch, n_products=6)
    stores = "".join(f"S{i},B2C_REST,https://s{i}.test,true\n" for i in range(5))
    (tmp_path / "s.csv").write_text("store_id,type,domain,enabled\n" + stores, encoding="utf-8")
    monkeypatch.setenv("MAX_WORKERS", "3")
    state = {"now": 0, "max": 0}
    lock = threading.Lock()

    def fake_scrape(store, product):
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        real_time.sleep(0.02)
        with lock:
            state["now"] -= 1
        return {"status": "Success", "price_brl": 1.0}

    monkeypatch.setattr(runner, "scrape_store_product", fake_scrape)
    runner.main()
    assert state["max"] == 3  # 5 stores x 2 threads available, but never more than MAX_WORKERS at once

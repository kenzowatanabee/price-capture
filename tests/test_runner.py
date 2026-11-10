import csv
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

    def fake_process_task(args):
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

    def fake_process_task(args):
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

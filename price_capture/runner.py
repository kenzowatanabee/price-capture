"""Runs the full scrape: builds the task matrix, executes it in threads, writes results and checks the success rate."""
import csv
import json
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from zoneinfo import ZoneInfo

from price_capture.config import ConfigError, load_products, load_stores
from price_capture.schema import CSV_COLUMNS
from price_capture.scrape import scrape_store_product

BRT = ZoneInfo("America/Sao_Paulo")


def make_run_id():
    """Identifies one scraper run: RUN_ID if set, else the GitHub Actions run, else a local timestamp."""
    if os.environ.get("RUN_ID"):
        return os.environ["RUN_ID"]
    github_run = os.environ.get("GITHUB_RUN_ID")
    if github_run:
        return f"gh-{github_run}-{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}"
    return datetime.now(BRT).strftime("local-%Y%m%d-%H%M%S")


def process_task(args):
    """Worker function to process a single (store, product) pair in a thread."""
    store, product, run_id = args
    res = scrape_store_product(store, product)
    store_name = store["name"]
    ean = product["ean"]

    # Timestamp in Brasilia time (YYYY-MM-DD HH:MM:SS), regardless of the runner's timezone
    fetched_at = datetime.now(BRT).strftime("%Y-%m-%d %H:%M:%S")

    record = {
        "fetched_at": fetched_at,
        "ean": ean,
        "desc_sku": product["desc_sku"],
        "store": store_name,
        "status": res.get("status"),
        "price_brl": res.get("price_brl"),
        "found_name": res.get("product_name"),
        "message": res.get("message"),
        "list_price": res.get("list_price"),
        "available": res.get("available"),
        "seller": res.get("seller"),
        "url": res.get("url"),
        "run_id": run_id,
    }

    # Thread-safe console log
    if res["status"] == "Success":
        p_val = res["price_brl"]
        price_str = (
            f"R$ {float(p_val):.2f}"
            if isinstance(p_val, (int, float))
            else f"R$ {p_val}"
        )
        print(f"✅ [{ean}] {store_name.ljust(22)}: {price_str.ljust(12)}")
    elif res["status"] == "Not Found":
        print(f"❌ [{ean}] {store_name.ljust(22)}: {res['message']}")
    else:
        print(f"⚠️ [{ean}] {store_name.ljust(22)}: {res['message']}")

    return record


def reason_code(result):
    """The CODE of a "CODE: detail" message, or OTHER."""
    message = result.get("message") or ""
    return message.split(":", 1)[0] if ":" in message else "OTHER"


def summarize_results(results, expected_total):
    """Prints per-store success with the reasons for non-successes; returns the overall success rate."""
    per_store = {}
    store_codes = {}
    store_messages = {}
    reasons = Counter()
    for r in results:
        stats = per_store.setdefault(r["store"], [0, 0])
        stats[1] += 1
        if r["status"] == "Success":
            stats[0] += 1
        else:
            reasons[reason_code(r)] += 1
            store_codes.setdefault(r["store"], Counter())[reason_code(r)] += 1
            store_messages.setdefault(r["store"], Counter())[r.get("message") or "(no message)"] += 1

    print("\nPer-store success:")
    for store, (ok, total) in sorted(per_store.items()):
        print(f"  {store.ljust(22)}: {ok}/{total}")
        for code, count in store_codes.get(store, Counter()).most_common():
            print(f"      {code}: {count}")
        if ok == 0:
            message, count = store_messages[store].most_common(1)[0]
            warning = (
                f"{store}: no successful prices ({total} requests). "
                f"Most common: {message} ({count}x). Blocked or layout changed?"
            )
            print(f"  ⚠️ {warning}")
            if os.environ.get("GITHUB_ACTIONS") == "true":
                print(f"::warning::{warning}")

    if reasons:
        print("\nNon-success reasons:")
        for code, count in reasons.most_common():
            print(f"  {code}: {count}")

    successes = sum(ok for ok, _ in per_store.values())
    rate = successes / expected_total if expected_total else 0.0
    print(f"Overall: {successes}/{expected_total} ({rate:.0%})")
    return rate


def evaluate_run(results, expected_total, min_success_rate, max_error_rate):
    """
    Returns a list of problems; empty means the run looks healthy.

    "Not Found" rows are a coverage gap, not a failure, so they only matter through the
    success-rate floor (which catches a silently broken parser). Errors (network, blocked,
    parse) and results that never arrived (crashed workers) count against the error rate.
    """
    if not expected_total:
        return ["no requests were made"]

    successes = sum(1 for r in results if r["status"] == "Success")
    not_found = sum(1 for r in results if r["status"] == "Not Found")
    errors = expected_total - successes - not_found

    problems = []
    error_rate = errors / expected_total
    if error_rate > max_error_rate:
        problems.append(f"error rate {error_rate:.0%} is above the allowed {max_error_rate:.0%}")
    success_rate = successes / expected_total
    if success_rate < min_success_rate:
        problems.append(f"success rate {success_rate:.0%} is below the required {min_success_rate:.0%}")
    return problems


def main():
    products_file = os.environ.get("PRODUCTS_FILE", "data/products.csv")
    stores_file = os.environ.get("STORES_FILE", "data/stores.csv")
    max_workers = int(os.environ.get("MAX_WORKERS", 10))  # Default to 10 concurrent threads

    try:
        products = load_products(products_file)
        stores = load_stores(stores_file)
    except ConfigError as e:
        print(f"❌ [ERROR] {e}")
        sys.exit(1)

    if not products or not stores:
        print("No valid products or stores loaded. Exiting.")
        sys.exit(1)

    # Build matrix of all (store, product) tasks
    run_id = make_run_id()
    tasks = [(store, product, run_id) for product in products for store in stores]
    print(f"Run id: {run_id}")
    print(f"Loaded {len(products)} product(s) and {len(stores)} active store(s).")
    print(f"Starting {len(tasks)} requests across {max_workers} worker threads...\n{'='*60}")

    all_results = []

    # Execute requests concurrently, appending each row to results.csv as soon as it completes
    # so a crash part-way through still leaves the rows scraped so far.
    with open("results.csv", "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        csv_file.flush()

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_task = {executor.submit(process_task, task): task for task in tasks}
            for future in as_completed(future_to_task):
                try:
                    result = future.result()
                except Exception as e:
                    print(f"⚠️ Worker error: {e}")
                    continue
                all_results.append(result)
                writer.writerow(result)
                csv_file.flush()

    with open("results.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    print(f"\nScraping complete. Total requests processed: {len(all_results)}")

    # Fail the run (and so skip the Domo upload) if the data looks broken,
    # e.g. when a WAF starts blocking the runner or a parser stops matching.
    summarize_results(all_results, len(tasks))
    problems = evaluate_run(
        all_results,
        len(tasks),
        min_success_rate=float(os.environ.get("MIN_SUCCESS_RATE", 0.2)),
        max_error_rate=float(os.environ.get("MAX_ERROR_RATE", 0.5)),
    )
    if problems:
        for problem in problems:
            print(f"❌ {problem}")
        print("Failing the run.")
        sys.exit(1)

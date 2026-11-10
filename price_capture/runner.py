"""Runs the full scrape: builds the task matrix, executes it in threads, writes results and checks the success rate."""
import csv
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from zoneinfo import ZoneInfo

from price_capture.config import ConfigError, load_products, load_stores
from price_capture.schema import CSV_COLUMNS
from price_capture.scrape import scrape_store_product

BRT = ZoneInfo("America/Sao_Paulo")


def process_task(args):
    """Worker function to process a single (store, product) pair in a thread."""
    store, product = args
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


def summarize_results(results, expected_total):
    """Prints a per-store success summary and returns the overall success rate."""
    per_store = {}
    for r in results:
        stats = per_store.setdefault(r["store"], [0, 0])
        stats[1] += 1
        if r["status"] == "Success":
            stats[0] += 1

    print("\nPer-store success:")
    for store, (ok, total) in sorted(per_store.items()):
        print(f"  {store.ljust(22)}: {ok}/{total}")

    successes = sum(ok for ok, _ in per_store.values())
    rate = successes / expected_total if expected_total else 0.0
    print(f"Overall: {successes}/{expected_total} ({rate:.0%})")
    return rate


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
    tasks = [(store, product) for product in products for store in stores]
    print(f"Loaded {len(products)} product(s) and {len(stores)} active store(s).")
    print(f"Starting {len(tasks)} requests across {max_workers} worker threads...\n{'='*60}")

    all_results = []

    # Execute requests concurrently
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_task = {executor.submit(process_task, task): task for task in tasks}
        for future in as_completed(future_to_task):
            try:
                result = future.result()
                all_results.append(result)
            except Exception as e:
                print(f"⚠️ Worker error: {e}")

    # Save outputs
    with open("results.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    # Save to CSV output
    with open("results.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=CSV_COLUMNS,
        )
        writer.writeheader()
        writer.writerows(all_results)

    print(f"\nScraping complete. Total requests processed: {len(all_results)}")

    # Fail the run (and so skip the Domo upload) if too many requests failed,
    # e.g. when a WAF starts blocking the runner.
    min_rate = float(os.environ.get("MIN_SUCCESS_RATE", 0.5))
    rate = summarize_results(all_results, len(tasks))
    if rate < min_rate:
        print(f"❌ Success rate {rate:.0%} is below the required {min_rate:.0%}. Failing the run.")
        sys.exit(1)

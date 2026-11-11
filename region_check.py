"""
Regional pricing check: does a store show a different price/availability depending on the
customer's CEP? One-off local analysis, separate from the daily scraper.

For each VTEX (B2C_REST) store and a few SKUs it:
  1. finds the SKU id with the same catalog search the scraper uses (and records that default price),
  2. asks the store which seller serves each CEP   (GET  /api/checkout/pub/regions),
  3. simulates a cart for that CEP                 (POST /api/checkout/pub/orderForms/simulation),
     which returns the price and availability a customer there would see.

    python region_check.py                       # 3 single-pack SKUs, default CEPs, all B2C_REST stores
    python region_check.py --limit 5 --stores "Drogal,Pague Menos"
    python region_check.py --eans 7891024132005,7891024035504 --ceps "SP=01310-100,PE=50030-230"

Writes region_check.csv and prints, per store, how many SKUs had more than one price across CEPs.
RD_NEXTJS / VTEX_IO_HTML stores are skipped: how those sites set a location is not mapped yet.
"""
import argparse
import csv
import re
import sys
import time

from curl_cffi import requests

from price_capture.config import load_products, load_stores
from price_capture.http import HEADERS, IMPERSONATE, TIMEOUT, VERIFY_SSL
from price_capture.platforms.base import vtex_offer

# City-centre CEPs, one per region of interest. Replace with the CEPs that matter to you via --ceps.
DEFAULT_CEPS = {
    "SP-Sao Paulo": "01310-100",
    "RJ-Rio de Janeiro": "20010-000",
    "MG-Belo Horizonte": "30130-010",
    "PR-Curitiba": "80010-000",
    "RS-Porto Alegre": "90010-000",
    "BA-Salvador": "40020-000",
    "PE-Recife": "50030-230",
    "DF-Brasilia": "70040-010",
}
SUPPORTED_TYPES = {"B2C_REST"}
CSV_FIELDS = ["store", "ean", "desc_sku", "region", "cep", "seller", "price", "list_price", "availability", "note"]


def make_session(store):
    return requests.Session(
        impersonate=IMPERSONATE,
        headers=HEADERS,
        timeout=TIMEOUT,
        verify=VERIFY_SSL and store.get("verify_ssl", True) is not False,
    )


def cents(value):
    return round(value / 100, 2) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def parse_simulation(payload):
    """Price/availability of the first item of a VTEX simulation response (prices there are in cents)."""
    items = (payload or {}).get("items") or []
    if not items:
        messages = "; ".join(m.get("text", "") for m in (payload or {}).get("messages") or [])
        return {"price": None, "list_price": None, "availability": "not_returned", "seller": None, "note": messages[:120]}
    item = items[0]
    return {
        "price": cents(item.get("sellingPrice", item.get("price"))),
        "list_price": cents(item.get("listPrice")),
        "availability": item.get("availability"),
        "seller": item.get("seller"),
        "note": "",
    }


def find_sku(session, domain, ean):
    """(sku_id, default seller id, catalog price) via the catalog search, or None if not listed."""
    r = session.get(f"{domain}/api/catalog_system/pub/products/search?fq=alternateIds_Ean:{ean}")
    if r.status_code != 200:
        raise RuntimeError(f"catalog search HTTP {r.status_code}")
    data = r.json()
    if not data or not data[0].get("items"):
        return None
    item = data[0]["items"][0]
    sellers = item.get("sellers") or []
    default = next((s for s in sellers if s.get("sellerDefault") is True), sellers[0] if sellers else {})
    return item.get("itemId"), default.get("sellerId") or "1", vtex_offer(data[0]).price


def region_sellers(session, domain, cep, sc):
    """Seller ids that serve a CEP according to the store (empty list if the store returns none)."""
    digits = re.sub(r"\D", "", cep)
    r = session.get(f"{domain}/api/checkout/pub/regions?country=BRA&postalCode={digits}&sc={sc}")
    if r.status_code != 200:
        raise RuntimeError(f"regions HTTP {r.status_code}")
    regions = r.json() or []
    return [s.get("id") for s in (regions[0].get("sellers") or [])] if regions else []


def simulate(session, domain, sku_id, seller, cep, sc):
    body = {"items": [{"id": sku_id, "quantity": 1, "seller": seller}], "country": "BRA"}
    if cep:
        body["postalCode"] = re.sub(r"\D", "", cep)
    r = session.post(f"{domain}/api/checkout/pub/orderForms/simulation?sc={sc}", json=body)
    if r.status_code != 200:
        raise RuntimeError(f"simulation HTTP {r.status_code}")
    return parse_simulation(r.json())


def check_store(store, products, ceps, sc, delay, session):
    """Rows for one store: a catalog row and a no-CEP simulation per SKU, then one row per CEP."""
    rows = []
    domain = store["domain"]
    for product in products:
        base = {"store": store["name"], "ean": product["ean"], "desc_sku": product["desc_sku"]}
        try:
            found = find_sku(session, domain, product["ean"])
        except Exception as e:
            rows.append(base | {"region": "catalog", "note": f"error: {str(e)[:100]}"})
            continue
        if not found:
            rows.append(base | {"region": "catalog", "note": "not listed"})
            continue
        sku_id, default_seller, catalog_price = found
        rows.append(base | {"region": "catalog (what the scraper records)", "seller": default_seller, "price": catalog_price})

        targets = [("no CEP", None)] + list(ceps.items())
        for region, cep in targets:
            time.sleep(delay)
            row = base | {"region": region, "cep": cep or ""}
            try:
                seller = default_seller
                if cep:
                    sellers = region_sellers(session, domain, cep, sc)
                    if sellers:
                        seller = sellers[0]
                    else:
                        row["note"] = "no regional seller returned; used default seller. "
                result = simulate(session, domain, sku_id, seller, cep, sc)
                row.update({k: v for k, v in result.items() if k != "note"})
                row["note"] = (row.get("note", "") + result["note"]).strip()
            except Exception as e:
                row["note"] = f"error: {str(e)[:100]}"
            rows.append(row)
    return rows


def summarize(rows):
    """{store: (skus with >1 distinct available price across CEPs, skus with at least one CEP price)}."""
    prices = {}
    for r in rows:
        if r.get("cep") and r.get("price") is not None and r.get("availability") == "available":
            prices.setdefault((r["store"], r["ean"]), set()).add(r["price"])
    summary = {}
    for (store, _), distinct in prices.items():
        varies, total = summary.get(store, (0, 0))
        summary[store] = (varies + (len(distinct) > 1), total + 1)
    return summary


def parse_ceps(text):
    ceps = {}
    for part in filter(None, (p.strip() for p in text.split(","))):
        label, _, cep = part.rpartition("=")
        ceps[label or cep] = cep
    return ceps


def main(argv=None, session_factory=make_session):
    parser = argparse.ArgumentParser(description="Compare SKU prices across Brazilian CEPs for the VTEX stores.")
    parser.add_argument("--stores", help="comma-separated store_id filter (default: every enabled B2C_REST store)")
    parser.add_argument("--eans", help="comma-separated EANs (default: the first --limit single-pack products)")
    parser.add_argument("--limit", type=int, default=3, help="how many products when --eans is not given (default 3)")
    parser.add_argument("--ceps", help='e.g. "SP=01310-100,PE=50030-230" (default: one city-centre CEP per region)')
    parser.add_argument("--sc", default="1", help="VTEX sales channel (default 1)")
    parser.add_argument("--delay", type=float, default=0.3, help="seconds between requests (default 0.3)")
    parser.add_argument("--products-file", default="data/products.csv")
    parser.add_argument("--stores-file", default="data/stores.csv")
    parser.add_argument("--out", default="region_check.csv")
    args = parser.parse_args(argv)

    products = load_products(args.products_file)
    if args.eans:
        wanted = {e.strip() for e in args.eans.split(",")}
        products = [p for p in products if p["ean"] in wanted]
    else:
        products = [p for p in products if p["desc_sku"].upper().endswith("SINGLE")][: args.limit]
    ceps = parse_ceps(args.ceps) if args.ceps else DEFAULT_CEPS

    stores = load_stores(args.stores_file)
    if args.stores:
        names = {s.strip() for s in args.stores.split(",")}
        stores = [s for s in stores if s["name"] in names]
    for s in stores:
        if s["type"] not in SUPPORTED_TYPES:
            print(f"⏭️ {s['name']}: {s['type']} is not supported by this check yet")
    stores = [s for s in stores if s["type"] in SUPPORTED_TYPES]

    if not products or not stores:
        print("Nothing to check (no matching products or supported stores).")
        return 1

    print(f"Checking {len(products)} SKU(s) x {len(stores)} store(s) x {len(ceps)} CEP(s)...")
    all_rows = []
    for store in stores:
        print(f"  {store['name']}...")
        all_rows += check_store(store, products, ceps, args.sc, args.delay, session_factory(store))

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"\nWrote {len(all_rows)} rows to {args.out}\n\nSKUs whose price differs between CEPs (available offers only):")
    summary = summarize(all_rows)
    for store in sorted({s["name"] for s in stores}):
        varies, total = summary.get(store, (0, 0))
        verdict = "no CEP prices returned, see notes in the CSV" if total == 0 else (
            "REGIONAL pricing" if varies else "same price everywhere checked")
        print(f"  {store.ljust(22)}: {varies}/{total} SKUs vary  -> {verdict}")
    errors = sum(1 for r in all_rows if str(r.get("note", "")).startswith("error"))
    if errors:
        print(f"\n⚠️ {errors} request(s) failed; see the 'note' column in {args.out}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

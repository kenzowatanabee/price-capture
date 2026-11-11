"""
Capture raw store responses for diagnosis, so they can be inspected offline.

Two checks, both on by default:

  RD   (Drogasil / Droga Raia): the search page for EANs that come back NO_PRICE, plus a few that
       succeed as a control. Saves the embedded Next.js state and lists, per product, every
       price / stock / identity field, so we can see which product and which price field is right.
       For sponsored products (no price in the search data) it also fetches the product page, and one
       priced product page per store as a control, listing every price-like field found there.
  VTEX (default: Venancio, Indiana, Drogaria Sao Paulo): the catalog, a no-CEP simulation and, per CEP,
       three ways of judging regional availability: E1 the main seller with the CEP, E2 one simulation
       with up to --max-sellers regional sellers, E3 the catalog search with the store's regionId.

    python diagnose.py                          # both checks with the defaults below
    python diagnose.py --skip-vtex              # only RD
    python diagnose.py --rd-eans 7891024035504,7509546060781
    python diagnose.py --vtex-stores "Drogal" --vtex-eans 7509546651040 --sc 1,2

Output: diagnostics/<timestamp>/ with summary.md (small, the part to share), index.json and every
raw response, plus diagnostics_<timestamp>.zip of the same folder. Cookies are never saved.
"""
import argparse
import csv
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime
from urllib.parse import urljoin

from price_capture.config import load_products, load_stores
from price_capture.platforms.embedded_state import extract_state_from_html, find_true_products_array
from price_capture.platforms.rd_nextjs import RdNextJs
from region_check import DEFAULT_CEPS, make_session

DEFAULT_VTEX_STORES = ["Drogaria Venancio", "Farmácia Indiana", "Drogaria São Paulo"]
DEFAULT_VTEX_EANS = ["7509546651040", "7509546060781"]  # out of stock in the simulation vs fine in the catalog
DEFAULT_CEP_LABELS = ["SP-Sao Paulo", "RJ-Rio de Janeiro", "MG-Belo Horizonte"]
INTERESTING_KEY = re.compile(r"price|preco|valor|stock|estoque|avail|dispon|quant|qty|ean|gtin|sku|name|nome|1p|seller|status|buy|sale|sponsor|amount", re.I)
HTML_LIMIT = 300_000  # bytes of HTML kept when a page has no embedded state (unless --full-html)


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


class Capture:
    """Saves each raw response to a file and keeps an index of what was requested."""

    def __init__(self, out_dir):
        self.dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        self.index = []

    def save(self, name, store, ean, purpose, method, url, response=None, body=None, ext="json", error=None):
        entry = {"store": store, "ean": ean, "purpose": purpose, "method": method, "url": url}
        if response is not None:
            headers = {k: v for k, v in dict(response.headers or {}).items() if k.lower() != "set-cookie"}
            entry.update(status=response.status_code, content_type=headers.get("content-type") or headers.get("Content-Type"))
        if error is not None:
            entry["error"] = str(error)[:300]
        if body is not None:
            path = os.path.join(self.dir, f"{name}.{ext}")
            with open(path, "w", encoding="utf-8") as f:
                f.write(body if isinstance(body, str) else json.dumps(body, ensure_ascii=False, indent=1))
            entry["file"] = os.path.basename(path)
        self.index.append(entry)
        return entry


def body_of(response):
    """(parsed JSON, ext) when the body is JSON, else (raw text, "txt") so block pages are kept too."""
    try:
        return response.json(), "json"
    except Exception:
        return (getattr(response, "text", "") or "")[:HTML_LIMIT], "txt"


def scalar_fields(obj, depth=0, prefix=""):
    """Flattens the interesting scalar fields of a product dict (two levels deep) for the summary."""
    out = {}
    if not isinstance(obj, dict):
        return out
    for key, value in obj.items():
        path = f"{prefix}{key}"
        if isinstance(value, (str, int, float, bool)) or value is None:
            if INTERESTING_KEY.search(key):
                out[path] = value if not isinstance(value, str) else value[:120]
        elif isinstance(value, dict) and depth < 2:
            out.update(scalar_fields(value, depth + 1, f"{path}."))
    return out


PRICE_KEY = re.compile(r"price|preco|valor|amount|cost", re.I)


def price_paths(state, limit=40):
    """Every scalar under a price-like key in a JSON tree, as (path, value), for locating a price field."""
    found = []

    def walk(obj, path, under_price):
        if len(found) >= limit:
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                p = f"{path}.{k}"
                pricey = under_price or bool(PRICE_KEY.search(str(k)))
                if isinstance(v, (int, float, str)) and not isinstance(v, bool):
                    if pricey:
                        found.append((p, v if not isinstance(v, str) else v[:60]))
                else:
                    walk(v, p, pricey)  # everything under e.g. "priceInfo": {...} counts
        elif isinstance(obj, list):
            for i, v in enumerate(obj[:50]):
                walk(v, f"{path}[{i}]", under_price)

    walk(state, "$", False)
    return found


def ld_json_prices(html):
    """"price" values inside application/ld+json blocks (schema.org offers), as a fallback signal."""
    prices = []
    for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html or "", re.S | re.I):
        prices += re.findall(r'"(?:price|lowPrice|highPrice)"\s*:\s*"?([\d.,]+)', block)
    return prices[:5]


def rd_page_prices(state, html):
    """The price fields of an RD product page: main price (price_aux), live price, best/benefit price and ld+json."""
    pd = (((state or {}).get("props") or {}).get("pageProps") or {}).get("productData") or {}
    aux = pd.get("price_aux") or {}
    live = ((pd.get("liveComposition") or {}).get("livePrice")) or {}
    best = live.get("bestPrice") or {}
    ld = ld_json_prices(html)
    return {
        "value_to": aux.get("value_to"),
        "value_from": aux.get("value_from"),
        "live": live.get("valueTo"),
        "best": best.get("valueTo"),
        "best_type": best.get("type"),
        "ld_json": ld[0] if ld else None,
    }


def diagnose_product_page(store, ean, product, role, capture, session, delay, full_html, verbose=True):
    """Fetches a product page from the search result's url and lists where price-like fields live."""
    page_url = urljoin(store["domain"], product["url"])
    name = f"rd_{slug(store['name'])}_{ean}_page_{product.get('sku')}"
    time.sleep(delay)
    try:
        r = session.get(page_url)
    except Exception as e:
        capture.save(name, store["name"], ean, f"rd_product_page ({role})", "GET", page_url, error=e)
        return [f"  - product page ({role}, sku {product.get('sku')}): request failed: {str(e)[:150]}"], None
    html = r.text or ""
    state, kind = extract_state_from_html(html)
    ld = ld_json_prices(html)
    if kind != "NEXT_JS":
        capture.save(name, store["name"], ean, f"rd_product_page ({role})", "GET", page_url, r,
                     html if full_html else html[:HTML_LIMIT], ext="html")
        return [f"  - product page ({role}, sku {product.get('sku')}): HTTP {r.status_code}, no __NEXT_DATA__; ld+json prices {ld}"], \
            {"value_to": None, "value_from": None, "live": None, "best": None, "best_type": None, "ld_json": ld[0] if ld else None}
    capture.save(name, store["name"], ean, f"rd_product_page ({role})", "GET", page_url, r, state)
    prices = rd_page_prices(state, html)
    if not verbose:
        return [], prices
    paths = "; ".join(f"{p}={v}" for p, v in price_paths(state))
    return [f"  - product page ({role}, sku {product.get('sku')}): HTTP {r.status_code}; ld+json prices {ld}; "
            f"price-like fields: `{paths[:2500] or 'none'}`"], prices


def comparison_table(rows):
    """Markdown table of search priceService vs product-page prices, plus a tally of how they relate."""
    def fmt(v):
        return "" if v is None else str(v)

    lines = ["| store | ean | sku | sponsored | search priceService | page value_to | page value_from | ld+json | best (type) | search vs page |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    tally = {"equal": 0, "search lower": 0, "search higher": 0, "no search price": 0, "no page price": 0}
    for r in rows:
        search, page = r.get("search"), r.get("value_to")
        if search is None:
            relation = "no search price"
        elif page is None:
            relation = "no page price"
        elif abs(float(search) - float(page)) < 0.005:
            relation = "equal"
        else:
            relation = "search lower" if float(search) < float(page) else "search higher"
        tally[relation] += 1
        diff = f"{(float(search) - float(page)) / float(page):+.1%}" if search is not None and page else relation
        best = f"{fmt(r.get('best'))} ({r.get('best_type')})" if r.get("best") is not None else ""
        lines.append(f"| {r['store']} | {r['ean']} | {r.get('sku')} | {'yes' if r['sponsored'] else ''} | {fmt(search)} | "
                     f"{fmt(page)} | {fmt(r.get('value_from'))} | {fmt(r.get('ld_json'))} | {best} | {diff} |")
    return lines + ["", "Tally: " + ", ".join(f"{k}: {v}" for k, v in tally.items() if v), ""]


def find_path(data, target, path="$"):
    """JSON path of the list object `target` inside `data` (to show where the products array lives)."""
    if data is target:
        return path
    if isinstance(data, dict):
        for k, v in data.items():
            found = find_path(v, target, f"{path}.{k}")
            if found:
                return found
    elif isinstance(data, list):
        for i, v in enumerate(data):
            found = find_path(v, target, f"{path}[{i}]")
            if found:
                return found
    return None


def pick_rd_eans(results_file, rd_names, products, limit=5, controls=2):
    """NO_PRICE EANs at the RD stores from results.csv (plus a few successes), else multipacks + singles."""
    if results_file and os.path.exists(results_file):
        with open(results_file, encoding="utf-8") as f:
            rows = [r for r in csv.DictReader(f) if r.get("store") in rd_names]
        failing = list(dict.fromkeys(r["ean"] for r in rows if (r.get("message") or "").startswith("NO_PRICE")))
        ok = list(dict.fromkeys(r["ean"] for r in rows if r.get("status") == "Success"))
        if failing:
            return failing[:limit] + [e for e in ok if e not in failing][:controls], "results.csv (NO_PRICE + controls)"
    multipacks = [p["ean"] for p in products if not p["desc_sku"].upper().endswith("SINGLE")]
    singles = [p["ean"] for p in products if p["desc_sku"].upper().endswith("SINGLE")]
    return multipacks[:limit] + singles[:controls], "products.csv (multipacks + single-pack controls)"


def diagnose_rd(store, eans, capture, session, delay, full_html, desc_by_ean, all_pages=False, comparison=None):
    lines = [f"### {store['name']} (RD_NEXTJS)", ""]
    platform = RdNextJs()
    control_needed = True  # fetch one priced product page per store to confirm which field holds the price
    for ean in eans:
        time.sleep(delay)
        url = platform.build_url(store["domain"], ean)
        name = f"rd_{slug(store['name'])}_{ean}"
        lines.append(f"**EAN {ean}** ({desc_by_ean.get(ean, '?')})")
        try:
            r = session.get(url)
        except Exception as e:
            capture.save(name, store["name"], ean, "rd_search", "GET", url, error=e)
            lines += [f"- request failed: {str(e)[:200]}", ""]
            continue
        html = r.text or ""
        state, kind = extract_state_from_html(html)
        if kind != "NEXT_JS":
            body = html if full_html else html[:HTML_LIMIT]
            capture.save(name, store["name"], ean, "rd_search", "GET", url, r, body, ext="html")
            lines += [f"- HTTP {r.status_code}, {len(html)} bytes, **no __NEXT_DATA__** (saved {name}.html)", ""]
            continue
        capture.save(name, store["name"], ean, "rd_search", "GET", url, r, state)
        products = find_true_products_array(state) or []
        chosen = next((p for p in products if p.get("is1P") is True), products[0] if products else None)
        lines.append(f"- HTTP {r.status_code}; products array at `{find_path(state, products) if products else '-'}` with {len(products)} item(s)")
        for i, p in enumerate(products[:5]):
            marker = " <- parser picks this" if p is chosen else ""
            lines.append(f"- product[{i}]{marker}: `{json.dumps(scalar_fields(p), ensure_ascii=False)[:1500]}`")
        if chosen is not None:
            lines.append(f"- all keys of the picked product: `{', '.join(sorted(chosen.keys()))[:1500]}`")
            prices = None
            if chosen.get("priceService") is None and chosen.get("url"):
                more, prices = diagnose_product_page(store, ean, chosen, "picked, no price", capture, session, delay, full_html)
                lines += more
            elif chosen.get("url") and (control_needed or all_pages):
                more, prices = diagnose_product_page(store, ean, chosen, f"control, search price {chosen.get('priceService')}",
                                                     capture, session, delay, full_html, verbose=control_needed)
                lines += more
                control_needed = False
            if comparison is not None and prices is not None:
                comparison.append({"store": store["name"], "ean": ean, "sku": chosen.get("sku"),
                                   "sponsored": bool(chosen.get("isSponsored")), "search": chosen.get("priceService"), **prices})
        lines.append("")
    return lines


def offer_facts(seller):
    o = seller.get("commertialOffer") or {}
    keep = ("Price", "ListPrice", "PriceWithoutDiscount", "AvailableQuantity", "IsAvailable", "PriceValidUntil")
    return {"sellerId": seller.get("sellerId"), "sellerDefault": seller.get("sellerDefault")} | {k: o.get(k) for k in keep if k in o}


def simulation_facts(payload):
    items = (payload or {}).get("items") or []
    item = items[0] if items else {}
    return {
        "availability": item.get("availability"),
        "price": item.get("price"),
        "sellingPrice": item.get("sellingPrice"),
        "seller": item.get("seller"),
        "messages": [m.get("text") for m in (payload or {}).get("messages") or []][:3],
    }


def batch_facts(payload, limit=5):
    """Availability counts of a multi-seller simulation and the first sellers that can deliver."""
    items = (payload or {}).get("items") or []
    counts = {}
    for it in items:
        counts[it.get("availability") or "none"] = counts.get(it.get("availability") or "none", 0) + 1
    available = [{"seller": it.get("seller"), "sellingPrice": it.get("sellingPrice")}
                 for it in items if it.get("availability") == "available"][:limit]
    return {"returned": len(items), "by_availability": counts, "available_sellers": available}


def regional_sellers(regions):
    """Distinct seller ids across every region entry the store returned, in order."""
    ids = []
    for entry in regions or []:
        for seller in entry.get("sellers") or []:
            if seller.get("id") and seller["id"] not in ids:
                ids.append(seller["id"])
    return ids


def fetch_json(session, capture, name, store, ean, purpose, url, body=None):
    """GET (or POST when body is given), save the raw response, return (status, parsed JSON or None)."""
    method = "POST" if body is not None else "GET"
    try:
        r = session.post(url, json=body) if body is not None else session.get(url)
    except Exception as e:
        capture.save(name, store["name"], ean, purpose, method, url, error=e)
        return None, None
    payload, ext = body_of(r)
    capture.save(name, store["name"], ean, purpose, method, url, r, payload, ext=ext)
    return r.status_code, payload if ext == "json" else None


def diagnose_vtex(store, eans, ceps, scs, capture, session, delay, max_sellers=50):
    """
    Per SKU: the catalog, a no-CEP simulation, and per CEP three ways of judging regional availability:
      E1 the main online seller (the catalog's default seller) simulated with the CEP,
      E2 one simulation with up to `max_sellers` of the sellers the regions lookup returns,
      E3 the catalog search with the store's regionId.
    """
    lines = [f"### {store['name']} (VTEX)", ""]
    domain = store["domain"]
    for ean in eans:
        base = f"vtex_{slug(store['name'])}_{ean}"
        url = f"{domain}/api/catalog_system/pub/products/search?fq=alternateIds_Ean:{ean}"
        lines.append(f"**EAN {ean}**")
        time.sleep(delay)
        status, data = fetch_json(session, capture, f"{base}_catalog", store, ean, "catalog", url)
        if not data or not isinstance(data, list) or not data[0].get("items"):
            lines += [f"- catalog: HTTP {status}, not listed or not JSON", ""]
            continue
        item = data[0]["items"][0]
        sellers = item.get("sellers") or []
        lines.append(f"- catalog: itemId={item.get('itemId')}, sellers: "
                     f"`{json.dumps([offer_facts(s) for s in sellers], ensure_ascii=False)[:800]}`")
        default = next((s for s in sellers if s.get("sellerDefault") is True), sellers[0] if sellers else {})
        sku_id, main_seller = item.get("itemId"), default.get("sellerId") or "1"

        for sc in scs:
            time.sleep(delay)
            surl = f"{domain}/api/checkout/pub/orderForms/simulation?sc={sc}"
            body = {"items": [{"id": sku_id, "quantity": 1, "seller": main_seller}], "country": "BRA"}
            status, payload = fetch_json(session, capture, f"{base}_sim_nocep_sc{sc}", store, ean, f"simulation no CEP sc={sc}", surl, body)
            lines.append(f"- no CEP, sc={sc}, seller {main_seller}: HTTP {status} `{json.dumps(simulation_facts(payload), ensure_ascii=False)[:300]}`")

        for label, cep in ceps.items():
            digits = re.sub(r"\D", "", cep)
            time.sleep(delay)
            rurl = f"{domain}/api/checkout/pub/regions?country=BRA&postalCode={digits}&sc={scs[0]}"
            status, regions = fetch_json(session, capture, f"{base}_regions_{digits}", store, ean, f"regions {label}", rurl)
            regions = regions if isinstance(regions, list) else []
            ids = regional_sellers(regions)
            lines.append(f"- {label} {cep}: regions HTTP {status}, {len(regions)} region(s), {len(ids)} distinct seller(s); main seller in list: {main_seller in ids}")
            for sc in scs:
                surl = f"{domain}/api/checkout/pub/orderForms/simulation?sc={sc}"
                time.sleep(delay)
                body = {"items": [{"id": sku_id, "quantity": 1, "seller": main_seller}], "country": "BRA", "postalCode": digits}
                status, payload = fetch_json(session, capture, f"{base}_e1_{digits}_sc{sc}", store, ean, f"E1 main seller {label} sc={sc}", surl, body)
                lines.append(f"  - E1 main seller {main_seller}, sc={sc}: HTTP {status} `{json.dumps(simulation_facts(payload), ensure_ascii=False)[:300]}`")
                if ids:
                    batch = ids[:max_sellers]
                    time.sleep(delay)
                    body = {"items": [{"id": sku_id, "quantity": 1, "seller": sid} for sid in batch], "country": "BRA", "postalCode": digits}
                    status, payload = fetch_json(session, capture, f"{base}_e2_{digits}_sc{sc}", store, ean, f"E2 regional sellers {label} sc={sc}", surl, body)
                    cut = f" (first {len(batch)} of {len(ids)})" if len(ids) > len(batch) else ""
                    lines.append(f"  - E2 {len(batch)} regional sellers{cut}, sc={sc}: HTTP {status} `{json.dumps(batch_facts(payload), ensure_ascii=False)[:400]}`")
            if regions and regions[0].get("id"):
                time.sleep(delay)
                curl = f"{url}&regionId={regions[0]['id']}"
                status, rdata = fetch_json(session, capture, f"{base}_e3_{digits}", store, ean, f"E3 catalog regionId {label}", curl)
                offers = (rdata[0]["items"][0].get("sellers") or []) if isinstance(rdata, list) and rdata and rdata[0].get("items") else []
                lines.append(f"  - E3 catalog with regionId: HTTP {status}, {len(offers)} seller offer(s) "
                             f"`{json.dumps([offer_facts(o) for o in offers[:5]], ensure_ascii=False)[:500]}`")
        lines.append("")
    return lines


def main(argv=None, session_factory=make_session):
    parser = argparse.ArgumentParser(description="Capture raw store responses for diagnosis.")
    parser.add_argument("--rd-eans", help='comma-separated EANs for the RD check, or "all" (default: from results.csv)')
    parser.add_argument("--rd-pages", choices=["sponsored", "all"], default="sponsored",
                        help="product pages to fetch: only sponsored ones plus one control (default), or all, with a price comparison table")
    parser.add_argument("--results", default="results.csv", help="results file used to pick NO_PRICE EANs")
    parser.add_argument("--vtex-stores", help=f"comma-separated store_ids (default: {', '.join(DEFAULT_VTEX_STORES)})")
    parser.add_argument("--vtex-eans", help=f"comma-separated EANs (default: {', '.join(DEFAULT_VTEX_EANS)})")
    parser.add_argument("--ceps", help='e.g. "SP=01310-100,RJ=20010-000" (default: SP, RJ, MG city centres)')
    parser.add_argument("--sc", default="1", help="VTEX sales channels to simulate, e.g. 1,2,3 (default 1, what the scraper uses)")
    parser.add_argument("--max-sellers", type=int, default=50, help="regional sellers per batch simulation (default 50)")
    parser.add_argument("--skip-rd", action="store_true")
    parser.add_argument("--skip-vtex", action="store_true")
    parser.add_argument("--full-html", action="store_true", help="keep whole HTML pages (large)")
    parser.add_argument("--delay", type=float, default=0.3)
    parser.add_argument("--products-file", default="data/products.csv")
    parser.add_argument("--stores-file", default="data/stores.csv")
    parser.add_argument("--out", default="diagnostics")
    args = parser.parse_args(argv)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    capture = Capture(os.path.join(args.out, stamp))
    products = load_products(args.products_file)
    desc_by_ean = {p["ean"]: p["desc_sku"] for p in products}
    stores = load_stores(args.stores_file)
    summary = [f"# Diagnostics {stamp}", ""]

    if not args.skip_rd:
        rd_stores = [s for s in stores if s["type"] == "RD_NEXTJS"]
        if args.rd_eans == "all":
            eans, source = [p["ean"] for p in products], "products.csv (all)"
        elif args.rd_eans:
            eans, source = [e.strip() for e in args.rd_eans.split(",") if e.strip()], "--rd-eans"
        else:
            eans, source = pick_rd_eans(args.results, {s["name"] for s in rd_stores}, products)
        summary += ["## RD search pages", f"EANs from {source}: {', '.join(eans)}", ""]
        comparison = []
        for store in rd_stores:
            print(f"RD: {store['name']} ({len(eans)} EANs)...")
            summary += diagnose_rd(store, eans, capture, session_factory(store), args.delay, args.full_html, desc_by_ean,
                                   all_pages=args.rd_pages == "all", comparison=comparison)
        if comparison:
            summary += ["## RD price comparison: search vs product page", ""] + comparison_table(comparison)

    if not args.skip_vtex:
        names = [s.strip() for s in (args.vtex_stores.split(",") if args.vtex_stores else DEFAULT_VTEX_STORES)]
        by_name = {s["name"]: s for s in stores}
        missing = [n for n in names if n not in by_name]
        eans = [e.strip() for e in args.vtex_eans.split(",")] if args.vtex_eans else DEFAULT_VTEX_EANS
        if args.ceps:
            from region_check import parse_ceps
            ceps = parse_ceps(args.ceps)
        else:
            ceps = {k: DEFAULT_CEPS[k] for k in DEFAULT_CEP_LABELS}
        scs = [s.strip() for s in args.sc.split(",") if s.strip()]
        summary += ["## VTEX catalog vs cart simulation", f"EANs: {', '.join(eans)}; CEPs: {ceps}; sales channels: {scs}", ""]
        if missing:
            summary += [f"Stores not found in stores.csv: {missing}", ""]
        for name in names:
            if name in by_name:
                print(f"VTEX: {name}...")
                summary += diagnose_vtex(by_name[name], eans, ceps, scs, capture, session_factory(by_name[name]), args.delay, args.max_sellers)

    with open(os.path.join(capture.dir, "index.json"), "w", encoding="utf-8") as f:
        json.dump(capture.index, f, ensure_ascii=False, indent=1)
    summary_path = os.path.join(capture.dir, "summary.md")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("\n".join(summary) + "\n")
    archive = shutil.make_archive(os.path.join(args.out, f"diagnostics_{stamp}"), "zip", capture.dir)

    errors = sum(1 for e in capture.index if "error" in e)
    print(f"\n{len(capture.index)} requests captured ({errors} failed).")
    print(f"Summary: {summary_path}\nArchive: {archive}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

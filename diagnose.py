"""
Capture raw store responses for diagnosis, so they can be inspected offline.

Two checks, both on by default:

  RD   (Drogasil / Droga Raia): the search page for EANs that come back NO_PRICE, plus a few that
       succeed as a control. Saves the embedded Next.js state and lists, per product, every
       price / stock / identity field, so we can see which product and which price field is right.
  VTEX (default: Venancio, Indiana, Drogaria Sao Paulo): the catalog search, the regions lookup and
       cart simulations for several CEPs and sales channels, to explain why the catalog says
       "available" while the simulation says "withoutStock".

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

from price_capture.config import load_products, load_stores
from price_capture.platforms.embedded_state import extract_state_from_html, find_true_products_array
from price_capture.platforms.rd_nextjs import RdNextJs
from region_check import DEFAULT_CEPS, make_session

DEFAULT_VTEX_STORES = ["Drogaria Venancio", "Farmácia Indiana", "Drogaria São Paulo"]
DEFAULT_VTEX_EANS = ["7509546651040", "7509546060781"]  # out of stock in the simulation vs fine in the catalog
DEFAULT_CEP_LABELS = ["SP-Sao Paulo", "RJ-Rio de Janeiro", "MG-Belo Horizonte"]
INTERESTING_KEY = re.compile(r"price|preco|valor|stock|estoque|avail|dispon|quant|qty|ean|gtin|sku|name|nome|1p|seller|status|buy|sale", re.I)
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


def diagnose_rd(store, eans, capture, session, delay, full_html, desc_by_ean):
    lines = [f"### {store['name']} (RD_NEXTJS)", ""]
    platform = RdNextJs()
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


def diagnose_vtex(store, eans, ceps, scs, capture, session, delay):
    lines = [f"### {store['name']} (VTEX)", ""]
    domain = store["domain"]
    for ean in eans:
        time.sleep(delay)
        base = f"vtex_{slug(store['name'])}_{ean}"
        url = f"{domain}/api/catalog_system/pub/products/search?fq=alternateIds_Ean:{ean}"
        lines.append(f"**EAN {ean}**")
        try:
            r = session.get(url)
        except Exception as e:
            capture.save(f"{base}_catalog", store["name"], ean, "catalog", "GET", url, error=e)
            lines += [f"- catalog request failed: {str(e)[:200]}", ""]
            continue
        data, ext = body_of(r)
        capture.save(f"{base}_catalog", store["name"], ean, "catalog", "GET", url, r, data, ext=ext)
        if ext != "json":
            lines += [f"- catalog: HTTP {r.status_code}, not JSON (saved {base}_catalog.txt)", ""]
            continue
        if not data or not data[0].get("items"):
            lines += [f"- catalog: HTTP {r.status_code}, not listed", ""]
            continue
        item = data[0]["items"][0]
        sellers = item.get("sellers") or []
        lines.append(f"- catalog: {len(data[0]['items'])} item(s); item[0] itemId={item.get('itemId')}, sellers: "
                     f"`{json.dumps([offer_facts(s) for s in sellers], ensure_ascii=False)[:1200]}`")
        default = next((s for s in sellers if s.get("sellerDefault") is True), sellers[0] if sellers else {})
        sku_id, default_seller = item.get("itemId"), default.get("sellerId") or "1"

        for label, cep in [("no CEP", None)] + list(ceps.items()):
            digits = re.sub(r"\D", "", cep) if cep else None
            seller = default_seller
            if cep:
                time.sleep(delay)
                rurl = f"{domain}/api/checkout/pub/regions?country=BRA&postalCode={digits}&sc={scs[0]}"
                try:
                    rr = session.get(rurl)
                    regions, ext = body_of(rr)
                    capture.save(f"{base}_regions_{digits}", store["name"], ean, f"regions {label}", "GET", rurl, rr, regions, ext=ext)
                    ok = ext == "json" and isinstance(regions, list) and regions
                    regional = [s.get("id") for s in (regions[0].get("sellers") or [])] if ok else []
                    seller = regional[0] if regional else default_seller
                    lines.append(f"- {label} {cep}: regional sellers {regional[:5] or 'none'}")
                except Exception as e:
                    capture.save(f"{base}_regions_{digits}", store["name"], ean, f"regions {label}", "GET", rurl, error=e)
                    lines.append(f"- {label} {cep}: regions lookup failed: {str(e)[:150]}")
            for sc in scs:
                time.sleep(delay)
                surl = f"{domain}/api/checkout/pub/orderForms/simulation?sc={sc}"
                body = {"items": [{"id": sku_id, "quantity": 1, "seller": seller}], "country": "BRA"}
                if digits:
                    body["postalCode"] = digits
                name = f"{base}_sim_{digits or 'nocep'}_sc{sc}"
                try:
                    sr = session.post(surl, json=body)
                    payload, ext = body_of(sr)
                    capture.save(name, store["name"], ean, f"simulation {label} sc={sc}", "POST", surl, sr, payload, ext=ext)
                    facts = simulation_facts(payload) if ext == "json" and isinstance(payload, dict) else "not JSON"
                    lines.append(f"  - simulation {label} sc={sc} seller={seller}: HTTP {sr.status_code} "
                                 f"`{json.dumps(facts, ensure_ascii=False)[:400]}`")
                except Exception as e:
                    capture.save(name, store["name"], ean, f"simulation {label} sc={sc}", "POST", surl, error=e)
                    lines.append(f"  - simulation {label} sc={sc}: failed: {str(e)[:150]}")
        lines.append("")
    return lines


def main(argv=None, session_factory=make_session):
    parser = argparse.ArgumentParser(description="Capture raw store responses for diagnosis.")
    parser.add_argument("--rd-eans", help="comma-separated EANs for the RD check (default: from results.csv)")
    parser.add_argument("--results", default="results.csv", help="results file used to pick NO_PRICE EANs")
    parser.add_argument("--vtex-stores", help=f"comma-separated store_ids (default: {', '.join(DEFAULT_VTEX_STORES)})")
    parser.add_argument("--vtex-eans", help=f"comma-separated EANs (default: {', '.join(DEFAULT_VTEX_EANS)})")
    parser.add_argument("--ceps", help='e.g. "SP=01310-100,RJ=20010-000" (default: SP, RJ, MG city centres)')
    parser.add_argument("--sc", default="1,2,3", help="VTEX sales channels to simulate (default 1,2,3)")
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
        if args.rd_eans:
            eans, source = [e.strip() for e in args.rd_eans.split(",") if e.strip()], "--rd-eans"
        else:
            eans, source = pick_rd_eans(args.results, {s["name"] for s in rd_stores}, products)
        summary += ["## RD search pages", f"EANs from {source}: {', '.join(eans)}", ""]
        for store in rd_stores:
            print(f"RD: {store['name']} ({len(eans)} EANs)...")
            summary += diagnose_rd(store, eans, capture, session_factory(store), args.delay, args.full_html, desc_by_ean)

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
                summary += diagnose_vtex(by_name[name], eans, ceps, scs, capture, session_factory(by_name[name]), args.delay)

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

import os, re, sys, csv, json
from curl_cffi import requests

def find_true_products_array(data):
    """Recursively search the JSON tree to find the TRUE products array for Next.js endpoints."""
    if isinstance(data, dict):
        if 'products' in data:
            candidate = data['products']
            if isinstance(candidate, list) and len(candidate) > 0:
                if isinstance(candidate[0], dict) and 'sku' in candidate[0]:
                    return candidate
        for key, value in data.items():
            result = find_true_products_array(value)
            if result is not None:
                return result
    elif isinstance(data, list):
        for item in data:
            result = find_true_products_array(item)
            if result is not None:
                return result
    return None

def extract_state_from_html(html):
    """Extract embedded JSON state from VTEX or Next.js payloads."""
    template_marker = '<template data-type="json" data-varname="__STATE__">'
    if template_marker in html:
        start_idx = html.find(template_marker) + len(template_marker)
        end_idx = html.find('</template>', start_idx)
        chunk = html[start_idx:end_idx]
        
        script_start = chunk.find('<script>')
        if script_start != -1:
            script_start += len('<script>')
            script_end = chunk.find('</script>', script_start)
            chunk = chunk[script_start:script_end]
            
        try:
            return json.loads(chunk.strip()), "VTEX_IO"
        except Exception:
            pass

    for state_marker in ['window.__STATE__ = ', '__STATE__ = ']:
        if state_marker in html:
            start_idx = html.find(state_marker) + len(state_marker)
            end_idx = html.find('</script>', start_idx)
            if end_idx != -1:
                chunk = html[start_idx:end_idx].strip()
                if chunk.endswith(';'):
                    chunk = chunk[:-1]
                try:
                    return json.loads(chunk), "VTEX_IO"
                except Exception:
                    pass

    next_marker = '<script id="__NEXT_DATA__" type="application/json">'
    if next_marker in html:
        start_idx = html.find(next_marker) + len(next_marker)
        end_idx = html.find('</script>', start_idx)
        if end_idx != -1:
            try:
                return json.loads(html[start_idx:end_idx].strip()), "NEXT_JS"
            except Exception:
                pass

    return None, None

def clean_ean(ean_input):
    """Removes spaces and dashes from the EAN input."""
    return re.sub(r'[-\s]', '', str(ean_input).strip())

def load_products(file_path):
    """Reads data/products.csv into a list of dicts."""
    products = []
    if not os.path.exists(file_path):
        print(f"Error: Product file '{file_path}' not found.")
        return products

    with open(file_path, mode='r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            ean = clean_ean(row.get('ean', ''))
            desc = row.get('desc_sku', '').strip()
            if ean:
                products.append({'ean': ean, 'desc_sku': desc})
    return products

def load_stores(file_path):
    """Reads data/stores.csv into a list of dicts with domain mappings."""
    # Mapping known store names to domain URLs
    domain_map = {
        "Drogasil": "https://www.drogasil.com.br",
        "Droga Raia": "https://www.drogaraia.com.br",
        "Drogaria Araujo": "https://www.araujo.com.br",
        "Drogaria São Paulo": "https://www.drogariasaopaulo.com.br",
        "Drogarias Pacheco": "https://www.drogariaspacheco.com.br",
        "Pague Menos": "https://www.paguemenos.com.br",
        "Farmácias São João": "https://www.saojoaofarmacias.com.br",
        "Drogal": "https://www.drogal.com.br",
        "Drogaria Venancio": "https://www.drogariavenancio.com.br",
        "Drogaria Catarinense": "https://www.drogariacatarinense.com.br",
        "Farmácia Indiana": "https://www.farmaciaindiana.com.br",
    }

    stores = []
    if not os.path.exists(file_path):
        print(f"Error: Store file '{file_path}' not found.")
        return stores

    with open(file_path, mode='r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            store_id = row.get('store_id', '').strip()
            store_type = row.get('type', '').strip()
            domain = domain_map.get(store_id)
            if store_id and store_type:
                stores.append({
                    'name': store_id,
                    'type': store_type,
                    'domain': domain
                })
    return stores

def scrape_store_product(store, product):
    """Queries a single store for a single product EAN."""
    name = store["name"]
    cat_type = store["type"]
    domain = store["domain"]
    cleaned_ean = product["ean"]

    if not domain:
        return {"status": "Error", "message": "Domain URL not configured"}

    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1"
    }

    if cat_type == "CARREFOUR_INTELLIGENT_SEARCH":
        url = f"{domain}/_v/api/intelligent-search/product_search/v2/?query={cleaned_ean}&sc=1&page=1&count=1&sort=&hideUnavailableItems=false"
    elif cat_type == "RD_NEXTJS":
        url = f"{domain}/search?w={cleaned_ean}&search-type=direct"
    elif cat_type == "VTEX_IO_HTML":
        url = f"{domain}/busca?q={cleaned_ean}&lang=pt_BR"
    else:  # B2C_REST
        url = f"{domain}/api/catalog_system/pub/products/search?fq=alternateIds_Ean:{cleaned_ean}"

    try:
        response = requests.get(
            url,
            headers=headers,
            impersonate="chrome120",
            timeout=15,
            allow_redirects=True,
            verify=False
        )

        if response.status_code not in [200, 301, 302]:
            return {"status": "Error", "message": f"HTTP {response.status_code}"}

        price = None
        found_name = None

        if cat_type == "VTEX_IO_HTML":
            html = response.text
            if any(w in html.lower() for w in ["cloudflare", "challenge-running", "just a moment"]):
                return {"status": "Error", "message": "Blocked by WAF / Cloudflare"}
            
            state, state_type = extract_state_from_html(html)
            if not state:
                return {"status": "Not Found", "message": f"HTML bytes: {len(html)}"}
                
            if state_type == "VTEX_IO":
                for key, value in state.items():
                    if isinstance(value, dict):
                        if value.get('__typename') == 'Product' and not found_name:
                            found_name = value.get('productName')
                        if value.get('__typename') == 'CommertialOffer':
                            offer_price = value.get('Price')
                            if offer_price and offer_price > 0:
                                if price is None or offer_price < price:
                                    price = offer_price
            elif state_type == "NEXT_JS":
                products = find_true_products_array(state)
                if products:
                    target_product = next((p for p in products if p.get('is1P') is True), products[0])
                    found_name = target_product.get('name')
                    price = target_product.get('priceService')

        elif cat_type == "RD_NEXTJS":
            html = response.text
            state, state_type = extract_state_from_html(html)
            if state and state_type == "NEXT_JS":
                products = find_true_products_array(state)
                if products:
                    target_product = next((p for p in products if p.get('is1P') is True), products[0])
                    found_name = target_product.get('name')
                    price = target_product.get('priceService')

        elif cat_type == "CARREFOUR_INTELLIGENT_SEARCH":
            data = response.json()
            if data and data.get("products"):
                p = data["products"][0]
                found_name = p.get("productName")
                items = p.get("items", [])
                if items and items[0].get("sellers"):
                    price = items[0]["sellers"][0].get("commertialOffer", {}).get("Price")

        else:  # B2C_REST
            data = response.json()
            if data and len(data) > 0:
                p = data[0]
                found_name = p.get("productName")
                items = p.get("items", [])
                if items and items[0].get("sellers"):
                    price = items[0]["sellers"][0].get("commertialOffer", {}).get("Price")

        if price is not None:
            return {
                "status": "Success",
                "product_name": found_name or product["desc_sku"],
                "price_brl": price
            }
        else:
            return {"status": "Not Found", "message": "Product found, but no price available."}

    except Exception as e:
        return {"status": "Error", "message": f"Exception: {str(e)[:50]}"}

def main():
    products_file = os.environ.get("PRODUCTS_FILE", "data/products.csv")
    stores_file = os.environ.get("STORES_FILE", "data/stores.csv")

    products = load_products(products_file)
    stores = load_stores(stores_file)

    if not products:
        print("No valid products loaded. Exiting.")
        sys.exit(1)
    if not stores:
        print("No valid stores loaded. Exiting.")
        sys.exit(1)

    print(f"Loaded {len(products)} product(s) and {len(stores)} store(s).\n{'='*60}")

    all_results = []

    for product in products:
        ean = product["ean"]
        desc = product["desc_sku"]
        print(f"\n📦 EAN: {ean} - {desc}\n{'-'*60}")

        for store in stores:
            res = scrape_store_product(store, product)
            store_name = store["name"]
            
            record = {
                "ean": ean,
                "desc_sku": desc,
                "store": store_name,
                "status": res.get("status"),
                "price_brl": res.get("price_brl"),
                "found_name": res.get("product_name"),
                "message": res.get("message")
            }
            all_results.append(record)

            if res["status"] == "Success":
                p_val = res['price_brl']
                price_str = f"R$ {float(p_val):.2f}" if isinstance(p_val, (int, float)) else f"R$ {p_val}"
                print(f"  ✅ {store_name.ljust(22)}: {price_str.ljust(12)}")
            elif res["status"] == "Not Found":
                print(f"  ❌ {store_name.ljust(22)}: {res['message']}")
            else:
                print(f"  ⚠️ {store_name.ljust(22)}: {res['message']}")

    # Save to JSON
    with open("results.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    # Save to CSV output
    with open("results.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["ean", "desc_sku", "store", "status", "price_brl", "found_name", "message"])
        writer.writeheader()
        writer.writerows(all_results)

    print(f"\nScraping complete. Results saved to results.json and results.csv.")

if __name__ == "__main__":
    main()
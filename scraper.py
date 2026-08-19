import os, re, sys, csv, json, time
from curl_cffi import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

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
    """Reads data/stores.csv and filters for active stores."""
    stores = []
    if not os.path.exists(file_path):
        print(f"Error: Store file '{file_path}' not found.")
        return stores

    with open(file_path, mode='r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            store_id = row.get('store_id', '').strip()
            store_type = row.get('type', '').strip()
            domain = row.get('domain', '').strip()
            
            # Check if store is enabled (defaults to true if column is missing)
            enabled_val = row.get('enabled', 'true').strip().lower()
            is_enabled = enabled_val in ['true', '1', 'yes']

            if not is_enabled:
                print(f"⏭️ Skipping disabled store: {store_id}")
                continue
            
            if store_id and store_type and domain:
                stores.append({
                    'name': store_id,
                    'type': store_type,
                    'domain': domain
                })
    return stores

def scrape_store_product(store, product, max_retries=2):
    """
    Queries a single store for a single product EAN with split timeouts and
    exponential backoff retry logic for network drops (curl 28).
    """
    name = store["name"]
    cat_type = store["type"]
    domain = store["domain"]
    cleaned_ean = product["ean"]

    if not domain:
        return {"status": "Error", "message": "Domain URL not configured"}

    # Build target endpoint URL based on store architecture
    if cat_type == "CARREFOUR_INTELLIGENT_SEARCH":
        url = f"{domain}/_v/api/intelligent-search/product_search/v2/?query={cleaned_ean}&sc=1&page=1&count=1&sort=&hideUnavailableItems=false"
    elif cat_type == "RD_NEXTJS":
        url = f"{domain}/search?w={cleaned_ean}&search-type=direct"
    elif cat_type == "VTEX_IO_HTML":
        url = f"{domain}/busca?q={cleaned_ean}&lang=pt_BR"
    else:  # B2C_REST (Legacy VTEX)
        url = f"{domain}/api/catalog_system/pub/products/search?fq=alternateIds_Ean:{cleaned_ean}"

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

    response = None
    
    # Retry loop with exponential backoff for handling transient drops / timeouts
    for attempt in range(max_retries + 1):
        try:
            # timeout=(connect_timeout, read_timeout)
            response = requests.get(
                url,
                headers=headers,
                impersonate="chrome120",
                timeout=(10, 20),
                allow_redirects=True,
                verify=False
            )
            break  # Connection successful
        except requests.errors.RequestsError as e:
            err_msg = str(e).lower()
            if ("timed out" in err_msg or "28" in err_msg) and attempt < max_retries:
                time.sleep(2 * (attempt + 1))  # Waits 2s, then 4s
                continue
            return {"status": "Error", "message": "Connection Timed Out (curl 28)"}
        except Exception as e:
            if attempt < max_retries:
                time.sleep(2 * (attempt + 1))
                continue
            return {"status": "Error", "message": f"Exception: {str(e)[:40]}"}

    if not response or response.status_code not in [200, 301, 302]:
        status_code = response.status_code if response else "No Response"
        return {"status": "Error", "message": f"HTTP {status_code}"}

    price = None
    found_name = None

    try:
        # --- 1. VTEX IO HTML Scraping ---
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

        # --- 2. RD Group Next.js (Drogasil / Droga Raia) ---
        elif cat_type == "RD_NEXTJS":
            html = response.text
            state, state_type = extract_state_from_html(html)
            if state and state_type == "NEXT_JS":
                products = find_true_products_array(state)
                if products:
                    target_product = next((p for p in products if p.get('is1P') is True), products[0])
                    found_name = target_product.get('name')
                    price = target_product.get('priceService')

        # --- 3. Carrefour Intelligent Search ---
        elif cat_type == "CARREFOUR_INTELLIGENT_SEARCH":
            data = response.json()
            if data and data.get("products"):
                p = data["products"][0]
                found_name = p.get("productName")
                items = p.get("items", [])
                if items and items[0].get("sellers"):
                    price = items[0]["sellers"][0].get("commertialOffer", {}).get("Price")

        # --- 4. B2C REST API (Legacy VTEX) ---
        else:
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
                "product_name": found_name or product.get("desc_sku", "Produto Encontrado"),
                "price_brl": price
            }
        else:
            return {"status": "Not Found", "message": "Product found, but no price available."}

    except Exception as e:
        return {"status": "Error", "message": f"Parsing Error: {str(e)[:40]}"}
    
def process_task(args):
    """Worker function to process a single (store, product) pair in a thread."""
    store, product = args
    res = scrape_store_product(store, product)
    store_name = store["name"]
    ean = product["ean"]

    # Generate timestamp in YYYY-MM-DD HH:MM format
    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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

def main():
    products_file = os.environ.get("PRODUCTS_FILE", "data/products.csv")
    stores_file = os.environ.get("STORES_FILE", "data/stores.csv")
    max_workers = int(os.environ.get("MAX_WORKERS", 10))  # Default to 10 concurrent threads

    products = load_products(products_file)
    stores = load_stores(stores_file)

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
            fieldnames=[
                "ean",
                "desc_sku",
                "store",
                "status",
                "price_brl",
                "found_name",
                "fetched_at",
                "message",
            ],
        )
        writer.writeheader()
        writer.writerows(all_results)

    print(f"\nScraping complete. Total requests processed: {len(all_results)}")

if __name__ == "__main__":
    main()
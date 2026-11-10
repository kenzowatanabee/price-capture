"""Loading of the product and store lists from CSV."""
import csv
import os
import re


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

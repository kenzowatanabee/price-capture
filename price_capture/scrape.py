"""Scrape a single (store, product) pair."""
from price_capture.http import fetch
from price_capture.platforms import get_platform
from price_capture.results import error


def scrape_store_product(store, product):
    """Queries a single store for a single product EAN through its platform implementation."""
    domain = store["domain"]
    if not domain:
        return error("CONFIG", "domain URL not configured")

    platform = get_platform(store["type"])
    if platform is None:
        return error("CONFIG", f"unknown store type '{store['type']}'")
    response, fetch_error = fetch(
        platform.build_url(domain, product["ean"]),
        verify=store.get("verify_ssl", True),
    )
    if fetch_error:
        return fetch_error

    if not response or response.status_code not in [200, 301, 302]:
        status_code = response.status_code if response else "No Response"
        return error("HTTP_ERROR", status_code)

    try:
        return platform.parse(response, product)
    except Exception as e:
        return error("PARSE_ERROR", str(e)[:60])

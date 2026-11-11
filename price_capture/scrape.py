"""Scrape a single (store, product) pair."""
from urllib.parse import urljoin

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
    verify = store.get("verify_ssl", True)
    response, problem = _get(platform.build_url(domain, product["ean"]), verify)
    if problem:
        return problem
    try:
        result = platform.parse(response, product)
    except Exception as e:
        return error("PARSE_ERROR", str(e)[:60])

    # Some platforms need a second page (e.g. RD reads the price from the product page).
    if "follow_path" in result:
        page_url = urljoin(domain + "/", result["follow_path"])
        response, problem = _get(page_url, verify)
        if problem:
            return problem
        try:
            return platform.parse_follow_up(response, product, result | {"page_url": page_url})
        except Exception as e:
            return error("PARSE_ERROR", str(e)[:60])
    return result


def _get(url, verify):
    """(response, None) for a usable response, else (None, error result)."""
    response, fetch_error = fetch(url, verify=verify)
    if fetch_error:
        return None, fetch_error
    if not response or response.status_code not in [200, 301, 302]:
        status_code = response.status_code if response else "No Response"
        return None, error("HTTP_ERROR", status_code)
    return response, None

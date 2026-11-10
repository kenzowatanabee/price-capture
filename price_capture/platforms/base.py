"""Platform interface and helpers shared by the store implementations."""
from price_capture.platforms.embedded_state import find_true_products_array


class Platform:
    """A store architecture: knows how to build its search URL and parse the response."""

    def build_url(self, domain, ean):
        raise NotImplementedError

    def parse(self, response, product):
        """Returns a result dict ({"status": ..., ...}). May raise; the caller reports parsing errors."""
        raise NotImplementedError


def build_result(found_name, price, product):
    """Common tail of every parser: a price makes it a success, otherwise 'Not Found'."""
    if price is not None:
        return {
            "status": "Success",
            "product_name": found_name or product.get("desc_sku", "Produto Encontrado"),
            "price_brl": price,
        }
    return {"status": "Not Found", "message": "Product found, but no price available."}


def vtex_first_seller_price(vtex_product):
    """Price of the first item's first seller in a VTEX product payload (or None)."""
    items = vtex_product.get("items", [])
    if items and items[0].get("sellers"):
        return items[0]["sellers"][0].get("commertialOffer", {}).get("Price")
    return None


def next_js_product(state):
    """(name, price) of the preferred product in Next.js state: first-party seller first, else the first one."""
    products = find_true_products_array(state)
    if not products:
        return None, None
    target = next((p for p in products if p.get('is1P') is True), products[0])
    return target.get('name'), target.get('priceService')

"""Platform interface and helpers shared by the store implementations."""
from price_capture.platforms.embedded_state import find_true_products_array


class Platform:
    """A store architecture: knows how to build its search URL and parse the response."""

    def build_url(self, domain, ean):
        raise NotImplementedError

    def parse(self, response, product):
        """Returns a result dict ({"status": ..., ...}). May raise; the caller reports parsing errors."""
        raise NotImplementedError


def build_result(found_name, price, product, available=True):
    """Common tail of every parser: an in-stock positive price is a success, otherwise 'Not Found'."""
    if price is None:
        return {"status": "Not Found", "message": "Product found, but no price available."}
    if not available:
        return {"status": "Not Found", "message": "Product found, but out of stock."}
    # Stores report 0 for items they cannot sell; that is not a shelf price.
    if price <= 0:
        return {"status": "Not Found", "message": f"Product found, but price is {price} (unavailable)."}
    return {
        "status": "Success",
        "product_name": found_name or product.get("desc_sku", "Produto Encontrado"),
        "price_brl": price,
    }


def vtex_offer(vtex_product):
    """
    (price, available) of the preferred offer of a VTEX product payload: the first item's
    default seller, else its first seller. Availability is only false when the payload says
    so explicitly (IsAvailable false, or AvailableQuantity present and not positive).
    """
    items = vtex_product.get("items", [])
    if not items or not items[0].get("sellers"):
        return None, True
    sellers = items[0]["sellers"]
    chosen = next((s for s in sellers if s.get("sellerDefault") is True), sellers[0])
    offer = chosen.get("commertialOffer", {})
    quantity = offer.get("AvailableQuantity")
    available = offer.get("IsAvailable") is not False and not (quantity is not None and quantity <= 0)
    return offer.get("Price"), available


def next_js_product(state):
    """(name, price) of the preferred product in Next.js state: first-party seller first, else the first one."""
    products = find_true_products_array(state)
    if not products:
        return None, None
    target = next((p for p in products if p.get('is1P') is True), products[0])
    return target.get('name'), target.get('priceService')

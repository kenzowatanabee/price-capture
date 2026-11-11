"""Platform interface and helpers shared by the store implementations."""
import re
from typing import NamedTuple, Optional
from urllib.parse import urljoin

from price_capture.platforms.embedded_state import find_true_products_array
from price_capture.results import not_found


class Platform:
    """A store architecture: knows how to build its search URL and parse the response."""

    def build_url(self, domain, ean):
        raise NotImplementedError

    def parse(self, response, product):
        """
        Returns a result dict ({"status": ..., ...}). May raise; the caller reports parsing errors.
        A platform that needs a second page returns {"follow_path": <path or url>, ...} instead; the
        caller fetches it and passes the response to parse_follow_up together with this dict.
        """
        raise NotImplementedError

    def parse_follow_up(self, response, product, first):
        raise NotImplementedError


def to_price(value):
    """
    A price as a number. Numbers pass through; numeric strings are converted ("19.90",
    "R$ 19,90", "1.299,90"); an empty string means no price (None). Anything else raises
    ValueError, which the caller reports as PARSE_ERROR instead of storing it in a DECIMAL column.
    """
    if value is None or (isinstance(value, (int, float)) and not isinstance(value, bool)):
        return value
    if isinstance(value, str):
        text = value.replace("R$", "").strip()
        if not text:
            return None
        if re.fullmatch(r"\d+(\.\d+)?", text):  # 19.90 / 1299.90
            return float(text)
        if re.fullmatch(r"\d{1,3}(\.\d{3})*(,\d+)?|\d+,\d+", text):  # 19,90 / 1.299,90
            return float(text.replace(".", "").replace(",", "."))
    raise ValueError(f"unparseable price {value!r}")


def build_result(found_name, price, product, available=None, extra=None):
    """
    Common tail of every parser: an in-stock positive price is a success, otherwise 'Not Found'.
    `available` is True/False when the payload says so and None when unknown (treated as available).
    `extra` carries the descriptive columns (list_price, available, seller, url); they are kept on
    every outcome for a product that was found, while price_brl is only set on success. The
    matched product's name is likewise kept on every outcome for a found product.
    """
    price = to_price(price)
    if price is None:
        result = not_found("NO_PRICE", "product found, but no price available")
    elif available is False:
        result = not_found("OUT_OF_STOCK", "product found, but out of stock")
    # Stores report 0 for items they cannot sell; that is not a shelf price.
    elif price <= 0:
        result = not_found("ZERO_PRICE", f"product found, but price is {price}")
    else:
        result = {
            "status": "Success",
            "product_name": found_name or product.get("desc_sku", "Produto Encontrado"),
            "price_brl": price,
        }
    # Keep the matched product's name on rows without a price too, so a wrong match can be audited.
    if found_name and result["status"] != "Success":
        result["product_name"] = found_name
    result.update(extra or {})
    return result


class VtexOffer(NamedTuple):
    price: Optional[float]
    available: Optional[bool]  # None when the payload does not say
    extra: dict  # list_price, available, seller, url (only the ones present)


def vtex_offer(vtex_product, base_url=""):
    """
    The preferred offer of a VTEX product payload: the first item's default seller, else its
    first seller. Availability is False when the payload says so (IsAvailable false, or
    AvailableQuantity not positive), True when it says it is available, otherwise unknown.
    """
    items = vtex_product.get("items", [])
    if not items or not items[0].get("sellers"):
        return VtexOffer(None, None, {})
    sellers = items[0]["sellers"]
    chosen = next((s for s in sellers if s.get("sellerDefault") is True), sellers[0])
    offer = chosen.get("commertialOffer", {})

    flag = offer.get("IsAvailable")
    quantity = offer.get("AvailableQuantity")
    if flag is False or (quantity is not None and quantity <= 0):
        available = False
    elif flag is True or (quantity is not None and quantity > 0):
        available = True
    else:
        available = None

    list_price = offer.get("ListPrice")
    link = vtex_product.get("link")
    extra = {
        "list_price": list_price if isinstance(list_price, (int, float)) and list_price > 0 else None,
        "available": None if available is None else str(available).lower(),
        "seller": chosen.get("sellerName"),
        "url": urljoin(base_url, link) if link else None,
    }
    return VtexOffer(offer.get("Price"), available, {k: v for k, v in extra.items() if v is not None})


def next_js_product(state):
    """(name, price) of the preferred product in Next.js state (first-party seller first, else the first one), or None if the page lists no products."""
    products = find_true_products_array(state)
    if not products:
        return None
    target = next((p for p in products if p.get('is1P') is True), products[0])
    return target.get('name'), target.get('priceService')

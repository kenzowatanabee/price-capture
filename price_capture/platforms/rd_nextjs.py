import re
from urllib.parse import urlsplit, urlunsplit

from price_capture.platforms.base import Platform, build_result, to_price
from price_capture.platforms.embedded_state import extract_state_from_html, find_true_products_array
from price_capture.results import error, not_found


class RdNextJs(Platform):
    """
    RD Group storefronts: Drogasil / Droga Raia (type RD_NEXTJS).

    The search page identifies the product (first-party offer first), but its price field
    (priceService) is a search-index value that often differs from the price on the site, and
    sponsored results carry no price at all. So the price is read from the product page:
    productData.price_aux.value_to (what the site shows), with value_from as the list price.
    """

    def build_url(self, domain, ean):
        return f"{domain}/search?w={ean}&search-type=direct"

    def parse(self, response, product):
        html = response.text
        state, state_type = extract_state_from_html(html)
        if not state or state_type != "NEXT_JS":
            return not_found("NO_STATE", f"no embedded state in page ({len(html)} bytes)")
        products = find_true_products_array(state)
        if not products:
            return not_found("NOT_LISTED", "store returned no product")
        target = next((p for p in products if p.get("is1P") is True), products[0])
        if not target.get("url"):
            result = not_found("NO_PRICE", "search result has no product page url")
            if target.get("name"):
                result["product_name"] = target["name"]
            return result
        return {"follow_path": target["url"], "search_sku": target.get("sku"), "search_name": target.get("name")}

    def parse_follow_up(self, response, product, first):
        html = response.text
        state, state_type = extract_state_from_html(html)
        if not state or state_type != "NEXT_JS":
            return not_found("NO_STATE", f"no embedded state in product page ({len(html)} bytes)")
        data = ((state.get("props") or {}).get("pageProps") or {}).get("productData") or {}
        if not data:
            return not_found("NO_STATE", "product page has no productData")

        page_sku, search_sku = str(data.get("sku") or ""), str(first.get("search_sku") or "")
        if page_sku and search_sku and page_sku != search_sku:
            return error("PARSE_ERROR", f"product page sku {page_sku} does not match search sku {search_sku}")

        # The search page carries no EAN, the product page does: make sure it is the product we asked for.
        page_ean = re.sub(r"\D", "", str(data.get("productEan") or ""))
        if page_ean and page_ean.lstrip("0") != str(product["ean"]).lstrip("0"):
            result = not_found("EAN_MISMATCH", f"product page EAN {page_ean} is not {product['ean']}")
            if data.get("name"):
                result["product_name"] = data["name"]
            return result

        aux = data.get("price_aux") or {}
        live = ((data.get("liveComposition") or {}).get("livePrice")) or {}
        price = aux.get("value_to") if aux.get("value_to") is not None else live.get("valueTo")
        list_price = to_price(aux.get("value_from"))

        extra = {}
        if isinstance(list_price, (int, float)) and list_price > 0:
            extra["list_price"] = list_price
        if first.get("page_url"):
            parts = urlsplit(first["page_url"])
            extra["url"] = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))  # without ?origin=search
        return build_result(data.get("name") or first.get("search_name"), price, product, extra=extra)

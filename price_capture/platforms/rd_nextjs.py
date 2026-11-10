from price_capture.platforms.base import Platform, build_result, next_js_product
from price_capture.platforms.embedded_state import extract_state_from_html


class RdNextJs(Platform):
    """RD Group storefronts: Drogasil / Droga Raia (type RD_NEXTJS)."""

    def build_url(self, domain, ean):
        return f"{domain}/search?w={ean}&search-type=direct"

    def parse(self, response, product):
        found_name = None
        price = None
        state, state_type = extract_state_from_html(response.text)
        if state and state_type == "NEXT_JS":
            found_name, price = next_js_product(state)
        return build_result(found_name, price, product)

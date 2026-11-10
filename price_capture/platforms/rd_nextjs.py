from price_capture.platforms.base import Platform, build_result, next_js_product
from price_capture.platforms.embedded_state import extract_state_from_html
from price_capture.results import not_found


class RdNextJs(Platform):
    """RD Group storefronts: Drogasil / Droga Raia (type RD_NEXTJS)."""

    def build_url(self, domain, ean):
        return f"{domain}/search?w={ean}&search-type=direct"

    def parse(self, response, product):
        html = response.text
        state, state_type = extract_state_from_html(html)
        if not state or state_type != "NEXT_JS":
            return not_found("NO_STATE", f"no embedded state in page ({len(html)} bytes)")
        picked = next_js_product(state)
        if picked is None:
            return not_found("NOT_LISTED", "store returned no product")
        found_name, price = picked
        return build_result(found_name, price, product)

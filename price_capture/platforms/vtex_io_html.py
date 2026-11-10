from price_capture.platforms.base import Platform, build_result, next_js_product
from price_capture.platforms.embedded_state import extract_state_from_html


class VtexIoHtml(Platform):
    """VTEX IO storefront, scraped from the embedded page state (type VTEX_IO_HTML)."""

    def build_url(self, domain, ean):
        return f"{domain}/busca?q={ean}&lang=pt_BR"

    def parse(self, response, product):
        html = response.text
        if any(w in html.lower() for w in ["cloudflare", "challenge-running", "just a moment"]):
            return {"status": "Error", "message": "Blocked by WAF / Cloudflare"}

        state, state_type = extract_state_from_html(html)
        if not state:
            return {"status": "Not Found", "message": f"HTML bytes: {len(html)}"}

        found_name = None
        price = None
        if state_type == "VTEX_IO":
            for value in state.values():
                if isinstance(value, dict):
                    if value.get('__typename') == 'Product' and not found_name:
                        found_name = value.get('productName')
                    if value.get('__typename') == 'CommertialOffer':
                        offer_price = value.get('Price')
                        if offer_price and offer_price > 0:
                            if price is None or offer_price < price:
                                price = offer_price
        elif state_type == "NEXT_JS":
            found_name, price = next_js_product(state)
        return build_result(found_name, price, product)

from price_capture.platforms.base import Platform, apply_cart_check, build_result, vtex_offer, with_cart_check
from price_capture.results import not_found


class VtexCatalog(Platform):
    """Legacy VTEX catalog REST API (type B2C_REST)."""

    def build_url(self, domain, ean):
        return f"{domain}/api/catalog_system/pub/products/search?fq=alternateIds_Ean:{ean}"

    def parse(self, response, product):
        data = response.json()
        if not data:
            return not_found("NOT_LISTED", "store returned no product")
        p = data[0]
        offer = vtex_offer(p, getattr(response, "url", ""))
        result = build_result(p.get("productName"), offer.price, product, offer.available, offer.extra)
        return with_cart_check(result, offer)

    def parse_follow_up(self, response, product, first):
        return apply_cart_check(response, first)

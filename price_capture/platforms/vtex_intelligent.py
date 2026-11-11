from price_capture.platforms.base import Platform, apply_cart_check, build_result, vtex_offer, with_cart_check
from price_capture.results import not_found


class VtexIntelligentSearch(Platform):
    """VTEX Intelligent Search JSON API (type CARREFOUR_INTELLIGENT_SEARCH)."""

    def build_url(self, domain, ean):
        return f"{domain}/_v/api/intelligent-search/product_search/v2/?query={ean}&sc=1&page=1&count=1&sort=&hideUnavailableItems=false"

    def parse(self, response, product):
        data = response.json()
        if not data or not data.get("products"):
            return not_found("NOT_LISTED", "store returned no product")
        p = data["products"][0]
        offer = vtex_offer(p, getattr(response, "url", ""))
        result = build_result(p.get("productName"), offer.price, product, offer.available, offer.extra)
        return with_cart_check(result, offer)

    def parse_follow_up(self, response, product, first):
        return apply_cart_check(response, first)

from price_capture.platforms.base import Platform, build_result, vtex_offer
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
        return build_result(p.get("productName"), offer.price, product, offer.available, offer.extra)

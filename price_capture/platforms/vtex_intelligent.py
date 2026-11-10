from price_capture.platforms.base import Platform, build_result, vtex_offer


class VtexIntelligentSearch(Platform):
    """VTEX Intelligent Search JSON API (type CARREFOUR_INTELLIGENT_SEARCH)."""

    def build_url(self, domain, ean):
        return f"{domain}/_v/api/intelligent-search/product_search/v2/?query={ean}&sc=1&page=1&count=1&sort=&hideUnavailableItems=false"

    def parse(self, response, product):
        found_name = None
        price = None
        available = True
        data = response.json()
        if data and data.get("products"):
            p = data["products"][0]
            found_name = p.get("productName")
            price, available = vtex_offer(p)
        return build_result(found_name, price, product, available)

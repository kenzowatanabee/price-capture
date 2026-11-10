from price_capture.platforms.base import Platform, build_result, vtex_offer
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
        price, available = vtex_offer(p)
        return build_result(p.get("productName"), price, product, available)

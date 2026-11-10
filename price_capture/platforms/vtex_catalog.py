from price_capture.platforms.base import Platform, build_result, vtex_first_seller_price


class VtexCatalog(Platform):
    """Legacy VTEX catalog REST API (type B2C_REST)."""

    def build_url(self, domain, ean):
        return f"{domain}/api/catalog_system/pub/products/search?fq=alternateIds_Ean:{ean}"

    def parse(self, response, product):
        found_name = None
        price = None
        data = response.json()
        if data and len(data) > 0:
            p = data[0]
            found_name = p.get("productName")
            price = vtex_first_seller_price(p)
        return build_result(found_name, price, product)

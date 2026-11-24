import re
from urllib.parse import urlsplit, urlunsplit

import os

from price_capture.platforms.base import Platform, build_result, first_image, make_offer, same_ean, to_price
from price_capture.platforms.embedded_state import extract_state_from_html, find_true_products_array
from price_capture.results import error, not_found


# How many other sellers' pages are fetched per product (one request each). 0 turns it off.
MAX_PARTNER_PAGES = int(os.environ.get("RD_PARTNER_PAGES", "5"))


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
        # Other sellers of the same search: each has its own product page (fetched after the main one).
        others = [p["url"] for p in products if p is not target and p.get("is1P") is not True and p.get("url")]
        return {"follow_path": target["url"], "search_sku": target.get("sku"), "search_name": target.get("name"),
                "offer_paths": others[:MAX_PARTNER_PAGES]}

    @staticmethod
    def _product_data(response):
        state, state_type = extract_state_from_html(response.text)
        if not state or state_type != "NEXT_JS":
            return None
        return ((state.get("props") or {}).get("pageProps") or {}).get("productData") or None

    @staticmethod
    def _seller_fields(data):
        """seller_type, seller, seller_id and seller_cnpj as the product page states them."""
        fields = {}
        if isinstance(data.get("is3p"), bool):
            fields["seller_type"] = "partner" if data["is3p"] else "store"
        partner = data.get("seller")
        if isinstance(partner, dict):
            if partner.get("storeName") or partner.get("corporateName"):
                fields["seller"] = partner.get("storeName") or partner.get("corporateName")
            if partner.get("id"):
                fields["seller_id"] = str(partner["id"])
            cnpj = re.sub(r"\D", "", str(partner.get("cnpj") or ""))
            if cnpj:
                fields["seller_cnpj"] = cnpj
        return fields

    @staticmethod
    def _price(data):
        aux = data.get("price_aux") or {}
        live = ((data.get("liveComposition") or {}).get("livePrice")) or {}
        price = aux.get("value_to") if aux.get("value_to") is not None else live.get("valueTo")
        list_price = to_price(aux.get("value_from"))
        return price, list_price if isinstance(list_price, (int, float)) and list_price > 0 else None

    def parse_offer_page(self, response, product, page_url):
        """Another seller's page for the same search: an offer only when its EAN is the one searched."""
        data = self._product_data(response)
        if not data or not same_ean(data.get("productEan"), product["ean"]):
            return None
        price, list_price = self._price(data)
        parts = urlsplit(page_url)
        return make_offer(price, list_price=list_price, product_name=data.get("name"),
                          url=urlunsplit((parts.scheme, parts.netloc, parts.path, "", "")),
                          **self._seller_fields(data))

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

        price, list_price = self._price(data)

        extra = {}
        if list_price is not None:
            extra["list_price"] = list_price
        if first.get("page_url"):
            parts = urlsplit(first["page_url"])
            extra["url"] = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))  # without ?origin=search
        # Who sells it: RD itself (1P) or a marketplace partner (3P), whose name and id the page carries.
        extra.update(self._seller_fields(data))
        gallery = data.get("mediaGallery")
        pictures = [m for m in gallery if isinstance(m, dict) and m.get("media_type") == "image"
                    and not m.get("disabled")] if isinstance(gallery, list) else []
        image_url = first_image(sorted(pictures, key=lambda m: m.get("position") or 0), "file")
        if image_url:
            extra["image_url"] = image_url
        return build_result(data.get("name") or first.get("search_name"), price, product, extra=extra)

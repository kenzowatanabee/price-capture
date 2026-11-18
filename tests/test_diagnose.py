import json
import os
import zipfile

import diagnose


class Resp:
    def __init__(self, status=200, text="", payload=None, headers=None):
        self.status_code = status
        self.text = text
        self._payload = payload
        self.headers = headers or {"content-type": "application/json", "set-cookie": "session=SECRET"}

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def next_page(state, extra_html=""):
    return '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(state) + "</script>" + extra_html


def search_page(products):
    return next_page({"props": {"pageProps": {"pageProps": {"results": {"products": products}}}}})


def product_page(sku, value_to, value_from, best=None):
    live = {"valueTo": value_to, "bestPrice": {"valueTo": best[0], "type": best[1]} if best else None}
    return {"props": {"pageProps": {"productData": {
        "sku": sku, "price": 0, "price_aux": {"value_to": value_to, "value_from": value_from}, "liveComposition": {"livePrice": live},
    }}}}


SPONSORED = {"sku": "1329580", "name": "Creme X 60g", "is1P": True, "isSponsored": True, "amount": "60g",
             "url": "/creme-x-60g-1329580.html?origin=search"}
PRICED = {"sku": "62451", "name": "Gel Y 50g", "is1P": True, "priceService": 17.99, "url": "/gel-y-50g-62451.html?origin=search"}
MARKETPLACE = {"sku": "891159", "name": "Creme X 60g", "is1P": False, "isThirdPartner": True, "priceService": 23.5,
               "url": "/creme-x-60g-891159.html?origin=search"}
PARTNER_2PACK = {"sku": "948117", "name": "Creme X 60g 2 unidades", "is1P": False, "isThirdPartner": True,
                 "priceService": 41.0, "url": "/creme-x-60g-2un-948117.html?origin=search"}


class FakeSession:
    def __init__(self):
        self.posts = []

    def get(self, url, **kw):
        # RD
        if "/search?w=111" in url:
            return Resp(text=search_page([SPONSORED, MARKETPLACE, PARTNER_2PACK]))
        if "/search?w=333" in url:
            return Resp(text=search_page([PRICED]))
        if "/search?w=222" in url:
            return Resp(status=403, text="<html>Access denied</html>", headers={"content-type": "text/html"})
        if "1329580.html" in url:
            ld = '<script type="application/ld+json">{"offers": {"price": "19.90"}}</script>'
            return Resp(text=next_page(product_page("1329580", 19.9, 24.9, best=(14.92, "PBM")), ld))
        if "891159.html" in url:  # a partner selling the same EAN
            page = product_page("891159", 23.5, 25.0)
            page["props"]["pageProps"]["productData"] |= {"productEan": "111", "sellerInfo": {"sellerName": "Loja Parceira"}}
            return Resp(text=next_page(page))
        if "948117.html" in url:  # a partner 2-pack: different EAN
            page = product_page("948117", 41.0, None)
            page["props"]["pageProps"]["productData"] |= {"productEan": "999", "sellerInfo": {"sellerName": "Outra Loja"}}
            return Resp(text=next_page(page))
        if "62451.html" in url:  # search said 17.99, the page says 21.99 (as seen in the real capture)
            ld = '<script type="application/ld+json">{"offers": {"price": "21.99"}}</script>'
            return Resp(text=next_page(product_page("62451", 21.99, 23.69), ld))
        # VTEX
        if "catalog_system" in url and "regionId=" in url:
            return Resp(payload=[{"items": [{"itemId": "55", "sellers": [
                {"sellerId": "loja-b", "commertialOffer": {"Price": 10.9, "AvailableQuantity": 3}}]}]}])
        if "catalog_system" in url:
            return Resp(payload=[{"items": [{"itemId": "55", "sellers": [{"sellerId": "1", "sellerDefault": True,
                     "commertialOffer": {"Price": 10.19, "ListPrice": 11.59, "AvailableQuantity": 100, "IsAvailable": True}}]}]}])
        if "/regions" in url:
            return Resp(payload=[
                {"id": "rid-1", "sellers": [{"id": "loja-a"}, {"id": "loja-b"}]},
                {"id": "rid-2", "sellers": [{"id": "loja-b"}, {"id": "loja-c"}]},
            ])
        raise AssertionError(url)

    def post(self, url, json=None, **kw):
        self.posts.append(json)
        items = json["items"]
        if "postalCode" not in json:
            return Resp(payload={"items": [{"availability": "available", "sellingPrice": 1019, "price": 1019, "seller": "1"}], "messages": []})
        if len(items) == 1 and items[0]["seller"] == "1":
            return Resp(payload={"items": [{"availability": "cannotBeDelivered", "sellingPrice": 1019, "price": 1019, "seller": "1"}],
                                 "messages": [{"text": "não pode ser entregue"}]})
        return Resp(payload={"items": [
            {"seller": it["seller"], "availability": "available" if it["seller"] == "loja-b" else "withoutStock",
             "sellingPrice": 1090 if it["seller"] == "loja-b" else None}
            for it in items
        ]})


def write_inputs(tmp_path):
    (tmp_path / "p.csv").write_text(
        "ean,desc_sku\n111,CREME X 60g SINGLE\n222,ESCOVA Y SINGLE\n333,GEL Y 50g SINGLE\n", encoding="utf-8"
    )
    (tmp_path / "s.csv").write_text(
        "store_id,type,domain,enabled\nDrogasil,RD_NEXTJS,https://rd.test,true\nVen,B2C_REST,https://ven.test,true\n",
        encoding="utf-8",
    )


def run(tmp_path, *extra, session=None):
    write_inputs(tmp_path)
    out = tmp_path / "diag"
    session = session or FakeSession()
    code = diagnose.main(
        ["--products-file", str(tmp_path / "p.csv"), "--stores-file", str(tmp_path / "s.csv"),
         "--results", str(tmp_path / "none.csv"), "--rd-eans", "111,222,333", "--vtex-stores", "Ven",
         "--vtex-eans", "7509546651040", "--ceps", "RJ=20010-000", "--delay", "0", "--out", str(out), *extra],
        session_factory=lambda store: session,
    )
    folder = out / next(p for p in os.listdir(out) if not p.endswith(".zip"))
    return code, out, folder, (folder / "summary.md").read_text(encoding="utf-8")


def test_rd_sponsored_product_page_is_fetched_and_price_fields_listed(tmp_path):
    code, _, folder, summary = run(tmp_path, "--skip-vtex")
    assert code == 0
    assert "products array at `$.props.pageProps.pageProps.results.products` with 3 item(s)" in summary
    assert '"isSponsored": true' in summary  # the flag that explains the missing price
    assert "product page (picked, no price, sku 1329580)" in summary
    assert "$.props.pageProps.productData.price_aux.value_to=19.9" in summary
    assert "ld+json prices ['19.90']" in summary
    assert (folder / "rd_drogasil_111_page_1329580.json").exists()


def test_rd_fetches_one_priced_control_page_to_confirm_the_field(tmp_path):
    _, _, folder, summary = run(tmp_path, "--skip-vtex")
    assert "product page (control, search price 17.99, sku 62451)" in summary
    assert "$.props.pageProps.productData.price_aux.value_to=21.99" in summary
    assert "**no __NEXT_DATA__**" in summary  # the 403 block page for EAN 222


def test_vtex_runs_the_three_regional_experiments(tmp_path):
    session = FakeSession()
    _, _, _, summary = run(tmp_path, "--skip-rd", session=session)
    assert "RJ 20010-000: regions HTTP 200, 2 region(s), 3 distinct seller(s); main seller in list: False" in summary
    assert 'E1 main seller 1, sc=1: HTTP 200' in summary and '"availability": "cannotBeDelivered"' in summary
    assert 'E2 3 regional sellers, sc=1' in summary and '"by_availability": {"withoutStock": 2, "available": 1}' in summary
    assert '"available_sellers": [{"seller": "loja-b", "sellingPrice": 1090}]' in summary
    assert "E3 catalog with regionId: HTTP 200, 1 seller offer(s)" in summary and '"sellerId": "loja-b"' in summary
    batch = next(body for body in session.posts if len(body["items"]) > 1)
    assert [it["seller"] for it in batch["items"]] == ["loja-a", "loja-b", "loja-c"]  # every region entry, deduplicated


def test_vtex_batch_is_capped_by_max_sellers(tmp_path):
    _, _, _, summary = run(tmp_path, "--skip-rd", "--max-sellers", "2")
    assert "E2 2 regional sellers (first 2 of 3), sc=1" in summary


def test_cookies_are_never_saved_and_archive_is_written(tmp_path):
    _, out, folder, _ = run(tmp_path)
    for name in os.listdir(folder):
        assert "SECRET" not in (folder / name).read_text(encoding="utf-8")
    archive = next(out / p for p in os.listdir(out) if p.endswith(".zip"))
    with zipfile.ZipFile(archive) as z:
        assert "summary.md" in z.namelist() and "index.json" in z.namelist()


def test_price_paths_include_values_nested_under_price_keys():
    state = {"a": {"priceInfo": {"final": 1}, "salePrice": "12,90", "list": [{"bestPrice": 9.9}], "name": "x"}}
    assert diagnose.price_paths(state) == [
        ("$.a.priceInfo.final", 1), ("$.a.salePrice", "12,90"), ("$.a.list[0].bestPrice", 9.9)
    ]


def test_pick_rd_eans_prefers_no_price_rows_from_results(tmp_path):
    results = tmp_path / "results.csv"
    results.write_text(
        "ean,store,status,message\n"
        "1,Drogasil,Not Found,NO_PRICE: x\n2,Drogasil,Success,\n1,Droga Raia,Not Found,NO_PRICE: x\n3,Drogal,Not Found,NO_PRICE: x\n",
        encoding="utf-8",
    )
    eans, source = diagnose.pick_rd_eans(str(results), {"Drogasil", "Droga Raia"}, [])
    assert eans == ["1", "2"] and "results.csv" in source


def test_pick_rd_eans_falls_back_to_multipacks_and_singles(tmp_path):
    products = [{"ean": "a", "desc_sku": "X BIPACK"}, {"ean": "b", "desc_sku": "Y SINGLE"}]
    eans, source = diagnose.pick_rd_eans(str(tmp_path / "missing.csv"), {"Drogasil"}, products)
    assert eans == ["a", "b"] and "products.csv" in source


def test_rd_pages_all_builds_a_search_vs_page_price_table(tmp_path):
    _, _, _, summary = run(tmp_path, "--skip-vtex", "--rd-pages", "all")
    table = summary[summary.index("## RD price comparison"):]
    assert "| Drogasil | 333 | 62451 |  | 17.99 | 21.99 | 23.69 | 21.99 |  | -18.2% |" in table
    assert "| Drogasil | 111 | 1329580 | yes |  | 19.9 | 24.9 | 19.90 | 14.92 (PBM) | no search price |" in table
    assert "Tally: search lower: 1, no search price: 1" in table


def test_default_mode_compares_the_sponsored_and_control_pages(tmp_path):
    _, _, _, summary = run(tmp_path, "--skip-vtex")
    # sponsored + one control are still compared, so the table appears with both rows
    assert "## RD price comparison" in summary and summary.count("| Drogasil |") == 2


def test_rd_eans_all_uses_every_product(tmp_path):
    _, _, _, summary = run(tmp_path, "--skip-vtex", "--rd-eans", "all")
    assert "EANs from products.csv (all): 111, 222, 333" in summary


def test_rd_page_prices_reads_the_main_live_best_and_ld_json_prices():
    html = '<script type="application/ld+json">{"offers":{"price":"14.92"}}</script>'
    prices = diagnose.rd_page_prices(product_page("1", 14.92, 19.89, best=(14.92, "PBM")), html)
    assert prices == {"value_to": 14.92, "value_from": 19.89, "live": 14.92, "best": 14.92, "best_type": "PBM", "ld_json": "14.92"}


def test_rd_partners_are_fetched_and_checked_against_the_searched_ean(tmp_path):
    _, _, folder, summary = run(tmp_path, "--skip-vtex", "--rd-partners", "5")
    table = summary[summary.index("## RD partner listings"):]
    assert "| Drogasil | 111 | 891159 | Creme X 60g | 23.5 | 111 | yes | 23.5 | 25.0 | `$.sellerInfo.sellerName=Loja Parceira` | HTTP 200 |" in table
    assert "| Drogasil | 111 | 948117 | Creme X 60g 2 unidades | 41.0 | 999 | NO | 41.0 |" in table
    assert "Partner pages with the searched EAN: 1 of 2" in table
    assert "- partner listings: 2 (pages fetched: 2)" in summary
    assert (folder / "rd_drogasil_111_partner_891159.json").exists()


def test_partner_limit_and_default_off(tmp_path):
    _, _, _, summary = run(tmp_path, "--skip-vtex")
    assert "## RD partner listings" not in summary
    (tmp_path / "b").mkdir()
    _, _, _, summary = run(tmp_path / "b", "--skip-vtex", "--rd-partners", "1")
    assert "- partner listings: 2 (pages fetched: 1)" in summary

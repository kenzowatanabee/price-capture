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


def next_page(products):
    state = {"props": {"pageProps": {"search": {"products": products}}}}
    return '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(state) + "</script>"


class FakeSession:
    def get(self, url, **kw):
        if "/search?w=111" in url:  # RD page with a product but no priceService
            return Resp(text=next_page([{"sku": "9", "name": "Creme X 90g", "is1P": True, "priceFrom": 12.5,
                                         "stockQuantity": 0, "nested": {"listPrice": 15.0}}]))
        if "/search?w=222" in url:  # RD block page
            return Resp(status=403, text="<html>Access denied</html>", headers={"content-type": "text/html"})
        if "catalog_system" in url:
            return Resp(payload=[{"items": [{"itemId": "55", "sellers": [{"sellerId": "1", "sellerDefault": True,
                     "commertialOffer": {"Price": 10.19, "ListPrice": 11.59, "AvailableQuantity": 10000, "IsAvailable": True}}]}]}])
        if "/regions" in url:
            return Resp(payload=[{"id": "v2.x", "sellers": [{"id": "loja-rj"}]}])
        raise AssertionError(url)

    def post(self, url, json=None, **kw):
        if "sc=2" in url:
            return Resp(status=200, text="<html>error page</html>", headers={"content-type": "text/html"})
        return Resp(payload={"items": [{"availability": "withoutStock", "price": 1019, "sellingPrice": 1019, "seller": json["items"][0]["seller"]}],
                             "messages": [{"text": "Produto sem estoque"}]})


def write_inputs(tmp_path):
    (tmp_path / "p.csv").write_text("ean,desc_sku\n111,CREME X 90g BIPACK\n222,ESCOVA Y SINGLE\n333,OTHER SINGLE\n", encoding="utf-8")
    (tmp_path / "s.csv").write_text(
        "store_id,type,domain,enabled\nDrogasil,RD_NEXTJS,https://rd.test,true\nVen,B2C_REST,https://ven.test,true\n",
        encoding="utf-8",
    )


def run(tmp_path, *extra):
    write_inputs(tmp_path)
    out = tmp_path / "diag"
    code = diagnose.main(
        ["--products-file", str(tmp_path / "p.csv"), "--stores-file", str(tmp_path / "s.csv"), "--results", str(tmp_path / "none.csv"),
         "--rd-eans", "111,222", "--vtex-stores", "Ven", "--vtex-eans", "7509546651040", "--ceps", "RJ=20010-000",
         "--sc", "1,2", "--delay", "0", "--out", str(out), *extra],
        session_factory=lambda store: FakeSession(),
    )
    folder = out / next(p for p in os.listdir(out) if not p.endswith(".zip"))
    return code, out, folder, (folder / "summary.md").read_text(encoding="utf-8")


def test_rd_summary_lists_price_and_stock_fields_of_the_picked_product(tmp_path):
    code, _, folder, summary = run(tmp_path, "--skip-vtex")
    assert code == 0
    assert "products array at `$.props.pageProps.search.products` with 1 item(s)" in summary
    assert "<- parser picks this" in summary
    assert '"priceFrom": 12.5' in summary and '"stockQuantity": 0' in summary and '"nested.listPrice": 15.0' in summary
    assert "**no __NEXT_DATA__**" in summary  # the block page for EAN 222
    assert (folder / "rd_drogasil_111.json").exists() and (folder / "rd_drogasil_222.html").exists()


def test_vtex_summary_compares_catalog_and_simulation_per_cep_and_sales_channel(tmp_path):
    _, _, folder, summary = run(tmp_path, "--skip-rd")
    assert '"AvailableQuantity": 10000' in summary and '"IsAvailable": true' in summary
    assert "RJ 20010-000: regional sellers ['loja-rj']" in summary
    assert "simulation RJ sc=1 seller=loja-rj" in summary and '"availability": "withoutStock"' in summary
    assert "simulation no CEP sc=2" in summary and "`\"not JSON\"`" in summary
    assert (folder / "vtex_ven_7509546651040_sim_20010000_sc2.txt").read_text(encoding="utf-8").startswith("<html>")


def test_cookies_are_never_saved_and_archive_is_written(tmp_path):
    _, out, folder, _ = run(tmp_path)
    index_text = (folder / "index.json").read_text(encoding="utf-8")
    assert "SECRET" not in index_text
    for name in os.listdir(folder):
        assert "SECRET" not in (folder / name).read_text(encoding="utf-8")
    archive = next(out / p for p in os.listdir(out) if p.endswith(".zip"))
    with zipfile.ZipFile(archive) as z:
        assert "summary.md" in z.namelist() and "index.json" in z.namelist()


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

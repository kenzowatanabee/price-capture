import csv

import region_check


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def sim_item(availability, cents=1019, seller="1"):
    return {"items": [{"id": "55", "seller": seller, "availability": availability,
                       "sellingPrice": cents, "price": cents, "listPrice": 1159}], "messages": []}


class FakeStore:
    """
    A VTEX store as seen in the real diagnostics: the main seller "1" sells online; per CEP the
    simulation says available, cannotBeDelivered, or returns no item at all.
    """

    def __init__(self, by_cep=None, fail_simulation=False):
        self.by_cep = by_cep or {
            None: sim_item("available"),
            "01310100": sim_item("available"),
            "30130010": {"items": [{"id": "55", "seller": "1", "availability": "cannotBeDelivered", "sellingPrice": 1019}],
                         "messages": [{"text": "O item não pode ser entregue para as coordenadas informadas"}]},
            "50030230": {"items": [], "messages": [{"text": "Item não encontrado ou indisponível"}]},
        }
        self.fail_simulation = fail_simulation
        self.gets = []
        self.posts = []

    def get(self, url, **kwargs):
        self.gets.append(url)
        if "catalog_system" in url:
            if "alternateIds_Ean:000" in url:
                return FakeResponse([])
            return FakeResponse([{"productName": "Creme", "items": [{"itemId": "55", "sellers": [
                {"sellerId": "1", "sellerDefault": True, "commertialOffer": {"Price": 10.19, "AvailableQuantity": 1}}]}]}])
        raise AssertionError(f"unexpected GET {url}")  # no regions lookup any more

    def post(self, url, json=None, **kwargs):
        self.posts.append(json)
        if self.fail_simulation:
            return FakeResponse({}, status_code=500)
        return FakeResponse(self.by_cep[json.get("postalCode")])


STORE = {"name": "Loja", "type": "B2C_REST", "domain": "https://loja.test", "verify_ssl": True}
PRODUCT = {"ean": "7891024132005", "desc_sku": "CREME DENTAL X 90g SINGLE"}
CEPS = {"SP": "01310-100", "MG": "30130-010", "PE": "50030-230"}


def test_every_cep_is_simulated_with_the_main_seller_only():
    store = FakeStore()
    rows = region_check.check_store(STORE, [PRODUCT], CEPS, "1", 0, store)
    assert [r["region"] for r in rows] == ["catalog (what the scraper records)", "no CEP", "SP", "MG", "PE"]
    assert {it["seller"] for body in store.posts for it in body["items"]} == {"1"}
    assert [body.get("postalCode") for body in store.posts] == [None, "01310100", "30130010", "50030230"]
    assert not any("/regions" in u for u in store.gets)


def test_rows_carry_availability_price_and_the_stores_message():
    rows = {r["region"]: r for r in region_check.check_store(STORE, [PRODUCT], CEPS, "1", 0, FakeStore())}
    assert rows["catalog (what the scraper records)"]["price"] == 10.19
    assert (rows["SP"]["availability"], rows["SP"]["price"], rows["SP"]["list_price"]) == ("available", 10.19, 11.59)
    assert rows["MG"]["availability"] == "cannotBeDelivered" and "não pode ser entregue" in rows["MG"]["note"]
    assert rows["PE"]["availability"] == "not_returned" and rows["PE"]["seller"] == "1"  # seller kept when no item


def test_coverage_counts_available_skus_per_region():
    rows = region_check.check_store(STORE, [PRODUCT, PRODUCT | {"ean": "7891024035504"}], CEPS, "1", 0, FakeStore())
    assert region_check.coverage(rows) == {"Loja": {"SP": (2, 2), "MG": (0, 2), "PE": (0, 2)}}
    assert region_check.coverage_lines(region_check.coverage(rows), list(CEPS)) == [
        "store  SP     MG     PE     ",
        "Loja   2/2    0/2    0/2    ",
    ]


def test_price_variation_only_counts_available_offers():
    by_cep = {None: sim_item("available"), "01310100": sim_item("available", 1019),
              "30130010": sim_item("available", 1199), "50030230": sim_item("withoutStock", 999)}
    rows = region_check.check_store(STORE, [PRODUCT], CEPS, "1", 0, FakeStore(by_cep))
    assert region_check.price_variation(rows) == {"Loja": (1, 1)}  # 10.19 vs 11.99; the 9.99 is out of stock
    flat = {k: sim_item("available") for k in (None, "01310100", "30130010", "50030230")}
    rows = region_check.check_store(STORE, [PRODUCT], CEPS, "1", 0, FakeStore(flat))
    assert region_check.price_variation(rows) == {"Loja": (0, 1)}


def test_unlisted_sku_and_failed_simulations_are_reported_not_raised():
    rows = region_check.check_store(STORE, [{"ean": "000", "desc_sku": "X SINGLE"}], CEPS, "1", 0, FakeStore())
    assert rows == [{"store": "Loja", "ean": "000", "desc_sku": "X SINGLE", "region": "catalog", "note": "not listed"}]
    rows = region_check.check_store(STORE, [PRODUCT], CEPS, "1", 0, FakeStore(fail_simulation=True))
    sims = [r for r in rows if r["region"] != "catalog (what the scraper records)"]
    assert all(r["note"].startswith("error: simulation HTTP 500") for r in sims)
    assert region_check.coverage(rows) == {}  # errors are not counted as "checked"


def test_parse_simulation_converts_cents_and_reports_missing_items():
    ok = region_check.parse_simulation(sim_item("available", 1290))
    assert (ok["price"], ok["list_price"], ok["availability"]) == (12.9, 11.59, "available")
    empty = region_check.parse_simulation({"items": [], "messages": [{"text": "Item indisponível"}]})
    assert empty["price"] is None and empty["availability"] == "not_returned" and "indispon" in empty["note"]


def test_parse_ceps():
    assert region_check.parse_ceps("SP=01310-100, PE=50030-230") == {"SP": "01310-100", "PE": "50030-230"}
    assert region_check.parse_ceps("01310-100") == {"01310-100": "01310-100"}


def test_main_end_to_end_prints_prices_and_coverage(tmp_path, capsys):
    (tmp_path / "p.csv").write_text(f"ean,desc_sku\n{PRODUCT['ean']},{PRODUCT['desc_sku']}\n", encoding="utf-8")
    (tmp_path / "s.csv").write_text(
        "store_id,type,domain,enabled\nLoja,B2C_REST,https://loja.test,true\nRD,RD_NEXTJS,https://rd.test,true\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.csv"
    code = region_check.main(
        ["--products-file", str(tmp_path / "p.csv"), "--stores-file", str(tmp_path / "s.csv"),
         "--ceps", "SP=01310-100,MG=30130-010,PE=50030-230", "--delay", "0", "--out", str(out)],
        session_factory=lambda store: FakeStore(),
    )
    printed = capsys.readouterr().out
    assert code == 0
    assert "RD: RD_NEXTJS is not supported" in printed
    assert "Loja                  : 0/1 SKUs vary  -> same price everywhere checked" in printed
    assert "Delivery coverage" in printed and "Loja   1/1    0/1    0/1" in printed
    with open(out, encoding="utf-8") as f:
        assert len(list(csv.DictReader(f))) == 5  # catalog + no CEP + 3 CEPs

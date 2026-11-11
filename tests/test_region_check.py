import csv

import region_check


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class FakeStoreSession:
    """A VTEX store that prices by region: SP has its own seller and price, PE falls back to the default."""

    def __init__(self, sim_prices=None, fail_simulation=False):
        self.sim_prices = sim_prices or {None: 1000, "01310100": 1090, "50030230": 1290}
        self.fail_simulation = fail_simulation
        self.posts = []

    def get(self, url, **kwargs):
        if "catalog_system" in url:
            if "alternateIds_Ean:000" in url:
                return FakeResponse([])
            return FakeResponse([{
                "productName": "Creme",
                "items": [{"itemId": "123", "sellers": [
                    {"sellerId": "1", "sellerDefault": True, "commertialOffer": {"Price": 10.0}},
                ]}],
            }])
        if "/regions" in url:
            if "postalCode=01310100" in url:
                return FakeResponse([{"id": "v2.sp", "sellers": [{"id": "loja-sp"}]}])
            return FakeResponse([])
        raise AssertionError(url)

    def post(self, url, json=None, **kwargs):
        self.posts.append((url, json))
        if self.fail_simulation:
            return FakeResponse({}, status_code=500)
        cep = json.get("postalCode")
        return FakeResponse({"items": [{
            "id": "123", "seller": json["items"][0]["seller"],
            "sellingPrice": self.sim_prices[cep], "listPrice": 1590, "availability": "available",
        }]})


STORE = {"name": "Loja", "type": "B2C_REST", "domain": "https://loja.test", "verify_ssl": True}
PRODUCT = {"ean": "7891024132005", "desc_sku": "CREME DENTAL X 90g SINGLE"}
CEPS = {"SP": "01310-100", "PE": "50030-230"}


def test_parse_simulation_converts_cents_and_reports_missing_items():
    ok = region_check.parse_simulation({"items": [{"sellingPrice": 1290, "listPrice": 1590, "availability": "available", "seller": "1"}]})
    assert (ok["price"], ok["list_price"], ok["availability"]) == (12.9, 15.9, "available")
    empty = region_check.parse_simulation({"items": [], "messages": [{"text": "Item indisponível"}]})
    assert empty["price"] is None and empty["availability"] == "not_returned" and "indispon" in empty["note"]


def test_check_store_uses_the_regional_seller_and_records_each_cep():
    session = FakeStoreSession()
    rows = region_check.check_store(STORE, [PRODUCT], CEPS, "1", 0, session)
    by_region = {r["region"]: r for r in rows}
    assert by_region["catalog (what the scraper records)"]["price"] == 10.0
    assert by_region["no CEP"]["price"] == 10.0
    assert (by_region["SP"]["seller"], by_region["SP"]["price"]) == ("loja-sp", 10.9)
    assert (by_region["PE"]["seller"], by_region["PE"]["price"]) == ("1", 12.9)
    assert "no regional seller" in by_region["PE"]["note"]
    # the CEP is sent digits-only in the simulation body
    assert {body.get("postalCode") for _, body in session.posts} == {None, "01310100", "50030230"}


def test_summary_flags_regional_pricing_only_when_prices_differ():
    rows = region_check.check_store(STORE, [PRODUCT], CEPS, "1", 0, FakeStoreSession())
    assert region_check.summarize(rows) == {"Loja": (1, 1)}
    flat = FakeStoreSession(sim_prices={None: 1000, "01310100": 1000, "50030230": 1000})
    rows = region_check.check_store(STORE, [PRODUCT], CEPS, "1", 0, flat)
    assert region_check.summarize(rows) == {"Loja": (0, 1)}


def test_unlisted_sku_and_failed_simulation_are_reported_not_raised():
    rows = region_check.check_store(STORE, [{"ean": "000", "desc_sku": "X SINGLE"}], CEPS, "1", 0, FakeStoreSession())
    assert rows == [{"store": "Loja", "ean": "000", "desc_sku": "X SINGLE", "region": "catalog", "note": "not listed"}]
    rows = region_check.check_store(STORE, [PRODUCT], CEPS, "1", 0, FakeStoreSession(fail_simulation=True))
    assert all(r["note"].startswith("error: simulation HTTP 500") for r in rows if r["region"] != "catalog (what the scraper records)")


def test_parse_ceps():
    assert region_check.parse_ceps("SP=01310-100, PE=50030-230") == {"SP": "01310-100", "PE": "50030-230"}
    assert region_check.parse_ceps("01310-100") == {"01310-100": "01310-100"}


def test_main_end_to_end_skips_unsupported_stores_and_writes_csv(tmp_path, capsys):
    (tmp_path / "p.csv").write_text(f"ean,desc_sku\n{PRODUCT['ean']},{PRODUCT['desc_sku']}\n", encoding="utf-8")
    (tmp_path / "s.csv").write_text(
        "store_id,type,domain,enabled\nLoja,B2C_REST,https://loja.test,true\nRD,RD_NEXTJS,https://rd.test,true\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.csv"
    code = region_check.main(
        ["--products-file", str(tmp_path / "p.csv"), "--stores-file", str(tmp_path / "s.csv"),
         "--ceps", "SP=01310-100,PE=50030-230", "--delay", "0", "--out", str(out)],
        session_factory=lambda store: FakeStoreSession(),
    )
    printed = capsys.readouterr().out
    assert code == 0
    assert "RD: RD_NEXTJS is not supported" in printed
    assert "Loja" in printed and "REGIONAL pricing" in printed
    with open(out, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 4  # catalog + no CEP + 2 CEPs

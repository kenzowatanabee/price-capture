import pytest

from price_capture import domo_schema
from price_capture.schema import COLUMNS

OLD8 = COLUMNS[:8]


def test_append_keeps_existing_columns_and_adds_the_rest_at_the_end():
    current = [("EAN", "STRING"), ("Desc", "STRING"), ("Store", "STRING"), ("Status", "STRING"),
               ("Price", "STRING"), ("Found", "STRING"), ("Fetched", "DATETIME"), ("Msg", "STRING")]
    desired, notes = domo_schema.plan_schema(current)
    assert desired[:8] == current  # untouched: names and types
    assert desired[8:] == COLUMNS[8:]
    assert len(desired) == len(COLUMNS)
    assert any("'Desc'" in n for n in notes)  # name differs from the scraper's: flagged
    assert any("Price" in n and "DECIMAL" in n for n in notes)  # type differs: flagged


def test_already_up_to_date_is_a_no_op():
    desired, notes = domo_schema.plan_schema(COLUMNS)
    assert desired == COLUMNS and notes == []


def test_empty_dataset_gets_the_full_schema():
    assert domo_schema.plan_schema([])[0] == COLUMNS


def test_rename_existing_replaces_everything_with_the_canonical_schema():
    current = [("a", "STRING")] * 8
    desired, _ = domo_schema.plan_schema(current, rename_existing=True)
    assert desired == COLUMNS


def test_dataset_with_extra_columns_is_refused():
    with pytest.raises(domo_schema.SchemaError):
        domo_schema.plan_schema([("c", "STRING")] * (len(COLUMNS) + 1))


def test_schema_mismatch_message_points_at_the_script():
    assert domo_schema.schema_mismatch(len(COLUMNS)) is None
    msg = domo_schema.schema_mismatch(8)
    assert "8 columns" in msg and f"{len(COLUMNS)} columns" in msg and "update_domo_schema.py" in msg


def test_columns_from_dataset():
    ds = {"schema": {"columns": [{"type": "STRING", "name": "a"}, {"type": "DOUBLE", "name": "b"}]}}
    assert domo_schema.columns_from_dataset(ds) == [("a", "STRING"), ("b", "DOUBLE")]
    assert domo_schema.columns_from_dataset({}) == []


def test_read_credentials_accepts_both_naming_styles():
    assert domo_schema.read_credentials({"client_id": "a", "secret_id": "b", "dataset_id": "c"}) == ("a", "b", "c")
    assert domo_schema.read_credentials({"DOMO_CLIENT_ID": "a", "DOMO_CLIENT_SECRET": "b", "DOMO_DATASET_ID": "c"}) == ("a", "b", "c")
    assert domo_schema.read_credentials({}) == (None, None, None)


def test_canonical_types_match_the_production_dataset():
    # --rename-existing must not retype production columns: price is DECIMAL and the timestamp DATETIME in Domo.
    types = dict(COLUMNS)
    assert types["price_brl"] == "DECIMAL" and types["fetched_at"] == "DATETIME"

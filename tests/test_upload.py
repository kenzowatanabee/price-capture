import pytest

import upload_to_domo
from price_capture.schema import COLUMNS, CSV_COLUMNS


class FakeDatasets:
    def __init__(self, n_columns):
        self.n_columns = n_columns
        self.imports = []

    def get(self, dataset_id):
        return {"schema": {"columns": [{"name": f"c{i}", "type": "STRING"} for i in range(self.n_columns)]}}

    def data_import(self, dataset_id, data, update_method):
        self.imports.append((dataset_id, data, update_method))


def run_upload(tmp_path, monkeypatch, n_domo_columns, header=None):
    header = header or CSV_COLUMNS
    row = ["x"] * len(header)
    (tmp_path / "results.csv").write_text(",".join(header) + "\n" + ",".join(row) + "\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DOMO_CLIENT_ID", "id")
    monkeypatch.setenv("DOMO_CLIENT_SECRET", "secret")
    monkeypatch.setenv("DOMO_DATASET_ID", "ds-1")
    datasets = FakeDatasets(n_domo_columns)

    class FakeDomo:
        def __init__(self, **kwargs):
            self.datasets = datasets

    monkeypatch.setattr(upload_to_domo, "Domo", FakeDomo)
    return datasets


def test_uploads_with_append_when_schemas_match(tmp_path, monkeypatch):
    datasets = run_upload(tmp_path, monkeypatch, len(COLUMNS))
    upload_to_domo.main()
    assert len(datasets.imports) == 1
    dataset_id, data, method = datasets.imports[0]
    assert (dataset_id, method) == ("ds-1", "APPEND")
    assert not data.startswith("ean,")  # positional import: no header row


def test_refuses_to_upload_when_the_domo_dataset_has_a_different_column_count(tmp_path, monkeypatch, capsys):
    datasets = run_upload(tmp_path, monkeypatch, 8)
    with pytest.raises(SystemExit) as exc:
        upload_to_domo.main()
    assert exc.value.code == 1
    assert datasets.imports == []
    assert "update_domo_schema.py" in capsys.readouterr().out


def test_refuses_to_upload_when_csv_columns_are_out_of_order(tmp_path, monkeypatch):
    swapped = [CSV_COLUMNS[1], CSV_COLUMNS[0]] + CSV_COLUMNS[2:]
    datasets = run_upload(tmp_path, monkeypatch, len(COLUMNS), header=swapped)
    with pytest.raises(SystemExit) as exc:
        upload_to_domo.main()
    assert exc.value.code == 1 and datasets.imports == []

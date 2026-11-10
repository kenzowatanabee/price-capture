import json

import pytest

import update_domo_schema
from price_capture.schema import COLUMNS

OLD8 = COLUMNS[:8]


class FakeDatasets:
    def __init__(self, columns, accept=True, fail_update=False):
        self.columns = list(columns)
        self.accept = accept
        self.fail_update = fail_update
        self.updates = []

    def get(self, dataset_id):
        return {
            "name": "Price capture",
            "description": "daily prices",
            "rows": 1234,
            "schema": {"columns": [{"type": t, "name": n} for n, t in self.columns]},
        }

    def update(self, dataset_id, request):
        if self.fail_update:
            raise Exception("403 forbidden")
        self.updates.append(request)
        if self.accept:
            self.columns = [(c["name"], c["type"]) for c in request["schema"]["columns"]]


class FakeClient:
    def __init__(self, datasets):
        self.datasets = datasets


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    for var in ("client_id", "secret_id", "dataset_id", "DOMO_CLIENT_ID", "DOMO_CLIENT_SECRET", "DOMO_DATASET_ID"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    path = tmp_path / ".env"
    path.write_text("DOMO_CLIENT_ID=abc\nDOMO_CLIENT_SECRET=s3cret\nDOMO_DATASET_ID=ds-9\n", encoding="utf-8")
    return str(path)


def test_dry_run_shows_the_plan_and_changes_nothing(env_file, capsys):
    datasets = FakeDatasets(OLD8)
    code = update_domo_schema.main(["--env-file", env_file], client=FakeClient(datasets))
    out = capsys.readouterr().out
    assert code == 0 and datasets.updates == []
    assert "Dry run" in out and "<- new" in out and "run_id" in out
    assert "s3cret" not in out  # never print the secret


def test_apply_backs_up_updates_and_verifies(env_file, tmp_path):
    datasets = FakeDatasets(OLD8)
    code = update_domo_schema.main(["--apply", "--env-file", env_file], client=FakeClient(datasets))
    assert code == 0
    assert datasets.columns == COLUMNS

    request = datasets.updates[0]
    assert request["name"] == "Price capture" and request["description"] == "daily prices"

    backups = list(tmp_path.glob("domo_schema_backup_ds-9_*.json"))
    assert len(backups) == 1
    saved = json.loads(backups[0].read_text(encoding="utf-8"))
    assert len(saved["schema"]["columns"]) == 8  # the schema as it was before the change


def test_default_keeps_existing_names_and_types(env_file):
    current = [(f"Col{i}", "STRING") for i in range(8)]
    datasets = FakeDatasets(current)
    update_domo_schema.main(["--apply", "--env-file", env_file], client=FakeClient(datasets))
    assert datasets.columns[:8] == current and datasets.columns[8:] == COLUMNS[8:]


def test_rename_existing_replaces_names_and_types(env_file):
    datasets = FakeDatasets([(f"Col{i}", "STRING") for i in range(8)])
    update_domo_schema.main(["--apply", "--rename-existing", "--env-file", env_file], client=FakeClient(datasets))
    assert datasets.columns == COLUMNS


def test_already_up_to_date_does_not_update(env_file, capsys):
    datasets = FakeDatasets(COLUMNS)
    assert update_domo_schema.main(["--apply", "--env-file", env_file], client=FakeClient(datasets)) == 0
    assert datasets.updates == [] and "up to date" in capsys.readouterr().out


def test_dataset_with_extra_columns_is_refused(env_file):
    datasets = FakeDatasets(COLUMNS + [("extra", "STRING")])
    assert update_domo_schema.main(["--apply", "--env-file", env_file], client=FakeClient(datasets)) == 1
    assert datasets.updates == []


def test_update_failure_is_reported(env_file, capsys):
    datasets = FakeDatasets(OLD8, fail_update=True)
    assert update_domo_schema.main(["--apply", "--env-file", env_file], client=FakeClient(datasets)) == 1
    assert "403 forbidden" in capsys.readouterr().out


def test_unverified_update_is_a_failure(env_file):
    # Domo accepts the call but the schema read back is unchanged.
    datasets = FakeDatasets(OLD8, accept=False)
    assert update_domo_schema.main(["--apply", "--env-file", env_file], client=FakeClient(datasets)) == 1


def test_missing_credentials_names_the_variables(tmp_path, monkeypatch, capsys):
    for var in ("client_id", "secret_id", "dataset_id", "DOMO_CLIENT_ID", "DOMO_CLIENT_SECRET", "DOMO_DATASET_ID"):
        monkeypatch.delenv(var, raising=False)
    empty = tmp_path / "empty.env"
    empty.write_text("", encoding="utf-8")
    assert update_domo_schema.main(["--env-file", str(empty)], client=FakeClient(FakeDatasets(OLD8))) == 1
    out = capsys.readouterr().out
    assert "DOMO_CLIENT_ID" in out and "DOMO_DATASET_ID" in out

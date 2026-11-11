"""Helpers to compare and plan the Domo dataset schema against the CSV schema."""
import os

from price_capture.schema import COLUMNS


class SchemaError(Exception):
    """The Domo schema cannot be reconciled with the CSV schema automatically."""


def read_credentials(env=None):
    """(client_id, client_secret, dataset_id) from the environment; entries are None when missing."""
    env = os.environ if env is None else env
    return (
        env.get("client_id") or env.get("DOMO_CLIENT_ID"),
        env.get("secret_id") or env.get("DOMO_CLIENT_SECRET"),
        env.get("dataset_id") or env.get("DOMO_DATASET_ID"),
    )


def columns_from_dataset(dataset):
    """[(name, type), ...] from the dict returned by `client.datasets.get()`."""
    return [(c["name"], c["type"]) for c in (dataset.get("schema") or {}).get("columns", [])]


def schema_mismatch(domo_names, csv_names=None):
    """
    Message describing why a positional import would misalign, or None if the Domo dataset has the
    same columns in the same order as the CSV (names compared case-insensitively).
    """
    csv_names = [name for name, _ in COLUMNS] if csv_names is None else list(csv_names)
    domo_names = list(domo_names)
    if len(domo_names) != len(csv_names):
        return (
            f"The Domo dataset has {len(domo_names)} columns but the CSV has {len(csv_names)} columns. "
            "Run `python update_domo_schema.py` (dry run first, then --apply) to update the dataset schema."
        )
    differences = [
        f"column {i + 1} is '{domo}' in Domo but '{csv}' in the CSV"
        for i, (domo, csv) in enumerate(zip(domo_names, csv_names))
        if domo.lower() != csv.lower()
    ]
    if differences:
        return (
            "The Domo dataset columns do not match the CSV: " + "; ".join(differences[:5])
            + ". The import is positional, so rows would land in the wrong columns. Fix the dataset or schema.py first."
        )
    return None


def plan_schema(current, canonical=None, rename_existing=False):
    """
    Plans the new Domo schema. Returns (desired, notes).

    Default: keep every existing column exactly as it is (name and type, so existing data and
    dashboards are untouched) and append the missing canonical columns at the end.
    rename_existing=True: replace the whole schema with the canonical names and types.
    Raises SchemaError if the dataset has more columns than the canonical schema.
    """
    canonical = list(COLUMNS if canonical is None else canonical)
    current = [tuple(c) for c in current]

    if len(current) > len(canonical):
        raise SchemaError(
            f"The Domo dataset has {len(current)} columns, more than the {len(canonical)} the scraper "
            "writes. Refusing to drop columns automatically; fix the dataset in Domo first."
        )

    notes = []
    for i, (name, dtype) in enumerate(current):
        want_name, want_type = canonical[i]
        if name.lower() != want_name.lower():
            notes.append(
                f"column {i + 1}: Domo has '{name}' but the scraper writes '{want_name}' there. "
                "The import is positional, so check this column holds the right data."
            )
        if dtype != want_type:
            notes.append(f"column {i + 1} ('{name}'): type is {dtype} in Domo, scraper type is {want_type}.")

    if rename_existing:
        return canonical, notes
    return current + canonical[len(current):], notes

"""
Update the schema of the Domo dataset so it matches the columns the scraper writes.

Run it locally, with the Domo credentials in a .env file (see .env.example):

    python update_domo_schema.py                    # dry run: shows current vs proposed schema
    python update_domo_schema.py --apply            # back up the current schema, then update it
    python update_domo_schema.py --apply --rename-existing
                                                    # also replace existing column names/types
                                                    # with the scraper's (default keeps them as-is)

By default existing columns are left exactly as they are (name and type) and the missing ones are
appended at the end, because the import is positional and existing data/dashboards depend on them.
The script never deletes columns: a dataset with more columns than the scraper writes is refused.
"""
import argparse
import json
import sys
from datetime import datetime

from dotenv import load_dotenv

from price_capture.domo_schema import SchemaError, columns_from_dataset, plan_schema, read_credentials


def build_client(client_id, secret_id):
    from pydomo import Domo

    return Domo(client_id=client_id, client_secret=secret_id, api_host="api.domo.com")


def print_plan(current, desired, labels=("current", "proposed")):
    print(f"\n{'#':>3}  {labels[0]:<28} {labels[1]:<28}")
    for i in range(max(len(current), len(desired))):
        cur = current[i] if i < len(current) else None
        new = desired[i] if i < len(desired) else None
        marker = "  <- new" if cur is None else "" if cur == new else "  <- changes"
        cur_text = f"{cur[0]} ({cur[1]})" if cur else "-"
        new_text = f"{new[0]} ({new[1]})" if new else "-"
        print(f"{i + 1:>3}  {cur_text:<28} {new_text:<28}{marker}")


def main(argv=None, client=None):
    parser = argparse.ArgumentParser(description="Update the Domo dataset schema to match the scraper output.")
    parser.add_argument("--apply", action="store_true", help="write the change (default is a dry run)")
    parser.add_argument(
        "--rename-existing",
        action="store_true",
        help="replace existing column names/types with the scraper's instead of keeping them",
    )
    parser.add_argument("--env-file", default=None, help="path to the .env file (default: .env in the current directory)")
    args = parser.parse_args(argv)

    load_dotenv(args.env_file)
    client_id, secret_id, dataset_id = read_credentials()
    missing = [
        name
        for name, value in (("DOMO_CLIENT_ID", client_id), ("DOMO_CLIENT_SECRET", secret_id), ("DOMO_DATASET_ID", dataset_id))
        if not value
    ]
    if missing:
        print(f"❌ Missing in the environment / .env: {', '.join(missing)}")
        return 1

    try:
        client = client or build_client(client_id, secret_id)
        dataset = client.datasets.get(dataset_id)
    except Exception as e:
        print(f"❌ Could not read dataset {dataset_id}: {e}")
        return 1

    current = columns_from_dataset(dataset)
    print(f"Dataset: {dataset.get('name', '?')} ({dataset_id}), rows: {dataset.get('rows', '?')}")

    try:
        desired, notes = plan_schema(current, rename_existing=args.rename_existing)
    except SchemaError as e:
        print(f"❌ {e}")
        return 1

    print_plan(current, desired)
    for note in notes:
        print(f"⚠️  {note}")

    if desired == current:
        print("\n✅ The schema is already up to date. Nothing to do.")
        return 0

    if not args.apply:
        print("\nDry run only. Re-run with --apply to update the dataset schema.")
        return 0

    backup = f"domo_schema_backup_{dataset_id}_{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    with open(backup, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)
    print(f"\nSaved the current dataset definition to {backup}")

    try:
        from pydomo.datasets import Column, DataSetRequest, Schema

        request = DataSetRequest()
        request.name = dataset.get("name")
        if dataset.get("description"):
            request.description = dataset["description"]
        request.schema = Schema([Column(dtype, name) for name, dtype in desired])
        client.datasets.update(dataset_id, request)
    except Exception as e:
        print(f"❌ Schema update failed: {e}")
        return 1

    # Read it back instead of trusting the update call.
    after = columns_from_dataset(client.datasets.get(dataset_id))
    if after != desired:
        print("❌ The update was accepted but the dataset schema does not match what was requested:")
        print_plan(desired, after, labels=("requested", "actual"))
        return 1

    print(f"✅ Schema updated: {len(current)} -> {len(after)} columns.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

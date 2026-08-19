import os
import sys
import pandas as pd
from dotenv import load_dotenv
from pydomo import Domo

# Load local .env file if executing locally
load_dotenv()


def main():
    # Retrieve Domo credentials from environment variables
    client_id = os.getenv("client_id") or os.getenv("DOMO_CLIENT_ID")
    secret_id = os.getenv("secret_id") or os.getenv("DOMO_CLIENT_SECRET")
    dataset_id = os.getenv("dataset_id") or os.getenv("DOMO_DATASET_ID")

    csv_file = "results.csv"

    # Validate required credentials
    if not all([client_id, secret_id, dataset_id]):
        print(
            "❌ [ERROR] Missing Domo credentials or Dataset ID in environment variables."
        )
        print(
            "Ensure 'client_id', 'secret_id', and 'dataset_id' are provided."
        )
        sys.exit(1)

    # Validate target CSV exists
    if not os.path.exists(csv_file):
        print(
            f"❌ [ERROR] Target file '{csv_file}' not found. Cannot proceed with upload."
        )
        sys.exit(1)

    print(f"Connecting to Domo and importing '{csv_file}' into dataset {dataset_id}...")

    try:
        # Initialize Domo Client
        client = Domo(
            client_id=client_id, client_secret=secret_id, api_host="api.domo.com"
        )

        # Load scraped results
        df = pd.read_csv(csv_file)

        # Import payload into Domo (Replacing existing rows)
        client.datasets.data_import(
            dataset_id,
            df.to_csv(index=False, header=False),
            update_method="APPEND",
        )

        print(f"✅ [OK] Data uploaded successfully to DOMO dataset: {dataset_id}")

    except Exception as e:
        print(f"❌ [ERROR] Upload failed for dataset {dataset_id}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
"""HTTP fetching with browser impersonation, split timeouts and retry/backoff."""
import os
import time

from curl_cffi import requests

# TLS verification is on by default; set VERIFY_SSL=false only to work around a store with a broken certificate
VERIFY_SSL = os.environ.get("VERIFY_SSL", "true").strip().lower() not in ("0", "false", "no")

IMPERSONATE = "chrome120"
TIMEOUT = (10, 20)  # (connect_timeout, read_timeout)

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


def fetch(url, max_retries=2):
    """
    GETs a URL, retrying timeouts (curl 28) and unexpected exceptions with
    linear backoff. Returns (response, None) on success or (None, error_result).
    """
    for attempt in range(max_retries + 1):
        try:
            response = requests.get(
                url,
                headers=HEADERS,
                impersonate=IMPERSONATE,
                timeout=TIMEOUT,
                allow_redirects=True,
                verify=VERIFY_SSL,
            )
            return response, None
        except requests.errors.RequestsError as e:
            # curl error 28 = operation timed out
            if e.code == 28 and attempt < max_retries:
                time.sleep(2 * (attempt + 1))  # Waits 2s, then 4s
                continue
            if e.code == 28:
                return None, {"status": "Error", "message": "Connection Timed Out (curl 28)"}
            return None, {"status": "Error", "message": f"Request failed (curl {int(e.code)}): {str(e)[:60]}"}
        except Exception as e:
            if attempt < max_retries:
                time.sleep(2 * (attempt + 1))
                continue
            return None, {"status": "Error", "message": f"Exception: {str(e)[:40]}"}

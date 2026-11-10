"""HTTP fetching with browser impersonation, split timeouts and retry/backoff."""
import os
import random
import threading
import time
from urllib.parse import urlsplit

from curl_cffi import requests

from price_capture.results import error

# TLS verification is on by default; set VERIFY_SSL=false only to work around a store with a broken certificate
VERIFY_SSL = os.environ.get("VERIFY_SSL", "true").strip().lower() not in ("0", "false", "no")

IMPERSONATE = "chrome120"
TIMEOUT = (10, 20)  # (connect_timeout, read_timeout)

# Politeness towards each store: at most N requests in flight per domain, and a small
# random delay (up to REQUEST_JITTER seconds) before each one.
RETRY_STATUSES = {429, 503}  # rate limited / temporarily unavailable
MAX_RETRY_AFTER = 30  # never wait longer than this (seconds) on a Retry-After header

PER_STORE_CONCURRENCY = int(os.environ.get("PER_STORE_CONCURRENCY", 2))
REQUEST_JITTER = float(os.environ.get("REQUEST_JITTER", 0.3))

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


_local = threading.local()
_domain_limits = {}
_domain_limits_lock = threading.Lock()


def _limit_for(domain):
    """The semaphore capping concurrent requests to one domain (shared by all threads)."""
    with _domain_limits_lock:
        if domain not in _domain_limits:
            _domain_limits[domain] = threading.BoundedSemaphore(PER_STORE_CONCURRENCY)
        return _domain_limits[domain]


def _session_for(domain):
    """
    One session per (thread, domain): connections and cookies are reused across the many
    requests a store receives, and sessions are never shared between threads.
    """
    sessions = getattr(_local, "sessions", None)
    if sessions is None:
        sessions = _local.sessions = {}
    session = sessions.get(domain)
    if session is None:
        session = sessions[domain] = requests.Session(
            impersonate=IMPERSONATE,
            headers=HEADERS,
            timeout=TIMEOUT,
            allow_redirects=True,
            verify=VERIFY_SSL,
        )
    return session


def fetch(url, max_retries=2):
    """
    GETs a URL, retrying timeouts (curl 28), unexpected exceptions and 429/503
    responses (honouring Retry-After) with backoff. Returns (response, None) on success or (None, error_result).
    """
    domain = urlsplit(url).netloc
    # The slot is held across retries and their backoff, so a struggling store gets less traffic.
    with _limit_for(domain):
        if REQUEST_JITTER > 0:
            time.sleep(random.uniform(0, REQUEST_JITTER))
        return _fetch_with_retries(_session_for(domain), url, max_retries)


def _retry_delay(response, attempt):
    """Seconds to wait before retrying a 429/503: the Retry-After header (in seconds) if sane, else linear backoff."""
    headers = getattr(response, "headers", None) or {}
    try:
        delay = float(headers.get("Retry-After"))
    except (TypeError, ValueError):
        return 2 * (attempt + 1)
    return min(max(delay, 0), MAX_RETRY_AFTER)


def _fetch_with_retries(session, url, max_retries):
    for attempt in range(max_retries + 1):
        try:
            response = session.get(url)
            if response.status_code in RETRY_STATUSES and attempt < max_retries:
                time.sleep(_retry_delay(response, attempt))
                continue
            return response, None
        except requests.errors.RequestsError as e:
            # curl error 28 = operation timed out
            if e.code == 28 and attempt < max_retries:
                time.sleep(2 * (attempt + 1))  # Waits 2s, then 4s
                continue
            if e.code == 28:
                return None, error("TIMEOUT", "connection timed out (curl 28)")
            return None, error("NETWORK", f"curl {int(e.code)}: {str(e)[:60]}")
        except Exception as e:
            if attempt < max_retries:
                time.sleep(2 * (attempt + 1))
                continue
            return None, error("NETWORK", f"exception: {str(e)[:40]}")

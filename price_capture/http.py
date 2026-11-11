"""HTTP fetching with browser impersonation, split timeouts and retry/backoff."""
import os
import random
import re
import threading
import time
from urllib.parse import urlsplit

from curl_cffi import requests

from price_capture.results import error

# TLS verification is on by default; set VERIFY_SSL=false only to work around a store with a broken certificate
VERIFY_SSL = os.environ.get("VERIFY_SSL", "true").strip().lower() not in ("0", "false", "no")

# Browser fingerprint (TLS + HTTP/2 + default headers) sent by curl_cffi. The matching User-Agent and
# sec-ch-ua headers come from the impersonation itself, so do not hand-write them here: a header set that
# disagrees with the fingerprint is easier to flag than a consistent one.
IMPERSONATE = os.environ.get("IMPERSONATE", "chrome142")
TIMEOUT = (10, 20)  # (connect_timeout, read_timeout)

HEADERS = {"Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7"}

RETRY_STATUSES = {429, 503}  # rate limited / temporarily unavailable
# curl errors worth another try: couldn't connect, timed out, empty reply, send/receive failure.
# Certificate and DNS errors are not here: retrying them only repeats the same failure.
RETRYABLE_CURL_CODES = {7, 28, 52, 55, 56}
MAX_RETRY_AFTER = 30  # never wait longer than this (seconds) on a Retry-After header

# Politeness towards each store: at most N requests in flight per domain, and a small
# random delay (up to REQUEST_JITTER seconds) before each one.
PER_STORE_CONCURRENCY = int(os.environ.get("PER_STORE_CONCURRENCY", 2))
REQUEST_JITTER = float(os.environ.get("REQUEST_JITTER", 0.3))

_local = threading.local()
_domain_limits = {}
_domain_limits_lock = threading.Lock()


def _limit_for(domain):
    """The semaphore capping concurrent requests to one domain (shared by all threads)."""
    with _domain_limits_lock:
        if domain not in _domain_limits:
            _domain_limits[domain] = threading.BoundedSemaphore(PER_STORE_CONCURRENCY)
        return _domain_limits[domain]


def _session_for(domain, verify):
    """
    One session per (thread, domain, verify): connections and cookies are reused across the many
    requests a store receives, and sessions are never shared between threads.
    """
    sessions = getattr(_local, "sessions", None)
    if sessions is None:
        sessions = _local.sessions = {}
    key = (domain, verify)
    session = sessions.get(key)
    if session is None:
        session = sessions[key] = requests.Session(
            impersonate=IMPERSONATE,
            headers=HEADERS,
            timeout=TIMEOUT,
            allow_redirects=True,
            verify=verify,
        )
    return session


def _clean_curl_message(message):
    """Drops curl's boilerplate ("Failed to perform, curl: (N)" and the docs link) so the real reason fits."""
    message = re.sub(r"^Failed to perform, curl: \(\d+\)\s*", "", message)
    message = re.sub(r"\s*See https://curl\.se\S*.*$", "", message, flags=re.S)
    return message.strip()


def fetch(url, max_retries=2, verify=True, json=None):
    """
    GETs a URL, retrying transient curl errors (timeouts, resets), unexpected exceptions and 429/503
    responses (honouring Retry-After) with backoff. Returns (response, None) on success or (None, error_result).

    `verify` is the store's own TLS-verification setting; the global VERIFY_SSL=false overrides it for every store.
    With `json`, the request is a POST of that body (used for read-only VTEX cart simulations, safe to retry).
    """
    domain = urlsplit(url).netloc
    verify = VERIFY_SSL and verify is not False
    # The slot is held across retries and their backoff, so a struggling store gets less traffic.
    with _limit_for(domain):
        if REQUEST_JITTER > 0:
            time.sleep(random.uniform(0, REQUEST_JITTER))
        return _fetch_with_retries(_session_for(domain, verify), url, max_retries, json)


def _retry_delay(response, attempt):
    """Seconds to wait before retrying a 429/503: the Retry-After header (in seconds) if sane, else linear backoff."""
    headers = getattr(response, "headers", None) or {}
    try:
        delay = float(headers.get("Retry-After"))
    except (TypeError, ValueError):
        return 2 * (attempt + 1)
    return min(max(delay, 0), MAX_RETRY_AFTER)


def _fetch_with_retries(session, url, max_retries, json=None):
    for attempt in range(max_retries + 1):
        try:
            response = session.post(url, json=json) if json is not None else session.get(url)
            if response.status_code in RETRY_STATUSES and attempt < max_retries:
                time.sleep(_retry_delay(response, attempt))
                continue
            return response, None
        except requests.errors.RequestsError as e:
            if int(e.code) in RETRYABLE_CURL_CODES and attempt < max_retries:
                time.sleep(2 * (attempt + 1))  # Waits 2s, then 4s
                continue
            if e.code == 28:
                return None, error("TIMEOUT", "connection timed out (curl 28)")
            return None, error("NETWORK", f"curl {int(e.code)}: {_clean_curl_message(str(e))[:120]}")
        except Exception as e:
            if attempt < max_retries:
                time.sleep(2 * (attempt + 1))
                continue
            return None, error("NETWORK", f"exception: {str(e)[:40]}")

"""Result dicts with a machine-readable reason code at the start of `message` ("CODE: detail").

The top-level `status` stays one of Success / Not Found / Error. Codes:

  Not Found: NOT_LISTED (store returned no product), NO_PRICE, OUT_OF_STOCK, ZERO_PRICE, NO_STATE,
             EAN_MISMATCH (the matched product page is for another EAN)
  Error:     TIMEOUT, NETWORK, HTTP_ERROR, BLOCKED, PARSE_ERROR, CONFIG, SKIPPED (store unreachable)
"""


def not_found(code, detail):
    return {"status": "Not Found", "message": f"{code}: {detail}"}


def error(code, detail):
    return {"status": "Error", "message": f"{code}: {detail}"}

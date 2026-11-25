"""Facebook Ad Library transport: plain curl_cffi on the GraphQL calls the
Ad Library's own page makes (queries.py). No browser, no login, no cookie,
no token: the page's calls carry the tokens of its page load, and the site
answers the same without them.

Egress (the pod's table and the pool measurements are in the config notes):

  * every call but two rides config.FACEBOOK_AD_LIBRARY_EXIT, in production
    the server's own IP. A call refused there moves the whole process to
    config.FACEBOOK_AD_LIBRARY_FALLBACK_EXIT for
    config.FACEBOOK_AD_LIBRARY_BLOCK_HOLD_S;
  * the next-page call of a list (AdLibrarySearchPaginationQuery) is
    answered "Rate limit exceeded" from the server's own IP and from
    ordinary datacenter addresses, on the first try and inside a full page
    session too. It passes from home lines and from datacenter exits that
    sit on home internet lines: it rides
    config.FACEBOOK_AD_LIBRARY_PAGING_EXIT. An address serves a few hundred
    of these calls and is then refused: a refused call is sent again on a
    new address, config.FACEBOOK_AD_LIBRARY_PAGING_RETRIES times, the last
    of them on config.FACEBOOK_AD_LIBRARY_PAGING_LAST_EXIT. A cursor works
    from any address;
  * the filter options (ads/filters) are the one thing that needs a page
    load: the site answers them only for the session id it wrote into a
    page it served. The page (GET /ads/library/?...) first answers 403
    with a 481-byte script that POSTs /__rd_verify_<token>?challenge=N and
    reloads; the same three requests on one session get the page (plain
    HTTP, no browser). It rides config.FACEBOOK_AD_LIBRARY_PAGE_EXIT; the
    session id then works from any address.

Language. The site writes an answer's texts (button labels, spend and
impression ranges, the disclaimer's "Submitted" line, page categories,
country names) in the language of the caller's address and Accept-Language:
the same ad answered "Learn more" / "$25K - $30K" from a US address,
"더 알아보기" / "US$3만~US$3만" from a Korean one and "$25K-$30K" from an
Australian one (2026-11-25). The `locale` cookie, which the site's own
language menu sets, pins it whatever the exit: every call carries
config.FACEBOOK_AD_LIBRARY_LOCALE, so page 2 of a list reads like page 1.

One session per thread and exit; a retryable failure drops it, so the retry
rides a fresh connection (and, on a proxy exit, a fresh address).

What an answer looks like:
  * the body is one JSON document, or several on separate lines (the root
    query streams the ad list after the page frame): payloads() reads all;
  * a refusal is HTTP 200 with {"errors": [{"message": "Rate limit
    exceeded", "code": 1675004}]} and no data;
  * a value the query's types refuse (an unknown country code) is HTTP 200
    with code 1675012 and no data;
  * a document may carry `data` AND `errors` (an unknown advertiser id: the
    advertiser's card errors and the list is of every advertiser). Those
    are returned as they are; the parsers read what is there.

Failure taxonomy (scraper_errors, mapped to HTTP by route_glue):
  FacebookAdLibraryUpstreamError  transport failure / 5xx / not JSON   — retryable
  FacebookAdLibraryBlocked        403 / 429 / "Rate limit exceeded" /
                                  a captcha asked for                 — retryable
  FacebookAdLibraryBadRequest     the site refused a value            — never retried
  FacebookAdLibraryNotFound       no such ad / advertiser / group     — never retried
                                  (raised by api.py: the site answers
                                  them with HTTP 200 and an empty record)
"""
import os
import re
import sys
import threading
import time
import types
from urllib.parse import urlencode

# Allow direct execution (python facebook_ad_library/fetch.py): flat imports
# resolve like under the server. Idempotent when imported normally.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import egress
import fastjson
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError
from scraper_utils import retry, sessions
from facebook_ad_library import queries
from facebook_ad_library.refs import LIBRARY, SITE, referer_of

IMPERSONATE = "chrome"
# The root query answers in 1-2 s direct and up to 5 s through a home line
# (300-500 KB); the small calls in 0.2-0.5 s.
TIMEOUT = 30

GRAPHQL_URL = SITE + "/api/graphql/"

# What the page sends with every GraphQL call (samples/calls/*.request.json
# in the research folder, 2026-11-24), minus the two values of its own page
# load (x-fb-lsd, x-fb-entry-nonce). x-fb-friendly-name and the referer are
# set per call.
GRAPHQL_HEADERS = {
    "accept": "*/*",
    "content-type": "application/x-www-form-urlencoded",
    "origin": SITE,
    "priority": "u=1, i",
    "sec-ch-prefers-color-scheme": "light",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "x-asbd-id": "359341",
}

# The page's own navigation headers (samples/pages/*.request.json, 2026-11-24).
PAGE_HEADERS = {
    "accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,"
               "*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"),
    "priority": "u=0, i",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
}
# The script of the 403 page: fetch('/__rd_verify_<token>?challenge=3', {method: 'POST'}).finally(reload)
_VERIFY_RE = re.compile(r"fetch\('(/__rd_verify_[^']+)'")
_SESSION_ID_RE = re.compile(r'"sessionID":"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"')
PAGE_TIMEOUT = 45       # the page is 450-850 KB

RATE_LIMIT_CODE = 1675004       # "Rate limit exceeded"
BAD_VALUE_CODE = 1675012        # noncoercible_variable_value / variable_value_out_of_range / missing_required_variable_value
LOGGED_OUT_CODE = 1675002       # "Unauthorized logged out query."


class FacebookAdLibraryUpstreamError(UpstreamError):
    """Transport failure, 5xx, or an answer that is not the expected JSON — retryable."""


class FacebookAdLibraryBlocked(FacebookAdLibraryUpstreamError, Blocked):
    """403 / 429, "Rate limit exceeded", or a captcha asked for — retried on a fresh exit."""


class FacebookAdLibraryBadRequest(BadRequest):
    """The site refused a value of the request — never retried."""


class FacebookAdLibraryNotFound(NotFound):
    """No such ad / advertiser / group of versions — never retried."""


# ---- sessions ------------------------------------------------------------------------
# One curl session per thread and kind of call ("main" or "paging"): a curl
# handle must not be shared across threads. The hold is process-wide: one
# refusal of the default exit moves every thread.

_local = threading.local()
_hold_until = 0.0
_hold_lock = threading.Lock()

MAIN, PAGING, PAGE = "main", "paging", "page"


def _exit(kind):
    """The exit a new session of `kind` rides."""
    if kind == PAGING:
        return _paging_exit()
    if kind == PAGE:
        return config.FACEBOOK_AD_LIBRARY_PAGE_EXIT
    fallback = getattr(config, "FACEBOOK_AD_LIBRARY_FALLBACK_EXIT", None)
    if fallback and _hold_until > time.monotonic():
        return fallback
    return config.FACEBOOK_AD_LIBRARY_EXIT


def _paging_exit():
    """The next-page call's exit: its own, and on a call's last try the exit behind it."""
    last = getattr(config, "FACEBOOK_AD_LIBRARY_PAGING_LAST_EXIT", None)
    if last and getattr(_local, "paging_last_try", False):
        return last
    return config.FACEBOOK_AD_LIBRARY_PAGING_EXIT


def _hold():
    """The default exit was refused: every thread's next sessions ride the fallback."""
    global _hold_until
    if getattr(config, "FACEBOOK_AD_LIBRARY_FALLBACK_EXIT", None):
        with _hold_lock:
            _hold_until = time.monotonic() + config.FACEBOOK_AD_LIBRARY_BLOCK_HOLD_S


def _session(kind=MAIN):
    store = sessions.thread_store(_local)
    exits = getattr(_local, "exits", None)
    if exits is None:
        exits = _local.exits = {}
    exit = _exit(kind)
    if kind in store and exits.get(kind) != exit:
        sessions.drop_keyed(store, kind)        # a hold began or ended: change exits
    sess = sessions.keyed(store, kind, lambda: egress.exit_session(exit, impersonate=IMPERSONATE), 3)
    exits[kind] = exit
    # No call needs a cookie the site set, and a jar that fills up over
    # hundreds of calls is one more thing for the site to count: each goes
    # out with the language cookie alone (see "Language" above).
    try:
        sess.cookies.clear()
        sess.cookies.set("locale", config.FACEBOOK_AD_LIBRARY_LOCALE, domain=".facebook.com")
    except Exception:
        pass
    return sess


def _drop_session(kind=MAIN):
    """Close the thread's session of `kind` so the retry rides a fresh connection / exit."""
    sessions.drop_keyed(sessions.thread_store(_local), kind)


def _on_failure(error):
    if isinstance(error, FacebookAdLibraryBlocked):
        _hold()
    _drop_session(MAIN)
    print(f"facebook-ad-library: attempt failed: {error}")


def _on_page_failure(error):
    _drop_session(PAGE)
    print(f"facebook-ad-library: page load failed: {error}")


def _on_paging_failure(error):
    _drop_session(PAGING)
    print(f"facebook-ad-library: next-page attempt failed: {error}")


def _retrying(fn):
    """fn() under the shared retry policy: FacebookAdLibraryUpstreamError only
    (Blocked included), each retry on a fresh session; BadRequest surfaces at once."""
    return retry.run(fn, config, FacebookAdLibraryUpstreamError, _on_failure)


def _paging_retrying(fn):
    """fn() for the next-page call: its own count and pause, each retry on a
    new address, the last one on the exit behind (`_local.paging_last_try`)."""
    policy = types.SimpleNamespace(MAX_RETRIES=config.FACEBOOK_AD_LIBRARY_PAGING_RETRIES,
                                   RETRY_BACKOFF=config.FACEBOOK_AD_LIBRARY_PAGING_PAUSE)
    return retry.run_flagged(fn, policy, FacebookAdLibraryUpstreamError, _on_paging_failure, _local,
                             flag="paging_last_try")


# ---- GraphQL -------------------------------------------------------------------------

def payloads(text):
    """The JSON documents of one answer body, in order: one, or several on
    separate lines. [] when the body holds none (a login or error page)."""
    text = (text or "").lstrip("﻿ \r\n\t")
    if text.startswith("for (;;);"):
        text = text[len("for (;;);"):]
    out = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            value = fastjson.loads(line)
        except ValueError:
            # a document that spans lines: read the whole body as one
            try:
                value = fastjson.loads(text)
            except ValueError:
                return []
            return [value] if isinstance(value, dict) else []
        if isinstance(value, dict):
            out.append(value)
    return out


def _errors(documents):
    return [error for document in documents for error in (document.get("errors") or []) if isinstance(error, dict)]


def _has_data(documents):
    return any(isinstance(document.get("data"), dict) and document["data"] for document in documents)


def _check(documents, label, status, text):
    """Raise for an answer that is a refusal; return the documents otherwise."""
    if not documents:
        head = (text or "")[:200]
        if "<html" in (text or "")[:2000].lower() or "login" in head.lower():
            raise FacebookAdLibraryBlocked(f"{label}: HTTP {status}, a page where JSON was expected")
        raise FacebookAdLibraryUpstreamError(f"{label}: HTTP {status}, not JSON: {head[:120]!r}")
    errors = _errors(documents)
    if any(error.get("code") == RATE_LIMIT_CODE or "rate limit" in str(error.get("message") or "").lower()
           for error in errors):
        raise FacebookAdLibraryBlocked(f"{label}: Rate limit exceeded")
    if any((document.get("data") or {}).get("xfb_ad_library_is_captcha_required") is True for document in documents
           if isinstance(document.get("data"), dict)):
        raise FacebookAdLibraryBlocked(f"{label}: the Ad Library asks for a captcha")
    if _has_data(documents):
        return documents
    first = errors[0] if errors else {}
    message = str(first.get("message") or documents[0].get("errorSummary") or "no data in the answer")
    code = first.get("code")
    if code == BAD_VALUE_CODE:
        raise FacebookAdLibraryBadRequest(
            f"The Ad Library refused a value of this request ({label}). A country it does not list is the usual cause.")
    if code == LOGGED_OUT_CODE:
        raise FacebookAdLibraryUpstreamError(f"{label}: the site answers this operation to signed-in visitors only "
                                             "(its doc id may have changed: config.FACEBOOK_AD_LIBRARY_DOC_IDS)")
    raise FacebookAdLibraryUpstreamError(f"{label}: {message[:200]}")


def _post(name, variables, label, kind, referer):
    doc_id = config.FACEBOOK_AD_LIBRARY_DOC_IDS[name]
    body = urlencode(queries.form(name, doc_id, variables))
    headers = dict(GRAPHQL_HEADERS, referer=referer or LIBRARY)
    headers["x-fb-friendly-name"] = name
    try:
        resp = _session(kind).post(GRAPHQL_URL, data=body, headers=headers, timeout=TIMEOUT)
    except Exception as e:
        raise FacebookAdLibraryUpstreamError(f"{label}: request failed: {type(e).__name__}: {e}")
    status = resp.status_code
    if status in (401, 403, 429):
        raise FacebookAdLibraryBlocked(f"HTTP {status} for {label}")
    if status != 200:
        raise FacebookAdLibraryUpstreamError(f"HTTP {status} for {label}")
    text = resp.text or ""
    return _check(payloads(text), label, status, text)


def graphql(name, variables, label, referer=None):
    """One operation -> the answer's JSON documents (see the module docstring)."""
    if name == queries.MORE:
        return _paging_retrying(lambda: _post(name, variables, label, PAGING, referer))
    return _retrying(lambda: _post(name, variables, label, MAIN, referer))


# ---- the calls -----------------------------------------------------------------------

def fetch_list(variables, label):
    """The ROOT query: the first page of a list (and whatever else its
    variables ask for) -> the documents."""
    return graphql(queries.ROOT, variables, label, referer_of(variables))


def fetch_more(variables, cursor, label):
    """The next 10 ads of the list `variables` (the ROOT call's) opened, after `cursor`."""
    return graphql(queries.MORE, queries.more_variables(variables, cursor), label, referer_of(variables))


def fetch_ad_details(ad_id, page_id, country, is_political, is_eu_eligible):
    """An ad's advertiser, reach and transparency data -> the documents."""
    variables = queries.details_variables(ad_id, page_id, country, is_political, is_eu_eligible)
    return graphql(queries.DETAILS, variables, f"ad {ad_id} details")


def fetch_versions(collation_id, country, status, cursor=None):
    """One page (10 ads) of the ads that share a creative -> the documents."""
    variables = queries.versions_variables(collation_id, country, status, cursor)
    return graphql(queries.VERSIONS, variables, f"versions {collation_id}")


def fetch_suggestions(query, country="ALL"):
    """The advertisers the Ad Library's search box offers for `query` -> the documents."""
    return graphql(queries.SUGGEST, queries.suggest_variables(query, country), f"advertiser search {query!r}")


def fetch_page_session(link, label):
    """Load an Ad Library page as a browser does -> the session id the site
    ran the page's search under (the only id the FILTERS query answers
    for). Three requests on one session: the page (403 with the verify
    script), the POST the script makes (it sets the rd_challenge cookie),
    the page again."""
    def once():
        sess = _session(PAGE)
        try:
            resp = sess.get(link, headers=PAGE_HEADERS, timeout=PAGE_TIMEOUT)
            verify = _VERIFY_RE.search(resp.text or "") if resp.status_code in (200, 403) else None
            if verify:
                sess.post(SITE + verify.group(1), timeout=TIMEOUT, headers={
                    "accept": "*/*", "origin": SITE, "referer": link,
                    "sec-fetch-dest": "empty", "sec-fetch-mode": "cors", "sec-fetch-site": "same-origin"})
                resp = sess.get(link, headers=dict(PAGE_HEADERS, referer=link), timeout=PAGE_TIMEOUT)
        except Exception as e:
            raise FacebookAdLibraryUpstreamError(f"{label}: request failed: {type(e).__name__}: {e}")
        status, text = resp.status_code, resp.text or ""
        if status in (401, 403, 429) or _VERIFY_RE.search(text[:2000]) or "/login" in (resp.url or ""):
            raise FacebookAdLibraryBlocked(f"HTTP {status} for {label}")
        if status != 200:
            raise FacebookAdLibraryUpstreamError(f"HTTP {status} for {label}")
        found = _SESSION_ID_RE.search(text)
        if not found:
            raise FacebookAdLibraryUpstreamError(f"{label}: the page names no session id ({len(text)} characters)")
        return found.group(1)
    return retry.run(once, config, FacebookAdLibraryUpstreamError, _on_page_failure)


def fetch_filters(query, advertiser, country, status, ad_type, exact_phrase):
    """The filter options of a search -> the documents. Two steps: the page
    of that search (for its session id), then the FILTERS query, which the
    site takes 6-8 s to answer for a search with long option lists."""
    search = queries.search_variables(query=query, advertiser=advertiser, country=country, status=status,
                                      ad_type=ad_type, exact_phrase=exact_phrase)
    link = referer_of(search)
    session_id = fetch_page_session(link, "the page of the search")
    variables = queries.filters_variables(session_id, query, advertiser, country, ad_type, exact_phrase)
    return graphql(queries.FILTERS, variables, "filters", link)


def fetch_audience(page_id, period):
    """An advertiser's targeting summary of a period -> the documents."""
    return graphql(queries.AUDIENCE, queries.audience_variables(page_id, period), f"advertiser {page_id} audience")

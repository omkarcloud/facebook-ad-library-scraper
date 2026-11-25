"""The /facebook-ad-library/* endpoint functions (route_glue's route_table
calls them with the schema's loaded kwargs). Every endpoint caches through
cache.cached_call keyed on the validated params.

Lists. The site pages its lists with a cursor; the API takes `page`. The
first page of ads/search and advertisers/ads is one call that answers up
to 30 ads and the total; every later page is up to three next-page calls
of 10 ads each, chained, so a page is up to 30 ads everywhere. Each page is
cached with the cursor that opens the page after it, and those cursors are
also kept in this process, so a client walking a list pays one page of
upstream calls per page. A page whose opening cursor is not known is
reached by walking forward from the nearest page that is, at most
config.FACEBOOK_AD_LIBRARY_COLD_PAGES pages; a jump farther than that is
refused with a message that says to ask for the pages in order.

ads/details is two calls: the ad itself (the query the site's own page
shows a linked ad with, asked with a keyword no ad matches so the list
around it is empty), then its advertiser, reach and transparency data.
"""
import math
import threading
import time
from collections import OrderedDict

import cache
import config
from cache import DontCache, cached_call
from facebook_ad_library import cache_config as ttl
from facebook_ad_library import parsers, queries, refs
from scraper_utils import memo
from facebook_ad_library.fetch import (FacebookAdLibraryBadRequest, FacebookAdLibraryNotFound,
                                       FacebookAdLibraryUpstreamError, fetch_ad_details, fetch_audience,
                                       fetch_filters, fetch_list, fetch_more, fetch_suggestions, fetch_versions)

ADS_PER_PAGE = queries.FIRST_PAGE_ADS
MORE_CALLS_PER_PAGE = ADS_PER_PAGE // queries.MORE_ADS      # next-page calls chained into one page
CURSOR_MEMO_CAP = 20000     # cursors kept in this process (about 100 bytes each)

FIRST_MEMO_CAP = 5000       # lists whose first-page summary (total, advertiser card) is kept in this process

_AD_CATEGORY_NAMES = {token: name for name, token in refs.AD_CATEGORIES.items()}


# ---- advertisers by alias ------------------------------------------------------------

def _resolve_alias(alias):
    found = parsers.parse_suggestions(fetch_suggestions(alias))["advertisers"]
    for advertiser in found:
        if (advertiser.get("alias") or "").lower() == alias and advertiser.get("id"):
            return advertiser["id"]
    raise FacebookAdLibraryNotFound(
        f"No advertiser with the Facebook alias {alias!r} in the Ad Library. "
        "Find its page id with /facebook-ad-library/advertisers/search.")


def _advertiser_id(ref):
    """The page id of an `advertiser` param: the id itself, or the one the
    Ad Library's own search names for a Page alias (cached)."""
    alias = refs.alias_of(ref)
    if alias is None:
        return ref
    return cached_call("facebook_ad_library.alias", {"alias": alias}, _resolve_alias, ttl.ALIAS_CACHE)


# ---- cursor-paged lists --------------------------------------------------------------

# (list name, frozen params, page) -> (the cursor that opens the page, or False = no such page; when it was kept)
_cursors = OrderedDict()
_cursors_lock = threading.Lock()


def _list_key(name, params):
    return (name, tuple(sorted((key, str(value)) for key, value in params.items() if value is not None)))


def _known_cursor(key, page):
    """(True, cursor or False) when this process knows what opens `page`, else (False, None)."""
    with _cursors_lock:
        if (key, page) in _cursors:
            cursor, kept_at = _cursors[(key, page)]
            if time.monotonic() - kept_at < ttl.CURSOR_CACHE.total_seconds():
                _cursors.move_to_end((key, page))
                return True, cursor
            del _cursors[(key, page)]
    return False, None


def _remember_cursor(key, page, cursor):
    with _cursors_lock:
        _cursors[(key, page)] = (cursor or False, time.monotonic())
        _cursors.move_to_end((key, page))
        while len(_cursors) > CURSOR_MEMO_CAP:
            _cursors.popitem(last=False)


def _peek(namespace, data):
    """The cached row of a page, or None: a look that never fetches. A cache
    module without shared storage (one that keeps its rows in this process)
    has nothing to add to the cursor memo above, so it answers None."""
    storage = getattr(cache, "storage", None)
    store = storage() if storage else None
    if store is None:
        return None
    try:
        hit = store.get(namespace, data)
    except Exception as e:
        print(f"facebook-ad-library: cache read failed for {namespace}: {type(e).__name__}: {e}")
        return None
    return hit["data"] if hit is not None else None


def _list_page(name, params, page, load):
    """Page `page` of a cursor list -> the row `load` built: {"ads": [...],
    "next": the cursor of the page after it or None, ...}. `load(cursor)`
    reads one page upstream (cursor None = the first); `params` identify the
    list (the cache key, with the page)."""
    key = _list_key(name, params)
    namespace = f"facebook_ad_library.{name}"

    def opener(wanted):
        """The cursor that opens page `wanted`: None for the first, False when the list ends before it."""
        at, cursor = wanted, None
        while at > 1:
            known, cursor = _known_cursor(key, at)
            if known:
                break
            row = _peek(namespace, {**params, "page": at - 1})
            if row is not None:
                cursor = row.get("next") or False
                _remember_cursor(key, at, cursor)
                break
            at -= 1
            if wanted - at > config.FACEBOOK_AD_LIBRARY_COLD_PAGES:
                raise FacebookAdLibraryBadRequest(
                    f"page: page {wanted} opens with the cursor of page {wanted - 1}, and the nearest page held here is "
                    f"more than {config.FACEBOOK_AD_LIBRARY_COLD_PAGES} pages back. Ask for the pages in order.")
        else:
            cursor = None
        while at < wanted:
            if cursor is False:
                return False
            cursor = page_row(at).get("next") or False
            at += 1
        return cursor

    def page_row(number):
        def build(**_):
            cursor = opener(number)
            if cursor is False:
                return {"ads": [], "next": None}        # a page past the end is an empty page
            return load(cursor)
        row = cached_call(namespace, {**params, "page": number}, build, ttl.LIST_CACHE)
        _remember_cursor(key, number + 1, row.get("next"))
        if number == 1:
            _remember_first(key, row)
        return row

    return page_row(page)


_firsts = OrderedDict()
_firsts_lock = threading.Lock()
_SUMMARY_KEYS = ("total_count", "is_total_capped", "advertiser")


def _remember_first(key, first):
    """Keep what the first page of a list knows, as memo.memoized() stores it."""
    with _firsts_lock:
        _firsts[key] = (time.monotonic(), {name: first.get(name) for name in _SUMMARY_KEYS})
        _firsts.move_to_end(key)
        while len(_firsts) > FIRST_MEMO_CAP:
            _firsts.popitem(last=False)


def _first_page(name, params, page, row, load):
    """What only the first page of a list knows (the total, the advertiser's
    card), for any page of it: from the row itself, this process's memo, or
    the first page (cached)."""
    def summary():
        first = row if page == 1 else _list_page(name, params, 1, load)
        return {key: first.get(key) for key in _SUMMARY_KEYS}
    return memo.memoized(_firsts, _firsts_lock, FIRST_MEMO_CAP, _list_key(name, params), summary,
                         ttl.LIST_CACHE.total_seconds())


def _ads_loader(variables, label, wants_advertiser=False, missing=None):
    """load(cursor) for ads/search and advertisers/ads."""
    def load(cursor):
        if cursor is None:
            parsed = parsers.parse_list(fetch_list(variables, label))
            if wants_advertiser and parsed["advertiser"] is None:
                # the site answers an unknown page id with the ads of EVERY advertiser
                raise FacebookAdLibraryNotFound(missing)
            return {"ads": parsed["ads"], "next": parsed["next_cursor"], "total_count": parsed["total_count"],
                    "is_total_capped": parsed["is_total_capped"], "advertiser": parsed["advertiser"]}
        ads = []
        for _ in range(MORE_CALLS_PER_PAGE):
            parsed = parsers.parse_list(fetch_more(variables, cursor, label))
            ads.extend(parsed["ads"])
            cursor = parsed["next_cursor"]
            if not cursor:
                break
        return {"ads": ads, "next": cursor}
    return load


def _pagination(page, per_page, row, total=None, exact_pages=False):
    return {
        "page": page,
        "items_per_page": per_page,
        "has_next": bool(row.get("next")) and page < config.FACEBOOK_AD_LIBRARY_MAX_PAGE,
        # a page of the two ad lists holds up to 30 ads, not exactly 30, so their page count is not published
        "total_pages": math.ceil(total / per_page) if exact_pages and total is not None else None,
        "total_count": total,
    }


def _filters_echo(data):
    """The filters of a list request, as the caller's own words."""
    return {
        "country": data.get("country"),
        "status": data.get("status"),
        "ad_category": _AD_CATEGORY_NAMES.get(data.get("ad_category"), "all"),
        "media_type": data.get("media_type"),
        "sort": data.get("sort") or refs.DEFAULT_SORT,
    }


def _search_variables(data, advertiser=None):
    return queries.search_variables(
        query=data.get("query"), advertiser=advertiser, country=data["country"], status=data["status"],
        ad_type=data["ad_category"], media_type=data["media_type"], platforms=data.get("platforms"),
        languages=data.get("languages"), date_from=data.get("date_from"), date_to=data.get("date_to"),
        exact_phrase=bool(data.get("exact_phrase")), sort=data.get("sort"),
        advertiser_ids=data.get("advertiser_ids"), payer=data.get("payer"), regions=data.get("regions"),
        audience_sizes=data.get("audience_size"))


# ---- ads/search ----------------------------------------------------------------------

def ads_search(**data):
    """Ads that match a keyword (or belong to up to 50 advertisers), 30 a
    page, with the site's own filters and sorts."""
    page = data.pop("page")
    variables = _search_variables(data)
    label = f"search {data.get('query') or data.get('advertiser_ids')!r}"
    load = _ads_loader(variables, label)
    row = _list_page("ads_search", data, page, load)
    first = _first_page("ads_search", data, page, row, load)
    return {
        "query": data.get("query"),
        **_filters_echo(data),
        "is_total_capped": first.get("is_total_capped"),
        "results": row["ads"],
        "pagination": _pagination(page, ADS_PER_PAGE, row, first.get("total_count")),
    }


# ---- advertisers/ads -----------------------------------------------------------------

def advertisers_ads(**data):
    """Every ad of one advertiser, 30 a page, optionally only those that
    match a keyword, with the same filters and sorts as ads/search."""
    page = data.pop("page")
    data["advertiser"] = page_id = _advertiser_id(data["advertiser"])
    variables = _search_variables(data, advertiser=page_id)
    load = _ads_loader(variables, f"advertiser {page_id} ads", wants_advertiser=True,
                       missing=f"No advertiser with the page id {page_id} in the Ad Library.")
    row = _list_page("advertisers_ads", data, page, load)
    first = _first_page("advertisers_ads", data, page, row, load)
    return {
        "advertiser": first.get("advertiser"),
        "query": data.get("query"),
        **_filters_echo(data),
        "is_total_capped": first.get("is_total_capped"),
        "results": row["ads"],
        "pagination": _pagination(page, ADS_PER_PAGE, row, first.get("total_count")),
    }


# ---- ads/details ---------------------------------------------------------------------

def _ad_website(ad):
    """The advertiser's own website behind the ad: its display link (the
    caption: the site the ad says it leads to), else its landing link, else
    the first card's. Trackers, shorteners, app stores and Facebook's own
    sites are passed over (refs.website_of)."""
    candidates = [ad.get("caption"), ad.get("landing_link")]
    for card in ad.get("cards") or []:
        candidates += [card.get("caption"), card.get("landing_link")]
    for link in candidates:
        website = refs.website_of(link)
        if website:
            return website
    return None


def _ad_details(ad, country):
    variables = queries.search_variables(query=queries.NO_ADS_KEYWORD, country=country, status="all", ad=ad)
    record = parsers.parse_ad_by_id(fetch_list(variables, f"ad {ad}"))
    if record is None:
        raise FacebookAdLibraryNotFound(f"No ad with the Library ID {ad} in the Ad Library.")
    page_id = record["advertiser"]["id"]
    details, failed = None, False
    if page_id:
        try:
            details = parsers.parse_ad_details(fetch_ad_details(
                ad, page_id, country, "political" in record["categories"], bool(record["is_eu_transparency_eligible"])))
        except FacebookAdLibraryUpstreamError as e:
            print(f"facebook-ad-library: details of ad {ad} failed: {e}")
            failed = True
    details = details or {}
    out = {}
    for key, value in record.items():
        if key == "advertiser":
            # the details call knows the advertiser better (alias, category, Instagram, about)
            value = {**value, **{k: v for k, v in (details.get("advertiser") or {}).items() if v is not None or k not in value}}
        out[key] = value
        if key == "landing_link":
            out["website"] = _ad_website(record)
        if key == "political":
            out["political_insights"] = details.get("political_insights")
            out["advertiser_political_spend"] = details.get("political_spend")
            out["transparency"] = details.get("transparency")
    out["country"] = country
    # an ad missing its second half because that call failed is served but
    # not kept, so the next request tries it again
    return DontCache(out) if failed else out


def ad_details(**data):
    """One ad in full: the creative, its advertiser, the EU / UK reach and
    audience, who paid and who benefits, and a political ad's spend,
    impressions and delivery by age, gender and region."""
    return cached_call("facebook_ad_library.ad_details", data, _ad_details, ttl.AD_CACHE)


# ---- ads/versions --------------------------------------------------------------------

def ads_versions(**data):
    """The ads that share one creative (an ad's `versions.collation_id`), 10 a page."""
    page = data.pop("page")
    collation_id, country, status = data["collation_id"], data["country"], data["status"]

    def load(cursor):
        parsed = parsers.parse_versions(fetch_versions(collation_id, country, status, cursor))
        if parsed is None:
            if cursor is None:
                raise FacebookAdLibraryNotFound(f"No group of ad versions with the collation id {collation_id}.")
            return {"ads": [], "next": None}
        return {"ads": parsed["ads"], "next": parsed["next_cursor"], "total_count": parsed["total_count"]}

    row = _list_page("ads_versions", data, page, load)
    first = _first_page("ads_versions", data, page, row, load)
    return {
        "collation_id": collation_id,
        "country": country,
        "status": status,
        "results": row["ads"],
        "pagination": _pagination(page, queries.VERSIONS_PER_PAGE, row, first.get("total_count"), exact_pages=True),
    }


# ---- advertisers/search --------------------------------------------------------------

def _advertisers_search(query):
    parsed = parsers.parse_suggestions(fetch_suggestions(query))
    return {"query": query, "count": len(parsed["advertisers"]), "results": parsed["advertisers"],
            "keywords": parsed["keywords"]}


def advertisers_search(**data):
    """Advertisers by name, Page alias, Instagram handle or page id: the
    rows the Ad Library's own search box offers, up to 15."""
    return cached_call("facebook_ad_library.advertisers_search", data, _advertisers_search,
                       ttl.ADVERTISER_SEARCH_CACHE)


# ---- advertisers/details -------------------------------------------------------------

def _advertiser_details(advertiser, country):
    variables = queries.search_variables(advertiser=advertiser, country=country, status="all", about=True)
    record = parsers.parse_advertiser(fetch_list(variables, f"advertiser {advertiser}"))
    if record is None:
        raise FacebookAdLibraryNotFound(f"No advertiser with the page id {advertiser} in the Ad Library.")
    record["country"] = country
    return record


def advertiser_details(**data):
    """One advertiser's About tab: category, likes, Instagram, the confirmed
    owner, the Page's history, where its admins are, and for the country
    asked how many ads it has in the library and its political spend."""
    data["advertiser"] = _advertiser_id(data["advertiser"])
    return cached_call("facebook_ad_library.advertiser_details", data, _advertiser_details, ttl.ADVERTISER_CACHE)


# ---- advertisers/audience ------------------------------------------------------------

def _advertiser_audience(advertiser, period):
    record = parsers.parse_audience(fetch_audience(advertiser, period))
    if record is None:
        raise FacebookAdLibraryNotFound(f"No advertiser with the page id {advertiser} in the Ad Library.")
    days = next((int(days) for days, token in refs.PERIODS.items() if token == period), None)
    return {"advertiser": record.pop("advertiser"), "period_days": days, **record}


def advertiser_audience(**data):
    """How an advertiser aimed its political and issue ads over the last 7,
    30 or 90 days: spend, ad count, and the share by gender, age, location
    and audience type."""
    data["advertiser"] = _advertiser_id(data["advertiser"])
    return cached_call("facebook_ad_library.advertiser_audience", data, _advertiser_audience, ttl.AUDIENCE_CACHE)


# ---- ads/filters ---------------------------------------------------------------------

def _ads_filters(query, advertiser, country, status, ad_category, exact_phrase):
    parsed = parsers.parse_filters(fetch_filters(query, advertiser, country, status, ad_category, exact_phrase))
    return {"query": query, "advertiser_id": advertiser, "country": country, "status": status,
            "ad_category": _AD_CATEGORY_NAMES.get(ad_category, "all"), **parsed}


def ads_filters(**data):
    """The filter options the site offers for a search or an advertiser:
    languages, advertisers, payers and regions, each with a rounded count."""
    if data.get("advertiser"):
        data["advertiser"] = _advertiser_id(data["advertiser"])
    return cached_call("facebook_ad_library.ads_filters", data, _ads_filters, ttl.FILTERS_CACHE)

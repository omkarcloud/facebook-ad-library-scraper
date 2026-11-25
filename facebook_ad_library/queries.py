"""The Ad Library's GraphQL operations: their names, the form a call is sent
as, and the variables of each, built the way the site's own page builds
them (the page's script, read 2026-11-24). Pure functions, no I/O.

Every call is `POST /api/graphql/` with a form body: the operation's
`doc_id` (a persisted query: the text is never sent), its name and its
`variables` as JSON. The ids change when Facebook ships a new build
(config.FACEBOOK_AD_LIBRARY_DOC_IDS, dated).

  ROOT        AdLibraryFoundationRootQuery   the query the page itself is
              rendered from. One call answers: the first 30 ads of a search
              or of an advertiser with the total, the advertiser's card, one
              ad by its id (deeplinkAdID) and the advertiser's About tab
              (isAboutTab).
  MORE        AdLibrarySearchPaginationQuery   the next 10 ads after a cursor
  DETAILS     AdLibraryV3AdDetailsQuery        an ad's advertiser, EU / UK
              reach, payer and beneficiary, political insights
  VERSIONS    AdLibraryV3AdCollationDetailsQuery   the ads that share one creative
  SUGGEST     useAdLibraryTypeaheadSuggestionDataSourceQuery   advertisers by name
  FILTERS     AdLibraryFilterContextProviderQuery   the filter options of a
              search. It answers only for the session id of a PAGE load of
              that search (the page's HTML names it), so it is the one call
              with a page load in front of it.
  AUDIENCE    AdLibraryPageAudienceTabQuery    an advertiser's targeting summary
"""
import hashlib
import json
import uuid

from facebook_ad_library import refs

ROOT = "AdLibraryFoundationRootQuery"
MORE = "AdLibrarySearchPaginationQuery"
DETAILS = "AdLibraryV3AdDetailsQuery"
VERSIONS = "AdLibraryV3AdCollationDetailsQuery"
SUGGEST = "useAdLibraryTypeaheadSuggestionDataSourceQuery"
FILTERS = "AdLibraryFilterContextProviderQuery"
AUDIENCE = "AdLibraryPageAudienceTabQuery"
OPERATIONS = (ROOT, MORE, DETAILS, VERSIONS, SUGGEST, FILTERS, AUDIENCE)

FIRST_PAGE_ADS = 30     # the root query's own page (a literal in the query)
MORE_ADS = 10           # what one cursor call answers, whatever `first` says (10 to 500 tried, 2026-11-24)
VERSIONS_PER_PAGE = 10  # the versions query's own page

# A keyword no ad matches. A call that wants one ad, or an advertiser's About
# tab, would otherwise also carry the first 30 ads of the list around it
# (260-400 KB); with this keyword that list is empty (4-9 KB).
NO_ADS_KEYWORD = "zzzqqqxxxyyy"

# The page stamps a search with md5(keyword + page id + this number)[:6] as
# `v` ("813ecb" for coffee). The answers do not change with it (2026-11-24).
_V_SALT = "760"


def form(name, doc_id, variables):
    """The form body of one call, in the page's field order. The page also
    sends the tokens of its own page load (lsd, __rev, __hsi, __dyn, ...);
    the site answers the same without them, and a made-up lsd passes
    (2026-11-24), so a call goes out without a page load behind it."""
    return {
        "av": "0",
        "__aaid": "0",
        "__user": "0",
        "__a": "1",
        "__comet_req": "94",
        "fb_api_caller_class": "RelayModern",
        "fb_api_req_friendly_name": name,
        "server_timestamps": "true",
        "variables": json.dumps(variables, separators=(",", ":"), ensure_ascii=False),
        "doc_id": str(doc_id),
    }


def _session_id():
    return str(uuid.uuid4())


def _v(query, page_id):
    text = (query or "") + ("" if page_id in (None, "", "0") else str(page_id))
    return hashlib.md5((text + _V_SALT).encode("utf-8")).hexdigest()[:6] if text else ""


def _date_range(date_from, date_to):
    return {"min": date_from, "max": date_to} if (date_from or date_to) else None


def search_variables(query="", advertiser=None, country=refs.ALL_COUNTRIES, status=refs.DEFAULT_STATUS,
                     ad_type="ALL", media_type="all", platforms=None, languages=None, date_from=None, date_to=None,
                     exact_phrase=False, sort=None, advertiser_ids=None, payer=None, regions=None,
                     audience_sizes=None, ad=None, about=False):
    """The variables of the ROOT query. `advertiser` (a page id) makes it the
    advertiser's own list (searchType "page"); `ad` asks for that ad too;
    `about` for the advertiser's About tab."""
    query = (query or "").strip()
    if advertiser:
        search_type = "page"
    elif exact_phrase:
        search_type = "keyword_exact_phrase"
        query = f'"{query}"' if query else query      # the page keeps the quotes in the keyword
    else:
        search_type = "keyword_unordered"
    page_id = str(advertiser) if advertiser else "0"
    return {
        "adType": ad_type,
        "activeStatus": status,
        "audienceTimeframe": refs.DEFAULT_PERIOD,
        "bylines": [payer] if payer else [],
        "collationToken": None,
        "contentLanguages": list(languages or []),
        "countries": [country],
        "country": country,
        "deeplinkAdID": str(ad) if ad else None,
        "isAboutTab": bool(about),
        "isAudienceTab": False,
        "isLandingPage": False,
        "isTargetedCountry": False,
        "hasDeeplinkAdID": bool(ad),
        "location": None,
        "mediaType": media_type,
        "multiCountryFilterMode": None,
        "pageIDs": [str(i) for i in (advertiser_ids or [])],
        "potentialReachInput": list(audience_sizes) if audience_sizes else None,
        "publisherPlatforms": list(platforms or []),
        "queryString": query,
        "regions": list(regions) if regions else None,
        "searchType": search_type,
        "sessionID": _session_id(),
        "source": None,
        "sortData": refs.SORTS.get(sort) if sort else None,
        "startDate": _date_range(date_from, date_to),
        "fetchPageInfo": bool(advertiser),
        "fetchSharedDisclaimers": bool(advertiser),
        "viewAllPageID": page_id,
        "v": _v(query, page_id),
    }


_ROOT_ONLY = ("audienceTimeframe", "country", "deeplinkAdID", "fetchPageInfo", "fetchSharedDisclaimers",
              "hasDeeplinkAdID", "isAboutTab", "isAudienceTab", "isLandingPage")


def more_variables(search, cursor):
    """The variables of the MORE query for the list `search` (a
    search_variables() result) opened: the same filters, plus the cursor."""
    out = {key: value for key, value in search.items() if key not in _ROOT_ONLY}
    out.update(cursor=cursor, excludedIDs=None, first=MORE_ADS)
    return dict(sorted(out.items(), key=lambda item: item[0].lower()))


def details_variables(ad_id, page_id, country, is_political, is_eu_eligible):
    """The variables of the DETAILS query. The two flags switch parts of the
    query on, as the page does from the ad it already shows: the political
    insights for a political ad, the payer and beneficiary for an ad that
    falls under the EU's ad transparency rules."""
    return {
        "adArchiveID": str(ad_id),
        "country": country,
        "isAdNonPolitical": not is_political,
        "isAdNotAAAEligible": not is_eu_eligible,
        "pageID": str(page_id),
        "sessionID": _session_id(),
        "source": None,
    }


def versions_variables(collation_id, country=refs.ALL_COUNTRIES, status="all", cursor=None):
    return {
        "collationGroupID": str(collation_id),
        "forwardCursor": cursor,
        "backwardCursor": None,
        "activeStatus": status,
        "adType": "ALL",
        "bylines": [],
        "location": None,
        "potentialReach": [],
        "publisherPlatforms": [],
        "regions": [],
        "sessionID": _session_id(),
        "startDate": None,
        "countries": [country],
    }


def suggest_variables(query, country=refs.ALL_COUNTRIES):
    return {"adType": "ALL", "country": country, "isMobile": False, "queryString": query}


def filters_variables(session_id, query=None, advertiser=None, country=refs.ALL_COUNTRIES, ad_type="ALL",
                      exact_phrase=False):
    """The variables of the FILTERS query. `session_id` must be the one a
    page load of that search was given (fetch.fetch_page_session): the site
    answers the options of the search that session ran, and nothing for an
    id it did not hand out itself (2026-11-25)."""
    return {
        "adType": ad_type,
        "country": country,
        "searchType": "PAGE" if advertiser else ("KEYWORD_EXACT_PHRASE" if exact_phrase else "KEYWORD_UNORDERED"),
        "location": None,
        "queryString": None if advertiser else (query or None),
        "source": None,
        "sessionId": session_id,
        "viewAllPageId": str(advertiser) if advertiser else None,
    }


def audience_variables(page_id, period=refs.DEFAULT_PERIOD):
    return {"audienceTimeframe": period, "viewAllPageID": str(page_id)}

"""Offline tests for the transport's reading of the site's answers
(facebook_ad_library/fetch.py) and the endpoint logic
(facebook_ad_library/api.py), with the network and the cache replaced by
fakes (the answers are the fixtures).

    python -m pytest facebook_ad_library/test_api.py -q
"""
import gzip
import json
import os
import sys
from urllib.parse import parse_qsl

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from facebook_ad_library import api, fetch, queries, refs  # noqa: E402
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def raw(name):
    """A fixture's raw body; a large one is kept packed (<name>.json.gz)."""
    path = os.path.join(FX, name)
    if name.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return f.read()
    with open(path, encoding="utf-8") as f:
        return f.read()


# ---- the transport -------------------------------------------------------------------

class Answer:
    def __init__(self, status=200, body="", url=None):
        self.status_code = status
        self.text = body if isinstance(body, str) else json.dumps(body)
        self.content = self.text.encode()
        self.headers = {}
        self.url = url


class Jar(dict):
    """The session's cookie jar: {name: value}."""

    def set(self, name, value, domain=None):
        self[name] = value


class Wire:
    """A session whose answers are queued; it records what was sent and on which kind of session."""

    def __init__(self, monkeypatch, *answers):
        self.answers = list(answers)
        self.sent = []
        self.dropped = []
        self.exits = []                 # the exit each request's session would ride
        self.cookies = Jar()
        monkeypatch.setattr(fetch, "_session", self._session)
        monkeypatch.setattr(fetch, "_drop_session", lambda kind=fetch.MAIN: self.dropped.append(kind))
        monkeypatch.setattr(fetch.retry.time, "sleep", lambda seconds: None)
        monkeypatch.setattr(fetch, "_hold_until", 0.0)

    def _session(self, kind=fetch.MAIN):
        self.kind = kind
        self.exits.append(fetch._exit(kind))
        return self

    def _next(self, method, url, headers, data):
        form = dict(parse_qsl(data, keep_blank_values=True)) if isinstance(data, str) else None
        self.sent.append({"method": method, "url": url, "headers": headers, "form": form, "kind": self.kind})
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def post(self, url, data=None, headers=None, timeout=None):
        return self._next("POST", url, headers, data)

    def get(self, url, headers=None, timeout=None):
        return self._next("GET", url, headers, None)


def test_payloads_reads_one_document_or_several():
    assert len(fetch.payloads(raw("suggestions_nike.json"))) == 1
    documents = fetch.payloads(raw("search_no_result.json"))
    assert len(documents) == 2 and "label" in documents[1]             # the page frame, then the streamed list
    assert len(fetch.payloads(raw("advertiser_unknown.json"))) == 3
    assert fetch.payloads('for (;;);{"a": 1}') == [{"a": 1}]
    assert fetch.payloads('{\n "a": 1\n}') == [{"a": 1}]                # one document over several lines
    assert fetch.payloads("<html>login</html>") == [] and fetch.payloads("") == [] and fetch.payloads(None) == []


def test_a_call_goes_out_as_the_page_sends_it(monkeypatch):
    wire = Wire(monkeypatch, Answer(body=raw("suggestions_nike.json")))
    documents = fetch.fetch_suggestions("nike")
    assert documents[0]["data"]["ad_library_main"]["typeahead_suggestions"]["page_results"]
    sent = wire.sent[0]
    assert sent["method"] == "POST" and sent["url"] == "https://www.facebook.com/api/graphql/" and sent["kind"] == fetch.MAIN
    assert list(sent["form"]) == ["av", "__aaid", "__user", "__a", "__comet_req", "fb_api_caller_class",
                                  "fb_api_req_friendly_name", "server_timestamps", "variables", "doc_id"]
    assert sent["form"]["doc_id"] == config.FACEBOOK_AD_LIBRARY_DOC_IDS[queries.SUGGEST]
    assert sent["form"]["fb_api_req_friendly_name"] == queries.SUGGEST
    assert json.loads(sent["form"]["variables"]) == {"adType": "ALL", "country": "ALL", "isMobile": False,
                                                     "queryString": "nike"}
    headers = sent["headers"]
    assert headers["x-fb-friendly-name"] == queries.SUGGEST and headers["x-asbd-id"] == "359341"
    assert headers["content-type"] == "application/x-www-form-urlencoded" and headers["origin"] == "https://www.facebook.com"
    assert headers["referer"] == "https://www.facebook.com/ads/library/"
    assert "accept-language" not in headers and "user-agent" not in headers and "x-fb-lsd" not in headers


def test_rate_limit_is_blocked_and_retried_on_a_fresh_session(monkeypatch):
    refusal = Answer(body=raw("error_rate_limit_exceeded.json"))
    wire = Wire(monkeypatch, refusal, Answer(body=raw("suggestions_nike.json")))
    assert fetch.fetch_suggestions("nike")
    assert wire.dropped == [fetch.MAIN] and len(wire.sent) == 2
    assert fetch._hold_until > 0                    # the default exit is set aside for the hold

    wire = Wire(monkeypatch, *[refusal] * config.MAX_RETRIES)
    with pytest.raises(Blocked):
        fetch.fetch_suggestions("nike")
    assert len(wire.sent) == config.MAX_RETRIES


def _exits(monkeypatch):
    monkeypatch.setattr(fetch, "_hold_until", 0.0)
    monkeypatch.setattr(config, "FACEBOOK_AD_LIBRARY_EXIT", "direct")
    monkeypatch.setattr(config, "FACEBOOK_AD_LIBRARY_FALLBACK_EXIT", "datacenter:isp")
    monkeypatch.setattr(config, "FACEBOOK_AD_LIBRARY_PAGING_EXIT", "datacenter:isp")
    monkeypatch.setattr(config, "FACEBOOK_AD_LIBRARY_PAGING_LAST_EXIT", "residential:clean-fast")
    monkeypatch.setattr(config, "FACEBOOK_AD_LIBRARY_PAGE_EXIT", "datacenter:isp")


def test_the_hold_moves_the_main_session_to_the_fallback_exit(monkeypatch):
    _exits(monkeypatch)
    monkeypatch.setattr(config, "FACEBOOK_AD_LIBRARY_PAGING_EXIT", "us")
    assert fetch._exit(fetch.MAIN) == "direct" and fetch._exit(fetch.PAGING) == "us" and fetch._exit(fetch.PAGE) == "datacenter:isp"
    fetch._hold()
    assert fetch._exit(fetch.MAIN) == "datacenter:isp" and fetch._exit(fetch.PAGING) == "us"
    monkeypatch.setattr(config, "FACEBOOK_AD_LIBRARY_FALLBACK_EXIT", None)
    assert fetch._exit(fetch.MAIN) == "direct"


def test_every_call_carries_the_language_cookie_and_no_other(monkeypatch):
    class Session:
        def __init__(self):
            self.cookies = Jar(datr="x", rd_challenge="y")      # what the site set on earlier calls
    built = Session()
    monkeypatch.setattr(fetch.egress, "exit_session", lambda exit, impersonate=None: built)
    monkeypatch.setattr(config, "FACEBOOK_AD_LIBRARY_LOCALE", "en_US")
    fetch._drop_session(fetch.MAIN)
    try:
        assert fetch._session(fetch.MAIN) is built and built.cookies == {"locale": "en_US"}
        built.cookies["datr"] = "again"
        assert fetch._session(fetch.MAIN).cookies == {"locale": "en_US"}
    finally:
        fetch.sessions.thread_store(fetch._local).pop(fetch.MAIN, None)


def test_the_next_page_call_rides_its_own_exit_with_its_own_retries(monkeypatch):
    refusal = Answer(body=raw("error_rate_limit_exceeded.json"))
    variables = queries.search_variables(query="coffee", country="US")
    wire = Wire(monkeypatch, refusal, refusal, Answer(body=raw("search_coffee_us_more.json.gz")))
    documents = fetch.fetch_more(variables, "CURSOR", "search")
    assert len(documents) == 1 and [sent["kind"] for sent in wire.sent] == [fetch.PAGING] * 3
    assert wire.dropped == [fetch.PAGING, fetch.PAGING]
    assert fetch._hold_until == 0.0                 # a refused next-page call says nothing about the default exit
    sent = json.loads(wire.sent[0]["form"]["variables"])
    assert sent["cursor"] == "CURSOR" and sent["first"] == 10 and sent["queryString"] == "coffee"
    assert "deeplinkAdID" not in sent and "country" not in sent and sent["countries"] == ["US"]
    assert wire.sent[0]["form"]["fb_api_req_friendly_name"] == queries.MORE

    wire = Wire(monkeypatch, *[refusal] * config.FACEBOOK_AD_LIBRARY_PAGING_RETRIES)
    with pytest.raises(Blocked):
        fetch.fetch_more(variables, "CURSOR", "search")
    assert len(wire.sent) == config.FACEBOOK_AD_LIBRARY_PAGING_RETRIES


def test_the_last_try_of_a_next_page_call_rides_the_exit_behind(monkeypatch):
    _exits(monkeypatch)
    monkeypatch.setattr(config, "FACEBOOK_AD_LIBRARY_PAGING_RETRIES", 4)
    refusal = Answer(body=raw("error_rate_limit_exceeded.json"))
    variables = queries.search_variables(query="coffee", country="US")
    wire = Wire(monkeypatch, refusal, refusal, refusal, Answer(body=raw("search_coffee_us_more.json.gz")))
    assert fetch.fetch_more(variables, "CURSOR", "search")
    assert wire.exits == ["datacenter:isp", "datacenter:isp", "datacenter:isp", "residential:clean-fast"]
    assert fetch._exit(fetch.PAGING) == "datacenter:isp"        # the next call starts on its own exit again
    monkeypatch.setattr(config, "FACEBOOK_AD_LIBRARY_PAGING_LAST_EXIT", None)
    wire = Wire(monkeypatch, *[refusal] * 4)
    with pytest.raises(Blocked):
        fetch.fetch_more(variables, "CURSOR", "search")
    assert wire.exits == ["datacenter:isp"] * 4


@pytest.mark.parametrize("answer, error", [
    (Answer(body=raw("error_bad_value.json")), BadRequest),
    (Answer(status=403, body="Forbidden"), Blocked),
    (Answer(status=429, body=""), Blocked),
    (Answer(status=500, body="<html>Sorry, something went wrong</html>"), UpstreamError),
    (Answer(body="<html><title>Log in to Facebook</title></html>"), Blocked),
    (Answer(body="not json"), UpstreamError),
    (Answer(body={"errors": [{"message": "Unauthorized logged out query.", "code": 1675002}]}), UpstreamError),
    (Answer(body={"data": {"xfb_ad_library_is_captcha_required": True, "ad_library_main": {}}}), Blocked),
    (ConnectionError("reset"), UpstreamError),
])
def test_refusals_and_failures(monkeypatch, answer, error):
    retried = error is not BadRequest
    wire = Wire(monkeypatch, *([answer] * (config.MAX_RETRIES if retried else 1)))
    with pytest.raises(error):
        fetch.fetch_suggestions("nike")
    assert len(wire.sent) == (config.MAX_RETRIES if retried else 1)


def test_an_answer_with_data_and_errors_is_passed_on(monkeypatch):
    Wire(monkeypatch, Answer(body=raw("advertiser_ads_unknown_id.json.gz")))
    documents = fetch.fetch_list(queries.search_variables(advertiser="999999999999999999"), "x")
    assert any(document.get("errors") for document in documents) and len(documents) == 2


def test_the_page_session_answers_the_verify_step(monkeypatch):
    link = "https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=US&q=coffee"
    page = '<html><script>{"sessionID":"8daea9ad-ce47-4683-b452-f40b96970bd7","source":null}</script>' + "x" * 3000
    wire = Wire(monkeypatch, Answer(status=403, body=raw("page_challenge_403.html")), Answer(body=""), Answer(body=page))
    assert fetch.fetch_page_session(link, "page") == "8daea9ad-ce47-4683-b452-f40b96970bd7"
    assert [(sent["method"], sent["kind"]) for sent in wire.sent] == [("GET", fetch.PAGE), ("POST", fetch.PAGE), ("GET", fetch.PAGE)]
    assert wire.sent[1]["url"].startswith("https://www.facebook.com/__rd_verify_") and wire.sent[1]["url"].endswith("?challenge=3")
    assert wire.sent[2]["headers"]["referer"] == link and wire.sent[0]["headers"]["sec-fetch-mode"] == "navigate"

    # a page that still asks to verify is a block; one without a session id is an upstream failure
    challenge = Answer(status=403, body=raw("page_challenge_403.html"))
    wire = Wire(monkeypatch, *[challenge, Answer(body=""), challenge] * config.MAX_RETRIES)
    with pytest.raises(Blocked):
        fetch.fetch_page_session(link, "page")
    assert wire.dropped == [fetch.PAGE] * config.MAX_RETRIES
    Wire(monkeypatch, *[Answer(body="<html>" + "x" * 3000)] * config.MAX_RETRIES)
    with pytest.raises(UpstreamError):
        fetch.fetch_page_session(link, "page")


def test_filters_take_the_session_id_of_the_page(monkeypatch):
    page = '{"sessionID":"8daea9ad-ce47-4683-b452-f40b96970bd7"}'
    wire = Wire(monkeypatch, Answer(body=page), Answer(body=raw("filters_keyword.json")))
    fetch.fetch_filters("coffee", None, "US", "active", "ALL", False)
    assert wire.sent[0]["method"] == "GET" and "q=coffee" in wire.sent[0]["url"] and "country=US" in wire.sent[0]["url"]
    variables = json.loads(wire.sent[1]["form"]["variables"])
    assert variables == {"adType": "ALL", "country": "US", "searchType": "KEYWORD_UNORDERED", "location": None,
                         "queryString": "coffee", "source": None,
                         "sessionId": "8daea9ad-ce47-4683-b452-f40b96970bd7", "viewAllPageId": None}
    assert wire.sent[1]["headers"]["referer"] == wire.sent[0]["url"]


# ---- the variables -------------------------------------------------------------------

def test_search_variables_are_the_pages():
    variables = queries.search_variables(query="coffee", country="US")
    assert list(variables) == [
        "adType", "activeStatus", "audienceTimeframe", "bylines", "collationToken", "contentLanguages", "countries",
        "country", "deeplinkAdID", "isAboutTab", "isAudienceTab", "isLandingPage", "isTargetedCountry",
        "hasDeeplinkAdID", "location", "mediaType", "multiCountryFilterMode", "pageIDs", "potentialReachInput",
        "publisherPlatforms", "queryString", "regions", "searchType", "sessionID", "source", "sortData", "startDate",
        "fetchPageInfo", "fetchSharedDisclaimers", "viewAllPageID", "v"]
    assert variables["v"] == "813ecb"               # what the site's page stamped the same search with
    assert variables["searchType"] == "keyword_unordered" and variables["viewAllPageID"] == "0"
    assert variables["sortData"] is None and variables["startDate"] is None and variables["potentialReachInput"] is None


def test_search_variables_of_every_filter():
    variables = queries.search_variables(
        query="coffee maker", country="GB", status="inactive", ad_type="POLITICAL_AND_ISSUE_ADS", media_type="video",
        platforms=["facebook", "instagram"], languages=["en"], date_from="2026-09-01", date_to=None, exact_phrase=True,
        sort="most_recent", advertiser_ids=["1", "2"], payer="ACME PAC", regions=["California"],
        audience_sizes=[{"min": 100, "max": 1000}])
    assert variables["queryString"] == '"coffee maker"' and variables["searchType"] == "keyword_exact_phrase"
    assert variables["countries"] == ["GB"] and variables["activeStatus"] == "inactive"
    assert variables["publisherPlatforms"] == ["facebook", "instagram"] and variables["contentLanguages"] == ["en"]
    assert variables["startDate"] == {"min": "2026-09-01", "max": None}
    assert variables["sortData"] == {"direction": "DESCENDING", "mode": "SORT_BY_RELEVANCY_MONTHLY_GROUPED"}
    assert variables["pageIDs"] == ["1", "2"] and variables["bylines"] == ["ACME PAC"]
    assert variables["regions"] == ["California"] and variables["potentialReachInput"] == [{"min": 100, "max": 1000}]
    assert queries.search_variables(query="x", sort="impressions_asc")["sortData"] == {
        "direction": "ASCENDING", "mode": "SORT_BY_TOTAL_IMPRESSIONS"}


def test_advertiser_ad_and_about_variables():
    advertiser = queries.search_variables(advertiser="15087023444", status="all")
    assert advertiser["searchType"] == "page" and advertiser["viewAllPageID"] == "15087023444"
    assert advertiser["fetchPageInfo"] is True and advertiser["v"] == "a7cdb2"
    ad = queries.search_variables(query=queries.NO_ADS_KEYWORD, status="all", ad="1953276048719851")
    assert ad["deeplinkAdID"] == "1953276048719851" and ad["hasDeeplinkAdID"] is True
    assert queries.search_variables(advertiser="1", about=True)["isAboutTab"] is True
    details = queries.details_variables("1", "2", "US", is_political=True, is_eu_eligible=False)
    assert details["isAdNonPolitical"] is False and details["isAdNotAAAEligible"] is True and details["pageID"] == "2"


def test_referer_is_the_page_of_the_search():
    assert refs.referer_of(queries.search_variables(query="coffee maker", country="US")) == (
        "https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=US&is_targeted_country=false"
        "&media_type=all&q=coffee%20maker&search_type=keyword_unordered")
    assert refs.referer_of(queries.search_variables(advertiser="15087023444", status="all")).endswith(
        "&search_type=page&view_all_page_id=15087023444")


# ---- the endpoints -------------------------------------------------------------------

class Site:
    """The fetch functions of api.py, answered from fixtures; it counts the calls."""

    def __init__(self, monkeypatch, lists=None, more=None, **others):
        self.calls = []
        self.lists = lists or {}
        self.more = more or (lambda cursor: None)
        monkeypatch.setattr(api, "fetch_list", self.fetch_list)
        monkeypatch.setattr(api, "fetch_more", self.fetch_more)
        for name, answer in others.items():
            monkeypatch.setattr(api, name, self._fixed(name, answer))
        monkeypatch.setattr(api, "cached_call", lambda namespace, data, fn, ttl, **kw: self._uncached(fn, data))
        monkeypatch.setattr(api.cache, "storage", lambda: None, raising=False)
        api._cursors.clear()
        api._firsts.clear()

    @staticmethod
    def _uncached(fn, data):
        result = fn(**data)
        return result.data if isinstance(result, api.DontCache) else result

    def _fixed(self, name, answer):
        def call(*args):
            self.calls.append((name, args))
            if isinstance(answer, Exception):
                raise answer
            return fetch.payloads(raw(answer(*args) if callable(answer) else answer))
        return call

    def fetch_list(self, variables, label):
        self.calls.append(("list", variables))
        for test, name in self.lists.items():
            if test(variables):
                return fetch.payloads(raw(name))
        raise AssertionError(f"no fixture for {variables}")

    def fetch_more(self, variables, cursor, label):
        self.calls.append(("more", cursor))
        return self.more(cursor)

    def count(self, kind):
        return sum(1 for name, _ in self.calls if name == kind)


def _more_page(ids, next_cursor):
    """A next-page answer with the ads `ids` (copies of a real raw ad)."""
    template = fetch.payloads(raw("search_coffee_us_more.json.gz"))[0]
    edge = template["data"]["ad_library_main"]["search_results_connection"]["edges"][0]
    edges = []
    for ad_id in ids:
        copy = json.loads(json.dumps(edge))
        copy["node"]["collated_results"] = copy["node"]["collated_results"][:1]
        copy["node"]["collated_results"][0]["ad_archive_id"] = ad_id
        edges.append(copy)
    return [{"data": {"ad_library_main": {"search_results_connection": {
        "edges": edges, "page_info": {"end_cursor": next_cursor or "", "has_next_page": bool(next_cursor)}}}}}]


SEARCH = {"query": "coffee", "exact_phrase": False, "advertiser_ids": None, "country": "US", "status": "active",
          "ad_category": "ALL", "media_type": "all", "platforms": None, "languages": None, "date_from": None,
          "date_to": None, "sort": "impressions_desc", "payer": None, "regions": None, "audience_size": None}


def _walkable(monkeypatch, pages_of_more=6):
    """A search whose first page is the fixture and whose cursors are c1, c2, …: each next-page call answers 10 ads."""
    first_cursor = api.parsers.parse_list(fetch.payloads(raw("search_coffee_us_page1.json.gz")))["next_cursor"]
    order = [first_cursor] + [f"c{n}" for n in range(1, pages_of_more)]

    def more(cursor):
        at = order.index(cursor)
        following = order[at + 1] if at + 1 < len(order) else None
        return _more_page([f"9{at:02d}{n:02d}00000" for n in range(10)], following)
    return Site(monkeypatch, lists={lambda v: True: "search_coffee_us_page1.json.gz"}, more=more)


def test_search_first_page(monkeypatch):
    site = _walkable(monkeypatch)
    answer = api.ads_search(**SEARCH, page=1)
    assert list(answer) == ["query", "country", "status", "ad_category", "media_type", "sort", "is_total_capped",
                            "results", "pagination"]
    assert answer["query"] == "coffee" and answer["ad_category"] == "all" and answer["sort"] == "impressions_desc"
    assert len(answer["results"]) == 30 and answer["is_total_capped"] is True
    assert answer["pagination"] == {"page": 1, "items_per_page": 30, "has_next": True, "total_pages": None,
                                    "total_count": 50000}
    assert site.count("list") == 1 and site.count("more") == 0
    sent = site.calls[0][1]
    assert sent["queryString"] == "coffee" and sent["countries"] == ["US"] and sent["adType"] == "ALL"


def test_a_later_page_chains_three_next_page_calls(monkeypatch):
    site = _walkable(monkeypatch, pages_of_more=5)
    answer = api.ads_search(**SEARCH, page=2)
    assert len(answer["results"]) == 30 and answer["pagination"]["page"] == 2 and answer["pagination"]["has_next"] is True
    assert answer["pagination"]["total_count"] == 50000         # the total comes with the first page
    assert site.count("list") == 1 and site.count("more") == 3
    # the cursors are remembered: page 3 costs only its own calls (two: the list ends there) and no first page
    answer = api.ads_search(**SEARCH, page=3)
    assert site.count("list") == 1 and site.count("more") == 5 and len(answer["results"]) == 20
    assert answer["pagination"]["has_next"] is False            # the list ended inside the page
    # a page past the end is an empty page, with no call
    answer = api.ads_search(**SEARCH, page=4)
    assert answer["results"] == [] and answer["pagination"]["has_next"] is False and site.count("more") == 5


def test_a_cold_page_walks_forward_and_a_far_jump_is_refused(monkeypatch):
    monkeypatch.setattr(config, "FACEBOOK_AD_LIBRARY_COLD_PAGES", 2)
    site = _walkable(monkeypatch, pages_of_more=30)
    api.ads_search(**SEARCH, page=3)                            # pages 1, 2, 3 are walked
    assert site.count("list") == 1 and site.count("more") == 6
    with pytest.raises(BadRequest) as e:
        api.ads_search(**SEARCH, page=9)                        # 5 pages past the last one known
    assert "in order" in str(e.value) and site.count("more") == 6
    api.ads_search(**SEARCH, page=5)                            # 1 page past it: walked
    assert site.count("more") == 12


def test_a_cache_module_without_shared_storage_is_read_as_none(monkeypatch):
    monkeypatch.delattr(api.cache, "storage", raising=False)
    assert api._peek("facebook_ad_library.ads_search", {"page": 1}) is None


def test_a_cached_page_hands_its_cursor_to_another_process(monkeypatch):
    site = _walkable(monkeypatch, pages_of_more=30)
    rows = {}

    class Store:
        def get(self, namespace, data):
            row = rows.get((namespace, json.dumps(data, sort_keys=True)))
            return {"data": row} if row is not None else None

    def cached_call(namespace, data, fn, ttl, **kw):
        key = (namespace, json.dumps(data, sort_keys=True))
        if key not in rows:
            rows[key] = fn(**data)
        return rows[key]
    monkeypatch.setattr(api, "cached_call", cached_call)
    monkeypatch.setattr(api.cache, "storage", lambda: Store(), raising=False)
    api.ads_search(**SEARCH, page=2)
    api._cursors.clear()                                        # another process: no cursor in memory
    api._firsts.clear()
    before = site.count("more")
    answer = api.ads_search(**SEARCH, page=3)
    assert len(answer["results"]) == 30 and site.count("more") == before + 3 and site.count("list") == 1


def test_advertiser_ads(monkeypatch):
    site = Site(monkeypatch, lists={lambda v: v["viewAllPageID"] == "15087023444": "advertiser_ads_nike_de.json.gz"})
    data = {k: v for k, v in SEARCH.items() if k not in ("exact_phrase", "advertiser_ids")}
    answer = api.advertisers_ads(**{**data, "advertiser": "15087023444", "query": None, "country": "DE"}, page=1)
    assert list(answer) == ["advertiser", "query", "country", "status", "ad_category", "media_type", "sort",
                            "is_total_capped", "results", "pagination"]
    assert answer["advertiser"]["name"] == "Nike" and len(answer["results"]) == 29
    assert answer["pagination"] == {"page": 1, "items_per_page": 30, "has_next": False, "total_pages": None,
                                    "total_count": 29}
    sent = site.calls[0][1]
    assert sent["searchType"] == "page" and sent["fetchPageInfo"] is True and sent["countries"] == ["DE"]


@pytest.mark.parametrize("fixture", ["advertiser_ads_unknown_id.json.gz", "advertiser_ads_unknown_id_empty.json"])
def test_unknown_advertiser_is_not_found_and_never_the_ads_of_everyone(monkeypatch, fixture):
    Site(monkeypatch, lists={lambda v: True: fixture})
    data = {k: v for k, v in SEARCH.items() if k not in ("exact_phrase", "advertiser_ids")}
    with pytest.raises(NotFound) as e:
        api.advertisers_ads(**{**data, "advertiser": "999999999999999999", "query": None}, page=1)
    assert "999999999999999999" in str(e.value)


def test_an_alias_is_looked_up_in_the_sites_own_search(monkeypatch):
    site = Site(monkeypatch, lists={lambda v: v["viewAllPageID"] == "15087023444": "advertiser_nike.json.gz"},
                fetch_suggestions="suggestions_nike.json")
    record = api.advertiser_details(advertiser="alias:nike", country="ALL")
    assert record["id"] == "15087023444" and record["country"] == "ALL" and record["ads_count"] == 14628
    assert site.calls[0] == ("fetch_suggestions", ("nike",))
    assert site.calls[1][1]["isAboutTab"] is True and site.calls[1][1]["activeStatus"] == "all"
    with pytest.raises(NotFound) as e:
        api.advertiser_details(advertiser="alias:nikezzz", country="ALL")
    assert "advertisers/search" in str(e.value)


def test_advertiser_details_unknown(monkeypatch):
    Site(monkeypatch, lists={lambda v: True: "advertiser_unknown.json"})
    with pytest.raises(NotFound):
        api.advertiser_details(advertiser="999999999999999999", country="ALL")


def test_ad_details_joins_the_ad_and_its_details(monkeypatch):
    site = Site(monkeypatch, lists={lambda v: v["deeplinkAdID"] == "1953276048719851": "ad_by_id_political.json"},
                fetch_ad_details="ad_details_political.json")
    record = api.ad_details(ad="1953276048719851", country="US")
    keys = list(record)
    assert keys[keys.index("landing_link") + 1] == "website"
    assert keys[keys.index("political") + 1:keys.index("political") + 4] == [
        "political_insights", "advertiser_political_spend", "transparency"]
    assert record["website"] == "https://www.jpmorganchase.com"     # the display link, not the click tracker
    assert record["landing_link"].startswith("https://ad.doubleclick.net/")
    assert record["advertiser"]["alias"] == "jpmorganchase" and record["advertiser"]["likes"] > 400000
    assert record["advertiser"]["categories"] == ["Financial Service"] and record["advertiser"]["is_verified"] is True
    assert record["political"]["spend"]["min"] == 25000 and record["political_insights"]["country"] == "US"
    assert record["advertiser_political_spend"]["is_political_advertiser"] is True and record["country"] == "US"
    # the ad is asked for with a keyword no ad matches, the details as the page would for a political ad
    assert site.calls[0][1]["queryString"] == queries.NO_ADS_KEYWORD
    assert site.calls[1] == ("fetch_ad_details", ("1953276048719851", "11465191261", "US", True, False))


@pytest.mark.parametrize("fixture", ["ad_by_id_unknown.json", "ad_by_id_not_in_library.json"])
def test_ad_details_not_found(monkeypatch, fixture):
    Site(monkeypatch, lists={lambda v: True: fixture})
    with pytest.raises(NotFound) as e:
        api.ad_details(ad="1111111111111111", country="ALL")
    assert "1111111111111111" in str(e.value)


def test_ad_details_without_its_second_half_is_served_and_not_kept(monkeypatch):
    Site(monkeypatch, lists={lambda v: True: "ad_by_id.json"}, fetch_ad_details=fetch.FacebookAdLibraryUpstreamError("down"))
    kept = []
    monkeypatch.setattr(api, "cached_call", lambda namespace, data, fn, ttl, **kw: kept.append(fn(**data)) or kept[-1])
    record = api.ad_details(ad="1235897841583167", country="ALL")
    assert isinstance(record, api.DontCache) and record.data["id"] == "1235897841583167"
    assert record.data["transparency"] is None and record.data["advertiser"]["name"] == "Candlewood Suites"


def test_versions(monkeypatch):
    site = Site(monkeypatch, fetch_versions=lambda collation, country, status, cursor: (
        "versions_unknown.json" if collation == "1111111111111111" else "versions_big_page1.json.gz"))
    answer = api.ads_versions(collation_id="2414742349356964", country="ALL", status="all", page=1)
    assert list(answer) == ["collation_id", "country", "status", "results", "pagination"]
    assert len(answer["results"]) == 10
    assert answer["pagination"] == {"page": 1, "items_per_page": 10, "has_next": True, "total_pages": 16, "total_count": 160}
    answer = api.ads_versions(collation_id="2414742349356964", country="ALL", status="all", page=2)
    assert site.calls[-1][1][3].startswith("AQHT")              # page 2 opens with page 1's forward cursor
    assert answer["pagination"]["total_count"] == 160
    with pytest.raises(NotFound):
        api.ads_versions(collation_id="1111111111111111", country="ALL", status="all", page=1)


def test_advertisers_search_audience_and_filters(monkeypatch):
    Site(monkeypatch, fetch_suggestions="suggestions_nike.json", fetch_audience=lambda page_id, period: (
        "audience_unknown.json" if page_id == "999999999999999999" else "audience_jpmorgan_90d.json"),
        fetch_filters="filters_political.json.gz")
    found = api.advertisers_search(query="nike")
    assert list(found) == ["query", "count", "results", "keywords"] and found["count"] == 15
    audience = api.advertiser_audience(advertiser="11465191261", period="LAST_90_DAYS")
    assert list(audience)[:2] == ["advertiser", "period_days"] and audience["period_days"] == 90
    assert audience["ads_count"] == 138
    with pytest.raises(NotFound):
        api.advertiser_audience(advertiser="999999999999999999", period="LAST_7_DAYS")
    filters = api.ads_filters(query="election", advertiser=None, country="US", status="all",
                              ad_category="POLITICAL_AND_ISSUE_ADS", exact_phrase=False)
    assert list(filters)[:5] == ["query", "advertiser_id", "country", "status", "ad_category"]
    assert filters["ad_category"] == "political_and_issue_ads" and len(filters["payers"]) == 262

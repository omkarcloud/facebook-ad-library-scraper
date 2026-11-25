"""Offline tests for the /facebook-ad-library/* request schemas and the id /
link resolvers behind them.

    python -m pytest facebook_ad_library/test_schemas.py -q
"""
import os
import sys

import pytest
from marshmallow import ValidationError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from facebook_ad_library import refs, schemas  # noqa: E402
from schema_fields import load_query  # noqa: E402

NIKE = "15087023444"
AD = "1953276048719851"


def _errors(schema, params):
    with pytest.raises(ValidationError) as e:
        schema().load(params)
    return e.value.normalized_messages()


# ---- ad ------------------------------------------------------------------------------

@pytest.mark.parametrize("value", [
    AD,
    f" {AD} ",
    f"https://www.facebook.com/ads/library/?id={AD}",
    f"https://www.facebook.com/ads/library?id={AD}",
    f"www.facebook.com/ads/library/?id={AD}",
    f"https://web.facebook.com/ads/library/?active_status=all&id={AD}&country=US",
    f"HTTPS://WWW.FACEBOOK.COM/ads/library/?id={AD}#x",
])
def test_ad_accepts_an_id_or_a_link(value):
    assert schemas.AdSchema().load({"ad": value}) == {"ad": AD, "country": "ALL"}


@pytest.mark.parametrize("value", [
    "abc", "12", "https://www.facebook.com/nike", "https://www.facebook.com/ads/library/?q=coffee",
    f"https://example.com/ads/library/?id={AD}", "1953276048719851x",
])
def test_ad_refuses_what_names_no_ad(value):
    errors = _errors(schemas.AdSchema, {"ad": value})
    assert "Library ID" in errors["ad"][0]


def test_ad_is_required_and_country_is_checked():
    assert "ad" in _errors(schemas.AdSchema, {})
    assert schemas.AdSchema().load({"ad": AD, "country": "de"})["country"] == "DE"
    assert schemas.AdSchema().load({"ad": AD, "country": "all"})["country"] == "ALL"
    assert "country" in _errors(schemas.AdSchema, {"ad": AD, "country": "XX"})
    assert "country" in _errors(schemas.AdSchema, {"ad": AD, "country": "Germany"})


# ---- advertiser ----------------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    (NIKE, NIKE),
    (f" {NIKE} ", NIKE),
    (f"https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=ALL&search_type=page&view_all_page_id={NIKE}", NIKE),
    (f"https://www.facebook.com/{NIKE}", NIKE),
    (f"https://www.facebook.com/{NIKE}/", NIKE),
    ("https://www.facebook.com/pages/Some-Shop/112078713601998", "112078713601998"),
    ("https://www.facebook.com/Brandon-Duensing-315614482659977/", "315614482659977"),
    ("https://www.facebook.com/nike", "alias:nike"),
    ("https://www.facebook.com/Nike/", "alias:nike"),
    ("facebook.com/nike?ref=x", "alias:nike"),
    ("https://m.facebook.com/jpmorganchase/about", "alias:jpmorganchase"),
    ("nike", "alias:nike"),
    ("JPMorganChase", "alias:jpmorganchase"),
    ("better.sleep.secret", "alias:better.sleep.secret"),
])
def test_advertiser_accepts_an_id_a_link_or_an_alias(value, expected):
    assert schemas.AdvertiserSchema().load({"advertiser": value}) == {"advertiser": expected, "country": "ALL"}


@pytest.mark.parametrize("value, word", [
    ("https://www.facebook.com/profile.php?id=100079655510355", "profile id"),
    ("https://www.facebook.com/people/Better-sleep/100079655510355/", "profile id"),
    ("https://www.facebook.com/ads/library/?q=coffee&search_type=keyword_unordered", "names no advertiser"),
    ("https://www.facebook.com/", "names no page"),
    ("https://www.facebook.com/groups/123456789", "names no page"),
    ("better sleep co", "page id"),
    ("https://example.com/nike", "page id"),
    ("1", "page id"),
    ("a", "page id"),
])
def test_advertiser_refuses_with_a_message_that_says_what_to_pass(value, word):
    message = _errors(schemas.AdvertiserSchema, {"advertiser": value})["advertiser"][0]
    assert word in message and "advertisers/search" in message


def test_alias_of():
    assert refs.alias_of("alias:nike") == "nike" and refs.alias_of(NIKE) is None and refs.alias_of(None) is None


# ---- ads/search ----------------------------------------------------------------------

def test_search_defaults():
    assert schemas.AdsSearchSchema().load({"query": " coffee "}) == {
        "query": "coffee", "exact_phrase": False, "advertiser_ids": None, "country": "ALL", "status": "active",
        "ad_category": "ALL", "media_type": "all", "platforms": None, "languages": None, "date_from": None,
        "date_to": None, "sort": "impressions_desc", "payer": None, "regions": None, "audience_size": None, "page": 1}


def test_search_filters():
    loaded = schemas.AdsSearchSchema().load({
        "query": "coffee", "country": "us", "status": "INACTIVE", "ad_category": "political_and_issue_ads",
        "media_type": "Video", "platforms": "threads, Instagram,instagram", "languages": "ES,en", "date_from": "2026-09-01",
        "date_to": "2026-09-30", "exact_phrase": "true", "sort": "impressions_asc", "advertiser_ids": f"{NIKE}, 11465191261",
        "payer": "JPMORGAN CHASE BANK, NA", "regions": "California, Alabama", "audience_size": "1k-5k,100-1k", "page": "3"})
    assert loaded == {
        "query": "coffee", "country": "US", "status": "inactive", "ad_category": "POLITICAL_AND_ISSUE_ADS",
        "media_type": "video", "platforms": ["instagram", "threads"], "languages": ["en", "es"],
        "date_from": "2026-09-01", "date_to": "2026-09-30", "exact_phrase": True, "sort": "impressions_asc",
        "advertiser_ids": ["11465191261", NIKE], "payer": "JPMORGAN CHASE BANK, NA",
        "regions": ["Alabama", "California"],
        "audience_size": [{"min": 100, "max": 1000}, {"min": 1000, "max": 5000}], "page": 3}


@pytest.mark.parametrize("category, token", list(refs.AD_CATEGORIES.items()))
def test_every_ad_category(category, token):
    assert schemas.AdsSearchSchema().load({"query": "x", "ad_category": category})["ad_category"] == token


@pytest.mark.parametrize("media_type", refs.MEDIA_TYPES)
def test_every_media_type(media_type):
    assert schemas.AdsSearchSchema().load({"query": "x", "media_type": media_type})["media_type"] == media_type


@pytest.mark.parametrize("platform", refs.PLATFORMS)
def test_every_platform(platform):
    assert schemas.AdsSearchSchema().load({"query": "x", "platforms": platform})["platforms"] == [platform]


@pytest.mark.parametrize("size", list(refs.AUDIENCE_SIZES))
def test_every_audience_size(size):
    loaded = schemas.AdsSearchSchema().load({"query": "x", "ad_category": "political_and_issue_ads", "audience_size": size})
    assert loaded["audience_size"] == [refs.AUDIENCE_SIZES[size]]


def test_search_takes_advertisers_without_a_keyword():
    loaded = schemas.AdsSearchSchema().load({"advertiser_ids": NIKE})
    assert loaded["query"] is None and loaded["advertiser_ids"] == [NIKE]


@pytest.mark.parametrize("params, field, word", [
    ({}, "query", "keyword"),
    ({"query": ""}, "query", "keyword"),
    ({"exact_phrase": "true", "advertiser_ids": NIKE}, "exact_phrase", "keyword"),
    ({"query": "x", "status": "paused"}, "status", "active"),
    ({"query": "x", "ad_category": "cars"}, "ad_category", "housing_ads"),
    ({"query": "x", "media_type": "gif"}, "media_type", "image_and_meme"),
    ({"query": "x", "platforms": "tiktok"}, "platforms", "'tiktok' is not one of"),
    ({"query": "x", "languages": "english"}, "languages", "language code"),
    ({"query": "x", "country": "XX"}, "country", "2-letter"),
    ({"query": "x", "country": "US,GB"}, "country", "2-letter"),
    ({"query": "x", "sort": "newest"}, "sort", "most_recent"),
    ({"query": "x", "sort": "impressions_asc"}, "sort", "political"),
    ({"query": "x", "payer": "ACME"}, "payer", "political"),
    ({"query": "x", "regions": "California"}, "regions", "political"),
    ({"query": "x", "audience_size": "100-1k"}, "audience_size", "political"),
    ({"query": "x", "ad_category": "political_and_issue_ads", "audience_size": "huge"}, "audience_size", "is not one of"),
    ({"query": "x", "advertiser_ids": "nike"}, "advertiser_ids", "page id"),
    ({"query": "x", "advertiser_ids": ",".join(str(10 ** 10 + n) for n in range(51))}, "advertiser_ids", "At most 50"),
    ({"query": "x", "date_from": "01/09/2026"}, "date_from", "YYYY-MM-DD"),
    ({"query": "x", "date_from": "2026-10-01", "date_to": "2026-09-01"}, "date_to", "before"),
    ({"query": "x", "page": "0"}, "page", ""),
    ({"query": "x", "page": str(schemas.MAX_PAGE + 1)}, "page", ""),
    ({"query": "x", "limit": "5"}, "limit", "Unknown"),
])
def test_search_refuses(params, field, word):
    errors = _errors(schemas.AdsSearchSchema, params)
    assert field in errors and word in " ".join(errors[field])


def test_fifty_advertisers_pass():
    ids = ",".join(str(10 ** 10 + n) for n in range(50))
    assert len(schemas.AdsSearchSchema().load({"advertiser_ids": ids})["advertiser_ids"]) == 50


LINK = ("https://www.facebook.com/ads/library/?active_status=inactive&ad_type=all&country=GB&is_targeted_country=false"
        "&media_type=video&q=%22coffee%20maker%22&search_type=keyword_exact_phrase&start_date[min]=2026-09-01"
        "&start_date[max]=2026-09-30&publisher_platforms[0]=facebook&publisher_platforms[1]=instagram"
        "&content_languages[0]=en&sort_data[mode]=relevancy_monthly_grouped&sort_data[direction]=desc")


def test_search_reads_an_ad_library_link():
    loaded = schemas.AdsSearchSchema().load({"query": LINK})
    assert loaded["query"] == "coffee maker" and loaded["exact_phrase"] is True
    assert loaded["country"] == "GB" and loaded["status"] == "inactive" and loaded["media_type"] == "video"
    assert loaded["platforms"] == ["facebook", "instagram"] and loaded["languages"] == ["en"]
    assert loaded["date_from"] == "2026-09-01" and loaded["date_to"] == "2026-09-30"
    assert loaded["sort"] == "most_recent" and loaded["ad_category"] == "ALL"


def test_the_callers_own_params_win_over_the_links():
    loaded = schemas.AdsSearchSchema().load({"query": LINK, "country": "US", "status": "all", "media_type": "image"})
    assert loaded["country"] == "US" and loaded["status"] == "all" and loaded["media_type"] == "image"
    assert loaded["query"] == "coffee maker"


def test_search_reads_a_political_advertiser_link():
    link = ("https://www.facebook.com/ads/library/?active_status=all&ad_type=political_and_issue_ads&country=US"
            "&media_type=all&search_type=page&view_all_page_id=11465191261&sort_data[mode]=total_impressions"
            "&sort_data[direction]=asc&regions[0]=California")
    loaded = schemas.AdsSearchSchema().load({"query": link})
    assert loaded["query"] is None and loaded["advertiser_ids"] == ["11465191261"]
    assert loaded["ad_category"] == "POLITICAL_AND_ISSUE_ADS" and loaded["sort"] == "impressions_asc"
    assert loaded["regions"] == ["California"] and loaded["status"] == "all"


def test_a_link_to_one_ad_is_sent_to_ads_details():
    errors = _errors(schemas.AdsSearchSchema, {"query": f"https://www.facebook.com/ads/library/?id={AD}"})
    assert "ads/details" in errors["query"][0]


def test_link_params():
    assert refs.link_params("coffee") == {}
    assert refs.link_params("https://example.com/ads/library/?q=x") == {}
    assert refs.link_params("https://www.facebook.com/ads/library/?ad_type=financial_products_and_services_ads&q=loan") == {
        "query": "loan", "ad_category": "credit_ads"}
    assert refs.is_library_link(LINK) and not refs.is_library_link("https://www.facebook.com/nike")


# ---- advertisers/ads -----------------------------------------------------------------

def test_advertiser_ads_defaults_and_keyword():
    loaded = schemas.AdvertisersAdsSchema().load({"advertiser": "nike", "query": "running", "country": "de"})
    assert loaded["advertiser"] == "alias:nike" and loaded["query"] == "running" and loaded["country"] == "DE"
    assert loaded["status"] == "active" and loaded["page"] == 1 and "exact_phrase" not in loaded


def test_advertiser_ads_reads_the_filters_of_a_link():
    link = ("https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=IN&media_type=image"
            f"&search_type=page&view_all_page_id={NIKE}&q=shoes")
    loaded = schemas.AdvertisersAdsSchema().load({"advertiser": link})
    assert loaded["advertiser"] == NIKE and loaded["country"] == "IN" and loaded["status"] == "all"
    assert loaded["media_type"] == "image" and loaded["query"] == "shoes"


@pytest.mark.parametrize("params, field", [
    ({}, "advertiser"),
    ({"advertiser": NIKE, "media_type": "gif"}, "media_type"),
    ({"advertiser": NIKE, "sort": "impressions_asc"}, "sort"),
    ({"advertiser": NIKE, "advertiser_ids": NIKE}, "advertiser_ids"),
    ({"advertiser": NIKE, "exact_phrase": "true"}, "exact_phrase"),
])
def test_advertiser_ads_refuses(params, field):
    assert field in _errors(schemas.AdvertisersAdsSchema, params)


# ---- ads/versions --------------------------------------------------------------------

def test_versions():
    assert schemas.AdsVersionsSchema().load({"collation_id": "1007223439092800"}) == {
        "collation_id": "1007223439092800", "country": "ALL", "status": "all", "page": 1}
    assert "collation id" in _errors(schemas.AdsVersionsSchema, {"collation_id": "abc"})["collation_id"][0]
    assert "status" in _errors(schemas.AdsVersionsSchema, {"collation_id": "1007223439092800", "status": "x"})
    assert "collation_id" in _errors(schemas.AdsVersionsSchema, {})


# ---- advertisers/search, audience ----------------------------------------------------

def test_advertisers_search():
    assert schemas.AdvertisersSearchSchema().load({"query": " nike "}) == {"query": "nike"}
    assert "query" in _errors(schemas.AdvertisersSearchSchema, {})
    assert "query" in _errors(schemas.AdvertisersSearchSchema, {"query": "x" * 101})


@pytest.mark.parametrize("days, token", list(refs.PERIODS.items()))
def test_audience_periods(days, token):
    assert schemas.AdvertiserAudienceSchema().load({"advertiser": NIKE, "period": days}) == {
        "advertiser": NIKE, "period": token}


def test_audience_defaults_and_refusals():
    assert schemas.AdvertiserAudienceSchema().load({"advertiser": NIKE})["period"] == "LAST_7_DAYS"
    assert "7, 30, 90" in _errors(schemas.AdvertiserAudienceSchema, {"advertiser": NIKE, "period": "365"})["period"][0]
    assert "country" in _errors(schemas.AdvertiserAudienceSchema, {"advertiser": NIKE, "country": "US"})


# ---- ads/filters ---------------------------------------------------------------------

def test_filters_take_a_keyword_or_an_advertiser():
    assert schemas.AdsFiltersSchema().load({"query": "coffee", "country": "us"}) == {
        "query": "coffee", "advertiser": None, "country": "US", "status": "active", "ad_category": "ALL",
        "exact_phrase": False}
    assert schemas.AdsFiltersSchema().load({"advertiser": "nike"})["advertiser"] == "alias:nike"
    assert "query" in _errors(schemas.AdsFiltersSchema, {})
    assert "query" in _errors(schemas.AdsFiltersSchema, {"query": "coffee", "advertiser": NIKE})


# ---- the 400 body --------------------------------------------------------------------

def test_load_query_names_the_field():
    data, body = load_query(schemas.AdsSearchSchema, {"query": "coffee", "status": "paused"})
    assert data is None and "status" in body["errors"] and body["error"].startswith("Invalid parameters")
    data, body = load_query(schemas.AdSchema, {"ad": AD})
    assert body is None and data == {"ad": AD, "country": "ALL"}


def test_every_field_has_a_description():
    for name in dir(schemas):
        schema = getattr(schemas, name)
        if isinstance(schema, type) and issubclass(schema, schemas.BaseSchema) and not name.startswith("_") \
                and schema is not schemas.BaseSchema:
            for field_name, field in schema().fields.items():
                assert field.metadata.get("description"), f"{name}.{field_name}"


# ---- links ---------------------------------------------------------------------------

@pytest.mark.parametrize("link, website", [
    ("https://secure.getquietlab.com/sp-m1?utm=1", "https://secure.getquietlab.com"),
    ("SHOP.COM", "https://shop.com"),
    ("WWW.JPMORGANCHASE.COM/communities/new-york", "https://www.jpmorganchase.com"),
    ("http://example.org/", "http://example.org"),
    ("https://ad.doubleclick.net/ddm/trackclk/N5762", None),
    ("https://www.facebook.com/nike", None),
    ("https://www.instagram.com/", None),
    ("fb.me/abc", None),
    ("https://apps.apple.com/us/app/x/id1", None),
    ("https://play.google.com/store/apps/details?id=x", None),
    ("https://bit.ly/3abc", None),
    ("Opens Instagram", None),
    ("Learn more", None),
    ("", None),
    (None, None),
])
def test_website_of(link, website):
    assert refs.website_of(link) == website


def test_built_links():
    assert refs.ad_link(AD) == f"https://www.facebook.com/ads/library/?id={AD}" and refs.ad_link(None) is None
    assert refs.advertiser_library_link(NIKE).endswith(f"search_type=page&view_all_page_id={NIKE}")
    assert refs.resolve_advertiser(refs.advertiser_library_link(NIKE)) == NIKE
    assert refs.resolve_ad(refs.ad_link(AD)) == AD

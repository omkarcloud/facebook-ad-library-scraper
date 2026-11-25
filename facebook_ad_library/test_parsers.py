"""Offline tests for facebook_ad_library/parsers.py: every parser against
every fixture (a full answer, a sparse one, and what an unknown id or an
empty answer becomes).

    python -m pytest facebook_ad_library/test_parsers.py -q
"""
import gzip
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from facebook_ad_library import fetch, parsers  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

AD_KEYS = [
    "id", "link", "is_active", "start_date", "end_date", "total_active_seconds", "format", "platforms", "categories",
    "title", "body", "caption", "link_description", "landing_link", "cta", "images", "videos", "cards", "extra_texts",
    "extra_links", "extra_images", "extra_videos", "event", "versions", "advertiser", "branded_content_partner",
    "reshared_from", "political", "state_media_label", "targeted_or_reached_countries", "gated_type", "hidden_data",
    "is_reshared", "is_sensitive", "has_ai_generated_media", "is_eu_transparency_eligible", "regulation",
    "brazil_tax_id", "ec_certificates", "additional_info",
]
LIST_FIXTURES = ["search_coffee_us_page1.json.gz", "search_coffee_us_more.json.gz", "search_political_coffee_us.json.gz",
                 "search_political_election_inactive.json.gz", "search_political_single_page.json.gz", "search_text_ads.json.gz",
                 "search_housing.json.gz", "advertiser_ads_nike_de.json.gz", "advertiser_ads_nike_us.json.gz"]


def raw(name):
    """A fixture's raw body; a large one is kept packed (<name>.json.gz)."""
    path = os.path.join(FX, name)
    if name.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return f.read()
    with open(path, encoding="utf-8") as f:
        return f.read()


def documents(name):
    return fetch.payloads(raw(name))


def ads_of(name):
    return parsers.parse_list(documents(name))["ads"]


def by_id(ads, ad_id):
    return next(ad for ad in ads if ad["id"] == ad_id)


# ---- the list ------------------------------------------------------------------------

def test_first_page_of_a_search():
    parsed = parsers.parse_list(documents("search_coffee_us_page1.json.gz"))
    assert len(parsed["ads"]) == 30
    assert parsed["total_count"] == 50000 and parsed["is_total_capped"] is True      # the site says 50,001
    assert parsed["next_cursor"].startswith("AQHT")
    assert parsed["advertiser"] is None                 # a keyword search is of no one advertiser


def test_next_page_has_no_total():
    parsed = parsers.parse_list(documents("search_coffee_us_more.json.gz"))
    assert len(parsed["ads"]) == 10
    assert parsed["total_count"] is None and parsed["is_total_capped"] is None
    assert parsed["next_cursor"]


def test_a_list_that_ends_has_no_cursor():
    parsed = parsers.parse_list(documents("search_political_single_page.json.gz"))
    assert len(parsed["ads"]) == 13 and parsed["total_count"] == 13 and parsed["is_total_capped"] is False
    assert parsed["next_cursor"] is None


@pytest.mark.parametrize("name", ["search_no_result.json", "advertiser_ads_no_result.json"])
def test_no_result(name):
    parsed = parsers.parse_list(documents(name))
    assert parsed["ads"] == [] and parsed["total_count"] == 0 and parsed["next_cursor"] is None


@pytest.mark.parametrize("name", LIST_FIXTURES)
def test_every_ad_has_every_key_in_order(name):
    ads = ads_of(name)
    assert ads
    for ad in ads:
        assert list(ad) == AD_KEYS
        assert re.fullmatch(r"\d{5,20}", ad["id"])
        assert ad["link"] == f"https://www.facebook.com/ads/library/?id={ad['id']}"
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", ad["start_date"])
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", ad["end_date"])
        assert ad["start_date"] <= ad["end_date"]
        assert isinstance(ad["is_active"], bool)
        assert ad["advertiser"]["id"] and ad["advertiser"]["name"]
        assert all(platform == platform.lower() for platform in ad["platforms"])
        for text in (ad["title"], ad["body"], ad["caption"], ad["link_description"]):
            assert text is None or not re.fullmatch(r"\s*\{\{[^{}]*\}\}\s*", text)     # no bare template


def test_image_ad():
    ad = by_id(ads_of("search_coffee_us_page1.json.gz"), "2006807903279406")
    assert ad["format"] == "image" and ad["is_active"] is True
    assert ad["start_date"] == "2026-05-26" and ad["end_date"] == "2026-11-24"
    assert ad["title"] == "Natural Relief in Weeks"
    assert ad["cta"] == {"text": "Learn more", "type": "learn_more"}
    assert ad["platforms"] == ["facebook", "instagram", "audience_network", "whatsapp", "threads"]
    assert len(ad["images"]) == 1 and ad["videos"] == [] and ad["cards"] == []
    assert ad["images"][0]["link"].startswith("https://") and ad["images"][0]["resized_link"].startswith("https://")
    assert ad["versions"] == {"collation_id": "1537692291702770", "count": 1}
    assert ad["advertiser"]["name"] == "Cholesterol Relief Community" and ad["advertiser"]["likes"] == 3581
    assert ad["advertiser"]["ad_library_link"].endswith("view_all_page_id=" + ad["advertiser"]["id"])
    assert ad["categories"] == [] and ad["political"] is None       # the site's UNKNOWN category is no category
    assert ad["gated_type"] == "eligible" and ad["hidden_data"] is None


def test_video_ad():
    ad = by_id(ads_of("search_coffee_us_page1.json.gz"), "27086586400970202")
    assert ad["format"] == "video" and ad["title"] == "Muse: Best Reels Maker"
    assert ad["cta"] == {"text": "Install now", "type": "install_mobile_app"}
    assert len(ad["videos"]) == 1 and ad["images"] == []
    video = ad["videos"][0]
    assert list(video) == ["hd_link", "sd_link", "preview_image_link", "watermarked_hd_link", "watermarked_sd_link"]
    assert video["sd_link"].startswith("https://") and video["preview_image_link"].startswith("https://")


def test_catalogue_and_carousel_ads_keep_their_media_in_cards():
    ads = ads_of("search_coffee_us_page1.json.gz")
    dpa = by_id(ads, "3306463819563531")
    assert dpa["format"] == "dpa" and len(dpa["cards"]) == 6 and dpa["images"] == [] and dpa["videos"] == []
    assert dpa["versions"] is None                      # a catalogue ad is in no group of versions
    card = dpa["cards"][0]
    assert list(card) == ["title", "body", "caption", "landing_link", "link_description", "cta", "image_link",
                          "resized_image_link", "video_hd_link", "video_sd_link", "video_preview_image_link"]
    assert card["image_link"].startswith("https://") and card["landing_link"].startswith("http")
    carousel = by_id(ads, "829955406539096")
    assert carousel["format"] == "carousel" and len(carousel["cards"]) == 5
    assert carousel["cta"] == {"text": "Book now", "type": "book_travel"}
    dco = by_id(ads, "3760007617611979")
    assert dco["format"] == "dco" and len(dco["cards"]) == 10
    assert dco["versions"] == {"collation_id": "1141703816972945", "count": 2}
    # only the first ad of a group carries the group's size
    assert by_id(ads, "27471746629083301")["versions"] == {"collation_id": "1141703816972945", "count": None}


def test_a_template_text_is_answered_with_the_first_cards():
    ad = by_id(ads_of("search_political_coffee_us.json.gz"), "1953276048719851")
    # raw: title "{{product.name}}", body "{{product.brand}}", link_description "{{product.description}}"
    assert '"{{product.brand}}"' in raw("search_political_coffee_us.json.gz")
    assert ad["title"] == ad["cards"][0]["title"] == "Bringing entrepreneurs’ big dreams to life"
    assert ad["body"] == ad["cards"][0]["body"] and ad["body"].startswith("From immersive slime experiences")
    assert ad["link_description"] is None               # the card has none either


def test_text_ad_is_sparse():
    ad = ads_of("search_text_ads.json.gz")[0]
    assert ad["id"] == "1471273230509485" and ad["format"] == "text"
    assert ad["title"] == "Chat With Us" and ad["body"] is None
    assert ad["images"] == [] and ad["videos"] == [] and ad["cards"] == []
    assert ad["cta"] == {"text": "Send message", "type": "instagram_message"}
    assert {ad["format"] for ad in ads_of("search_text_ads.json.gz")} == {"text"}


def test_political_ad():
    ad = by_id(ads_of("search_political_coffee_us.json.gz"), "1953276048719851")
    assert ad["categories"] == ["credit", "employment", "political"]
    political = ad["political"]
    assert list(political) == ["payer", "disclaimer_label", "country", "currency", "spend", "impressions",
                               "audience_size", "payer_info"]
    assert political["payer"] == "JPMORGAN CHASE BANK, NA" and political["disclaimer_label"] == "paid_for_by"
    assert political["country"] == "US" and political["currency"] == "USD"
    assert political["spend"] == {"text": "$25K-$30K", "min": 25000, "max": 30000}
    assert political["impressions"] == {"text": ">1M", "min": 1000000, "max": None}
    assert political["audience_size"] == {"text": "100K-500K", "min": 100000, "max": 500000}
    info = political["payer_info"]
    assert info["website"] == "http://www.jpmorgan.com/" and info["email"] == "jpm.social.media@jpmorgan.com"
    assert info["phone"] == "+12122706000" and info["address"] == "1111 POLARIS PARKWAY, COLUMBUS, OH 43240, US"
    assert info["submitted_on"] == "2022-03-07"         # raw: "Submitted on 7 Mar 2022"
    assert info["verification"].startswith("This information was confirmed")


def test_the_en_us_forms_the_scraper_asks_for():
    # the same ad as above, answered in en_US through a Korean exit (the language pinned by fetch.py)
    ad = parsers.parse_ad_by_id(documents("ad_by_id_political_en_us.json"))
    assert '"spend":"$25K - $30K"' in raw("ad_by_id_political_en_us.json").replace('": "', '":"')
    assert "Submitted Mar 7, 2022" in raw("ad_by_id_political_en_us.json")
    assert ad["political"]["spend"] == {"text": "$25K - $30K", "min": 25000, "max": 30000}
    assert ad["political"]["impressions"] == {"text": ">1M", "min": 1000000, "max": None}
    assert ad["political"]["payer_info"]["submitted_on"] == "2022-03-07"
    assert ad["cta"] == {"text": "Learn more", "type": "learn_more"}
    more = parsers.parse_list(documents("search_political_more_en_us.json.gz"))
    assert len(more["ads"]) == 10 and more["total_count"] is None
    assert all(ad["political"]["payer_info"]["submitted_on"][:2] == "20" for ad in more["ads"] if ad["political"]["payer_info"])
    assert all(ad["political"]["spend"]["min"] is not None or ad["political"]["spend"]["max"] is not None
               for ad in more["ads"] if ad["political"]["spend"])
    assert all(ad["cta"]["text"].isascii() for ad in more["ads"] if ad["cta"])       # English button labels


def test_political_ad_formats_and_lead_form_texts():
    ads = ads_of("search_political_coffee_us.json.gz")
    multi = by_id(ads, "1995369571170192")
    assert multi["format"] == "multi_images" and len(multi["images"]) == 7 and multi["landing_link"] is None
    form = by_id(ads, "2481942888882786")
    assert form["extra_texts"][:2] == ["Add Your Name Now", "First name"]
    assert form["extra_links"][0].startswith("https://firstliberty.org/paigerogers/")


def test_taken_down_and_sign_in_only_ads():
    ads = ads_of("search_political_election_inactive.json.gz")
    taken_down = by_id(ads, "352695862528588")
    assert taken_down["gated_type"] == "taken_down" and taken_down["is_active"] is False
    assert taken_down["start_date"] == "2020-08-27" and taken_down["end_date"] == "2020-08-30"
    assert taken_down["political"]["spend"] == {"text": ">$1M", "min": 1000000, "max": None}
    hidden = by_id(ads, "1572020043006144")
    assert hidden["gated_type"] == "logged_out" and hidden["format"] is None
    assert hidden["images"] == [] and hidden["videos"] == [] and hidden["cards"] == []
    assert hidden["political"]["payer"] == "CONSTELLATION BRANDS U.S. OPERATIONS, INC."
    assert hidden["political"]["disclaimer_label"] is None
    assert {ad["gated_type"] for ad in ads} >= {"eligible", "taken_down", "disclaimer_review", "logged_out"}


def test_special_category_ads():
    ads = ads_of("search_housing.json.gz")
    assert all("housing" in ad["categories"] for ad in ads)       # an ad can be in several (housing + credit)
    ad = by_id(ads, "1123588993873001")
    assert ad["format"] == "carousel" and ad["cards"][0]["caption"] == "drhorton.com"
    assert ad["cards"][0]["cta"] == {"text": "Learn More", "type": "learn_more"}


def test_advertiser_list_carries_its_card():
    parsed = parsers.parse_list(documents("advertiser_ads_nike_de.json.gz"))
    card = parsed["advertiser"]
    assert list(card) == ["id", "name", "link", "ad_library_link", "picture_link", "is_deleted", "is_actioned",
                          "hidden_ads_count", "has_blank_ads", "related_pages"]
    assert card["id"] == "15087023444" and card["name"] == "Nike" and card["link"] == "https://facebook.com/nike"
    assert card["is_deleted"] is False and card["hidden_ads_count"] == 0 and card["related_pages"] == []
    assert parsed["total_count"] == 29 and len(parsed["ads"]) == 29 and parsed["next_cursor"] is None
    assert all(ad["advertiser"]["id"] == "15087023444" or ad["branded_content_partner"] for ad in parsed["ads"])


def test_branded_content_partner():
    ad = by_id(ads_of("advertiser_ads_nike_de.json.gz"), "2219063555621382")
    partner = ad["branded_content_partner"]
    assert list(partner) == ["id", "name", "link", "ad_library_link", "is_deleted"]
    assert partner["id"] == "15087023444" and partner["name"] == "Nike" and partner["link"] == "https://www.facebook.com/nike/"
    assert ad["is_eu_transparency_eligible"] is True
    # the ad is listed under the brand and was run by the creator: the advertiser is the creator
    assert ad["advertiser"]["id"] != "15087023444" and ad["advertiser"]["name"] != "Nike"
    raw_ad = next(row for document in documents("advertiser_ads_nike_de.json.gz")
                  for edge in ((document.get("data") or {}).get("ad_library_main") or {}).get(
                      "search_results_connection", {}).get("edges", [])
                  for row in edge["node"]["collated_results"] if row["ad_archive_id"] == "2219063555621382")
    assert raw_ad["page_id"] == "15087023444" and raw_ad["snapshot"]["page_id"] == ad["advertiser"]["id"]


def test_extra_videos_name_their_files_differently():
    ad = next(ad for ad in ads_of("advertiser_ads_nike_de.json.gz") if ad["extra_videos"])
    assert ad["extra_videos"][0]["hd_link"].startswith("https://video") and ad["extra_videos"][0]["sd_link"].startswith("https://video")


def test_unknown_advertiser_has_no_card():
    # an id the site refuses: its card errors, and the list is of EVERY advertiser
    refused = parsers.parse_list(documents("advertiser_ads_unknown_id.json.gz"))
    assert refused["advertiser"] is None and len(refused["ads"]) == 30 and refused["is_total_capped"] is True
    # an id of no page: no card, no ads
    unknown = parsers.parse_list(documents("advertiser_ads_unknown_id_empty.json"))
    assert unknown["advertiser"] is None and unknown["ads"] == [] and unknown["total_count"] == 0
    # a known advertiser with no ad that matches still has its card
    assert parsers.parse_list(documents("advertiser_ads_no_result.json"))["advertiser"]["name"] == "Nike"


# ---- one ad --------------------------------------------------------------------------

def test_ad_by_id():
    ad = parsers.parse_ad_by_id(documents("ad_by_id.json"))
    assert list(ad) == AD_KEYS
    assert ad["id"] == "1235897841583167" and ad["format"] == "video"
    assert ad["advertiser"]["name"] == "Candlewood Suites" and ad["start_date"] == "2026-02-11"
    assert ad["versions"] is None                       # the site names no group for an ad asked for by id
    assert parsers.parse_list(documents("ad_by_id.json"))["ads"] == []       # the list around it is empty


def test_ad_by_id_sign_in_only():
    ad = parsers.parse_ad_by_id(documents("ad_by_id_gated.json"))
    assert ad["id"] == "1087458747581725" and ad["gated_type"] == "logged_out"
    assert ad["format"] is None and ad["title"] is None and ad["landing_link"] is None and ad["cta"] is None
    assert ad["body"].startswith("Sensodyne Clinical Repair")
    assert ad["advertiser"]["name"] == "Sensodyne US" and ad["advertiser"]["likes"] is None


def test_ad_by_id_eu_and_political():
    eu = parsers.parse_ad_by_id(documents("ad_by_id_eu.json"))
    assert eu["id"] == "1143404374711391" and eu["format"] == "dpa" and eu["is_eu_transparency_eligible"] is True
    political = parsers.parse_ad_by_id(documents("ad_by_id_political.json"))
    assert political["id"] == "1953276048719851" and "political" in political["categories"]
    assert political["political"]["spend"]["min"] == 25000


@pytest.mark.parametrize("name", ["ad_by_id_unknown.json", "ad_by_id_not_in_library.json"])
def test_ad_by_id_not_found(name):
    # NO_AD_FOUND for an id that never was, NOT_IN_LIBRARY for an ad that left the library
    assert parsers.parse_ad_by_id(documents(name)) is None


def test_ad_details_plain():
    details = parsers.parse_ad_details(documents("ad_details.json"))
    assert list(details) == ["advertiser", "political_spend", "transparency", "political_insights"]
    advertiser = details["advertiser"]
    assert list(advertiser) == ["id", "name", "link", "ad_library_link", "alias", "category", "entity_type",
                                "verification", "is_verified", "likes", "about", "picture_link", "cover_photo_link",
                                "instagram", "is_deleted", "is_restricted", "is_profile_page", "is_delegate_page"]
    assert advertiser["name"] == "Candlewood Suites" and advertiser["is_deleted"] is False
    assert details["political_spend"] is None and details["political_insights"] is None
    transparency = details["transparency"]
    assert transparency["eu"] is None and transparency["uk"] is None and transparency["payers"] == []
    assert transparency["violations"] == []


def test_ad_details_eu():
    details = parsers.parse_ad_details(documents("ad_details_eu.json"))
    assert details["advertiser"]["name"] == "Nike" and details["advertiser"]["alias"] == "nike"
    assert details["advertiser"]["is_verified"] is True and details["advertiser"]["verification"] == "blue_verified"
    assert details["advertiser"]["instagram"]["username"] == "nike"
    transparency = details["transparency"]
    assert transparency["payers"] == [{"payer": "NIKE, Inc.", "beneficiary": "NIKE, Inc."}]
    assert transparency["is_targeting_eu"] is True and transparency["is_taken_down"] is False
    eu = transparency["eu"]
    assert eu["is_targeted"] is True and eu["total_reach"] == 11079004
    assert eu["audience"]["gender"] == "All" and eu["audience"]["age_min"] == 18 and eu["audience"]["age_max"] == 65
    assert [row["name"] for row in eu["audience"]["locations"]] == ["Italy", "Netherlands", "Spain", "France", "Germany"]
    assert eu["audience"]["locations"][0] == {"name": "Italy", "type": "countries", "is_excluded": False, "obfuscated_count": 0}
    germany = next(row for row in eu["reach_by_country"] if row["country"] == "DE")
    assert germany["breakdown"][0] == {"age_range": "18-24", "male": 243730, "female": 164611, "unknown": 12163}
    uk = transparency["uk"]
    assert uk["total_reach"] > 0 and uk["reach_by_country"][0]["country"] == "GB" and uk["is_targeted"] is None
    # the advertiser's political spend in the country asked (Germany), per "Paid for by" name
    assert details["political_spend"]["lifetime"] == [{"payer": None, "amount": 114}, {"payer": "EDITION F", "amount": 50}]


def test_ad_details_political():
    details = parsers.parse_ad_details(documents("ad_details_political.json"))
    insights = details["political_insights"]
    assert insights["country"] == "US" and insights["is_currency_matched"] is True
    assert insights["by_age_and_gender"][0] == {"age_range": "18-24", "male": 0.03, "female": 0.03, "unknown": 0}
    assert len(insights["by_region"]) == 52 and insights["by_region"][0] == {"region": "Alabama", "share": 0}
    spend = details["political_spend"]
    assert spend["is_political_advertiser"] is True
    assert {"payer": "JPMORGAN CHASE BANK, NA", "amount": 15991085} in spend["lifetime"]
    assert spend["weekly"] == [{"payer": "JPMORGAN CHASE BANK, NA", "amount": 271081}]
    assert details["transparency"]["verified_voice_types"] == ["political"]
    assert details["transparency"]["is_violating_eu_rules"] is False


def test_ad_details_of_an_ad_the_site_no_longer_has():
    # the advertiser still answers; the transparency block errors and is null
    details = parsers.parse_ad_details(documents("ad_details_unknown_ad.json"))
    assert details["advertiser"]["name"] == "Better sleep"
    assert details["transparency"]["eu"] is None and details["transparency"]["is_violating_eu_rules"] is None


# ---- versions ------------------------------------------------------------------------

def test_versions():
    parsed = parsers.parse_versions(documents("versions_page1.json"))
    assert [ad["id"] for ad in parsed["ads"]] == ["2058358294867841", "1860187655250608", "2675784232925894",
                                                  "1013608031781929"]
    assert parsed["total_count"] == 4 and parsed["next_cursor"] is None
    assert all(list(ad) == AD_KEYS for ad in parsed["ads"])
    assert all(ad["versions"] == {"collation_id": "1007223439092800", "count": None} for ad in parsed["ads"])


def test_versions_with_more_pages():
    parsed = parsers.parse_versions(documents("versions_big_page1.json.gz"))
    assert len(parsed["ads"]) == 10 and parsed["total_count"] == 160 and parsed["next_cursor"].startswith("AQHT")


def test_versions_unknown():
    assert parsers.parse_versions(documents("versions_unknown.json")) is None


# ---- advertisers ---------------------------------------------------------------------

def test_suggestions():
    parsed = parsers.parse_suggestions(documents("suggestions_nike.json"))
    assert len(parsed["advertisers"]) == 15
    first = parsed["advertisers"][0]
    assert list(first) == ["id", "name", "link", "ad_library_link", "alias", "category", "entity_type", "verification",
                           "is_verified", "likes", "country", "picture_link", "instagram", "is_deleted"]
    assert first["id"] == "15087023444" and first["name"] == "Nike" and first["alias"] == "nike"
    assert first["link"] == "https://www.facebook.com/nike" and first["category"] == "Sportswear shop"
    assert first["is_verified"] is True and first["likes"] > 39_000_000
    assert first["instagram"]["username"] == "nike" and first["instagram"]["link"] == "https://www.instagram.com/nike/"
    assert first["instagram"]["is_verified"] is True and first["instagram"]["followers"] > 280_000_000
    without_instagram = [row for row in parsed["advertisers"] if row["instagram"] is None]
    assert without_instagram                              # a Page without an Instagram account: the block is null


def test_suggestions_none():
    assert parsers.parse_suggestions(documents("suggestions_none.json")) == {"advertisers": [], "keywords": []}


def test_advertiser():
    record = parsers.parse_advertiser(documents("advertiser_nike.json.gz"))
    assert list(record) == ["id", "name", "link", "ad_library_link", "alias", "category", "entity_type", "verification",
                            "is_verified", "likes", "about", "picture_link", "instagram", "is_deleted", "created_at",
                            "history", "owner", "admin_countries", "state_media_label", "ads_count",
                            "is_ads_count_capped", "hidden_ads_count", "has_blank_ads", "is_actioned",
                            "is_delegate_page", "related_pages", "political_spend", "shared_disclaimer_pages"]
    assert record["id"] == "15087023444" and record["name"] == "Nike" and record["alias"] == "nike"
    assert record["link"] == "https://www.facebook.com/nike/" and record["category"] == "Sportswear shop"
    assert record["created_at"] == "2008-05-21T14:51:36Z"
    assert record["history"] == [{"type": "name_change", "at": "2010-07-17T00:46:00Z"},
                                 {"type": "creation", "at": "2008-05-21T14:51:36Z"}]
    assert record["owner"] == {"id": "10152356643698445", "name": "NIKE, Inc.", "address": None, "phone": None}
    assert record["admin_countries"][:2] == [{"country": "United States", "count": 90}, {"country": "India", "count": 26}]
    assert record["ads_count"] == 14628 and record["is_ads_count_capped"] is False
    assert record["hidden_ads_count"] == 0 and record["about"] is None and record["political_spend"] is None


def test_advertiser_with_owner_address_and_political_spend():
    record = parsers.parse_advertiser(documents("advertiser_jpmorgan_us.json.gz"))
    assert record["name"] == "JPMorganChase" and record["about"].startswith("Welcome to the official")
    assert record["owner"] == {"id": "968532936505997", "name": "JP Morgan Chase & Co.", "phone": None,
                               "address": {"city": "New York", "state": "NY", "postal_code": "10017",
                                           "country": "United States of America"}}
    assert record["ads_count"] == 1277
    assert record["political_spend"] == {
        "current_week": None,
        "lifetime": [{"payer": None, "amount": 10158}, {"payer": "JPMORGAN CHASE BANK, NA", "amount": 15991085}],
        "weekly": [{"payer": "JPMORGAN CHASE BANK, NA", "amount": 271081}]}
    assert len(record["history"]) == 4 and record["created_at"] == "2008-03-14T19:32:37Z"


def test_advertiser_unknown():
    assert parsers.parse_advertiser(documents("advertiser_unknown.json")) is None
    assert parsers.parse_advertiser(documents("advertiser_ads_unknown_id.json.gz")) is None


# ---- audience ------------------------------------------------------------------------

def test_audience():
    record = parsers.parse_audience(documents("audience_jpmorgan_90d.json"))
    assert list(record) == ["advertiser", "has_political_ads", "has_political_ads_in_period", "data_date",
                            "ads_count", "spend", "spend_text", "currency", "spend_by_currency",
                            "is_30_day_available", "is_90_day_available", "genders", "ages", "locations",
                            "languages", "custom_audiences", "lookalike_audiences", "detailed_targeting"]
    assert record["advertiser"]["id"] == "11465191261" and record["advertiser"]["name"] == "JPMorganChase"
    assert record["ads_count"] == 138 and record["spend"] == 1691099 and record["spend_text"] == "$1,691,099"
    assert record["currency"] == "USD" and record["data_date"] == "2026-11-23"
    assert record["genders"][0] == {"name": "All", "ads_count": 138, "spend_share": 1}
    assert record["locations"][0]["name"] == "United States" and record["locations"][0]["type"] == "countries"
    assert set(record["locations"][0]) == {"name", "ads_count", "spend_share", "type", "obfuscated_count", "is_excluded"}
    assert record["ages"][0] == {"name": "13", "ads_count": 0, "spend_share": 0}
    assert record["custom_audiences"][0]["type"] == "has_ca_inclusion"


def test_audience_of_an_advertiser_without_political_ads_in_the_period():
    record = parsers.parse_audience(documents("audience_nike_7d.json"))
    assert record["ads_count"] == 0 and record["spend"] == 0 and record["currency"] is None
    assert record["has_political_ads"] is True and record["has_political_ads_in_period"] is False
    assert record["genders"] == [] and record["locations"] == []


def test_audience_unknown():
    assert parsers.parse_audience(documents("audience_unknown.json")) is None


# ---- filters -------------------------------------------------------------------------

def test_filters_of_a_keyword_search():
    parsed = parsers.parse_filters(documents("filters_keyword.json"))
    assert list(parsed) == ["languages", "advertisers", "payers", "regions", "targeted_regions", "countries", "cities"]
    assert parsed["languages"][0] == {"code": "en", "name": "English", "ads_at_least": 100}
    assert len(parsed["advertisers"]) == 21 and set(parsed["advertisers"][0]) == {"id", "name", "ads_at_least"}
    assert parsed["payers"] == [] and parsed["regions"] == []


def test_filters_of_a_political_search():
    parsed = parsers.parse_filters(documents("filters_political.json.gz"))
    assert len(parsed["payers"]) == 262 and parsed["payers"][0] == {"name": "1000 Friends of Martinez", "ads_at_least": 1}
    assert len(parsed["regions"]) == 51 and parsed["regions"][0] == {"name": "Alabama", "ads_at_least": 100}
    assert len(parsed["advertisers"]) == 295


def test_filters_of_an_advertiser_and_of_no_session():
    assert len(parsers.parse_filters(documents("filters_advertiser.json"))["languages"]) == 13
    empty = parsers.parse_filters(documents("filters_unknown_session.json"))
    assert all(value == [] for value in empty.values())


def test_one_document_alone_is_read_like_a_list_of_one():
    document = documents("suggestions_nike.json")[0]
    assert parsers.parse_suggestions(document) == parsers.parse_suggestions([document])
    details = documents("ad_details_eu.json")[0]
    assert parsers.parse_ad_details(details)["transparency"]["eu"]["total_reach"] == 11079004


# ---- values --------------------------------------------------------------------------

@pytest.mark.parametrize("text, low, high", [
    ("$25K-$30K", 25000, 30000), (">1M", 1000000, None), ("<100", None, 100), ("<$100", None, 100),
    (">$1M", 1000000, None), ("$1K-$1.5K", 1000, 1500), ("₹1K-₹1.5K", 1000, 1500), ("80K-90K", 80000, 90000),
    ("$200-$299", 200, 299), ("100K-500K", 100000, 500000),
    # a currency sign of several characters in front of the second end
    ("CA$1K - CA$2K", 1000, 2000), ("R$100-R$199", 100, 199),
])
def test_text_range(text, low, high):
    assert parsers.text_range(text) == {"text": text, "min": low, "max": high}


@pytest.mark.parametrize("value", [None, "", "   "])
def test_text_range_of_nothing(value):
    assert parsers.text_range(value) is None


@pytest.mark.parametrize("junk", [None, [], {}, [{}], [{"data": None}], [{"data": {}}], [{"errors": [{"message": "x"}]}]])
def test_parsers_tolerate_junk(junk):
    assert parsers.parse_list(junk)["ads"] == []
    assert parsers.parse_ad_by_id(junk) is None
    assert parsers.parse_ad_details(junk) is None
    assert parsers.parse_versions(junk) is None
    assert parsers.parse_suggestions(junk) == {"advertisers": [], "keywords": []}
    assert parsers.parse_advertiser(junk) is None
    assert parsers.parse_audience(junk) is None
    assert all(value == [] for value in parsers.parse_filters(junk).values())
    assert parsers.parse_ad({}) is None and parsers.parse_ad(None) is None

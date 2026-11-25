"""The Ad Library's GraphQL answers -> clean dicts for every
/facebook-ad-library/* endpoint. Pure functions, no I/O: each takes the
answer's JSON documents (fetch.payloads: one, or several when the root
query streamed the ad list after the page frame; a single document may be
passed as it is) and tolerates missing parts.

Fixtures (fixtures/, raw answer bodies captured through fetch.py on
2026-11-25; the names say what each is).

What the raw data looks like, and what is done with it:

  * The site writes texts in the language of the request (fetch.py pins
    en_US): "$25K - $30K", ">1M", "Submitted Mar 7, 2022". The fixtures
    captured before the language was pinned carry the British forms
    ("$25K-$30K", "Submitted on 7 Mar 2022"); both are read.
  * An ad is {ad_archive_id, page_id, is_active, start_date, end_date,
    publisher_platform, categories, snapshot{...}} plus, for political ads
    only, spend / impressions / reach as TEXT RANGES ("$25K-$30K", ">1M",
    "<100"): a range is answered as {text, min, max}.
  * Dates are epoch seconds at midnight Pacific time: answered as the
    calendar day. An active ad's end_date is the last day it was seen
    running, not an end.
  * snapshot.display_format says where the creative is: IMAGE in images[],
    VIDEO in videos[], CAROUSEL / DCO / DPA in cards[] (one card per
    variant, each with its own text, link and media), TEXT and
    MULTI_IMAGES without a link, EVENT with an event. A catalogue ad's
    top-level text can be the template itself ("{{product.brand}}"); the
    cards carry what was shown.
  * An ad the site shows to signed-in visitors only, or took down, comes
    without format and media (gated_type says why).
  * A paid partnership ("creator with Brand") is listed under the brand:
    the ad's own page_id / page_name are the brand's, its snapshot's page is
    the creator who ran it and snapshot.branded_content the brand again.
    `advertiser` is the snapshot's page, `branded_content_partner` the brand.
  * A list edge groups the ads that share one creative
    (collated_results); only the first of a group carries collation_count.
  * Media links are signed and stop working about 4.5 days after the answer
    (their `oe` value is the expiry, hexadecimal epoch seconds).

Left out on purpose: has_user_reported / report_count / menu_items (the
viewer's own state and the card's menu), impressions_index (the position of
the impressions text in the site's own list of ranges), image_crops (how
the card crops a picture), watermarked_resized_image_url and ad_id (null in
all 1,151 ads read), and the answer's server timings.
"""
import re

from scraper_utils.dates import epoch_utc, iso_date
from scraper_utils.text import clean, multiline
from scraper_utils.values import as_dict, as_list, bool_or_none, g, number, scaled_number, to_int
from facebook_ad_library import refs

TOTAL_CAP = 50000       # the site counts to 50,001 and no further: "over 50,000"


# ---- small helpers -------------------------------------------------------------------

def _parts(documents):
    """The `data` object of every document, in order. One document alone
    (an answer that was a single JSON object) is read as a list of one."""
    if isinstance(documents, dict):
        documents = [documents]
    return [document["data"] for document in as_list(documents)
            if isinstance(document, dict) and isinstance(document.get("data"), dict)]


def _first(documents, *path):
    """The first non-empty value at `path` across the documents."""
    for part in _parts(documents):
        value = g(part, *path)
        if value not in (None, "", [], {}):
            return value
    return None


def _has(documents, *path):
    """True when some document carries the key at `path` (even as null)."""
    for part in _parts(documents):
        node = part
        for step in path[:-1]:
            node = node.get(step) if isinstance(node, dict) else None
        if isinstance(node, dict) and path[-1] in node:
            return True
    return False


def _day(epoch):
    """Epoch seconds -> 'YYYY-MM-DD' (the site stamps a day at midnight Pacific time)."""
    stamp = epoch_utc(epoch)
    return stamp[:10] if stamp else None


def _lower(value):
    value = clean(value)
    return value.lower() if value else None


def _lower_list(values, drop=()):
    return [item.lower() for item in as_list(values) if isinstance(item, str) and item and item not in drop]


def _text_list(values):
    return [text for text in (clean(item) for item in as_list(values)) if text]


# The dash between the two ends; the second end may open with a currency sign
# of several characters ("CA$1K - CA$2K", "R$100-R$199").
_RANGE_SPLIT = re.compile(r"\s*[-–]\s*(?=[^\d\s]{0,4}\s*\d)")


def text_range(value):
    """A range the site prints as text -> {text, min, max}: "$25K-$30K" ->
    25000 / 30000, ">1M" -> min 1000000, "<100" -> max 100, "$1,665" -> both.
    None for an empty value."""
    text = clean(value)
    if not text:
        return None
    body = text.replace("≤", "<").replace("≥", ">")
    low = high = None
    if body.startswith(">"):
        low = scaled_number(body[1:])
    elif body.startswith("<"):
        high = scaled_number(body[1:])
    else:
        ends = _RANGE_SPLIT.split(body, maxsplit=1)
        low = scaled_number(ends[0])
        high = scaled_number(ends[1]) if len(ends) > 1 else low
    return {"text": text, "min": low, "max": high}


# ---- one ad --------------------------------------------------------------------------

def _image(raw):
    raw = as_dict(raw)
    link = clean(raw.get("original_image_url"))
    resized = clean(raw.get("resized_image_url"))
    if not (link or resized):
        return None
    return {"link": link, "resized_link": resized}


def _video(raw):
    raw = as_dict(raw)
    out = {
        # an extra video names its files video_hd_handle / video_sd_handle
        "hd_link": clean(raw.get("video_hd_url") or raw.get("video_hd_handle")),
        "sd_link": clean(raw.get("video_sd_url") or raw.get("video_sd_handle")),
        "preview_image_link": clean(raw.get("video_preview_image_url")),
        "watermarked_hd_link": clean(raw.get("watermarked_video_hd_url")),
        "watermarked_sd_link": clean(raw.get("watermarked_video_sd_url")),
    }
    return out if any(out.values()) else None


def _cta(text, kind):
    text, kind = clean(text), _lower(kind)
    return {"text": text, "type": kind} if (text or kind) else None


def _card(raw):
    raw = as_dict(raw)
    return {
        "title": clean(raw.get("title")),
        "body": multiline(raw.get("body")),
        "caption": clean(raw.get("caption")),
        "landing_link": clean(raw.get("link_url")),
        "link_description": clean(raw.get("link_description")),
        "cta": _cta(raw.get("cta_text"), raw.get("cta_type")),
        "image_link": clean(raw.get("original_image_url")),
        "resized_image_link": clean(raw.get("resized_image_url")),
        "video_hd_link": clean(raw.get("video_hd_url")),
        "video_sd_link": clean(raw.get("video_sd_url")),
        "video_preview_image_link": clean(raw.get("video_preview_image_url")),
    }


def _page_brief(raw, with_profile=False):
    """A {page_id, page_name, page_profile_uri, ...} object (a branded-content
    partner, a related page; with_profile: the page a post was reshared
    from, which also has a picture and categories) -> {id, name, link, ...}."""
    raw = as_dict(raw)
    if not raw:
        return None
    page_id = clean(raw.get("page_id"))
    out = {
        "id": page_id,
        "name": clean(raw.get("page_name")),
        "link": clean(raw.get("page_profile_uri")),
        "ad_library_link": refs.advertiser_library_link(page_id),
        "is_deleted": bool_or_none(raw.get("page_is_deleted")),
    }
    if with_profile:
        out["picture_link"] = clean(raw.get("page_profile_pic_url") or raw.get("page_profile_picture_url"))
        out["categories"] = _text_list(raw.get("page_categories"))
    return out


def _event(raw):
    raw = as_dict(raw)
    if not raw:
        return None
    return {
        "starts_at": epoch_utc(raw.get("event_start_timestamp")),
        "ends_at": epoch_utc(raw.get("event_end_timestamp")),
        "timezone": clean(raw.get("event_timezone")),
        "location": clean(raw.get("event_location")),
    }


def _payer_extra(raw):
    """One of the disclaimer's additional-info blocks -> its filled values, or None."""
    raw = as_dict(raw)
    out = {}
    for key, value in raw.items():
        if isinstance(value, list):
            value = _text_list(value)
        else:
            value = clean(value)
        if value:
            out[key] = value
    return out or None


def _payer_info(raw):
    """The disclaimer card of a political ad ("Information from the
    advertiser"): who paid, and the contacts they gave the site."""
    raw = as_dict(raw)
    if not raw:
        return None
    submitted = clean(raw.get("submitted_on"))
    return {
        "website": clean(raw.get("website")),
        "email": _lower(raw.get("email")),
        "phone": clean(raw.get("phone")),
        "address": clean(raw.get("address")),
        "verification": clean(raw.get("authorized_entity_text")),
        "submitted_on": (iso_date(re.sub(r"^Submitted(?: on)?\s+", "", submitted)) or submitted) if submitted else None,
        "fec": _payer_extra(raw.get("fec_additional_info")),
        "free_form": _payer_extra(raw.get("free_form_additional_info")),
        "page_admin": _payer_extra(raw.get("page_admin_additional_info")),
        "page_name": _payer_extra(raw.get("page_name_additional_info")),
    }


def _political(raw, snapshot, categories):
    """The political block of an ad, or None for an ad that is not one."""
    payer = clean(snapshot.get("byline"))
    spend = text_range(raw.get("spend"))
    impressions = text_range(g(raw, "impressions_with_index", "impressions_text"))
    audience = text_range(raw.get("reach_estimate"))
    payer_info = _payer_info(raw.get("fev_info"))
    if not ("political" in categories or payer or spend or impressions or audience or payer_info):
        return None
    return {
        "payer": payer,
        "disclaimer_label": _lower(snapshot.get("disclaimer_label")),
        "country": clean(snapshot.get("country_iso_code")),
        "currency": clean(raw.get("currency")),
        "spend": spend,
        "impressions": impressions,
        "audience_size": audience,
        "payer_info": payer_info,
    }


_TEMPLATE_RE = re.compile(r"^\s*\{\{[^{}]*\}\}\s*$")


def _shown(value, cards, key, reader=clean):
    """A text of the ad itself, or the first card's when the ad's own is a
    catalogue template ("{{product.brand}}": the site fills it per card)."""
    text = reader(value)
    if text and _TEMPLATE_RE.match(text):
        return next((card[key] for card in cards if card.get(key)), None)
    return text


def parse_ad(raw):
    """One raw ad (a list row, a version, or the ad a link names) -> the ad."""
    raw = as_dict(raw)
    snapshot = as_dict(raw.get("snapshot"))
    ad_id = clean(raw.get("ad_archive_id"))
    if not ad_id:
        return None
    cards = [_card(card) for card in as_list(snapshot.get("cards")) if isinstance(card, dict)]
    hidden = _lower(raw.get("hide_data_status"))
    # The snapshot's page is who ran the ad. For a paid partnership that is
    # the creator, and the ad's own page_id / page_name are the brand's, which
    # the snapshot repeats as branded_content.
    page_id = clean(snapshot.get("page_id") or raw.get("page_id"))
    categories = _lower_list(raw.get("categories"), drop=("UNKNOWN",))
    regulation = as_dict(raw.get("regional_regulation_data"))
    collation_id = clean(raw.get("collation_id"))
    return {
        "id": ad_id,
        "link": refs.ad_link(ad_id),
        "is_active": bool_or_none(raw.get("is_active")),
        "start_date": _day(raw.get("start_date")),
        "end_date": _day(raw.get("end_date")),
        "total_active_seconds": to_int(raw.get("total_active_time")),
        "format": _lower(snapshot.get("display_format")),
        "platforms": _lower_list(raw.get("publisher_platform")),
        "categories": categories,
        "title": _shown(snapshot.get("title"), cards, "title"),
        "body": _shown(g(snapshot, "body", "text"), cards, "body", multiline),
        "caption": _shown(snapshot.get("caption"), cards, "caption"),
        "link_description": _shown(snapshot.get("link_description"), cards, "link_description"),
        "landing_link": _shown(snapshot.get("link_url"), cards, "landing_link"),
        "cta": _cta(snapshot.get("cta_text"), snapshot.get("cta_type")),
        "images": [image for image in map(_image, as_list(snapshot.get("images"))) if image],
        "videos": [video for video in map(_video, as_list(snapshot.get("videos"))) if video],
        "cards": cards,
        "extra_texts": _text_list([as_dict(item).get("text") if isinstance(item, dict) else item
                                   for item in as_list(snapshot.get("extra_texts"))]),
        "extra_links": _text_list(snapshot.get("extra_links")),
        "extra_images": [image for image in map(_image, as_list(snapshot.get("extra_images"))) if image],
        "extra_videos": [video for video in map(_video, as_list(snapshot.get("extra_videos"))) if video],
        "event": _event(snapshot.get("event")),
        "versions": {"collation_id": collation_id, "count": to_int(raw.get("collation_count"))} if collation_id else None,
        "advertiser": {
            "id": page_id,
            "name": clean(snapshot.get("page_name") or raw.get("page_name")),
            "link": clean(snapshot.get("page_profile_uri")),
            "ad_library_link": refs.advertiser_library_link(page_id),
            "picture_link": clean(snapshot.get("page_profile_picture_url")),
            "likes": to_int(snapshot.get("page_like_count")),
            "categories": _text_list(snapshot.get("page_categories")),
            "is_deleted": bool_or_none(snapshot.get("page_is_deleted") if "page_is_deleted" in snapshot
                                       else raw.get("page_is_deleted")),
        },
        "branded_content_partner": _page_brief(snapshot.get("branded_content")),
        "reshared_from": _reshared(snapshot.get("root_reshared_post")),
        "political": _political(raw, snapshot, categories),
        "state_media_label": clean(raw.get("state_media_run_label")),
        "targeted_or_reached_countries": _text_list(raw.get("targeted_or_reached_countries")),
        "gated_type": _lower(raw.get("gated_type")),
        "hidden_data": hidden if hidden != "none" else None,
        "is_reshared": bool_or_none(snapshot.get("is_reshared")),
        "is_sensitive": bool_or_none(raw.get("contains_sensitive_content")),
        "has_ai_generated_media": bool_or_none(raw.get("contains_digital_created_media")),
        "is_eu_transparency_eligible": bool_or_none(raw.get("is_aaa_eligible")),
        "regulation": {
            "is_financial_services": bool_or_none(g(regulation, "finserv", "is_deemed_finserv")),
            "is_limited_delivery_financial_services": bool_or_none(g(regulation, "finserv", "is_limited_delivery")),
            "is_limited_delivery_taiwan_anti_scam": bool_or_none(g(regulation, "tw_anti_scam", "is_limited_delivery")),
        },
        "brazil_tax_id": clean(snapshot.get("brazil_tax_id")),
        "ec_certificates": _tidy(snapshot.get("ec_certificates")) or [],
        "additional_info": _tidy(snapshot.get("additional_info")),
    }


def _reshared(raw):
    raw = as_dict(raw)
    if not raw:
        return None
    return {"advertiser": _page_brief(raw.get("page"), with_profile=True), "body": multiline(g(raw, "text", "body", "text"))}


def _tidy(value):
    """A raw value whose shape was never seen filled (Brazil's transparency
    block, EC certificates): passed on without the site's type markers,
    texts cleaned, empty values as null."""
    if isinstance(value, dict):
        out = {key: _tidy(item) for key, item in value.items() if not str(key).startswith("__")}
        return out or None
    if isinstance(value, list):
        return [item for item in (_tidy(item) for item in value) if item is not None] or None
    if isinstance(value, str):
        return clean(value)
    return value


# ---- lists ---------------------------------------------------------------------------

def _connection(documents):
    for part in _parts(documents):
        connection = g(part, "ad_library_main", "search_results_connection")
        if isinstance(connection, dict):
            return connection
    return None


def _advertiser_card(documents):
    """The advertiser a list was asked for (its card above the list), or
    None when the site knows no such page."""
    page = as_dict(_first(documents, "page"))
    info = {}
    for part in _parts(documents):
        info.update({key: value for key, value in as_dict(g(part, "ad_library_page_info", "page_info")).items()
                     if value is not None})
    page_id = clean(page.get("id") or info.get("page_id"))
    if not page_id:
        return None
    return {
        "id": page_id,
        "name": clean(page.get("name") or info.get("page_name")),
        "link": clean(info.get("page_profile_uri") or page.get("url")),
        "ad_library_link": refs.advertiser_library_link(page_id),
        "picture_link": clean(info.get("profile_photo")),
        "is_deleted": bool_or_none(info.get("page_is_deleted")),
        "is_actioned": bool_or_none(info.get("page_is_actioned")),
        "hidden_ads_count": to_int(info.get("hidden_ads")),
        "has_blank_ads": bool_or_none(info.get("has_blank_ads")),
        "related_pages": [_page_brief(item) for item in as_list(info.get("related_pages")) if isinstance(item, dict)],
    }


def parse_list(documents):
    """A ROOT or MORE answer -> {ads, total_count, is_total_capped,
    next_cursor, advertiser}. `total_count` is None on a MORE answer (the
    site gives the total with the first page only); `advertiser` is the card
    of the advertiser the list was asked for, None when the site knows no
    such page (the list is then of EVERY advertiser: the caller must not
    serve it)."""
    connection = _connection(documents) or {}
    ads = []
    for edge in as_list(connection.get("edges")):
        for raw in as_list(g(edge, "node", "collated_results")):
            ad = parse_ad(raw)
            if ad:
                ads.append(ad)
    total = to_int(connection.get("count"))
    info = as_dict(connection.get("page_info"))
    cursor = clean(info.get("end_cursor"))
    return {
        "ads": ads,
        "total_count": min(total, TOTAL_CAP) if total is not None else None,
        "is_total_capped": total > TOTAL_CAP if total is not None else None,
        "next_cursor": cursor if info.get("has_next_page") and cursor else None,
        "advertiser": _advertiser_card(documents),
    }


def parse_ad_by_id(documents):
    """A ROOT answer asked for one ad (deeplinkAdID) -> the ad, or None when
    the site has no such ad ("NO_AD_FOUND")."""
    result = as_dict(_first(documents, "ad_library_main", "deeplink_ad_archive_result"))
    return parse_ad(result.get("deeplink_ad_archive"))


def parse_versions(documents):
    """A VERSIONS answer -> {ads, total_count, next_cursor}, or None when the
    site knows no such group."""
    results = _first(documents, "ad_library_main", "collation_results")
    if not isinstance(results, dict):
        return None
    cursor = clean(results.get("forward_cursor"))
    return {
        "ads": [ad for ad in map(parse_ad, as_list(results.get("ad_cards"))) if ad],
        "total_count": to_int(results.get("total_count")),
        "next_cursor": cursor if cursor and not results.get("is_complete") else None,
    }


# ---- advertisers ---------------------------------------------------------------------

_VERIFIED = ("blue_verified", "verified", "gray_verified")


def _instagram(info):
    username = clean(info.get("ig_username"))
    if not username and info.get("ig_followers") is None:
        return None
    return {
        "username": username,
        "link": refs.instagram_link(username),
        "followers": to_int(info.get("ig_followers")),
        "is_verified": bool_or_none(info.get("ig_verification")),
    }


def _advertiser(info, page=None, without=()):
    """The site's page_info object (+ the Page object beside it) -> the
    advertiser. `without` names the keys this answer never carries."""
    info, page = as_dict(info), as_dict(page)
    page_id = clean(info.get("page_id") or page.get("id"))
    alias = clean(info.get("page_alias"))
    verification = _lower(info.get("page_verification") or info.get("verification"))
    link = clean(info.get("page_profile_uri") or page.get("url"))
    if not link and page_id:
        link = f"{refs.SITE}/{alias or page_id}"
    record = {
        "id": page_id,
        "name": clean(info.get("page_name") or info.get("name") or page.get("name")),
        "link": link,
        "ad_library_link": refs.advertiser_library_link(page_id),
        "alias": alias,
        "category": clean(info.get("page_category") or info.get("category")),
        "entity_type": _lower(info.get("entity_type")),
        "verification": verification,
        "is_verified": verification in _VERIFIED if verification else None,
        "likes": to_int(info.get("likes")),
        "about": multiline(g(page, "about", "text")),
        "country": clean(info.get("country")),
        "picture_link": clean(info.get("profile_photo") or info.get("image_uri")),
        "cover_photo_link": clean(info.get("page_cover_photo")),
        "instagram": _instagram(info),
        "is_deleted": bool_or_none(info.get("page_is_deleted")),
        "is_restricted": bool_or_none(info.get("page_is_restricted")),
        "is_profile_page": bool_or_none(info.get("is_profile_page")),
    }
    for key in without:
        record.pop(key, None)
    return record


def _spend(raw):
    """page_spend -> the advertiser's political spend (whole units of the
    country's currency, per "Paid for by" name), or None when there is none."""
    raw = as_dict(raw)

    def rows(key):
        return [{"payer": clean(row.get("disclaimer")), "amount": number(row.get("spend"))}
                for row in as_list(raw.get(key)) if isinstance(row, dict)]
    lifetime, weekly = rows("lifetime_by_disclaimer"), rows("weekly_by_disclaimer")
    current = raw.get("current_week")
    if not (lifetime or weekly or current or raw.get("is_political_page")):
        return None
    out = {"current_week": _tidy(current), "lifetime": lifetime, "weekly": weekly}
    if "is_political_page" in raw:          # the details call says it; the About tab does not
        out = {"is_political_advertiser": bool_or_none(raw.get("is_political_page")), **out}
    return out


def parse_suggestions(documents):
    """A SUGGEST answer -> {advertisers, keywords}."""
    found = as_dict(_first(documents, "ad_library_main", "typeahead_suggestions"))
    advertisers = []
    for row in as_list(found.get("page_results")):
        if not isinstance(row, dict):
            continue
        # the search box's rows carry no about text, cover photo or restriction flags
        advertisers.append(_advertiser(row, without=("about", "cover_photo_link", "is_restricted", "is_profile_page")))
    return {"advertisers": advertisers, "keywords": _text_list(found.get("keyword_results"))}


def _address(raw):
    raw = as_dict(raw)
    out = {
        "city": clean(raw.get("city")),
        "state": clean(raw.get("state")),
        "postal_code": clean(raw.get("postal_code")),
        "country": clean(raw.get("country")),
    }
    return out if any(out.values()) else None


def parse_advertiser(documents):
    """A ROOT answer asked for the About tab -> the advertiser in full, or
    None when the site knows no such page."""
    page, info, spend, disclaimers = {}, {}, None, []
    for part in _parts(documents):
        for key, value in as_dict(part.get("page")).items():
            if value is not None:
                page.setdefault(key, value)
        for key, value in as_dict(g(part, "ad_library_page_info", "page_info")).items():
            if value is not None:
                info.setdefault(key, value)
        spend = spend or _spend(g(part, "ad_library_page_info", "page_spend"))
        disclaimers = disclaimers or as_list(g(part, "page", "shared_disclaimer_info", "shared_disclaimer_pages"))
    if not clean(page.get("id") or info.get("page_id")):
        return None
    # the About tab carries no cover photo, country or restriction flags (ads/details has the first and last)
    record = _advertiser(info, page, without=("country", "cover_photo_link", "is_restricted", "is_profile_page"))
    owner = as_dict(page.get("confirmed_page_owner"))
    transparency = as_dict(page.get("pages_transparency_info"))
    history = [{"type": _lower(item.get("item_type")), "at": epoch_utc(item.get("event_time"))}
               for item in as_list(transparency.get("history_items")) if isinstance(item, dict)]
    created = next((item["at"] for item in history if item["type"] == "creation"), None)
    connection = _connection(documents) or {}
    total = to_int(connection.get("count"))
    record.update({
        "created_at": created,
        "history": history,
        "owner": {
            "id": clean(owner.get("id")),
            "name": clean(owner.get("name")),
            "address": _address(g(owner, "information", "address")),
            "phone": clean(g(owner, "information", "phone_number")),
        } if owner else None,
        "admin_countries": [{"country": clean(g(row, "country", "iso_name")), "count": to_int(row.get("count"))}
                            for row in as_list(g(transparency, "admin_locations", "admin_country_counts"))
                            if isinstance(row, dict)],
        "state_media_label": clean(transparency.get("state_media_country_label")),
        "ads_count": min(total, TOTAL_CAP) if total is not None else None,
        "is_ads_count_capped": total > TOTAL_CAP if total is not None else None,
        "hidden_ads_count": to_int(info.get("hidden_ads")),
        "has_blank_ads": bool_or_none(info.get("has_blank_ads")),
        "is_actioned": bool_or_none(info.get("page_is_actioned")),
        "is_delegate_page": bool_or_none(page.get("is_delegate_page_with_linked_primary_profile")),
        "related_pages": [_page_brief(item) for item in as_list(info.get("related_pages")) if isinstance(item, dict)],
        "political_spend": spend,
        "shared_disclaimer_pages": [item for item in (_tidy(row) for row in disclaimers) if item],
    })
    return record


# ---- ad details ----------------------------------------------------------------------

def _breakdown(rows):
    return [{"age_range": clean(row.get("age_range")), "male": number(row.get("male")),
             "female": number(row.get("female")), "unknown": number(row.get("unknown"))}
            for row in as_list(rows) if isinstance(row, dict)]


def _reach(raw, total_key):
    """The EU or UK transparency block -> who the ad was aimed at and whom it reached."""
    raw = as_dict(raw)
    if not raw:
        return None
    return {
        "is_targeted": bool_or_none(raw.get("targets_eu")) if "targets_eu" in raw else None,
        "total_reach": to_int(raw.get(total_key)),
        "audience": {
            "gender": clean(raw.get("gender_audience")),
            "age_min": to_int(g(raw, "age_audience", "min")),
            "age_max": to_int(g(raw, "age_audience", "max")),
            "locations": [{"name": clean(row.get("name")), "type": _lower(row.get("type")),
                           "is_excluded": bool_or_none(row.get("excluded")),
                           "obfuscated_count": to_int(row.get("num_obfuscated"))}
                          for row in as_list(raw.get("location_audience")) if isinstance(row, dict)],
        },
        "reach_by_country": [{"country": clean(row.get("country")), "breakdown": _breakdown(row.get("age_gender_breakdowns"))}
                             for row in as_list(raw.get("age_country_gender_reach_breakdown")) if isinstance(row, dict)],
    }


def _insights(raw):
    """A political ad's delivery: the share of impressions by age and
    gender and by region (0 to 1)."""
    raw = as_dict(raw)
    if not raw:
        return None
    return {
        "country": clean(raw.get("single_country")),
        "is_currency_matched": bool_or_none(raw.get("currency_matched")),
        "by_age_and_gender": _breakdown(raw.get("age_gender_data")),
        "by_region": [{"region": clean(row.get("region")), "share": number(row.get("reach"))}
                      for row in as_list(raw.get("location_data")) if isinstance(row, dict)],
    }


def parse_ad_details(documents):
    """A DETAILS answer -> {advertiser, political_spend, transparency}, or
    None when the answer carries no details. An ad the site no longer has
    still answers its advertiser, with errors on the transparency block."""
    details = _first(documents, "ad_library_main", "ad_details")
    if not isinstance(details, dict):
        return None
    advertiser_raw = as_dict(details.get("advertiser"))
    info = as_dict(g(advertiser_raw, "ad_library_page_info", "page_info"))
    advertiser = (_advertiser(info, advertiser_raw.get("page"), without=("country",))
                  if (info or advertiser_raw.get("page")) else None)
    if advertiser:
        advertiser["is_delegate_page"] = bool_or_none(g(advertiser_raw, "page", "is_delegate_page_with_linked_primary_profile"))
    by_location = as_dict(details.get("transparency_by_location"))
    payers = as_dict(details.get("aaa_info"))
    voice = as_dict(details.get("verified_voice_context"))
    return {
        "advertiser": advertiser,
        "political_spend": _spend(g(advertiser_raw, "ad_library_page_info", "page_spend")),
        "transparency": {
            "eu": _reach(by_location.get("eu_transparency"), "eu_total_reach"),
            "uk": _reach(by_location.get("uk_transparency"), "total_reach"),
            "brazil": _tidy(by_location.get("br_transparency")),
            "payers": [{"payer": clean(row.get("payer")), "beneficiary": clean(row.get("beneficiary"))}
                       for row in as_list(payers.get("payer_beneficiary_data")) if isinstance(row, dict)],
            "is_targeting_eu": bool_or_none(payers.get("targets_eu")),
            "has_violating_payer_or_beneficiary": bool_or_none(payers.get("has_violating_payer_beneficiary")),
            "is_taken_down": bool_or_none(payers.get("is_ad_taken_down")),
            "is_violating_eu_rules": bool_or_none(details.get("is_violating_eu_siep")),
            "is_eligible_for_ai_disclosure": bool_or_none(details.get("is_siep_advertiser_eligible_for_ai_disclosure")),
            "violations": _lower_list(details.get("violation_types")),
            "verified_voice_types": _lower_list(voice.get("types")),
            "financial_services_info": _tidy(voice.get("ad_library_all_geo_fin_serv_info")),
        },
        "political_insights": _insights(details.get("insights")),
    }


# ---- audience ------------------------------------------------------------------------

_AUDIENCE_KEYS = {"value": "name", "num_ads": "ads_count", "total_spend_pct": "spend_share",
                  "is_exclusion": "is_excluded", "num_obfuscated": "obfuscated_count", "location_type": "type",
                  "custom_audience_type": "type"}


def _audience_rows(rows):
    out = []
    for row in as_list(rows):
        if not isinstance(row, dict):
            continue
        item = {}
        for key, value in row.items():
            if str(key).startswith("__"):
                continue
            name = _AUDIENCE_KEYS.get(key, key)
            if name == "type":
                value = _lower(value)
            elif isinstance(value, str):
                value = clean(value)
            item[name] = value
        out.append(item)
    return out


def parse_audience(documents):
    """An AUDIENCE answer -> the advertiser's targeting summary, or None
    when the site knows no such page."""
    page = _first(documents, "page")
    if not isinstance(page, dict) or not clean(page.get("id")):
        return None
    insight = as_dict(page.get("ad_library_page_targeting_insight"))
    summary = as_dict(insight.get("ad_library_page_targeting_summary"))
    info = as_dict(_first(documents, "audience_page_info", "page_info"))
    page_id = clean(page.get("id"))
    spend_text = clean(summary.get("total_spend_formatted"))
    return {
        "advertiser": {"id": page_id, "name": clean(info.get("page_name")),
                       "ad_library_link": refs.advertiser_library_link(page_id)},
        "has_political_ads": bool_or_none(info.get("page_has_any_siep_ads")),
        "has_political_ads_in_period": bool_or_none(insight.get("ad_library_page_has_siep_ads")),
        "data_date": clean(summary.get("ds")),
        "ads_count": to_int(summary.get("total_num_ads")),
        "spend": scaled_number(spend_text) if spend_text else None,
        "spend_text": spend_text,
        "currency": clean(summary.get("main_currency")),
        "spend_by_currency": _tidy(summary.get("detailed_spend")) or [],
        "is_30_day_available": bool_or_none(summary.get("is_30_day_available")),
        "is_90_day_available": bool_or_none(summary.get("is_90_day_available")),
        "genders": _audience_rows(insight.get("ad_library_page_targeting_gender")),
        "ages": _audience_rows(insight.get("ad_library_page_targeting_age")),
        "locations": _audience_rows(insight.get("ad_library_page_targeting_location")),
        "languages": _audience_rows(insight.get("ad_library_page_targeting_language")),
        "custom_audiences": _audience_rows(insight.get("ad_library_page_targeting_custom_audience")),
        "lookalike_audiences": _audience_rows(insight.get("ad_library_page_targeting_lookalike_audience")),
        "detailed_targeting": _audience_rows(insight.get("ad_library_page_targeting_detailed")),
    }


# ---- filters -------------------------------------------------------------------------

def _options(rows, key):
    return [{key: clean(row.get("key")), "name": clean(row.get("display_name")), "ads_at_least": to_int(row.get("count"))}
            for row in as_list(rows) if isinstance(row, dict)]


def parse_filters(documents):
    """A FILTERS answer -> the options the page's filters offer for a
    search, each with the site's own rounded count (1, 10, 100, 1000: "at
    least")."""
    options = as_dict(_first(documents, "ad_library_main", "dynamic_filter_options"))
    payers = [{"name": clean(row.get("display_name") or row.get("key")), "ads_at_least": to_int(row.get("count"))}
              for row in as_list(options.get("disclaimers")) if isinstance(row, dict)]

    def named(key):
        return [{"name": clean(row.get("display_name") or row.get("key")), "ads_at_least": to_int(row.get("count"))}
                for row in as_list(options.get(key)) if isinstance(row, dict)]
    return {
        "languages": _options(options.get("content_languages"), "code"),
        "advertisers": _options(options.get("pages"), "id"),
        "payers": payers,
        "regions": named("reached_regions"),
        "targeted_regions": named("targeted_regions"),
        "countries": named("countries"),
        "cities": named("cities"),
    }

"""Marshmallow request schemas for every /facebook-ad-library/* route.

ONE param per input (never a sibling `url` / `id` pair):

    query          (ads/search) a keyword, OR an Ad Library search link: the
                   filters in the link fill the params the caller left out
    advertiser     a page id (15087023444), an Ad Library advertiser link
                   (…&view_all_page_id=…), a facebook.com page link or the
                   page's alias (nike)
    ad             a Library ID (1823267245466803) or an Ad Library ad link
                   (…/ads/library/?id=…)
    collation_id   the `versions.collation_id` of an ad

The filters are the site's own, with the values its page sends
(facebook_ad_library/refs.py). A value the site would answer without
applying is refused here: impressions_asc orders political ads only, and
payer / regions / audience_size filter political ads only.
"""
import re

from marshmallow import ValidationError, post_load, pre_load, validates_schema

import config
from schema_fields import (BaseSchema, ChoiceField, CommaListField, CountryCodeField, DateField, Flag, PageField,
                           QueryField, RefField, StrippedString)
from scraper_utils.schema import max_length
from facebook_ad_library import refs

MAX_PAGE = config.FACEBOOK_AD_LIBRARY_MAX_PAGE
_LANGUAGE_RE = re.compile(r"^[a-z]{2,3}$")


# ---- fields --------------------------------------------------------------------------

class AdvertiserRefField(RefField):
    resolver = staticmethod(refs.resolve_advertiser)


class AdRefField(RefField):
    resolver = staticmethod(refs.resolve_ad)


class CollationRefField(RefField):
    resolver = staticmethod(refs.resolve_collation)


class CountryField(CountryCodeField):
    """ALL (default: every country) or one 2-letter ISO country code."""

    def __init__(self, **kwargs):
        kwargs.setdefault("load_default", refs.ALL_COUNTRIES)
        kwargs.setdefault("metadata", {
            "description": "where the ads ran: a 2-letter country code (US, GB, DE, ...) or ALL (default) for every country"})
        super().__init__(**kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        text = StrippedString._deserialize(self, value, attr, data, **kwargs)
        if text is None or text.upper() == refs.ALL_COUNTRIES:
            return refs.ALL_COUNTRIES
        return super()._deserialize(text, attr, data, **kwargs)


class LanguagesField(CommaListField):
    """Comma-separated language codes ("en,es"), lower-cased."""

    def __init__(self, **kwargs):
        super().__init__(upper=False, max_items=refs.MAX_LANGUAGES, **kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        items = super()._deserialize(value, attr, data, **kwargs)
        if items is None:
            return None
        items = [item.lower() for item in items]
        for item in items:
            if not _LANGUAGE_RE.match(item):
                raise ValidationError(f"'{item}' is not a language code (en, es, pt, ...).")
        return sorted(set(items))


class PageIdsField(CommaListField):
    """Comma-separated page ids, up to 50."""

    def __init__(self, **kwargs):
        super().__init__(upper=False, max_items=refs.MAX_ADVERTISER_IDS, **kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        items = super()._deserialize(value, attr, data, **kwargs)
        if items is None:
            return None
        for item in items:
            if not refs.ID_RE.match(item):
                raise ValidationError(f"'{item}' is not a page id (15087023444). "
                                      "Find one with /facebook-ad-library/advertisers/search.")
        return sorted(items)


def _status(default=refs.DEFAULT_STATUS):
    return ChoiceField(list(refs.STATUSES), load_default=default, metadata={
        "description": f"active (still running), inactive (stopped) or all (default {default})"})


def _ad_category():
    return ChoiceField(dict(refs.AD_CATEGORIES), load_default="ALL", metadata={
        "description": "all (default), or one special category: political_and_issue_ads, housing_ads, "
                       "employment_ads, credit_ads"})


# ---- the list filters ----------------------------------------------------------------

class _ListFilters(BaseSchema):
    """The filters ads/search and advertisers/ads share. A pasted Ad Library
    link (in the param `link_field` names) fills the params the caller left out."""
    link_field = None

    country = CountryField()
    status = _status()
    ad_category = _ad_category()
    media_type = ChoiceField(refs.MEDIA_TYPES, load_default="all", metadata={
        "description": "all (default), image, meme, image_and_meme, video, or none (ads without image or video)"})
    platforms = CommaListField(allowed=refs.PLATFORMS, upper=False, max_items=len(refs.PLATFORMS), metadata={
        "description": "only ads shown on these, comma-separated: facebook, instagram, audience_network, "
                       "messenger, whatsapp, threads (default all)"})
    languages = LanguagesField(metadata={
        "description": "only ads written in these languages, comma-separated codes: en, es, pt, ... (default all)"})
    date_from = DateField(metadata={"description": "only ads that were shown on or after this date, YYYY-MM-DD"})
    date_to = DateField(metadata={"description": "only ads that were shown on or before this date, YYYY-MM-DD"})
    sort = ChoiceField(list(refs.SORTS), load_default=refs.DEFAULT_SORT, metadata={
        "description": "impressions_desc (default: most shown first), most_recent, or impressions_asc "
                       "(political_and_issue_ads only)"})
    payer = StrippedString(load_default=None, validate=max_length(200), metadata={
        "description": "political_and_issue_ads only: the exact 'Paid for by' name on the ads "
                       "(ads/filters lists the names of a search)"})
    regions = CommaListField(upper=False, max_items=refs.MAX_REGIONS, metadata={
        "description": "political_and_issue_ads only: regions the ads were delivered to, comma-separated, as the "
                       "site names them (California, Alabama; ads/filters lists them)"})
    audience_size = CommaListField(value_map=refs.AUDIENCE_SIZES, upper=False, max_items=len(refs.AUDIENCE_SIZES), metadata={
        "description": "political_and_issue_ads only: estimated audience size, comma-separated: "
                       + ", ".join(refs.AUDIENCE_SIZES)})
    page = PageField(max_page=MAX_PAGE, metadata={
        "description": f"page number, 1-{MAX_PAGE} (default 1); a page holds up to 30 ads. Ask for the pages in "
                       "order: a page opens with the cursor of the page before it"})

    @pre_load
    def _from_link(self, data, **kwargs):
        data = dict(data)
        if not self.link_field:
            return data
        value = data.get(self.link_field)
        if not isinstance(value, str) or not refs.is_library_link(value):
            return data
        found = refs.link_params(value)
        if "ad" in found:
            raise ValidationError("This link names one ad: pass it to /facebook-ad-library/ads/details.", self.link_field)
        data = self._take_link(data, found)
        for name, value in found.items():
            if name in self.fields and name not in ("query", "advertiser") and not data.get(name):
                data[name] = value
        return data

    def _take_link(self, data, found):
        return data

    @validates_schema
    def _coherent(self, data, **kwargs):
        start, end = data.get("date_from"), data.get("date_to")
        if start and end and end < start:
            raise ValidationError("date_to must not be before date_from.", "date_to")
        political = data.get("ad_category") == refs.POLITICAL
        if data.get("sort") == "impressions_asc" and not political:
            raise ValidationError("impressions_asc orders political ads only: set ad_category=political_and_issue_ads.", "sort")
        for name in ("payer", "regions", "audience_size"):
            if data.get(name) and not political:
                raise ValidationError(f"{name} filters political ads only: set ad_category=political_and_issue_ads.", name)

    @post_load
    def _sorted(self, data, **kwargs):
        for name in ("platforms", "regions"):
            if data.get(name):
                data[name] = sorted(data[name])
        if data.get("audience_size"):
            data["audience_size"] = sorted(data["audience_size"], key=lambda size: size["min"])
        return data


# ---- ads/search ----------------------------------------------------------------------

class AdsSearchSchema(_ListFilters):
    link_field = "query"
    query = StrippedString(load_default=None, validate=max_length(500), metadata={
        "description": "keyword to search the ads for (coffee), or an Ad Library search link, whose filters are "
                       "used. Optional when advertiser_ids is given"})
    exact_phrase = Flag(load_default=False, metadata={
        "description": "true = only ads that contain the keyword as an exact phrase (default false: all its words, any order)"})
    advertiser_ids = PageIdsField(metadata={
        "description": f"only ads of these advertisers: page ids, comma-separated, up to {refs.MAX_ADVERTISER_IDS}"})

    def _take_link(self, data, found):
        data["query"] = found.get("query") or ""
        if found.get("exact_phrase") and not data.get("exact_phrase"):
            data["exact_phrase"] = found["exact_phrase"]
        if found.get("advertiser") and not data.get("advertiser_ids"):
            data["advertiser_ids"] = found["advertiser"]
        return data

    @validates_schema
    def _something_to_search(self, data, **kwargs):
        if not data.get("query") and not data.get("advertiser_ids"):
            raise ValidationError("Give a keyword (or an Ad Library search link), or advertiser_ids.", "query")
        if data.get("exact_phrase") and not data.get("query"):
            raise ValidationError("exact_phrase needs a keyword in query.", "exact_phrase")


# ---- advertisers/ads -----------------------------------------------------------------

class AdvertisersAdsSchema(_ListFilters):
    link_field = "advertiser"
    advertiser = AdvertiserRefField(metadata={
        "description": "page id (15087023444), Ad Library advertiser link, or Facebook page link / alias (nike)"})
    query = StrippedString(load_default=None, validate=max_length(200), metadata={
        "description": "optional: only this advertiser's ads that match the keyword"})

    def _take_link(self, data, found):
        if found.get("query") and not data.get("query"):
            data["query"] = found["query"]
        return data


# ---- ads/details ---------------------------------------------------------------------

class AdSchema(BaseSchema):
    ad = AdRefField(metadata={
        "description": "the ad's Library ID (1823267245466803) or its Ad Library link (…/ads/library/?id=…)"})
    country = CountryField(metadata={
        "description": "the country whose political-spend figures of the advertiser are answered: a 2-letter code, "
                       "or ALL (default)"})


# ---- ads/versions --------------------------------------------------------------------

class AdsVersionsSchema(BaseSchema):
    collation_id = CollationRefField(metadata={
        "description": "the versions.collation_id of an ad (1007223439092800): the group of ads that share its creative"})
    country = CountryField()
    status = _status("all")
    page = PageField(max_page=MAX_PAGE, metadata={
        "description": f"page number, 1-{MAX_PAGE} (default 1); a page holds 10 ads. Ask for the pages in order"})


# ---- advertisers/search --------------------------------------------------------------

class AdvertisersSearchSchema(BaseSchema):
    query = QueryField(max_length=100, metadata={
        "description": "advertiser name, Facebook page alias, Instagram handle or page id (nike, jpmorganchase, 15087023444)"})


# ---- advertisers/details -------------------------------------------------------------

class AdvertiserSchema(BaseSchema):
    advertiser = AdvertiserRefField(metadata={
        "description": "page id (15087023444), Ad Library advertiser link, or Facebook page link / alias (nike)"})
    country = CountryField(metadata={
        "description": "the country whose ad count and political-spend figures are answered: a 2-letter code, "
                       "or ALL (default: the count of every country, no spend figures)"})


# ---- advertisers/audience ------------------------------------------------------------

class AdvertiserAudienceSchema(BaseSchema):
    advertiser = AdvertiserRefField(metadata={
        "description": "page id (15087023444), Ad Library advertiser link, or Facebook page link / alias (nike)"})
    period = ChoiceField(dict(refs.PERIODS), load_default=refs.DEFAULT_PERIOD, metadata={
        "description": "the last 7 (default), 30 or 90 days"})


# ---- ads/filters ---------------------------------------------------------------------

class AdsFiltersSchema(BaseSchema):
    query = StrippedString(load_default=None, validate=max_length(500), metadata={
        "description": "the keyword of the search whose filter options are wanted (or give advertiser)"})
    advertiser = AdvertiserRefField(required=False, load_default=None, metadata={
        "description": "the advertiser whose filter options are wanted: page id, Ad Library link, or Facebook "
                       "page link / alias (or give query)"})
    country = CountryField()
    status = _status()
    ad_category = _ad_category()
    exact_phrase = Flag(load_default=False, metadata={
        "description": "true = the options of an exact-phrase search of the keyword (default false)"})

    @validates_schema
    def _one_subject(self, data, **kwargs):
        if bool(data.get("query")) == bool(data.get("advertiser")):
            raise ValidationError("Give either a keyword in query or an advertiser.", "query")

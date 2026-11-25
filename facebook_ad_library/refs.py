"""Reference resolvers, link builders and the filter tables for
/facebook-ad-library/*.

ONE param per input. Resolvers raise ValueError with a user-facing message
(RefField turns it into a 400).

Identifier shapes:
  * ad          1823267245466803     the Library ID printed on every ad
                https://www.facebook.com/ads/library/?id=<id>
  * advertiser  15087023444          the Facebook Page id the Ad Library uses
                https://www.facebook.com/ads/library/?...&view_all_page_id=<id>
                https://www.facebook.com/nike   (the Page's alias; "nike" alone too)
                A profile.php?id= link carries the profile id of a Page on
                the new Pages experience, which the Ad Library does not
                know (its search answers nothing for it): refused with a
                message that says what to pass.
  * collation   1007223439092800     the id of a group of ads that share one
                creative (`versions.collation_id` on an ad)

An Ad Library search link (the address bar of a search on the site) is read
into the params it carries: link_params().

The tables map a public value to the token the site's own page sends
(read from pages opened 2026-11-24, samples/pages/ in the research folder).
"""
import re
from urllib.parse import parse_qsl, quote, urlencode, urlparse

SITE = "https://www.facebook.com"
LIBRARY = SITE + "/ads/library/"

ID_RE = re.compile(r"^[0-9]{5,20}$")
ALIAS_RE = re.compile(r"^[A-Za-z0-9.\-_]{2,80}$")
ALIAS_PREFIX = "alias:"

ALL_COUNTRIES = "ALL"
DEFAULT_STATUS = "active"

# status -> activeStatus
STATUSES = {"active": "active", "inactive": "inactive", "all": "all"}

# ad_category -> adType. The page's enum also has FINANCIAL_PRODUCTS_AND_SERVICES_ADS,
# which answers the rows of CREDIT_ADS (2026-11-24).
AD_CATEGORIES = {
    "all": "ALL",
    "political_and_issue_ads": "POLITICAL_AND_ISSUE_ADS",
    "housing_ads": "HOUSING_ADS",
    "employment_ads": "EMPLOYMENT_ADS",
    "credit_ads": "CREDIT_ADS",
}
POLITICAL = "POLITICAL_AND_ISSUE_ADS"
_AD_TYPE_ALIASES = {"financial_products_and_services_ads": "credit_ads"}

# media_type -> mediaType (the same words)
MEDIA_TYPES = ["all", "image", "meme", "image_and_meme", "video", "none"]

# platforms -> publisherPlatforms (the six the page's filter lists)
PLATFORMS = ["facebook", "instagram", "audience_network", "messenger", "whatsapp", "threads"]

# sort -> sortData. The first is what the page sends when a visitor picked
# nothing (null). impressions_asc orders political ads only; on any other
# search the site answers the default order, so the schema refuses it there.
SORTS = {
    "impressions_desc": None,
    "most_recent": {"direction": "DESCENDING", "mode": "SORT_BY_RELEVANCY_MONTHLY_GROUPED"},
    "impressions_asc": {"direction": "ASCENDING", "mode": "SORT_BY_TOTAL_IMPRESSIONS"},
}
DEFAULT_SORT = "impressions_desc"
_SORT_OF_LINK = {("total_impressions", "desc"): "impressions_desc", ("total_impressions", "asc"): "impressions_asc",
                 ("relevancy_monthly_grouped", "desc"): "most_recent", ("relevancy_monthly_grouped", "asc"): "most_recent"}

# audience_size -> one potentialReachInput range (the page's "Estimated audience size" options)
AUDIENCE_SIZES = {
    "100-1k": {"min": 100, "max": 1000},
    "1k-5k": {"min": 1000, "max": 5000},
    "5k-10k": {"min": 5000, "max": 10000},
    "10k-50k": {"min": 10000, "max": 50000},
    "50k-100k": {"min": 50000, "max": 100000},
    "100k-500k": {"min": 100000, "max": 500000},
    "500k-1m": {"min": 500000, "max": 1000000},
    "over-1m": {"min": 1000000, "max": None},
}

# period (days) -> audienceTimeframe
PERIODS = {"7": "LAST_7_DAYS", "30": "LAST_30_DAYS", "90": "LAST_90_DAYS"}
DEFAULT_PERIOD = "LAST_7_DAYS"

MAX_ADVERTISER_IDS = 50     # pageIDs: 51 answers "variable_value_out_of_range" (2026-11-24)
MAX_LANGUAGES = 10
MAX_REGIONS = 10

_FACEBOOK_HOSTS = ("facebook.com", "fb.com", "fb.me", "fb.watch", "instagram.com", "messenger.com", "m.me",
                   "whatsapp.com", "wa.me", "threads.net", "threads.com", "meta.com")
_STORE_HOSTS = ("apps.apple.com", "itunes.apple.com", "play.google.com")
# Click trackers and link shorteners an ad's landing link may point at: not the advertiser's own site.
_TRACKER_HOSTS = ("doubleclick.net", "googleadservices.com", "googlesyndication.com", "bit.ly", "t.co", "tinyurl.com",
                  "lnk.to", "linktr.ee", "onelink.me", "appsflyer.com", "adjust.com", "app.link", "branch.io",
                  "smart.link", "goo.gl", "ow.ly", "rebrand.ly", "youtube.com", "youtu.be")
# Path words of facebook.com that are not a Page alias.
_RESERVED = {"ads", "pages", "profile.php", "people", "groups", "events", "watch", "marketplace", "gaming", "help",
             "login", "share", "sharer", "story.php", "photo", "photo.php", "permalink.php", "reel", "reels",
             "hashtag", "business", "policies", "privacy", "public", "l.php", "home.php", "search", "stories"}


# ---- links ---------------------------------------------------------------------------

def ad_link(ad_id):
    """The Ad Library page of one ad."""
    return f"{LIBRARY}?id={ad_id}" if ad_id else None


def advertiser_library_link(page_id):
    """The Ad Library page that lists every ad of one advertiser."""
    if not page_id:
        return None
    return (f"{LIBRARY}?active_status=all&ad_type=all&country=ALL&is_targeted_country=false&media_type=all"
            f"&search_type=page&view_all_page_id={page_id}")


def instagram_link(username):
    return f"https://www.instagram.com/{username}/" if username else None


def referer_of(variables):
    """The address of the Ad Library page that would send a call with these
    variables: what the browser names as the call's referer."""
    ad_type = next((name for name, token in AD_CATEGORIES.items() if token == variables.get("adType")), "all")
    countries = variables.get("countries") or [ALL_COUNTRIES]
    params = [("active_status", variables.get("activeStatus") or DEFAULT_STATUS), ("ad_type", ad_type),
              ("country", countries[0]), ("is_targeted_country", "false"),
              ("media_type", variables.get("mediaType") or "all")]
    if variables.get("queryString"):
        params.append(("q", variables["queryString"]))
    params.append(("search_type", variables.get("searchType") or "keyword_unordered"))
    if variables.get("viewAllPageID") not in (None, "", "0"):
        params.append(("view_all_page_id", variables["viewAllPageID"]))
    return LIBRARY + "?" + urlencode(params, quote_via=quote)


def website_of(link):
    """The advertiser's own website behind an ad's landing link or display
    link ("https://secure.shop.com/p/1?utm=x" -> "https://secure.shop.com",
    "SHOP.COM" -> "https://shop.com"). None for a link into Facebook's own
    sites, an app store, a click tracker or a link shortener: that is not
    the advertiser's website; None too for a text that is no link."""
    if not isinstance(link, str) or not link.strip():
        return None
    link = link.strip()
    if " " in link:
        return None
    parsed = urlparse(link if "://" in link else "https://" + link)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", host):
        return None
    bare = host[4:] if host.startswith("www.") else host
    if any(bare == site or bare.endswith("." + site) for site in _FACEBOOK_HOSTS + _TRACKER_HOSTS) or bare in _STORE_HOSTS:
        return None
    return f"{parsed.scheme}://{host}"


# ---- resolvers -----------------------------------------------------------------------

def _library_link(value):
    """The parsed link when `value` is an Ad Library link, else None."""
    if "/" not in value and "." not in value:
        return None
    parsed = urlparse(value if "://" in value else "https://" + value)
    host = (parsed.hostname or "").lower()
    if not (host == "facebook.com" or host.endswith(".facebook.com")):
        return None
    return parsed if parsed.path.rstrip("/").lower().endswith("/ads/library") else None


def _facebook_link(value):
    if "/" not in value and "." not in value:
        return None
    parsed = urlparse(value if "://" in value else "https://" + value)
    host = (parsed.hostname or "").lower()
    if host in ("facebook.com", "fb.com") or host.endswith(".facebook.com"):
        return parsed
    return None


def is_library_link(value):
    return isinstance(value, str) and _library_link(value.strip()) is not None


def link_params(value):
    """An Ad Library link -> the request params it carries ({} for anything
    else): query, country, status, ad_category, media_type, platforms,
    languages, date_from, date_to, exact_phrase, sort, regions, advertiser
    (view_all_page_id) and ad (id). Values are the public ones, as text, so
    the schema validates them like a caller's own."""
    parsed = _library_link(value.strip()) if isinstance(value, str) else None
    if parsed is None:
        return {}
    raw = parse_qsl(parsed.query, keep_blank_values=False)
    single = dict(raw)
    out = {}

    def listed(name):
        return [v for k, v in raw if re.fullmatch(re.escape(name) + r"\[\d*\]", k) or k == name]

    if single.get("id"):
        out["ad"] = single["id"]
    if single.get("view_all_page_id") and single["view_all_page_id"] != "0":
        out["advertiser"] = single["view_all_page_id"]
    query = (single.get("q") or "").strip()
    search_type = (single.get("search_type") or "").lower()
    if query:
        if search_type == "keyword_exact_phrase":
            out["exact_phrase"] = "true"
            query = query.strip('"')
        out["query"] = query
    if single.get("country"):
        out["country"] = single["country"]
    if single.get("active_status"):
        out["status"] = single["active_status"]
    if single.get("ad_type"):
        ad_type = single["ad_type"].lower()
        out["ad_category"] = _AD_TYPE_ALIASES.get(ad_type, ad_type)
    if single.get("media_type"):
        out["media_type"] = single["media_type"]
    if listed("publisher_platforms"):
        out["platforms"] = ",".join(listed("publisher_platforms"))
    if listed("content_languages"):
        out["languages"] = ",".join(listed("content_languages"))
    if listed("regions"):
        out["regions"] = ",".join(listed("regions"))
    if single.get("start_date[min]"):
        out["date_from"] = single["start_date[min]"]
    if single.get("start_date[max]"):
        out["date_to"] = single["start_date[max]"]
    sort = _SORT_OF_LINK.get(((single.get("sort_data[mode]") or "").lower(), (single.get("sort_data[direction]") or "").lower()))
    if sort:
        out["sort"] = sort
    return out


def resolve_ad(value):
    """A Library ID or an Ad Library link that names one ad -> the id."""
    value = (value or "").strip()
    if ID_RE.match(value):
        return value
    found = link_params(value).get("ad")
    if found and ID_RE.match(found):
        return found
    raise ValueError("Must be an ad's Library ID (1823267245466803) or its Ad Library link "
                     "(https://www.facebook.com/ads/library/?id=1823267245466803).")


_ADVERTISER_HELP = ("Must be the advertiser's page id (15087023444), its Ad Library link (…&view_all_page_id=15087023444) "
                    "or its Facebook page link or alias (https://www.facebook.com/nike, nike). "
                    "Find the page id of a name with /facebook-ad-library/advertisers/search.")


def resolve_advertiser(value):
    """A page id, an Ad Library advertiser link, a facebook.com page link or
    a bare alias -> the page id, or "alias:<alias>" for the endpoint to look
    up (the Ad Library's own search knows a Page by its alias)."""
    value = (value or "").strip()
    if ID_RE.match(value):
        return value
    if _library_link(value) is not None:
        found = link_params(value).get("advertiser")
        if found and ID_RE.match(found):
            return found
        raise ValueError("This Ad Library link names no advertiser (it has no view_all_page_id). " + _ADVERTISER_HELP)
    parsed = _facebook_link(value)
    if parsed is not None:
        parts = [part for part in parsed.path.split("/") if part]
        if parts and parts[0].lower() == "profile.php":
            raise ValueError("A profile.php link carries the Page's profile id, which the Ad Library does not use. "
                             + _ADVERTISER_HELP)
        if len(parts) >= 3 and parts[0].lower() == "pages" and ID_RE.match(parts[-1]):
            return parts[-1]                    # /pages/<name>/<page id>
        if parts and parts[0].lower() == "people":
            raise ValueError("A /people/ link carries the Page's profile id, which the Ad Library does not use. "
                             + _ADVERTISER_HELP)
        if parts and parts[0].lower() not in _RESERVED:
            first = parts[0]
            if ID_RE.match(first):
                return first
            tail = re.search(r"-([0-9]{8,20})$", first)     # /Brandon-Duensing-315614482659977/
            if tail:
                return tail.group(1)
            if ALIAS_RE.match(first):
                return ALIAS_PREFIX + first.lower()
        raise ValueError("This Facebook link names no page. " + _ADVERTISER_HELP)
    if ALIAS_RE.match(value) and "/" not in value:
        return ALIAS_PREFIX + value.lower()
    raise ValueError(_ADVERTISER_HELP)


def resolve_collation(value):
    """A collation id (the `versions.collation_id` of an ad) -> the id."""
    value = (value or "").strip()
    if ID_RE.match(value):
        return value
    raise ValueError("Must be a collation id: the versions.collation_id of an ad (1007223439092800).")


def alias_of(ref):
    """The alias inside an "alias:<alias>" reference, else None."""
    return ref[len(ALIAS_PREFIX):] if isinstance(ref, str) and ref.startswith(ALIAS_PREFIX) else None

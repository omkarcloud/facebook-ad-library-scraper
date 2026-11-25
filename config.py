"""Configuration for the Facebook Ad Library Scraper. Everything can be set with an environment
variable; the defaults work out of the box.

    PORT                        port the API listens on (default 8000)
    FACEBOOK_AD_LIBRARY_PROXY   proxy URL for every request, e.g. http://user:pass@host:port
                                (default: none, requests go out from your own connection)

Runs on your own home connection with no proxy. On a cloud server, set a residential proxy (an ISP proxy
works too): Facebook refuses page 2+ of ad lists from datacenter addresses.

Everything else below is a plain constant with a working default.
"""
import os

PORT = int(os.environ.get("PORT", "8000"))

FACEBOOK_AD_LIBRARY_PROXY = os.environ.get("FACEBOOK_AD_LIBRARY_PROXY") or None


# ---- what the code reads off config: defaults for a direct run ----
FACEBOOK_AD_LIBRARY_BLOCK_HOLD_S = 600
FACEBOOK_AD_LIBRARY_COLD_PAGES = 5
FACEBOOK_AD_LIBRARY_EXIT = FACEBOOK_AD_LIBRARY_PROXY      # the exit every session leaves through: your proxy, or direct
FACEBOOK_AD_LIBRARY_FALLBACK_EXIT = None
FACEBOOK_AD_LIBRARY_LOCALE = 'en_US'
FACEBOOK_AD_LIBRARY_MAX_PAGE = 1700
FACEBOOK_AD_LIBRARY_PAGE_EXIT = FACEBOOK_AD_LIBRARY_PROXY        # the page load ads/filters takes its session id from
FACEBOOK_AD_LIBRARY_PAGING_EXIT = FACEBOOK_AD_LIBRARY_PROXY      # page 2+ of an ad list: from a server, this is the call that needs the proxy
FACEBOOK_AD_LIBRARY_PAGING_LAST_EXIT = None
FACEBOOK_AD_LIBRARY_PAGING_PAUSE = 0.5
FACEBOOK_AD_LIBRARY_PAGING_RETRIES = 5
MAX_RETRIES = 3
RETRY_BACKOFF = 2

# The persisted-query id of each GraphQL operation the Ad Library's own page sends, read from its public
# script on 2026-11-24 (`__d("<Name>_facebookRelayOperation", ..., e.exports="<id>")`). They change when
# Facebook ships a new build: a call whose id is gone is answered without data, and the error names this dict.
FACEBOOK_AD_LIBRARY_DOC_IDS = {
    "AdLibraryFoundationRootQuery": "29162802513328320",
    "AdLibrarySearchPaginationQuery": "29218106027816007",
    "AdLibraryV3AdDetailsQuery": "25068828942793558",
    "AdLibraryV3AdCollationDetailsQuery": "29223182113971520",
    "useAdLibraryTypeaheadSuggestionDataSourceQuery": "9755915494515334",
    "AdLibraryFilterContextProviderQuery": "29650582277919185",
    "AdLibraryPageAudienceTabQuery": "10046771472112817",
}

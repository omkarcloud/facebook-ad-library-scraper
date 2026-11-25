"""Cache TTL per /facebook-ad-library/* endpoint (cache.py, keyed on the
validated params).

Lists (ads/search, advertisers/ads, ads/versions) are cached a page at a
time, each page with the cursor that opens the page after it: a client
walking a list pays one page of upstream calls per page. A cursor still
opened its page 22 hours after it was handed out (2026-11-25).
"""
from datetime import timedelta

# One row per (filters, sort, page). Ads start and stop all day and the
# default order is by impressions, which moves slowly: an hour keeps a walk
# of a long list on one snapshot (7,000 ads took 18 minutes to walk).
LIST_CACHE = timedelta(hours=1)
# How long this process trusts a cursor it was handed (a list walked again
# after that starts from the nearest cached page).
CURSOR_CACHE = timedelta(hours=6)
# One ad with its advertiser and reach: the creative never changes, the
# reach and spend figures are updated about daily.
AD_CACHE = timedelta(hours=6)
# The search box's advertisers for a name: pages are created and renamed slowly.
ADVERTISER_SEARCH_CACHE = timedelta(hours=12)
# A page alias -> its page id: an alias rarely changes hands.
ALIAS_CACHE = timedelta(days=7)
# An advertiser's About tab: likes and the ad count drift daily, the rest rarely.
ADVERTISER_CACHE = timedelta(hours=12)
# The targeting summary is stamped with the day it was computed (`data_date`).
AUDIENCE_CACHE = timedelta(hours=12)
# Filter options of a search, with rounded counts.
FILTERS_CACHE = timedelta(hours=6)

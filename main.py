"""Use the scraper straight from Python, no server needed.

    python main.py

Each call validates its params with the same schema the API uses (so the
defaults apply) and returns the same JSON the API does, except that a list
keeps its `pagination` block instead of next / previous links. The results
are written to output/*.json.
"""
import json
import os

from facebook_ad_library import api, schemas
from schema_fields import load_query

os.makedirs("output", exist_ok=True)


def run(schema_cls, impl, **params):
    data, error = load_query(schema_cls, {k: str(v) for k, v in params.items()})
    if error:
        raise ValueError(error)
    return impl(**data)


def save(name, data):
    path = os.path.join("output", name)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"saved {path}")


if __name__ == "__main__":
    # One ad in full: its creative, its advertiser, spend, impressions, who paid, its EU / UK reach
    # and the advertiser's own website.
    save("ads_details.json", run(schemas.AdSchema, api.ad_details, ad='1953276048719851'))

    # The first 30 ads that match a keyword, most shown first, with the total.
    save("ads_search.json", run(schemas.AdsSearchSchema, api.ads_search, query='nike shoes'))

    # The first 10 versions of one ad's creative (a campaign's variants), each with its own spend.
    save("ads_versions.json", run(schemas.AdsVersionsSchema, api.ads_versions, collation_id='1296463810973015'))

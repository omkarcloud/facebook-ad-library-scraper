# 🚀 Facebook Ad Library Scraper

Facebook Ad Library Scraper is a **free and open-source** scraper that gets you **unlimited** detailed Facebook Ad Library data for free.

## ✨ What Can I Get?

- 📢 **Every ad running right now, on 6 Meta apps in 201 countries** — text, headline, images, HD videos, carousel cards, CTA, landing page, platforms and run dates
- 🏛️ **7 years of political & issue ads, with their spend** — spend, impressions, audience size, who paid with their email, phone and address, delivery by age, gender and state
- 🇪🇺 **The real reach of every ad shown in the EU or UK (11.5M for one Nike ad)** — reach by country, age and gender, the targeted audience, payer and beneficiary
- 🏢 **Every advertiser's full picture (14,690 ads for Nike alone)** — likes, Instagram followers, verification, confirmed owner, page history, admin countries, political spend and targeting

## 📢 Example: A Full Facebook Ad

```json
{
  "id": "1953276048719851",
  "link": "https://www.facebook.com/ads/library/?id=1953276048719851",
  "is_active": true,
  "start_date": "2026-06-11",
  "format": "dco",
  "platforms": ["facebook", "instagram"],
  "title": "Bringing entrepreneurs’ big dreams to life",
  "body": "From immersive slime experiences to farm-fresh coffee, JPMorganChase helps NYC business owners build thriving businesses.",
  "website": "https://www.jpmorganchase.com",
  "cta": { "text": "Learn more", "type": "learn_more" },
  "cards": [{ "title": "Bringing entrepreneurs’ big dreams to life", "video_hd_link": "https://video-ord5-2.xx.fbcdn.net/o1/v/t2/f2/m366/AQM9hcXAsQs8lYoZI3he…" }],
  "advertiser": { "id": "11465191261", "name": "JPMorganChase", "likes": 484652, "category": "Financial service", "is_verified": true },
  "political": {
    "payer": "JPMORGAN CHASE BANK, NA",
    "spend": { "text": "$25K - $30K", "min": 25000, "max": 30000 },
    "impressions": { "text": ">1M", "min": 1000000, "max": null },
    "audience_size": { "text": "100K - 500K", "min": 100000, "max": 500000 },
    "payer_info": { "email": "jpm.social.media@jpmorgan.com", "phone": "+12122706000", "address": "1111 POLARIS PARKWAY, COLUMBUS, OH 43240, US" }
  },
  "political_insights": {
    "by_age_and_gender": [{ "age_range": "35-44", "male": 0.11, "female": 0.11 }],
    "by_region": [{ "region": "New York", "share": 0.33 }, { "region": "California", "share": 0.06 }]
  }
}
```

*Trimmed for readability.*

## 🚀 Unlimited Free Facebook Ad Library Data — Get It in 60 Seconds

1️⃣ Clone and install:
```bash
git clone https://github.com/omkarcloud/facebook-ad-library-scraper
cd facebook-ad-library-scraper
python -m pip install -r requirements.txt
```

2️⃣ Start the API:
```bash
python run.py
```

On your own home connection, every call works with no proxy.

Running it on a cloud server? Facebook refuses page 2+ of ad lists from datacenter addresses. Set `FACEBOOK_AD_LIBRARY_PROXY=http://user:pass@host:port` to a residential proxy (an ISP proxy works too) and keep going.

3️⃣ Get your first data:
```bash
curl "http://localhost:8000/ads/details?ad=1953276048719851"
```

```json
{
  "id": "1953276048719851",
  "link": "https://www.facebook.com/ads/library/?id=1953276048719851",
  "is_active": true,
  "start_date": "2026-06-11",
  "platforms": ["facebook", "instagram"],
  "title": "Bringing entrepreneurs’ big dreams to life",
  "website": "https://www.jpmorganchase.com",
  "advertiser": { "id": "11465191261", "name": "JPMorganChase", "likes": 484656, "alias": "jpmorganchase" },
  "political": {
    "payer": "JPMORGAN CHASE BANK, NA",
    "spend": { "text": "$25K - $30K", "min": 25000, "max": 30000 },
    "impressions": { "text": ">1M", "min": 1000000, "max": null },
    "payer_info": { "email": "jpm.social.media@jpmorgan.com", "phone": "+12122706000" }
  },
  "political_insights": {
    "by_region": [{ "region": "New York", "share": 0.33 }, { "region": "New Jersey", "share": 0.07 }]
  }
}
```

All 8 endpoints are now live at `http://localhost:8000`.

## 📚 Endpoints

8 endpoints cover everything you need.

| Endpoint | Path | Returns |
|---|---|---|
| Ad Details | `/ads/details` | One ad in full: creative, spend, who paid, EU and UK reach |
| Search Ads | `/ads/search` | Ads by keyword or advertiser, with every filter the site has |
| Ad Versions | `/ads/versions` | Every version of one creative, each with its own spend |
| Search Advertisers | `/advertisers/search` | Page ids, likes and Instagram followers for any brand name |
| Advertiser Details | `/advertisers/details` | Owner, page history, admin countries, ad count and political spend |
| Advertiser Ads | `/advertisers/ads` | Every ad of one brand, most shown or newest first |
| Advertiser Audience | `/advertisers/audience` | Who a political advertiser targets: spend by gender, age, place |
| Ad Filters | `/ads/filters` | The languages, advertisers, payers and regions of any search |

Ad Details also reads the advertiser's own website for emails, phones and socials (`website_contacts_crawl_mode`) on the hosted API only.

## 🔍 Exploring Parameters

The same API is published on RapidAPI, and its playground is the easiest place to try parameters and see raw responses. Once a request looks right, run it locally for **unlimited free** data.

1. [Subscribe to the free plan](https://rapidapi.com/OmkarCloud/api/best-facebook-ad-library-scraper-free-1000-calls/pricing) — 1,000 calls/month, no credit card.
2. [Try the endpoints in the playground](https://rapidapi.com/OmkarCloud/api/best-facebook-ad-library-scraper-free-1000-calls/playground) — every param is pre-filled, so you see real data in one click.
3. Copy the generated code and replace `https://best-facebook-ad-library-scraper-free-1000-calls.p.rapidapi.com` with `http://localhost:8000`. It will now run against your local API.

```python
import requests

# generated by the playground, host swapped for the local API
response = requests.get(
    "http://localhost:8000/ads/details",
    params={"ad": "1953276048719851"},
)
print(response.json())
```

## 💬 Have Questions? We Have Answers.

You're a developer — we know how hard completing a project can be. So we offer full support: just message us and we'll reply ✅ with a solution within 1 working day.

[![Message Us on WhatsApp about Facebook Ad Library Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/whatsapp-us.png)](https://www.omkar.cloud/l/facebook-ad-library-scraper-github-whatsapp)

[![Ask Us by Email about Facebook Ad Library Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/ask-on-email.png)](mailto:happy.to.help@omkar.cloud?subject=Help%20with%20Facebook%20Ad%20Library%20Scraper%20API&body=I%20need%20help%20using%20the%20Facebook%20Ad%20Library%20Scraper%20API.)

## ⚡ Popular Scrapers by Omkar Cloud

- [**Google Maps Scraper (3,100+ GitHub Stars)**](https://github.com/omkarcloud/google-maps-scraper) — type "dentists in New York", get every business as a ready-to-call lead list: phones, emails, websites & reviews. Up to 100K free leads/month.
- [**G2 Scraper**](https://www.omkar.cloud/tools/g2-scraper) — G2 product details, ratings & AI-found contacts
- [**Website Email Contact Scraper**](https://www.omkar.cloud/tools/website-email-contact-scraper) — emails, phones & socials from any website
- [**AliExpress Scraper**](https://www.omkar.cloud/tools/aliexpress-scraper-api) — live product details, SKU variants, stock & shipping
- [**Booking Scraper**](https://www.omkar.cloud/tools/booking-scraper) — Booking.com hotels: prices, ratings, rooms & amenities
- [**Etsy Scraper**](https://www.omkar.cloud/tools/etsy-scraper) — Etsy products: prices, discounts, shops & variations

## ⭐ Love It? [Star It ⭐!](https://github.com/omkarcloud/facebook-ad-library-scraper)

Star the repo ⭐ and become my star hero!

It's just 1 click, but it means the world to me.

[![Star us on GitHub](https://raw.githubusercontent.com/omkarcloud/google-maps-scraper/master/screenshots/star-us.png)](https://github.com/omkarcloud/facebook-ad-library-scraper)

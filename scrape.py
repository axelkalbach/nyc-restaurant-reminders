#!/usr/bin/env python3
"""
Fetches all restaurant data from nycrsvps.com's Supabase backend
and writes it to restaurants.json.

Run manually whenever you want to refresh the list:
  python3 scrape.py
"""

import json
import urllib.request
from pathlib import Path

SUPABASE_URL = "https://jguqwrdhbqdpunqmhflt.supabase.co"
SUPABASE_KEY = "sb_publishable_KyWlprDkN497iCHADtjYxA_2gjfBn05"
OUT_FILE = Path(__file__).parent / "restaurants.json"

COLUMNS = "name,restaurant_url,area,cuisine,reservation_method,reservation_link,advance_period,advance_unit,advance_type,release_time"


def fetch():
    url = f"{SUPABASE_URL}/rest/v1/restaurants?select={COLUMNS}&order=name"
    req = urllib.request.Request(url, headers={
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    })
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def convert(row):
    open_time = row["release_time"][:5]  # "HH:MM:SS" → "HH:MM"
    entry = {
        "name": row["name"],
        "advance_type": row["advance_type"],       # "days_advance" or "first_of_month"
        "advance_period": row["advance_period"],   # days or months depending on advance_type
        "open_time": open_time,
        "platform": row["reservation_method"],
        "platform_link": row["reservation_link"],
        "area": row["area"],
        "cuisine": row["cuisine"],
        "notes": "",
    }
    return entry


def main():
    print("Fetching from nycrsvps.com...")
    rows = fetch()
    print(f"  Got {len(rows)} restaurants")

    restaurants = [convert(r) for r in rows]

    with open(OUT_FILE, "w") as f:
        json.dump(restaurants, f, indent=2)

    print(f"  Wrote {OUT_FILE}")

    fom = [r for r in restaurants if r["advance_type"] == "first_of_month"]
    if fom:
        print(f"\n  Note: {len(fom)} restaurant(s) use first-of-month scheduling:")
        for r in fom:
            print(f"    - {r['name']} (opens 1st of month, {r['advance_period']} month(s) before target)")


if __name__ == "__main__":
    main()

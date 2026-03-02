#!/usr/bin/env python3
"""
NYC Restaurant Reservation Reminder
Sends Gmail alerts before a restaurant's reservation window opens
for target dates in watchlist.json. Run via cron every 15 minutes.

Usage:
  python3 remind.py            # normal mode (sends emails)
  python3 remind.py --dry-run  # prints what would be sent, no emails
"""

import json
import os
import smtplib
import sys
from datetime import datetime, date, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

import pytz
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
RESTAURANTS_FILE = BASE_DIR / "restaurants.json"
WATCHLIST_FILE = BASE_DIR / "watchlist.json"
NOTIFIED_FILE = BASE_DIR / "notified.json"

EASTERN = pytz.timezone("America/New_York")
WINDOW_MINUTES = 1440  # 24 hours — alert when opening is within this many minutes
DEDUP_ENABLED = False  # set to True to stop re-sending already-notified reminders


def load_json(path):
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def send_email(gmail_address, app_password, to_address, subject, body):
    msg = MIMEMultipart()
    msg["From"] = gmail_address
    msg["To"] = to_address
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_address, app_password)
        server.sendmail(gmail_address, to_address, msg.as_string())


def human_duration(minutes):
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours = minutes // 60
    mins = minutes % 60
    h = f"{hours} hour{'s' if hours != 1 else ''}"
    return h if mins == 0 else f"{h} {mins} min"


def build_email_body(restaurant, target_date, opening_dt, minutes_until):
    name = restaurant["name"]
    platform = restaurant.get("platform", "the booking platform")
    link = restaurant["platform_link"]
    area = restaurant.get("area", "")
    cuisine = restaurant.get("cuisine", "")
    advance_type = restaurant.get("advance_type", "days_advance")
    advance_period = restaurant["advance_period"]

    if advance_type == "first_of_month":
        rule = f"Opens on the 1st of the month, {advance_period} month(s) before your target month."
    else:
        rule = f"Releases {advance_period} days in advance at {opening_dt.strftime('%I:%M %p ET')}."

    target_dt = datetime.strptime(target_date, "%Y-%m-%d")
    lines = [
        f"Time to book {name}.",
        "",
        f"Reservation date:  {target_dt.strftime('%A, %B %-d, %Y')}" + (f"  —  {cuisine}, {area}" if cuisine and area else ""),
        f"Book at:           {opening_dt.strftime('%A, %B %-d at %I:%M %p ET')} ({human_duration(minutes_until)} from now)",
        f"Platform:          {platform}  —  {link}",
        "",
        rule,
    ]

    if restaurant.get("notes"):
        lines.append(restaurant["notes"])

    source = "GitHub Actions (scheduled)" if os.getenv("GITHUB_ACTIONS") else "local run"
    lines += ["", "Good luck — move fast.", "", f"Sent via {source}."]

    return "\n".join(lines)


def main():
    dry_run = "--dry-run" in sys.argv

    load_dotenv(BASE_DIR / ".env")

    gmail_address = os.getenv("GMAIL_ADDRESS")
    app_password = os.getenv("GMAIL_APP_PASSWORD")
    notify_email = os.getenv("NOTIFY_EMAIL", gmail_address)

    if not dry_run and (not gmail_address or not app_password):
        print("ERROR: GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set in .env")
        return

    restaurants = load_json(RESTAURANTS_FILE)
    watchlist = load_json(WATCHLIST_FILE)
    notified = load_json(NOTIFIED_FILE) if NOTIFIED_FILE.exists() else []

    now = datetime.now(EASTERN)
    print(f"Current time: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"Alert window: {WINDOW_MINUTES} minutes ({WINDOW_MINUTES // 60}h)")
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print(f"Checking {len(watchlist)} date(s)...")
    print()

    for entry in watchlist:
        # Support both plain strings and {"date": ..., "restaurants": [...]} objects
        if isinstance(entry, str):
            target_date_str = entry
            filter_names = None
            recipient = notify_email
        else:
            target_date_str = entry["date"]
            filter_names = {r.lower() for r in entry.get("restaurants", [])} or None
            recipient = entry.get("email", notify_email)

        target_date = date.fromisoformat(target_date_str)
        active_restaurants = [
            r for r in restaurants
            if filter_names is None or r["name"].lower() in filter_names
        ]

        label = f"{len(active_restaurants)} restaurant(s)" if filter_names is None else f"{len(active_restaurants)} selected restaurant(s)"
        print(f"  {target_date_str}: checking {label} -> {recipient}")

        for restaurant in active_restaurants:
            key = f"{restaurant['name']}_{target_date_str}"

            # Parse the restaurant's open_time and build a timezone-aware datetime
            open_hour, open_minute = map(int, restaurant["open_time"].split(":"))
            if restaurant.get("advance_type") == "first_of_month":
                # Opens on the 1st of the month N months before the target month
                month = target_date.month - restaurant["advance_period"]
                year = target_date.year + (month - 1) // 12
                month = ((month - 1) % 12) + 1
                opening_dt = EASTERN.localize(datetime(year, month, 1, open_hour, open_minute))
            else:
                opening_dt = EASTERN.localize(
                    datetime(
                        target_date.year,
                        target_date.month,
                        target_date.day,
                        open_hour,
                        open_minute,
                    )
                ) - timedelta(days=restaurant["advance_period"])

            delta_seconds = (opening_dt - now).total_seconds()
            delta_minutes = delta_seconds / 60
            in_window = 0 <= delta_seconds <= WINDOW_MINUTES * 60
            already_notified = DEDUP_ENABLED and key in notified

            # Always print the evaluation row
            status = (
                "ALREADY NOTIFIED" if already_notified
                else ">>> WOULD SEND EMAIL <<<" if in_window
                else f"{'opens in' if delta_minutes > 0 else 'opened'} "
                     f"{abs(int(delta_minutes))} min {'from now' if delta_minutes > 0 else 'ago'}"
            )
            print(
                f"  [{target_date_str}] {restaurant['name']:<16} "
                f"opens {opening_dt.strftime('%m/%d %I:%M %p ET')}  "
                f"({delta_minutes:+.0f} min)  {status}"
            )

            if not in_window or already_notified:
                continue

            minutes_until = max(1, int(delta_minutes))
            target_dt = datetime.strptime(target_date_str, "%Y-%m-%d")
            subject = (
                f"\u23f0 {restaurant['name']} | "
                f"{target_dt.strftime('%b %-d')} | "
                f"Book {opening_dt.strftime('%b %-d at %I:%M %p ET')}"
            )
            body = build_email_body(restaurant, target_date_str, opening_dt, minutes_until)

            if dry_run:
                print()
                print(f"    Subject: {subject}")
                print(f"    Body:\n" + "\n".join(f"      {l}" for l in body.splitlines()))
                print()
                continue

            try:
                send_email(gmail_address, app_password, recipient, subject, body)
                print(f"    -> Email sent to {recipient}")
            except Exception as e:
                print(f"    -> ERROR sending email: {e}")
                continue

            if DEDUP_ENABLED:
                notified.append(key)
                save_json(NOTIFIED_FILE, notified)


if __name__ == "__main__":
    main()

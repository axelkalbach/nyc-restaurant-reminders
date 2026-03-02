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

    # Human-readable booking rule
    if advance_type == "first_of_month":
        rule = (
            f"{name} releases reservations on the 1st of the month, "
            f"{advance_period} month(s) before your target month. "
            f"Reservations for {target_date} opened on {opening_dt.strftime('%B 1, %Y')} "
            f"at {opening_dt.strftime('%I:%M %p ET')}."
        )
    else:
        rule = (
            f"{name} releases reservations exactly {advance_period} days in advance, "
            f"at {opening_dt.strftime('%I:%M %p ET')} Eastern. "
            f"That means today — {opening_dt.strftime('%A, %B %-d')} — is the day to book "
            f"for your target date of {target_date}."
        )

    lines = [
        f"Reservations at {name} open in {human_duration(minutes_until)}. Book now.",
        "",
        "=" * 52,
        "  RESERVATION DETAILS",
        "=" * 52,
        f"  Restaurant:   {name}" + (f" ({cuisine}, {area})" if cuisine and area else ""),
        f"  Target date:  {target_date}",
        f"  Window opens: {opening_dt.strftime('%A, %B %-d at %I:%M %p ET')}",
        f"  Time to book: {human_duration(minutes_until)} from now",
        "",
        "=" * 52,
        "  HOW TO BOOK",
        "=" * 52,
        f"  Platform:     {platform}",
        f"  Link:         {link}",
        "",
        "=" * 52,
        "  THE RULES",
        "=" * 52,
        f"  {rule}",
    ]

    if restaurant.get("notes"):
        lines += ["", f"  Note: {restaurant['notes']}"]

    lines += [
        "",
        "=" * 52,
        "  Good luck — move fast!",
        "=" * 52,
    ]

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
    print(f"Checking {len(watchlist)} date(s) x {len(restaurants)} restaurant(s)...")
    print()

    for target_date_str in watchlist:
        target_date = date.fromisoformat(target_date_str)

        for restaurant in restaurants:
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
            subject = (
                f"\u23f0 Book {restaurant['name']} NOW \u2014 "
                f"reservations open in {human_duration(minutes_until)}!"
            )
            body = build_email_body(restaurant, target_date_str, opening_dt, minutes_until)

            if dry_run:
                print()
                print(f"    Subject: {subject}")
                print(f"    Body:\n" + "\n".join(f"      {l}" for l in body.splitlines()))
                print()
                continue

            try:
                send_email(gmail_address, app_password, notify_email, subject, body)
                print(f"    -> Email sent to {notify_email}")
            except Exception as e:
                print(f"    -> ERROR sending email: {e}")
                continue

            if DEDUP_ENABLED:
                notified.append(key)
                save_json(NOTIFIED_FILE, notified)


if __name__ == "__main__":
    main()

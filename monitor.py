#!/usr/bin/env python3
"""
Monitors fungamescentral.com and thesecondfarm.com for Virtual/Magic Fishing
events with a multiplier of x5-x10 and sends alerts to a Telegram chat.

Env vars required:
  TELEGRAM_BOT_TOKEN  - Telegram bot token (from @BotFather)
  TELEGRAM_CHAT_ID    - Chat/user/channel id to send messages to

Env vars optional:
  TIMEZONE            - IANA tz name, default "Europe/Amsterdam"
  MIN_MULTIPLIER      - default 5
  MAX_MULTIPLIER      - default 10
  WINDOW_START        - "HH:MM", default "11:00"
  WINDOW_END          - "HH:MM", default "04:00"
  STATE_FILE          - default "state.json"
"""

import os
import re
import json
import sys
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

FUNGAMES_URL = "https://www.fungamescentral.com/vf_lands.php"
SECONDFARM_URL = "https://www.thesecondfarm.com/MagicFishing/BuoysList"

STATE_FILE = os.environ.get("STATE_FILE", "state.json")
MIN_MULTIPLIER = int(os.environ.get("MIN_MULTIPLIER", "5"))
MAX_MULTIPLIER = int(os.environ.get("MAX_MULTIPLIER", "10"))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


# --------------------------------------------------------------------------
# Time window helpers
# --------------------------------------------------------------------------

def _parse_hhmm(s: str) -> dt_time:
    h, m = s.split(":")
    return dt_time(int(h), int(m))


def in_notification_window() -> bool:
    tz_name = os.environ.get("TIMEZONE", "Europe/Amsterdam")
    start = _parse_hhmm(os.environ.get("WINDOW_START", "11:00"))
    end = _parse_hhmm(os.environ.get("WINDOW_END", "04:00"))

    now = datetime.now(ZoneInfo(tz_name)).time()

    if start <= end:
        # Simple same-day window, e.g. 09:00-17:00
        return start <= now <= end
    else:
        # Wraps past midnight, e.g. 11:00-04:00
        return now >= start or now <= end


# --------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------

def parse_multiplier(text: str):
    """Extract an integer multiplier from strings like 'x10', '5 X', 'X1'."""
    if not text:
        return None
    m = re.search(r"(\d+)", text)
    return int(m.group(1)) if m else None


def parse_fungames():
    r = requests.get(FUNGAMES_URL, headers=HEADERS, timeout=25)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    table = soup.find("table")
    events = []
    if not table:
        print("WARN: no table found on fungamescentral page")
        return events

    for row in table.find_all("tr")[1:]:
        cells = row.find_all("td")
        if len(cells) < 7:
            continue

        host = cells[1].get_text(strip=True)

        parcel_cell = cells[2]
        parcel_link = parcel_cell.find("a")
        parcel_name = parcel_link.get_text(strip=True) if parcel_link else parcel_cell.get_text(strip=True)
        parcel_href = parcel_link["href"] if parcel_link and parcel_link.has_attr("href") else None

        map_cell = cells[3]
        map_link = map_cell.find("a")
        map_href = map_link["href"] if map_link and map_link.has_attr("href") else None

        event_text = cells[4].get_text(strip=True)
        lindens = cells[5].get_text(strip=True)
        land_points = cells[6].get_text(strip=True)

        mult = parse_multiplier(event_text)
        if mult is None:
            continue

        key = f"fgc:{parcel_href or parcel_name}"
        events.append({
            "source": "FunGamesCentral",
            "key": key,
            "multiplier": mult,
            "title": parcel_name,
            "extra": f"Host: {host} | L$ Total: {lindens} | Land Points: {land_points}",
            "link": map_href or parcel_href,
        })
    return events


def parse_secondfarm():
    r = requests.get(SECONDFARM_URL, headers=HEADERS, timeout=25)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    table = soup.find("table")
    events = []
    if not table:
        print("WARN: no table found on thesecondfarm page")
        return events

    for row in table.find_all("tr")[1:]:
        cells = row.find_all("td")
        if len(cells) < 8:
            continue

        land_points = cells[2].get_text(strip=True)
        parcel = cells[4].get_text(strip=True)
        money = cells[5].get_text(strip=True)
        event_text = cells[6].get_text(strip=True)

        loc_cell = cells[7]
        loc_link = loc_cell.find("a")
        location = loc_link.get_text(strip=True) if loc_link else loc_cell.get_text(strip=True)
        loc_href = loc_link["href"] if loc_link and loc_link.has_attr("href") else None

        mult = parse_multiplier(event_text)
        if mult is None:
            continue

        key = f"tsf:{loc_href or location}"
        events.append({
            "source": "TheSecondFarm",
            "key": key,
            "multiplier": mult,
            "title": parcel or location,
            "extra": f"Money: {money} | Land Points: {land_points} | Location: {location}",
            "link": loc_href,
        })
    return events


# --------------------------------------------------------------------------
# State handling (used to avoid re-sending the same event/multiplier twice)
# --------------------------------------------------------------------------

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"WARN: could not read state file ({e}), starting fresh")
    return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)


# --------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------

def send_telegram(token: str, chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(
        url,
        data={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=20,
    )
    resp.raise_for_status()


def format_message(e: dict) -> str:
    lines = [
        f"🎣 <b>x{e['multiplier']} event!</b>",
        f"Source: {e['source']}",
        f"Parcel: {e['title']}",
        e["extra"],
    ]
    if e.get("link"):
        lines.append(f"Link: {e['link']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    if not in_notification_window():
        print("Outside notification window, skipping this run.")
        return

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("ERROR: TELEGRAM_BOT_TOKEN and/or TELEGRAM_CHAT_ID are not set.")
        sys.exit(1)

    state = load_state()

    all_events = []
    for parser in (parse_fungames, parse_secondfarm):
        try:
            all_events.extend(parser())
        except Exception as e:
            print(f"ERROR while parsing with {parser.__name__}: {e}")

    print(f"Scraped {len(all_events)} total event rows.")

    # Drop state entries for events that are no longer listed at all,
    # so they can trigger a fresh notification if they reappear later.
    current_keys = {e["key"] for e in all_events}
    for k in list(state.keys()):
        if k not in current_keys:
            del state[k]

    notified = 0
    for e in all_events:
        if not (MIN_MULTIPLIER <= e["multiplier"] <= MAX_MULTIPLIER):
            continue

        # Only notify if this is new, or the multiplier changed since last time.
        if state.get(e["key"]) == e["multiplier"]:
            continue

        try:
            send_telegram(token, chat_id, format_message(e))
            state[e["key"]] = e["multiplier"]
            notified += 1
        except Exception as ex:
            print(f"ERROR sending Telegram message for {e['key']}: {ex}")

    save_state(state)
    print(f"Done. Sent {notified} new notification(s).")


if __name__ == "__main__":
    main()

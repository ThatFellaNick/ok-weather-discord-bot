"""US Weather Discord Bot.

Runs as a long-lived Docker service for configurable US weather monitoring.

Responsibilities:
- Poll NWS active alerts and SPC products.
- Post selected regional alerts and SPC items to Discord and/or Teams.
- Send scheduled regional weather briefings with SPC, radar, forecast point,
  and forecaster-discussion context.
- Store dedupe and scheduling state under the Docker /data mount.

Security notes:
- Discord and Microsoft Teams webhook URLs must come from environment variables only.
- Runtime state, logs, and local .env files are not source files.
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from html import unescape
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import feedparser
import requests
from dateutil import parser as dtparser

# Runtime configuration loaded from Docker/.env.
TZ = ZoneInfo(os.getenv("TZ", "America/Chicago"))
NWS_USER_AGENT = os.getenv("NWS_USER_AGENT", "ok-weather-discord-bot/2.4")
BRIEF_WEBHOOK_URL = os.getenv("BRIEF_WEBHOOK_URL", "")
ALERT_WEBHOOK_URL = os.getenv("ALERT_WEBHOOK_URL", "")
BRIEF_WEBHOOK_URLS = os.getenv("BRIEF_WEBHOOK_URLS", "")
ALERT_WEBHOOK_URLS = os.getenv("ALERT_WEBHOOK_URLS", "")
TEAMS_BRIEF_WEBHOOK_URL = os.getenv("TEAMS_BRIEF_WEBHOOK_URL", "")
TEAMS_ALERT_WEBHOOK_URL = os.getenv("TEAMS_ALERT_WEBHOOK_URL", "")
TEAMS_BRIEF_WEBHOOK_URLS = os.getenv("TEAMS_BRIEF_WEBHOOK_URLS", "")
TEAMS_ALERT_WEBHOOK_URLS = os.getenv("TEAMS_ALERT_WEBHOOK_URLS", "")
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "180"))
BRIEF_HOUR = int(os.getenv("BRIEF_HOUR", "9"))
BRIEF_MINUTE = int(os.getenv("BRIEF_MINUTE", "0"))
AFTERNOON_SEVERE_BRIEF_ENABLED = os.getenv("AFTERNOON_SEVERE_BRIEF_ENABLED", "true").lower() == "true"
AFTERNOON_SEVERE_BRIEF_HOUR = int(os.getenv("AFTERNOON_SEVERE_BRIEF_HOUR", "15"))
AFTERNOON_SEVERE_BRIEF_MINUTE = int(os.getenv("AFTERNOON_SEVERE_BRIEF_MINUTE", "30"))
DISCORD_MAX_RETRIES = int(os.getenv("DISCORD_MAX_RETRIES", "3"))
TEAMS_MAX_RETRIES = int(os.getenv("TEAMS_MAX_RETRIES", os.getenv("DISCORD_MAX_RETRIES", "3")))
HTTP_MAX_RETRIES = int(os.getenv("HTTP_MAX_RETRIES", "3"))
SEVERE_THUNDERSTORM_WARNING_MODE = os.getenv("SEVERE_THUNDERSTORM_WARNING_MODE", "all").lower()
STATE_FILE = os.getenv("STATE_FILE", "/data/state.json")
LOG_FILE = os.getenv("LOG_FILE", "/data/weather.log")
SEND_STARTUP_MESSAGE = os.getenv("SEND_STARTUP_MESSAGE", "true").lower() == "true"
TEST_BRIEF_ON_START = os.getenv("TEST_BRIEF_ON_START", "false").lower() == "true"
TRIGGER_BRIEF_FILE = os.getenv("TRIGGER_BRIEF_FILE", "/data/trigger_brief")
TRIGGER_ALERT_TEST_FILE = os.getenv("TRIGGER_ALERT_TEST_FILE", "/data/trigger_alert_test")
AFD_OFFICES_RAW = os.getenv("AFD_OFFICES", "")
INCLUDE_BRIEF_IMAGES = os.getenv("INCLUDE_BRIEF_IMAGES", "true").lower() == "true"
TARGET_NAME = os.getenv("TARGET_NAME", "Oklahoma")
TARGET_MODE_RAW = os.getenv("TARGET_MODE", "")
TARGET_STATES = [state.strip().upper() for state in os.getenv("TARGET_STATES", os.getenv("TARGET_STATE", "OK")).split(",") if state.strip()]
TARGET_POINTS_RAW = os.getenv("TARGET_POINTS", "")
TARGET_RADIUS_MILES = float(os.getenv("TARGET_RADIUS_MILES", "0") or "0")
TARGET_BBOX = os.getenv("TARGET_BBOX", "")
RADAR_STATIONS_RAW = os.getenv("RADAR_STATIONS", "")

RADAR_STATION_POINTS = {
    "KTLX": (35.333, -97.277),
    "KINX": (36.175, -95.564),
    "KFDR": (34.362, -98.976),
}
OKLAHOMA_RADAR_STATIONS = ["KTLX", "KINX", "KFDR"]
_RADAR_STATIONS_CACHE = None
OKLAHOMA_AFD_OFFICES = ["OUN", "TSA"]
_AFD_OFFICES_CACHE = None

# External data sources used by the bot.
NWS_ALERTS_ACTIVE = "https://api.weather.gov/alerts/active"
NWS_PRODUCT_LATEST = "https://api.weather.gov/products/types/{product_type}/locations/{office}/latest"
SPC_RSS = "https://www.spc.noaa.gov/products/spcrss.xml"
SPC_DAY1_TXT = "https://www.spc.noaa.gov/products/outlook/day1otlk.txt"
SPC_DAY2_TXT = "https://www.spc.noaa.gov/products/outlook/day2otlk.txt"
# Discord brief embeds render the static SPC PNG outlook maps reliably.
SPC_DAY1_MAP = "https://www.spc.noaa.gov/products/outlook/day1otlk.png"
SPC_DAY2_MAP = "https://www.spc.noaa.gov/products/outlook/day2otlk.png"
SPC_OUTLOOK_MAPSERVER = "https://mapservices.weather.noaa.gov/vector/rest/services/outlooks/SPC_wx_outlks/MapServer"
OKLAHOMA_BBOX = "-103.1,33.5,-94.4,37.1"
TARGET_POINTS_DEFAULT = "OKC:35.4676,-97.5164;Tulsa:36.1540,-95.9928;Lawton:34.6036,-98.3959"
STATE_BBOXES = {
    "AL": "-88.5,30.1,-84.9,35.1",
    "AK": "-179.2,51.2,-129.9,71.5",
    "AZ": "-114.9,31.2,-109.0,37.1",
    "AR": "-94.7,33.0,-89.6,36.5",
    "CA": "-124.5,32.5,-114.1,42.1",
    "CO": "-109.1,36.9,-102.0,41.1",
    "CT": "-73.8,40.9,-71.8,42.1",
    "DE": "-75.8,38.4,-75.0,39.9",
    "FL": "-87.7,24.4,-80.0,31.1",
    "GA": "-85.7,30.3,-80.8,35.1",
    "HI": "-160.3,18.8,-154.8,22.3",
    "ID": "-117.3,42.0,-111.0,49.1",
    "IL": "-91.6,36.9,-87.0,42.6",
    "IN": "-88.2,37.7,-84.8,41.8",
    "IA": "-96.7,40.3,-90.1,43.6",
    "KS": "-102.1,36.9,-94.5,40.1",
    "KY": "-89.6,36.4,-81.9,39.2",
    "LA": "-94.1,28.9,-88.8,33.1",
    "ME": "-71.2,43.0,-66.9,47.5",
    "MD": "-79.6,37.8,-75.0,39.8",
    "MA": "-73.6,41.2,-69.9,42.9",
    "MI": "-90.5,41.7,-82.1,48.4",
    "MN": "-97.3,43.4,-89.5,49.4",
    "MS": "-91.7,30.1,-88.1,35.1",
    "MO": "-95.8,35.9,-89.1,40.7",
    "MT": "-116.1,44.3,-104.0,49.1",
    "NE": "-104.1,39.9,-95.3,43.1",
    "NV": "-120.1,35.0,-114.0,42.1",
    "NH": "-72.6,42.7,-70.6,45.4",
    "NJ": "-75.6,38.8,-73.9,41.4",
    "NM": "-109.1,31.3,-103.0,37.1",
    "NY": "-79.8,40.4,-71.8,45.1",
    "NC": "-84.4,33.8,-75.4,36.7",
    "ND": "-104.1,45.9,-96.5,49.1",
    "OH": "-84.9,38.4,-80.5,42.1",
    "OK": OKLAHOMA_BBOX,
    "OR": "-124.7,41.9,-116.4,46.4",
    "PA": "-80.6,39.7,-74.7,42.6",
    "RI": "-71.9,41.1,-71.1,42.1",
    "SC": "-83.4,32.0,-78.5,35.3",
    "SD": "-104.1,42.4,-96.4,45.9",
    "TN": "-90.4,34.9,-81.6,36.8",
    "TX": "-106.7,25.8,-93.5,36.6",
    "UT": "-114.1,36.9,-109.0,42.1",
    "VT": "-73.5,42.7,-71.4,45.1",
    "VA": "-83.8,36.5,-75.2,39.5",
    "WA": "-124.9,45.5,-116.9,49.1",
    "WV": "-82.7,37.1,-77.7,40.7",
    "WI": "-92.9,42.4,-86.8,47.1",
    "WY": "-111.1,40.9,-104.0,45.1",
    "DC": "-77.2,38.8,-76.9,39.0",
}

# NOAA MapServer layer IDs for SPC categorical and hazard probability products.
SPC_GIS_LAYERS = {
    "Day 1": {
        "category": 1,
        "tornado_intensity": 2,
        "tornado": 3,
        "hail_intensity": 4,
        "hail": 5,
        "wind_intensity": 6,
        "wind": 7,
    },
    "Day 2": {
        "category": 9,
        "tornado_intensity": 10,
        "tornado": 11,
        "hail_intensity": 12,
        "hail": 13,
        "wind_intensity": 14,
        "wind": 15,
    },
}

IMPORTANT_EVENTS = {
    "Tornado Warning",
    "Tornado Watch",
    "Severe Thunderstorm Warning",
    "Severe Thunderstorm Watch",
    "Severe Weather Statement",
    "Special Weather Statement",
    "Flash Flood Warning",
}

HIGH_SIGNAL_EVENTS = {
    "Tornado Warning",
    "Tornado Watch",
    "Severe Thunderstorm Watch",
}

RADAR_IMAGE_EVENTS = {
    "Special Weather Statement",
    "Tornado Warning",
    "Severe Thunderstorm Warning",
}

# Shared parsing and presentation tables.
OKLAHOMA_WORDS = re.compile(
    r"\b(oklahoma|\bok\b|okc|oklahoma city|tulsa|norman|lawton|enid|ardmore|woodward|ponca|stillwater|mcalester|altus|guymon|elk city|clinton|chickasha|shawnee|seminole|ada|durant|idabel)\b",
    re.I,
)
TIMING_WORDS = re.compile(r"\b(this|today|tonight|overnight|morning|afternoon|evening|late|early|after|before|through|by|around|\d{1,2}\s*(?:am|pm|AM|PM)|CDT|CST)\b")
SPC_IMPORTANT = re.compile(r"(mesoscale discussion|tornado watch|severe thunderstorm watch|convective outlook|day 1|day 2)", re.I)
RISK_ORDER = ["TSTM", "MRGL", "SLGT", "ENH", "MDT", "HIGH"]
RISK_LABELS = {
    "TSTM": "General Thunder",
    "MRGL": "Marginal",
    "SLGT": "Slight",
    "ENH": "Enhanced",
    "MDT": "Moderate",
    "HIGH": "High",
}
RISK_WORD_TO_CODE = {
    "THUNDER": "TSTM",
    "MARGINAL": "MRGL",
    "SLIGHT": "SLGT",
    "ENHANCED": "ENH",
    "MODERATE": "MDT",
    "HIGH": "HIGH",
}
CATEGORY_DN_LABELS = {
    2: "General Thunder",
    3: "Marginal",
    4: "Slight",
    5: "Enhanced",
    6: "Moderate",
    8: "High",
}
GIS_RISK_ORDER = ["None found", "General Thunder", "Marginal", "Slight", "Enhanced", "Moderate", "High", "Unavailable"]
RISK_COLORS = {
    "None found": 0x607D8B,
    "General Thunder": 0x55BB55,
    "Marginal": 0x006B00,
    "Slight": 0xDDAA00,
    "Enhanced": 0xFF6600,
    "Moderate": 0xCC0000,
    "High": 0xCC00CC,
    "Unavailable": 0x607D8B,
}

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE)],
)
log = logging.getLogger("ok-weather-bot")


# Target and delivery configuration helpers.
def split_webhook_urls(primary, extra):
    """Build a de-duplicated webhook list without logging or exposing secrets."""
    urls = []
    for raw in (primary, extra):
        for value in re.split(r"[\s,]+", raw or ""):
            value = value.strip()
            if value and value not in urls:
                urls.append(value)
    return urls


def parse_target_points(raw):
    """Parse semicolon-separated Name:lat,lon forecast/radius points."""
    points = {}
    if raw and raw.strip():
        source = raw.strip()
    elif TARGET_STATES == ["OK"] and target_label().lower() == "oklahoma":
        source = TARGET_POINTS_DEFAULT
    else:
        source = ""
    for item in source.split(";"):
        item = item.strip()
        if not item:
            continue
        name, separator, coords = item.partition(":")
        if not separator:
            log.warning("Ignoring TARGET_POINTS entry without name:lat,lon format")
            continue
        try:
            lat_text, lon_text = coords.split(",", 1)
            points[name.strip()] = (float(lat_text.strip()), float(lon_text.strip()))
        except ValueError:
            log.warning("Ignoring TARGET_POINTS entry with invalid coordinates: %s", name.strip())
    return points


def target_label():
    return TARGET_NAME or ", ".join(TARGET_STATES) or "configured area"


def target_mode():
    """Choose state or radius behavior while preserving pre-mode configs."""
    mode = (TARGET_MODE_RAW or "").strip().lower()
    if mode in {"state", "states"}:
        return "state"
    if mode in {"radius", "point", "points"}:
        return "radius"
    return "radius" if TARGET_RADIUS_MILES > 0 else "state"


def radius_target_enabled():
    return target_mode() == "radius" and TARGET_RADIUS_MILES > 0


def target_search_terms():
    terms = [target_label()]
    terms.extend(TARGET_STATES)
    terms.extend(target_points().keys())
    if "OK" in TARGET_STATES or target_label().lower() == "oklahoma":
        terms.extend(["Oklahoma", "OK", "OKC", "Oklahoma City", "Tulsa"])
    return [term for term in terms if term]


def target_location_pattern():
    terms = sorted(target_search_terms(), key=len, reverse=True)
    return "|".join(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])" for term in terms)


def target_words_regex():
    pattern = target_location_pattern()
    return re.compile(rf"\b(?:{pattern})\b", re.I) if pattern else OKLAHOMA_WORDS


def target_points():
    return parse_target_points(TARGET_POINTS_RAW)


def explicit_afd_offices():
    return [office.strip().upper() for office in AFD_OFFICES_RAW.split(",") if office.strip()]


def explicit_radar_stations():
    return [station.strip().upper() for station in RADAR_STATIONS_RAW.split(",") if station.strip()]


def default_oklahoma_profile():
    return TARGET_STATES == ["OK"] and target_label().lower() == "oklahoma"


def derived_afd_offices():
    global _AFD_OFFICES_CACHE
    if _AFD_OFFICES_CACHE is not None:
        return _AFD_OFFICES_CACHE
    offices = []
    for lat, lon in target_points().values():
        try:
            office = point_metadata(lat, lon).get("properties", {}).get("cwa", "")
        except Exception as e:
            log.warning("AFD office lookup failed for %.4f,%.4f: %s", lat, lon, e)
            office = ""
        office = str(office or "").strip().upper()
        if office and office not in offices:
            offices.append(office)
    _AFD_OFFICES_CACHE = offices
    return offices


def configured_afd_offices():
    explicit = explicit_afd_offices()
    if explicit:
        return explicit
    if default_oklahoma_profile():
        return OKLAHOMA_AFD_OFFICES
    return derived_afd_offices()


def derived_radar_stations():
    global _RADAR_STATIONS_CACHE
    if _RADAR_STATIONS_CACHE is not None:
        return _RADAR_STATIONS_CACHE
    stations = []
    for lat, lon in target_points().values():
        try:
            station = point_metadata(lat, lon).get("properties", {}).get("radarStation", "")
        except Exception as e:
            log.warning("Radar station lookup failed for %.4f,%.4f: %s", lat, lon, e)
            station = ""
        station = str(station or "").strip().upper()
        if station and station not in stations:
            stations.append(station)
    _RADAR_STATIONS_CACHE = stations
    return stations


def configured_radar_stations():
    explicit = explicit_radar_stations()
    if explicit:
        return explicit
    if default_oklahoma_profile():
        return OKLAHOMA_RADAR_STATIONS
    return derived_radar_stations()


def target_bbox():
    if TARGET_BBOX:
        return TARGET_BBOX
    if radius_target_enabled():
        points = target_points()
        if points:
            lats = [point[0] for point in points.values()]
            lons = [point[1] for point in points.values()]
            degrees = TARGET_RADIUS_MILES / 69.0
            return f"{min(lons) - degrees:.3f},{min(lats) - degrees:.3f},{max(lons) + degrees:.3f},{max(lats) + degrees:.3f}"
    if len(TARGET_STATES) == 1 and TARGET_STATES[0] in STATE_BBOXES:
        return STATE_BBOXES[TARGET_STATES[0]]
    boxes = [STATE_BBOXES[state] for state in TARGET_STATES if state in STATE_BBOXES]
    if boxes:
        coords = [[float(part) for part in box.split(",")] for box in boxes]
        return f"{min(box[0] for box in coords):.3f},{min(box[1] for box in coords):.3f},{max(box[2] for box in coords):.3f},{max(box[3] for box in coords):.3f}"
    return OKLAHOMA_BBOX


def miles_between(lat1, lon1, lat2, lon2):
    """Return an approximate great-circle distance in miles."""
    from math import asin, cos, radians, sin, sqrt

    radius_miles = 3958.8
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * radius_miles * asin(sqrt(a))


def feature_in_target_radius(feature):
    if not radius_target_enabled():
        return True
    center = geometry_center(feature.get("geometry"))
    if not center:
        return False
    points = target_points()
    if not points:
        return True
    lat, lon = center
    return any(miles_between(lat, lon, point_lat, point_lon) <= TARGET_RADIUS_MILES for point_lat, point_lon in points.values())


# Persistent state helpers.
def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except FileNotFoundError:
        state = {}
    except Exception as e:
        log.warning("State load failed: %s", e)
        state = {}
    state.setdefault("seen_alerts", [])
    state.setdefault("seen_spc", [])
    state.setdefault("last_brief_date", None)
    state.setdefault("last_afternoon_severe_brief_date", None)
    state.setdefault("startup_sent", False)
    return state


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    state["seen_alerts"] = state.get("seen_alerts", [])[-500:]
    # SPC stores multiple aliases per product so RSS ID drift cannot repost it.
    state["seen_spc"] = state.get("seen_spc", [])[-1200:]
    temp_file = f"{STATE_FILE}.tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(temp_file, STATE_FILE)


# HTTP and Discord delivery helpers.
def get(url, accept="application/json"):
    headers = {"User-Agent": NWS_USER_AGENT, "Accept": accept}
    for attempt in range(1, HTTP_MAX_RETRIES + 1):
        try:
            r = requests.get(url, headers=headers, timeout=25)
            if r.status_code >= 500 and attempt < HTTP_MAX_RETRIES:
                log.warning("GET failed %s for %s; retrying", r.status_code, url)
                time.sleep(attempt)
                continue
            r.raise_for_status()
            return r
        except Exception:
            if attempt >= HTTP_MAX_RETRIES:
                raise
            log.warning("GET exception for %s; retrying", url)
            time.sleep(attempt)


def get_json(url):
    return get(url, "application/geo+json").json()


def get_text(url):
    return get(url, "text/plain").text


def get_json_with_params(url, params):
    headers = {"User-Agent": NWS_USER_AGENT, "Accept": "application/json"}
    for attempt in range(1, HTTP_MAX_RETRIES + 1):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=25)
            if r.status_code >= 500 and attempt < HTTP_MAX_RETRIES:
                log.warning("GET failed %s for %s; retrying", r.status_code, url)
                time.sleep(attempt)
                continue
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt >= HTTP_MAX_RETRIES:
                raise
            log.warning("GET exception for %s; retrying", url)
            time.sleep(attempt)


def post_discord(webhook_url, content=None, embeds=None):
    if not webhook_url or "YOUR_" in webhook_url or "PASTE_" in webhook_url or "REPLACE_ME" in webhook_url:
        log.info("Webhook URL not configured, skipping Discord post")
        return False
    payload = {}
    if content:
        payload["content"] = content[:1900]
    if embeds:
        payload["embeds"] = embeds[:10]
    for attempt in range(1, DISCORD_MAX_RETRIES + 1):
        try:
            r = requests.post(webhook_url, json=payload, timeout=20)
            if r.status_code == 429:
                retry_after = float(r.json().get("retry_after", 1))
                log.warning("Discord rate limited; retrying in %.1f seconds", retry_after)
                time.sleep(retry_after)
                continue
            if r.status_code >= 500 and attempt < DISCORD_MAX_RETRIES:
                log.warning("Discord post failed %s; retrying", r.status_code)
                time.sleep(attempt)
                continue
            if r.status_code >= 300:
                log.warning("Discord post failed %s: %s", r.status_code, r.text[:500])
                return False
            return True
        except Exception as e:
            if attempt >= DISCORD_MAX_RETRIES:
                log.warning("Discord post exception: %s", e)
                return False
            log.warning("Discord post exception; retrying: %s", e)
            time.sleep(attempt)
    return False


def teams_color(color):
    if not isinstance(color, int):
        return "607D8B"
    return f"{max(0, min(color, 0xFFFFFF)):06X}"


def teams_text(*parts, max_len=7000):
    text = "\n\n".join(str(part) for part in parts if part)
    if not text:
        return ""
    text = text.replace("**", "")
    return clean(text, max_len)


def teams_adaptive_card_from_embed(embed=None, content=None):
    """Convert one Discord-style embed into a Teams Workflow Adaptive Card."""
    embed = embed or {}
    title = teams_text(embed.get("title") or content or "Weather Bot", max_len=200)
    body_items = [
        {
            "type": "TextBlock",
            "text": title,
            "weight": "Bolder",
            "size": "Medium",
            "wrap": True,
        }
    ]

    body = teams_text(content, embed.get("description"), max_len=7000)
    if body:
        body_items.append({"type": "TextBlock", "text": body, "wrap": True})

    facts = []
    for field in embed.get("fields", [])[:12]:
        name = teams_text(field.get("name", ""), max_len=80)
        value = teams_text(field.get("value", ""), max_len=800)
        if name and value:
            facts.append({"title": name, "value": value})
    if facts:
        body_items.append({"type": "FactSet", "facts": facts})

    image_url = (embed.get("image") or {}).get("url", "")
    if isinstance(image_url, str) and image_url.startswith(("https://", "http://")):
        body_items.append({"type": "Image", "url": image_url, "size": "Stretch"})

    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body_items,
    }

    url = embed.get("url", "")
    if isinstance(url, str) and url.startswith(("https://", "http://")):
        card["actions"] = [{"type": "Action.OpenUrl", "title": "Open source", "url": url}]
    return card


def teams_payload_from_embed(embed=None, content=None):
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": teams_adaptive_card_from_embed(embed, content=content),
            }
        ],
    }


def post_teams(webhook_url, content=None, embeds=None):
    if not webhook_url or "YOUR_" in webhook_url or "PASTE_" in webhook_url or "REPLACE_ME" in webhook_url:
        log.info("Webhook URL not configured, skipping Teams post")
        return False

    embed_cards = embeds[:10] if embeds else [None]
    sent_any = False
    for index, embed in enumerate(embed_cards):
        payload = teams_payload_from_embed(embed, content=content if index == 0 else None)
        for attempt in range(1, TEAMS_MAX_RETRIES + 1):
            try:
                r = requests.post(webhook_url, json=payload, timeout=20)
                if r.status_code == 429:
                    retry_after = float(r.headers.get("Retry-After", attempt))
                    log.warning("Teams rate limited; retrying in %.1f seconds", retry_after)
                    time.sleep(retry_after)
                    continue
                if r.status_code >= 500 and attempt < TEAMS_MAX_RETRIES:
                    log.warning("Teams post failed %s; retrying", r.status_code)
                    time.sleep(attempt)
                    continue
                if r.status_code >= 300:
                    log.warning("Teams post failed %s: %s", r.status_code, r.text[:500])
                    return sent_any
                sent_any = True
                break
            except Exception as e:
                if attempt >= TEAMS_MAX_RETRIES:
                    log.warning("Teams post exception: %s", e)
                    return sent_any
                log.warning("Teams post exception; retrying: %s", e)
                time.sleep(attempt)
    return sent_any


def post_discord_many(webhook_urls, content=None, embeds=None):
    urls = webhook_urls if isinstance(webhook_urls, list) else [webhook_urls]
    if not urls:
        return post_discord("", content=content, embeds=embeds)
    sent_any = False
    for url in urls:
        if post_discord(url, content=content, embeds=embeds):
            sent_any = True
    return sent_any


def post_teams_many(webhook_urls, content=None, embeds=None):
    urls = webhook_urls if isinstance(webhook_urls, list) else [webhook_urls]
    sent_any = False
    for url in urls:
        if post_teams(url, content=content, embeds=embeds):
            sent_any = True
    return sent_any


def brief_webhook_urls():
    return split_webhook_urls(BRIEF_WEBHOOK_URL, BRIEF_WEBHOOK_URLS)


def alert_webhook_urls():
    return split_webhook_urls(ALERT_WEBHOOK_URL, ALERT_WEBHOOK_URLS)


def teams_brief_webhook_urls():
    return split_webhook_urls(TEAMS_BRIEF_WEBHOOK_URL, TEAMS_BRIEF_WEBHOOK_URLS)


def teams_alert_webhook_urls():
    return split_webhook_urls(TEAMS_ALERT_WEBHOOK_URL, TEAMS_ALERT_WEBHOOK_URLS)


def post_brief_channels(content=None, embeds=None):
    discord_sent = post_discord_many(brief_webhook_urls(), content=content, embeds=embeds)
    teams_sent = post_teams_many(teams_brief_webhook_urls(), content=content, embeds=embeds)
    return discord_sent or teams_sent


def post_alert_channels(content=None, embeds=None):
    discord_sent = post_discord_many(alert_webhook_urls(), content=content, embeds=embeds)
    teams_sent = post_teams_many(teams_alert_webhook_urls(), content=content, embeds=embeds)
    return discord_sent or teams_sent


# Text cleanup and small formatting helpers.
def clean(text, max_len=900):
    if not text:
        return ""
    text = str(text).replace("Â°", "°")
    text = re.sub(r"\s+", " ", str(text)).strip()
    return text[:max_len] + ("..." if len(text) > max_len else "")


def clean_html(text, max_len=900):
    if not text:
        return ""
    text = unescape(str(text))
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?is)<img\b[^>]*>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", " ", text)
    text = re.sub(r"(?i)</(?:p|div|pre|li|tr|h[1-6])>", " ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    return clean(unescape(text), max_len)


def bullet_list(items):
    return "\n".join(f"• {item}" for item in items if item)


def format_local_time(value):
    if not value:
        return "Unknown"
    try:
        return dtparser.parse(value).astimezone(TZ).strftime("%-I:%M %p %Z")
    except Exception:
        return value


def county_count(area_desc):
    if not area_desc:
        return 0
    return len([part for part in re.split(r";", area_desc) if part.strip()])


def watch_number(props):
    text = " ".join(str(props.get(key, "")) for key in ("headline", "description", "event"))
    match = re.search(r"\b(?:Watch(?: Number)?|WW)\s*#?\s*(\d{1,4})\b", text, re.I)
    return f" #{match.group(1)}" if match else ""


def brief_alert_line(props):
    event = props.get("event", "Alert")
    if event in {"Severe Thunderstorm Watch", "Tornado Watch"}:
        count = county_count(props.get("areaDesc", ""))
        area_kind = f"{target_label()} counties" if TARGET_STATES == ["OK"] and target_label().lower() == "oklahoma" else f"{target_label()} areas"
        county_text = f"{count} {area_kind}" if count else area_kind
        return f"**{event}{watch_number(props)}**: {county_text}, expires {format_local_time(props.get('expires'))}"
    return f"**{event}**: {clean(props.get('areaDesc', ''), 120)}"


# NWS alert fetching, filtering, and embed formatting.
def fetch_alert_features(params):
    data = get_json_with_params(NWS_ALERTS_ACTIVE, params)
    return data.get("features", [])


def fetch_active_target_alerts():
    features = []
    seen = set()
    states = TARGET_STATES or ["OK"]

    for state in states:
        for feature in fetch_alert_features({"area": state}):
            key = alert_key(feature)
            if key not in seen and feature_in_target_radius(feature):
                features.append(feature)
                seen.add(key)

    if radius_target_enabled():
        for lat, lon in target_points().values():
            for feature in fetch_alert_features({"point": f"{lat},{lon}"}):
                key = alert_key(feature)
                if key not in seen:
                    features.append(feature)
                    seen.add(key)
    return features


def alert_key(feature):
    props = feature.get("properties", {})
    return props.get("id") or props.get("@id") or props.get("event", "") + props.get("sent", "")


def should_send_alert(props):
    event = props.get("event", "")
    severity = props.get("severity", "")
    desc = f"{props.get('headline','')} {props.get('description','')} {props.get('instruction','')}".lower()
    if event in HIGH_SIGNAL_EVENTS:
        return True
    if event == "Severe Thunderstorm Warning":
        if SEVERE_THUNDERSTORM_WARNING_MODE == "high_end":
            return bool(re.search(r"(baseball|softball|tennis ball|golf ball|2\.00|1\.75|70 mph|75 mph|80 mph|considerable|destructive)", desc))
        return True
    if event == "Special Weather Statement":
        return True
    if event in IMPORTANT_EVENTS and severity in {"Extreme", "Severe"}:
        return True
    if severity == "Extreme":
        return True
    return False


def alert_color(event, severity):
    if event in {"Tornado Warning", "Tornado Watch"}:
        return 0xB00020
    if event in {"Severe Thunderstorm Warning", "Severe Thunderstorm Watch"}:
        return 0xFF9900
    if event in {"Flash Flood Warning"}:
        return 0x00AEEF
    if severity == "Extreme":
        return 0xB00020
    if severity == "Severe":
        return 0xFF9900
    return 0x607D8B


def alert_severity_label(event, severity, urgency="", certainty=""):
    parts = [part for part in (severity, urgency, certainty) if part]
    if not parts:
        return "Unavailable"
    return " / ".join(parts)


def alert_title(event, title, props):
    base = title or f"{event}{watch_number(props)}"
    if event == "Tornado Warning":
        return f"🚨🌪️ {base}"
    if event == "Severe Thunderstorm Warning":
        return f"⚠️⛈️ {base}"
    return base


def alert_headline(event, headline):
    if event == "Tornado Warning":
        return f"🚨 **TAKE SHELTER NOW:** {headline}"
    if event == "Severe Thunderstorm Warning":
        return f"⚠️ **SEVERE STORM WARNING:** {headline}"
    return f"**{headline}**"


def alert_importance_text(event):
    if event == "Tornado Warning":
        return "Highest priority alert. Move to shelter immediately if you are in the warned area."
    if event == "Severe Thunderstorm Warning":
        return "High priority alert. Damaging wind, hail, and frequent lightning may be possible."
    return ""


def notification_area(props):
    area = compact_area_desc((props or {}).get("areaDesc", ""), max_items=2)
    return area or target_label()


def alert_post_content(event, props=None):
    area = notification_area(props)
    if event == "Tornado Warning":
        return f"🚨🌪️ **TORNADO WARNING:** in/near {area}"
    if event == "Severe Thunderstorm Warning":
        return f"⚠️⛈️ **Severe Thunderstorm Warning:** in/near {area}"
    return f"**{event}** - in/near {area}"


def alert_time_label(name, value):
    formatted = format_local_time(value)
    if formatted == "Unknown":
        return ""
    return f"**{name}:** {formatted}"


def alert_footer(props):
    sent = alert_time_label("Sent", props.get("sent"))
    effective = alert_time_label("Effective", props.get("effective"))
    expires = alert_time_label("Expires", props.get("expires"))
    return "\n".join(part for part in (sent, effective, expires) if part)


def compact_area_desc(area_desc, max_items=8):
    areas = [part.strip() for part in re.split(r";", area_desc or "") if part.strip()]
    if not areas:
        return ""
    if len(areas) <= max_items:
        return "; ".join(areas)
    shown = "; ".join(areas[:max_items])
    return f"{len(areas)} areas: {shown}; +{len(areas) - max_items} more"


def product_sentence(text, max_len=180):
    text = clean(text.strip(" .;:-"), max_len)
    if not text:
        return ""
    if text.isupper():
        text = text.capitalize()
    return text


def first_sentences(text, limit=2):
    sentences = []
    for sentence in re.split(r"(?<=[.!?])\s+", clean(text, 1200)):
        sentence = product_sentence(sentence)
        if not sentence:
            continue
        if re.search(r"national weather service|following areas|this watch includes", sentence, re.I):
            continue
        sentences.append(sentence)
        if len(sentences) >= limit:
            break
    return sentences


def labeled_product_lines(text, labels):
    lines = []
    for label in labels:
        pattern = rf"\b{label}\b\.*\s*(.*?)(?=\b(?:{'|'.join(labels)})\b\.*|$)"
        match = re.search(pattern, text, re.I | re.S)
        if match:
            value = product_sentence(match.group(1), 190)
            if value:
                lines.append(f"**{label.title()}:** {value}")
    return lines


def primary_threat_lines(text, max_items=3):
    match = re.search(r"primary threats include\.*(.*?)(?=\bsummary\b|\bdiscussion\b|\bprecautionary\b|&&|$)", text, re.I | re.S)
    if not match:
        return []
    threat_text = clean(match.group(1), 700)
    parts = re.split(r"\s*\.\.\.\s*|(?<=[.!?])\s+", threat_text)
    lines = []
    for part in parts:
        item = product_sentence(part, 170)
        if item:
            lines.append(item)
        if len(lines) >= max_items:
            break
    return lines


def summary_section_lines(text, max_items=2):
    match = re.search(r"\bsummary\b\.*\s*(.*?)(?=\bdiscussion\b|\bprecautionary\b|&&|$)", text, re.I | re.S)
    if not match:
        return []
    return first_sentences(match.group(1), max_items)


def concise_product_summary(text, *, fallback="Details unavailable."):
    text = clean_html(text, 1800)
    lines = primary_threat_lines(text)
    if not lines:
        lines = summary_section_lines(text)
    if not lines:
        lines = labeled_product_lines(text, ["HAZARD", "SOURCE", "IMPACT"])
    if not lines:
        lines = first_sentences(text)
    return bullet_list(lines) or fallback


def geometry_center(geometry):
    points = []

    def collect(value):
        if not isinstance(value, list):
            return
        if len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
            points.append((float(value[1]), float(value[0])))
            return
        for item in value:
            collect(item)

    collect((geometry or {}).get("coordinates", []))
    if not points:
        return None
    lat = sum(point[0] for point in points) / len(points)
    lon = sum(point[1] for point in points) / len(points)
    return lat, lon


def nearest_radar_station(geometry=None):
    stations = configured_radar_stations()
    if not stations:
        return ""
    center = geometry_center(geometry)
    candidates = [station for station in stations if station in RADAR_STATION_POINTS]
    if not center or not candidates:
        return stations[0]
    lat, lon = center
    return min(candidates, key=lambda station: (RADAR_STATION_POINTS[station][0] - lat) ** 2 + (RADAR_STATION_POINTS[station][1] - lon) ** 2)


def radar_image_url(station=None):
    stations = configured_radar_stations()
    station = station or (stations[0] if stations else "")
    if not station:
        return ""
    cache_key = datetime.now(TZ).strftime("%Y%m%d%H%M")
    # Keep radar on the animated GIF loop; the query string nudges Discord past stale caches.
    return f"https://radar.weather.gov/ridge/standard/{station}_loop.gif?v={cache_key}"


def alert_radar_image_url(event, geometry=None):
    if not INCLUDE_BRIEF_IMAGES:
        return ""
    if event not in RADAR_IMAGE_EVENTS:
        return ""
    return radar_image_url(nearest_radar_station(geometry))


def build_alert_embed(props, *, title=None, description=None, geometry=None):
    event = props.get("event", "Weather Alert")
    severity = props.get("severity", "")
    urgency = props.get("urgency", "")
    certainty = props.get("certainty", "")
    area = clean(compact_area_desc(props.get("areaDesc", "")), 700)
    headline = clean(props.get("headline", event), 250)
    desc = concise_product_summary(description if description is not None else props.get("description", ""))
    instr = clean(props.get("instruction", ""), 650)
    importance = alert_importance_text(event)

    embed = {
        "title": alert_title(event, title, props),
        "description": alert_headline(event, headline) + (f"\n\n{desc}" if desc else ""),
        "color": alert_color(event, severity),
        "url": props.get("@id") or props.get("id") or "https://alerts.weather.gov",
        "fields": [],
    }
    if importance:
        add_embed_field(embed, "🚨 Importance", importance, False)
    add_embed_field(embed, "📍 Affected area", area or target_label(), False)
    add_embed_field(embed, "🚨 Severity", alert_severity_label(event, severity, urgency, certainty), True)
    add_embed_field(embed, "⏱️ Timing", alert_footer(props), True)
    if instr:
        add_embed_field(embed, "📢 Instruction", instr, False)
    radar = alert_radar_image_url(event, geometry)
    if radar:
        embed["image"] = {"url": radar}
    return embed


def strongest_alert_color(alerts):
    priority = {
        0xB00020: 6,
        0xFF0000: 5,
        0xFF9900: 3,
        0x00AEEF: 2,
        0x607D8B: 1,
    }
    best = 0x607D8B
    best_priority = 0
    for props in alerts:
        color = alert_color(props.get("event", ""), props.get("severity", ""))
        if priority.get(color, 0) > best_priority:
            best = color
            best_priority = priority.get(color, 0)
    return best


def send_new_nws_alerts(state):
    alerts = fetch_active_target_alerts()
    seen = set(state.get("seen_alerts", []))
    new_keys = []
    sent = 0
    for feature in alerts:
        props = feature.get("properties", {})
        if not should_send_alert(props):
            continue
        key = alert_key(feature)
        if key in seen:
            continue
        event = props.get("event", "Weather Alert")
        embed = build_alert_embed(props, geometry=feature.get("geometry"))
        if post_alert_channels(content=alert_post_content(event, props), embeds=[embed]):
            new_keys.append(key)
            seen.add(key)
            sent += 1
    state.setdefault("seen_alerts", []).extend(new_keys)
    if sent:
        log.info("Sent %s NWS alert(s)", sent)


# SPC RSS item filtering and Discord card formatting.
def fetch_spc_entries():
    feed = feedparser.parse(SPC_RSS)
    return feed.entries[:40]


def entry_id(entry):
    return entry.get("id") or entry.get("link") or entry.get("title", "")


def normalize_spc_identity(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def spc_entry_keys(entry):
    keys = set()
    for field in ("id", "link", "title"):
        raw = str(entry.get(field, "") or "").strip()
        if not raw:
            continue
        # Keep raw values for compatibility with state written by older versions.
        keys.add(raw)
        keys.add(f"{field}:{normalize_spc_identity(raw)}")
    fallback = entry_id(entry)
    if fallback:
        keys.add(str(fallback))
    return keys


def spc_target_search_text(entry, summary):
    text = f"{entry.get('title', '')} {summary}"
    text = re.sub(r"\bNWS Storm Prediction Center\s+Norman\s+(?:OK|Oklahoma)\b", " ", text, flags=re.I)
    text = re.sub(r"\bStorm Prediction Center\s+Norman\s+(?:OK|Oklahoma)\b", " ", text, flags=re.I)
    text = re.sub(r"\bATTN\.\.\.WFO\.\.\.[A-Z.]+", " ", text, flags=re.I)
    return clean(text, 2000)


def spc_explicit_location(entry):
    text = spc_target_search_text(entry, clean_html(entry.get("summary", "") or entry.get("description", ""), 1800))
    target_terms = target_location_pattern()
    location_patterns = [
        rf"\b(?:across|over|near|for portions of|from|in)\s+([^.;\n]*(?:{target_terms})[^.;\n]*)",
        rf"\b((?:central|northern|southern|eastern|western|northeastern|northwestern|southeastern|southwestern)[^.;\n]*(?:{target_terms})[^.;\n]*)",
    ]
    for pattern in location_patterns:
        match = re.search(pattern, text, re.I)
        if match:
            location = re.sub(r"\s+", " ", match.group(1)).strip(" .,:;-")
            return clean(location, 90)
    return ""


def spc_status_report(entry):
    title = entry.get("title", "")
    return bool(re.search(r"\bstatus (?:report|reports|update|updates)\b", title, re.I))


def should_post_spc_item(entry, summary):
    combined = spc_target_search_text(entry, summary)
    if not SPC_IMPORTANT.search(combined):
        return False
    location = spc_explicit_location(entry)
    if spc_status_report(entry) and not location:
        return False
    return bool(location or target_words_regex().search(combined))


def spc_item_color(title):
    title = title.lower()
    if "tornado watch" in title:
        return 0xFF0000
    if "severe thunderstorm watch" in title:
        return 0xFF9900
    if "mesoscale discussion" in title:
        return 0xFF9900
    if "convective outlook" in title:
        return 0xDDAA00
    return 0x607D8B


def build_spc_item_embed(entry):
    title = clean(entry.get("title", "SPC product"), 250)
    summary = concise_product_summary(entry.get("summary", ""), fallback=f"SPC product mentioning {target_label()}.")
    link = entry.get("link", "")
    embed = {
        "title": f"🌩️ {title}",
        "description": summary,
        "color": spc_item_color(title),
        "fields": [],
    }
    if link:
        embed["url"] = link
    image_url = spc_item_image_url(entry)
    if INCLUDE_BRIEF_IMAGES and image_url:
        embed["image"] = {"url": image_url}
    add_embed_field(embed, "📡 Source", "Storm Prediction Center", False)
    return embed


def spc_item_content(entry):
    title = clean(entry.get("title", "SPC product"), 120)
    location = spc_item_location(entry)
    if location:
        return f"🌩️ **SPC Item:** {title} - near {location}"
    return f"🌩️ **SPC Item:** {title}"


def spc_item_location(entry):
    location = spc_explicit_location(entry)
    if location:
        return location
    text = spc_target_search_text(entry, clean_html(entry.get("summary", "") or entry.get("description", ""), 1800))
    if target_words_regex().search(text):
        return f"{target_label()}/nearby region"
    return ""


def spc_item_image_url(entry):
    for media in entry.get("media_content", []) or []:
        url = media.get("url", "")
        image_url = normalize_spc_url(url)
        if image_url:
            return image_url
    # SPC RSS outlook images usually live inside the HTML summary/description.
    summary = unescape(entry.get("summary", "") or entry.get("description", ""))
    match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', summary, re.I)
    if match:
        return normalize_spc_url(unescape(match.group(1)))
    return ""


def normalize_spc_url(url):
    if not isinstance(url, str) or not url:
        return ""
    # Some SPC feed image paths are relative, such as /products/outlook/day2otlk.png.
    absolute_url = urljoin("https://www.spc.noaa.gov/", url.strip())
    if is_http_url(absolute_url):
        return absolute_url
    return ""


def is_http_url(url):
    return isinstance(url, str) and url.startswith(("https://", "http://"))


def send_new_spc_items(state):
    seen = set(state.get("seen_spc", []))
    new_keys = []
    sent = 0
    for entry in fetch_spc_entries():
        summary = clean_html(entry.get("summary", ""), 700)
        if not should_post_spc_item(entry, summary):
            continue
        keys = spc_entry_keys(entry)
        if seen.intersection(keys):
            continue
        if post_alert_channels(
            content=spc_item_content(entry),
            embeds=[build_spc_item_embed(entry)],
        ):
            new_keys.extend(sorted(keys))
            seen.update(keys)
            sent += 1
    state.setdefault("seen_spc", []).extend(new_keys)
    if sent:
        log.info("Sent %s SPC item(s)", sent)


# SPC Day 1/Day 2 outlook text parsing.
def parse_spc_outlook(text, day_label, url):
    outlook = {"day": day_label, "url": url, "headline": "", "risk": "None found", "risk_lines": [], "summary": ""}
    if not text:
        return outlook

    # Keep the most relevant headline block.
    head_match = re.search(r"DAY \d CONVECTIVE OUTLOOK.*?(?=\.\.\.|VALID|$)", text, re.I | re.S)
    if head_match:
        outlook["headline"] = clean(head_match.group(0), 160)

    risks_found = []
    risk_lines = []

    # IMPORTANT: do not scan the whole outlook for words like HIGH.
    # Phrases such as "High Plains" are not a categorical High Risk.
    # Only parse actual SPC categorical risk statements.
    risk_patterns = [
        r"\.\.\.THERE IS (?:A |AN )?(THUNDER|MARGINAL|SLIGHT|ENHANCED|MODERATE|HIGH) RISK OF .*?\.\.\.",
        r"THERE IS (?:A |AN )?(THUNDER|MARGINAL|SLIGHT|ENHANCED|MODERATE|HIGH) RISK OF [^\n]+",
    ]
    for pattern in risk_patterns:
        for m in re.finditer(pattern, text, re.I | re.S):
            word = m.group(1).upper()
            code = RISK_WORD_TO_CODE.get(word)
            if code:
                risks_found.append(code)
                line = clean(m.group(0).replace("\n", " "), 170)
                if line not in risk_lines:
                    risk_lines.append(line)

    # Some SPC products use categorical abbreviations in compact sections.
    # Only count those when they appear as isolated categorical tokens near risk text.
    for m in re.finditer(r"\b(TSTM|MRGL|SLGT|ENH|MDT|HIGH)\b", text, re.I):
        window = text[max(0, m.start()-80):m.end()+80]
        if re.search(r"risk|categorical|outlook", window, re.I):
            code = m.group(1).upper()
            risks_found.append(code)

    if risks_found:
        highest = max(set(risks_found), key=lambda c: RISK_ORDER.index(c))
        outlook["risk"] = RISK_LABELS.get(highest, highest)

    outlook["risk_lines"] = risk_lines[:2]

    # Prefer a general severe summary section if present. Keep it short for Discord.
    summary_match = re.search(r"\.\.\.SUMMARY\.\.\.(.*?)(?=&&|\.\.\.[A-Z]|$)", text, re.I | re.S)
    if summary_match:
        outlook["summary"] = clean(summary_match.group(1), 180)
    return outlook


def fetch_spc_outlook(url, label):
    try:
        text = get_text(url)
        return parse_spc_outlook(text, label, url)
    except Exception as e:
        log.warning("Failed to fetch %s outlook: %s", label, e)
        return {"day": label, "url": url, "headline": "", "risk": "Unavailable", "summary": "", "risk_lines": []}


# SPC GIS probability and categorical risk summaries for the configured target.
def fetch_spc_gis_layer(layer_id):
    params = {
        "f": "json",
        "where": "1=1",
        "outFields": "dn,label,label2,valid,issue,expire",
        "returnGeometry": "false",
        "geometry": target_bbox(),
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }
    data = get_json_with_params(f"{SPC_OUTLOOK_MAPSERVER}/{layer_id}/query", params)
    return data.get("features", [])


def highest_dn_feature(features):
    best = None
    for feature in features:
        attrs = feature.get("attributes", {})
        dn = attrs.get("dn")
        if dn is None:
            continue
        try:
            dn = float(dn)
        except (TypeError, ValueError):
            continue
        if best is None or dn > best["dn"]:
            best = {
                "dn": dn,
                "label": attrs.get("label") or attrs.get("label2") or str(dn),
                "valid": attrs.get("valid", ""),
                "issue": attrs.get("issue", ""),
                "expire": attrs.get("expire", ""),
            }
    return best


def probability_label(feature):
    if not feature:
        return "None found"
    label = normalize_probability(feature.get("label"))
    if label and label.lower() not in {"none", "null"}:
        return label
    return normalize_probability(feature["dn"])


def normalize_probability(value):
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if text.endswith("%"):
        return text
    try:
        number = float(text)
    except ValueError:
        return text
    if 0 < number < 1:
        number *= 100
    return f"{number:g}%"


def intensity_label(feature):
    if not feature:
        return ""
    label = str(feature.get("label") or "").strip()
    if label:
        return label
    return f"CIG{feature['dn']}"


def fetch_spc_gis_summary(day_label):
    layers = SPC_GIS_LAYERS[day_label]
    summary = {
        "day": day_label,
        "source": "SPC GIS",
        "risk": "None found",
        "probabilities": {},
        "intensity": {},
        "valid": "",
        "available": True,
    }
    try:
        category = highest_dn_feature(fetch_spc_gis_layer(layers["category"]))
        if category:
            summary["risk"] = CATEGORY_DN_LABELS.get(category["dn"], category["label"])
            summary["valid"] = category.get("valid", "")

        for hazard in ("tornado", "hail", "wind"):
            prob = highest_dn_feature(fetch_spc_gis_layer(layers[hazard]))
            summary["probabilities"][hazard] = probability_label(prob)

            intensity = highest_dn_feature(fetch_spc_gis_layer(layers[f"{hazard}_intensity"]))
            label = intensity_label(intensity)
            if label:
                summary["intensity"][hazard] = label
    except Exception as e:
        log.warning("Failed to fetch %s SPC GIS summary: %s", day_label, e)
        summary["available"] = False
        summary["source"] = "SPC text fallback"
        summary["risk"] = "Unavailable"
    return summary


def merged_spc_day(text_outlook, gis_summary):
    if not gis_summary.get("available"):
        text_outlook["source"] = "SPC text"
        text_outlook["probabilities"] = {}
        text_outlook["intensity"] = {}
        return text_outlook
    merged = dict(text_outlook)
    merged["risk"] = gis_summary.get("risk", text_outlook.get("risk", "Unavailable"))
    merged["source"] = gis_summary.get("source", "SPC GIS")
    merged["probabilities"] = gis_summary.get("probabilities", {})
    merged["intensity"] = gis_summary.get("intensity", {})
    merged["valid"] = gis_summary.get("valid", "")
    return merged


# Forecast discussion, probability, timing, and city forecast summaries.
def format_probabilities(outlook):
    probabilities = outlook.get("probabilities", {})
    if not probabilities:
        return "Unavailable"
    parts = []
    for hazard in ("tornado", "hail", "wind"):
        value = probabilities.get(hazard, "None found")
        intensity = outlook.get("intensity", {}).get(hazard)
        label = f"{hazard.title()}: {value}"
        if intensity:
            label += f" ({intensity})"
        parts.append(label)
    return " | ".join(parts)


def should_show_text_risk_lines(outlook):
    return outlook.get("source") != "SPC GIS"


def fetch_latest_product(product_type, office):
    url = NWS_PRODUCT_LATEST.format(product_type=product_type, office=office)
    return get(url, "application/json").json()


def extract_afd_section(text, headings):
    heading_pattern = "|".join(re.escape(heading) for heading in headings)
    pattern = rf"(?:^|\n)\.?\s*({heading_pattern})[^\n]*\n(.*?)(?=\n\.?[A-Z][A-Z /-]+[^\n]*\n|\n&&|$)"
    match = re.search(pattern, text, re.I | re.S)
    if not match:
        return ""
    return clean(match.group(2), 320)


def parse_afd_notes(product_text):
    if not product_text:
        return ""
    text = product_text.replace("\r\n", "\n")
    for headings in (
        ["KEY MESSAGES"],
        ["SHORT TERM", "NEAR TERM"],
        ["DISCUSSION"],
        ["LONG TERM"],
    ):
        section = extract_afd_section(text, headings)
        if section:
            return summarize_afd_section(section)
    return clean(text, 320)


def summarize_afd_section(section):
    section = clean(section, 700)
    bullets = re.findall(r"(?:^|\s)-\s+(.+?)(?=\s+-\s+|$)", section)
    if bullets:
        return bullet_list(clean(item, 150) for item in bullets[:3])
    sentences = re.split(r"(?<=[.!?])\s+", section)
    return " ".join(clean(sentence, 160) for sentence in sentences[:2] if sentence)


def timing_candidates(data):
    texts = []
    for note in data.get("forecaster_notes", []):
        texts.append(note.get("text", ""))
    for day in (data.get("day1", {}), data.get("day2", {})):
        texts.append(day.get("summary", ""))
    for text in texts:
        for part in re.split(r"(?:\n|•|(?<=[.!?])\s+)", text):
            part = clean(part, 180)
            if part and TIMING_WORDS.search(part):
                yield part


def expected_timing(data):
    seen = set()
    selected = []
    for candidate in timing_candidates(data):
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        selected.append(candidate)
        if len(selected) >= 2:
            break
    return bullet_list(selected)


def fetch_forecaster_notes():
    notes = []
    for office in configured_afd_offices()[:4]:
        try:
            product = fetch_latest_product("AFD", office)
            note = parse_afd_notes(product.get("productText", ""))
            if note:
                notes.append({"office": office, "text": note, "url": product.get("@id", "")})
        except Exception as e:
            log.warning("Failed to fetch AFD for %s: %s", office, e)
    return notes


def point_metadata(lat, lon):
    return get_json(f"https://api.weather.gov/points/{lat},{lon}")


def point_forecast_url(lat, lon):
    points = point_metadata(lat, lon)
    return points.get("properties", {}).get("forecast")


def city_forecast_summary(name, lat, lon):
    try:
        forecast_url = point_forecast_url(lat, lon)
        if not forecast_url:
            return f"{name}: forecast unavailable"
        data = get_json(forecast_url)
        periods = data.get("properties", {}).get("periods", [])
        if not periods:
            return f"{name}: forecast unavailable"
        first = periods[0]
        second = periods[1] if len(periods) > 1 else None
        line = f"{name}: {first.get('name','Today')} {first.get('temperature','?')}°{first.get('temperatureUnit','F')}, {clean(first.get('shortForecast',''), 32)}"
        if second:
            line += f" | {second.get('name','Tonight')} {second.get('temperature','?')}°{second.get('temperatureUnit','F')}, {clean(second.get('shortForecast',''), 32)}"
        return line
    except Exception as e:
        log.warning("Forecast failed for %s: %s", name, e)
        return f"{name}: forecast unavailable"


# Briefing text, images, and Discord embeds.
def bottom_line(day1, active_alerts):
    risk = day1.get("risk", "Unavailable")
    if active_alerts:
        return f"Active notable alerts are in effect. Highest {target_label()} Day 1 SPC signal found: {risk}."
    if risk in {"Enhanced", "Moderate", "High"}:
        return f"Heads up: SPC Day 1 shows {risk} risk signal intersecting {target_label()}. Review timing and threats before any travel."
    if risk in {"Slight", "Marginal"}:
        return f"Some severe potential is showing in the SPC Day 1 outlook for {target_label()}. Highest signal found: {risk}."
    if risk == "General Thunder":
        return "Thunderstorms may be possible, but no organized severe signal was found by the bot."
    if risk == "Unavailable":
        return "SPC outlook fetch failed, so use SPC/NWS directly for confidence."
    return f"No active notable {target_label()} alerts and no meaningful severe signal found by the bot."


def risk_color(risk):
    return RISK_COLORS.get(risk, 0x607D8B)


def add_embed_field(embed, name, value, inline=False):
    value = clean(value, 1000) or "Unavailable"
    embed.setdefault("fields", []).append({"name": name, "value": value, "inline": inline})


def active_alerts_embed(important):
    """Present active alerts as separate rows instead of one dense paragraph."""
    embed = {
        "title": f"⚠️ Active notable alerts: {len(important)}",
        "color": strongest_alert_color(important),
        "fields": [],
    }
    for props in important[:5]:
        event = f"{props.get('event', 'Alert')}{watch_number(props)}"
        area = compact_area_desc(props.get("areaDesc", ""), max_items=3) or "Area unavailable"
        expires = format_local_time(props.get("expires"))
        add_embed_field(embed, event, f"📍 {area}\n⏱️ Expires {expires}", False)
    if len(important) > 5:
        embed["description"] = f"Showing 5 of {len(important)} active alerts."
    return embed


def city_snapshots_embed(forecasts):
    """Give each city its own row so forecasts remain readable on mobile."""
    embed = {"title": "🏙️ City snapshots", "color": 0x4A90E2, "fields": []}
    for forecast in forecasts:
        city, separator, details = forecast.partition(":")
        if not separator:
            add_embed_field(embed, "Forecast", forecast, False)
            continue
        add_embed_field(embed, city.strip(), details.strip().replace(" | ", "\n"), False)
    return embed


def spc_embed(day, map_url):
    embed = {
        "title": f"🗺️ SPC {day.get('day', 'Outlook')}",
        "description": f"Highest {target_label()} signal: **{day.get('risk', 'Unavailable')}**",
        "color": risk_color(day.get("risk", "Unavailable")),
        "url": day.get("url"),
        "fields": [],
    }
    add_embed_field(embed, f"📊 {target_label()} probabilities", format_probabilities(day), False)
    if day.get("summary"):
        add_embed_field(embed, "🌎 SPC national context", day["summary"], False)
    if should_show_text_risk_lines(day):
        for risk_line in day.get("risk_lines", [])[:1]:
            add_embed_field(embed, "⚠️ Text risk line", risk_line, False)
    if INCLUDE_BRIEF_IMAGES:
        embed["image"] = {"url": map_url}
    return embed


def build_brief_data():
    alerts = fetch_active_target_alerts()
    important = []
    for f in alerts:
        p = f.get("properties", {})
        if p.get("event") in IMPORTANT_EVENTS or p.get("severity") in {"Extreme", "Severe"}:
            important.append(p)

    day1 = merged_spc_day(fetch_spc_outlook(SPC_DAY1_TXT, "Day 1"), fetch_spc_gis_summary("Day 1"))
    day2 = merged_spc_day(fetch_spc_outlook(SPC_DAY2_TXT, "Day 2"), fetch_spc_gis_summary("Day 2"))
    forecasts = [city_forecast_summary(name, lat, lon) for name, (lat, lon) in target_points().items()]
    forecaster_notes = fetch_forecaster_notes()
    now = datetime.now(TZ).strftime("%A, %B %-d at %-I:%M %p")
    return {
        "alerts": alerts,
        "important": important,
        "day1": day1,
        "day2": day2,
        "forecasts": forecasts,
        "forecaster_notes": forecaster_notes,
        "now": now,
    }


def build_brief_embeds(data=None, title=None, bottom_line_label="Bottom line"):
    title = title or f"🌦️ {target_label()} Weather Brief"
    data = data or build_brief_data()
    day1 = data["day1"]
    day2 = data["day2"]
    important = data["important"]
    alert_props = [feature.get("properties", {}) for feature in data.get("alerts", [])]
    forecasts = data["forecasts"]
    notes = data["forecaster_notes"]

    overview = {
        "title": title,
        "description": f"_{data['now']}_\n\n**{bottom_line_label}:**\n{bottom_line(day1, important)}",
        "color": strongest_alert_color(alert_props) if important else risk_color(day1.get("risk", "Unavailable")),
        "fields": [],
    }
    timing = expected_timing(data)
    if timing:
        add_embed_field(overview, "⏱️ Expected timing / focus", timing, False)

    embeds = [overview]
    if important:
        embeds.append(active_alerts_embed(important))
    if forecasts:
        embeds.append(city_snapshots_embed(forecasts))
    embeds.extend([spc_embed(day1, SPC_DAY1_MAP), spc_embed(day2, SPC_DAY2_MAP)])

    if notes:
        notes_embed = {"title": "📝 Forecaster Notes", "color": 0x4A90E2, "fields": []}
        for note in notes[:4]:
            add_embed_field(notes_embed, f"NWS {note['office']}", note["text"], False)
        embeds.append(notes_embed)

    if INCLUDE_BRIEF_IMAGES and important:
        station = configured_radar_stations()[0] if configured_radar_stations() else ""
        radar = radar_image_url(station)
        if radar:
            embeds.append({
                "title": f"📡 {station} Radar",
                "description": "Active notable alerts are in effect.",
                "color": strongest_alert_color(alert_props),
                "image": {"url": radar},
                "url": "https://radar.weather.gov/",
            })

    embeds.append({
        "title": "📚 Sources",
        "description": "NWS active alerts, NWS point forecasts, NWS forecast discussions, SPC Day 1/Day 2 outlook text, SPC GIS, SPC RSS.",
        "color": 0x607D8B,
    })
    return embeds[:10]


def build_brief_message(data=None, title=None, bottom_line_label="Bottom line"):
    title = title or f"🌦️ {target_label()} Weather Brief"
    data = data or build_brief_data()
    important = data["important"]
    day1 = data["day1"]
    day2 = data["day2"]
    forecasts = data["forecasts"]
    forecaster_notes = data["forecaster_notes"]
    timing = expected_timing(data)

    lines = [f"**{title}**", f"_{data['now']}_", ""]
    lines.append(f"**{bottom_line_label}:**")
    lines.append(bottom_line(day1, important))
    lines.append("")

    lines.append("**🗺️ SPC Day 1:**")
    lines.append(f"• Highest {target_label()} signal found: **{day1.get('risk', 'Unavailable')}**")
    lines.append(f"• {target_label()} probabilities: {format_probabilities(day1)}")
    if day1.get("summary"):
        lines.append(f"• Summary: {day1['summary']}")
    if should_show_text_risk_lines(day1):
        for risk_line in day1.get("risk_lines", [])[:1]:
            lines.append(f"• {risk_line}")
    lines.append(f"• Link: {day1.get('url')}")
    lines.append("")

    lines.append("**🗺️ SPC Day 2:**")
    lines.append(f"• Highest {target_label()} signal found: **{day2.get('risk', 'Unavailable')}**")
    lines.append(f"• {target_label()} probabilities: {format_probabilities(day2)}")
    if day2.get("summary"):
        lines.append(f"• Summary: {day2['summary']}")
    if should_show_text_risk_lines(day2):
        for risk_line in day2.get("risk_lines", [])[:1]:
            lines.append(f"• {risk_line}")
    lines.append(f"• Link: {day2.get('url')}")
    lines.append("")

    if important:
        lines.append(f"**⚠️ Active notable alerts:** {len(important)}")
        for p in important[:5]:
            lines.append(f"• {brief_alert_line(p)}")
    else:
        lines.append(f"**✅ Active notable alerts:** None found from NWS for {target_label()}.")

    if timing:
        lines.append("")
        lines.append("**⏱️ Expected timing / focus:**")
        lines.append(timing)

    lines.append("")
    lines.append("**🏙️ City snapshots:**")
    for f in forecasts:
        lines.append(f"• {f}")

    if forecaster_notes:
        lines.append("")
        lines.append("**📝 Forecaster notes:**")
        for note in forecaster_notes[:3]:
            lines.append(f"• **NWS {note['office']}**: {note['text']}")

    lines.append("")
    lines.append("Sources: NWS active alerts, NWS point forecasts, NWS forecast discussions, SPC Day 1/Day 2 outlook text, SPC GIS, SPC RSS.")
    message = "\n".join(lines)
    if len(message) > 1900:
        message = message[:1850] + "\n\n...brief truncated to fit Discord."
    return message


def post_brief(title=None, content_prefix=None, bottom_line_label="Bottom line"):
    title = title or f"🌦️ {target_label()} Weather Brief"
    content_prefix = content_prefix or f"🌦️ {target_label()} Weather Brief"
    data = build_brief_data()
    content = f"**{content_prefix}** - {data['now']}"
    return post_brief_channels(content=content, embeds=build_brief_embeds(data, title, bottom_line_label))


# Manual triggers, scheduled briefings, startup messages, and main loop.
def maybe_send_manual_brief(state):
    """Send a brief when TRIGGER_BRIEF_FILE exists, then remove the file.

    Example from Unraid host:
    touch /mnt/user/appdata/ok-weather-discord-bot/data/trigger_brief
    """
    if not TRIGGER_BRIEF_FILE:
        return
    if not os.path.exists(TRIGGER_BRIEF_FILE):
        return
    log.info("Manual brief trigger detected: %s", TRIGGER_BRIEF_FILE)
    if not post_brief():
        log.warning("Manual brief was not sent; leaving trigger file for retry")
        return
    try:
        os.remove(TRIGGER_BRIEF_FILE)
        log.info("Manual brief trigger consumed and removed")
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning("Could not remove manual trigger file: %s", e)


def maybe_send_alert_test():
    if not TRIGGER_ALERT_TEST_FILE or not os.path.exists(TRIGGER_ALERT_TEST_FILE):
        return
    log.info("Manual alert test trigger detected: %s", TRIGGER_ALERT_TEST_FILE)
    now = datetime.now(TZ)
    test_props = {
        "event": "Severe Thunderstorm Warning",
        "headline": f"Test alert card for {target_label()} weather bot",
        "description": "This test uses the same card layout as real NWS alerts. No radar image is attached, so Discord cannot show a stale cached radar loop.",
        "instruction": "No action needed. This is only a webhook and formatting test.",
        "areaDesc": f"{target_label()} test area",
        "severity": "Severe",
        "urgency": "Immediate",
        "certainty": "Observed",
        "sent": now.isoformat(),
        "effective": now.isoformat(),
        "expires": (now + timedelta(minutes=30)).isoformat(),
        "@id": "https://alerts.weather.gov",
    }
    ok = post_alert_channels(
        content="**Alert webhook test**",
        embeds=[build_alert_embed(test_props, title=f"{target_label()} Weather Bot Alert Test")],
    )
    if not ok:
        log.warning("Alert webhook test was not sent; leaving trigger file for retry")
        return
    try:
        os.remove(TRIGGER_ALERT_TEST_FILE)
        log.info("Manual alert test trigger consumed and removed")
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning("Could not remove manual alert test trigger file: %s", e)


def maybe_send_daily_brief(state):
    now = datetime.now(TZ)
    today = now.date().isoformat()
    if now.hour == BRIEF_HOUR and now.minute >= BRIEF_MINUTE and state.get("last_brief_date") != today:
        if post_brief():
            state["last_brief_date"] = today
            log.info("Daily brief sent for %s", today)
        else:
            log.warning("Daily brief was not sent")


def maybe_send_afternoon_severe_brief(state):
    if not AFTERNOON_SEVERE_BRIEF_ENABLED:
        return
    now = datetime.now(TZ)
    today = now.date().isoformat()
    if (
        now.hour == AFTERNOON_SEVERE_BRIEF_HOUR
        and now.minute >= AFTERNOON_SEVERE_BRIEF_MINUTE
        and state.get("last_afternoon_severe_brief_date") != today
    ):
        if post_brief(
            title=f"⛈️ {target_label()} Rest-of-Day Severe Weather Brief",
            content_prefix=f"⛈️ {target_label()} Rest-of-Day Severe Weather Brief",
            bottom_line_label="Rest-of-day severe weather",
        ):
            state["last_afternoon_severe_brief_date"] = today
            log.info("Afternoon severe weather brief sent for %s", today)
        else:
            log.warning("Afternoon severe weather brief was not sent")


def send_startup_message_once(state):
    if not SEND_STARTUP_MESSAGE or state.get("startup_sent"):
        return
    afternoon_brief_status = "disabled"
    if AFTERNOON_SEVERE_BRIEF_ENABLED:
        afternoon_brief_status = f"{AFTERNOON_SEVERE_BRIEF_HOUR:02d}:{AFTERNOON_SEVERE_BRIEF_MINUTE:02d} {TZ.key}"
    msg = (
        f"âœ… **{target_label()} Weather Bot Started**\n"
        f"Poll interval: {POLL_SECONDS} seconds\n"
        f"Daily brief: {BRIEF_HOUR:02d}:{BRIEF_MINUTE:02d} {TZ.key}\n"
        f"Afternoon severe brief: {afternoon_brief_status}\n"
        "Version: v2.5.5"
    )
    if post_brief_channels(content=msg):
        state["startup_sent"] = True
        log.info("Startup message sent")


def log_config_summary():
    log.info(
        "Config: discord_brief_webhooks=%s discord_alert_webhooks=%s teams_brief_webhooks=%s teams_alert_webhooks=%s severe_thunderstorm_warning_mode=%s",
        len(brief_webhook_urls()),
        len(alert_webhook_urls()),
        len(teams_brief_webhook_urls()),
        len(teams_alert_webhook_urls()),
        SEVERE_THUNDERSTORM_WARNING_MODE,
    )


def main():
    log.info("Starting Weather Discord/Teams Bot v2.5.5")
    log_config_summary()
    state = load_state()
    send_startup_message_once(state)
    if TEST_BRIEF_ON_START:
        log.info("TEST_BRIEF_ON_START enabled, sending test brief")
        post_brief()
    save_state(state)
    while True:
        try:
            send_new_nws_alerts(state)
            send_new_spc_items(state)
            maybe_send_manual_brief(state)
            maybe_send_alert_test()
            maybe_send_daily_brief(state)
            maybe_send_afternoon_severe_brief(state)
            save_state(state)
        except Exception as e:
            log.exception("Loop error: %s", e)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()


import json
import logging
import os
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import feedparser
import requests
from dateutil import parser as dtparser

TZ = ZoneInfo(os.getenv("TZ", "America/Chicago"))
NWS_USER_AGENT = os.getenv("NWS_USER_AGENT", "ok-weather-discord-bot/2.4")
BRIEF_WEBHOOK_URL = os.getenv("BRIEF_WEBHOOK_URL", "")
ALERT_WEBHOOK_URL = os.getenv("ALERT_WEBHOOK_URL", "")
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "180"))
BRIEF_HOUR = int(os.getenv("BRIEF_HOUR", "9"))
BRIEF_MINUTE = int(os.getenv("BRIEF_MINUTE", "0"))
DISCORD_MAX_RETRIES = int(os.getenv("DISCORD_MAX_RETRIES", "3"))
HTTP_MAX_RETRIES = int(os.getenv("HTTP_MAX_RETRIES", "3"))
STATE_FILE = os.getenv("STATE_FILE", "/data/state.json")
LOG_FILE = os.getenv("LOG_FILE", "/data/weather.log")
SEND_STARTUP_MESSAGE = os.getenv("SEND_STARTUP_MESSAGE", "true").lower() == "true"
TEST_BRIEF_ON_START = os.getenv("TEST_BRIEF_ON_START", "false").lower() == "true"
TRIGGER_BRIEF_FILE = os.getenv("TRIGGER_BRIEF_FILE", "/data/trigger_brief")

NWS_ALERTS_OK = "https://api.weather.gov/alerts/active?area=OK"
SPC_RSS = "https://www.spc.noaa.gov/products/spcrss.xml"
SPC_DAY1_TXT = "https://www.spc.noaa.gov/products/outlook/day1otlk.txt"
SPC_DAY2_TXT = "https://www.spc.noaa.gov/products/outlook/day2otlk.txt"
SPC_OUTLOOK_MAPSERVER = "https://mapservices.weather.noaa.gov/vector/rest/services/outlooks/SPC_wx_outlks/MapServer"
OKLAHOMA_BBOX = "-103.1,33.5,-94.4,37.1"

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

CITY_POINTS = {
    "OKC": (35.4676, -97.5164),
    "Tulsa": (36.1540, -95.9928),
    "Lawton": (34.6036, -98.3959),
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

OKLAHOMA_WORDS = re.compile(
    r"\b(oklahoma|\bok\b|okc|oklahoma city|tulsa|norman|lawton|enid|ardmore|woodward|ponca|stillwater|mcalester|altus|guymon|elk city|clinton|chickasha|shawnee|seminole|ada|durant|idabel)\b",
    re.I,
)
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

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE)],
)
log = logging.getLogger("ok-weather-bot")


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
    state.setdefault("startup_sent", False)
    return state


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    state["seen_alerts"] = state.get("seen_alerts", [])[-500:]
    state["seen_spc"] = state.get("seen_spc", [])[-500:]
    temp_file = f"{STATE_FILE}.tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(temp_file, STATE_FILE)


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
    if not webhook_url or "YOUR_" in webhook_url or "PASTE_" in webhook_url:
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


def clean(text, max_len=900):
    if not text:
        return ""
    text = re.sub(r"\s+", " ", str(text)).strip()
    return text[:max_len] + ("..." if len(text) > max_len else "")


def fetch_active_ok_alerts():
    data = get_json(NWS_ALERTS_OK)
    return data.get("features", [])


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
        return bool(re.search(r"(baseball|softball|tennis ball|golf ball|2\.00|1\.75|70 mph|75 mph|80 mph|considerable|destructive)", desc))
    if event == "Special Weather Statement":
        return True
    if event in IMPORTANT_EVENTS and severity in {"Extreme", "Severe"}:
        return True
    if severity == "Extreme":
        return True
    return False


def alert_color(event, severity):
    if event in {"Tornado Warning", "Tornado Watch"}:
        return 0xFF0000
    if event in {"Severe Thunderstorm Warning", "Severe Thunderstorm Watch"}:
        return 0xFF9900
    if event in {"Flash Flood Warning"}:
        return 0x00AEEF
    if severity == "Extreme":
        return 0xB00020
    if severity == "Severe":
        return 0xFF9900
    return 0x607D8B


def send_new_nws_alerts(state):
    alerts = fetch_active_ok_alerts()
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
        severity = props.get("severity", "")
        urgency = props.get("urgency", "")
        certainty = props.get("certainty", "")
        area = clean(props.get("areaDesc", ""), 450)
        headline = clean(props.get("headline", event), 250)
        desc = clean(props.get("description", ""), 900)
        instr = clean(props.get("instruction", ""), 500)
        expires = props.get("expires") or "Unknown"
        try:
            expires_local = dtparser.parse(expires).astimezone(TZ).strftime("%b %-d, %-I:%M %p %Z")
        except Exception:
            expires_local = expires
        embed = {
            "title": event,
            "description": f"**{headline}**\n\n{desc}",
            "fields": [
                {"name": "Area", "value": area or "Oklahoma", "inline": False},
                {"name": "Severity", "value": f"{severity} / {urgency} / {certainty}", "inline": True},
                {"name": "Expires", "value": expires_local, "inline": True},
            ],
            "color": alert_color(event, severity),
            "url": props.get("@id", "https://alerts.weather.gov"),
        }
        if instr:
            embed["fields"].append({"name": "Instruction", "value": instr, "inline": False})
        if post_discord(ALERT_WEBHOOK_URL, content="🚨 New Oklahoma weather alert", embeds=[embed]):
            new_keys.append(key)
            seen.add(key)
            sent += 1
    state.setdefault("seen_alerts", []).extend(new_keys)
    if sent:
        log.info("Sent %s NWS alert(s)", sent)


def fetch_spc_entries():
    feed = feedparser.parse(SPC_RSS)
    return feed.entries[:40]


def entry_id(entry):
    return entry.get("id") or entry.get("link") or entry.get("title", "")


def send_new_spc_items(state):
    seen = set(state.get("seen_spc", []))
    new_ids = []
    sent = 0
    for entry in fetch_spc_entries():
        title = entry.get("title", "")
        summary = clean(entry.get("summary", ""), 700)
        combined = f"{title} {summary}"
        if not SPC_IMPORTANT.search(combined):
            continue
        if not OKLAHOMA_WORDS.search(combined):
            continue
        key = entry_id(entry)
        if key in seen:
            continue
        if post_discord(
            ALERT_WEBHOOK_URL,
            content=f"⚡ **SPC item mentioning Oklahoma**\n**{title}**\n{summary}\n{entry.get('link', '')}",
        ):
            new_ids.append(key)
            seen.add(key)
            sent += 1
    state.setdefault("seen_spc", []).extend(new_ids)
    if sent:
        log.info("Sent %s SPC item(s)", sent)


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


def fetch_spc_gis_layer(layer_id):
    params = {
        "f": "json",
        "where": "1=1",
        "outFields": "dn,label,label2,valid,issue,expire",
        "returnGeometry": "false",
        "geometry": OKLAHOMA_BBOX,
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


def point_forecast_url(lat, lon):
    points = get_json(f"https://api.weather.gov/points/{lat},{lon}")
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
        line = f"{name}: {first.get('name','Today')} {first.get('temperature','?')}°{first.get('temperatureUnit','F')}, {clean(first.get('shortForecast',''), 55)}"
        if second:
            line += f" | {second.get('name','Tonight')} {second.get('temperature','?')}°{second.get('temperatureUnit','F')}, {clean(second.get('shortForecast',''), 55)}"
        return line
    except Exception as e:
        log.warning("Forecast failed for %s: %s", name, e)
        return f"{name}: forecast unavailable"


def bottom_line(day1, active_alerts):
    risk = day1.get("risk", "Unavailable")
    if active_alerts:
        return f"Active notable alerts are in effect. Highest Oklahoma Day 1 SPC signal found: {risk}."
    if risk in {"Enhanced", "Moderate", "High"}:
        return f"Heads up: SPC Day 1 shows {risk} risk signal intersecting Oklahoma. Review timing and threats before any travel."
    if risk in {"Slight", "Marginal"}:
        return f"Some severe potential is showing in the SPC Day 1 outlook for Oklahoma. Highest signal found: {risk}."
    if risk == "General Thunder":
        return "Thunderstorms may be possible, but no organized severe signal was found by the bot."
    if risk == "Unavailable":
        return "SPC outlook fetch failed, so use SPC/NWS directly for confidence."
    return "No active notable Oklahoma alerts and no meaningful severe signal found by the bot."


def build_brief_message():
    alerts = fetch_active_ok_alerts()
    important = []
    for f in alerts:
        p = f.get("properties", {})
        if p.get("event") in IMPORTANT_EVENTS or p.get("severity") in {"Extreme", "Severe"}:
            important.append(p)

    day1 = merged_spc_day(fetch_spc_outlook(SPC_DAY1_TXT, "Day 1"), fetch_spc_gis_summary("Day 1"))
    day2 = merged_spc_day(fetch_spc_outlook(SPC_DAY2_TXT, "Day 2"), fetch_spc_gis_summary("Day 2"))

    forecasts = [city_forecast_summary(name, lat, lon) for name, (lat, lon) in CITY_POINTS.items()]

    now = datetime.now(TZ).strftime("%A, %B %-d at %-I:%M %p")
    lines = ["🌦️ **Oklahoma Weather Brief**", f"_{now}_", ""]
    lines.append("**Bottom line:**")
    lines.append(bottom_line(day1, important))
    lines.append("")

    lines.append("**SPC Day 1:**")
    lines.append(f"• Highest Oklahoma signal found: **{day1.get('risk', 'Unavailable')}**")
    lines.append(f"• Oklahoma probabilities: {format_probabilities(day1)}")
    if day1.get("summary"):
        lines.append(f"• Summary: {day1['summary']}")
    if should_show_text_risk_lines(day1):
        for risk_line in day1.get("risk_lines", [])[:1]:
            lines.append(f"• {risk_line}")
    lines.append(f"• Link: {day1.get('url')}")
    lines.append("")

    lines.append("**SPC Day 2:**")
    lines.append(f"• Highest Oklahoma signal found: **{day2.get('risk', 'Unavailable')}**")
    lines.append(f"• Oklahoma probabilities: {format_probabilities(day2)}")
    if day2.get("summary"):
        lines.append(f"• Summary: {day2['summary']}")
    if should_show_text_risk_lines(day2):
        for risk_line in day2.get("risk_lines", [])[:1]:
            lines.append(f"• {risk_line}")
    lines.append(f"• Link: {day2.get('url')}")
    lines.append("")

    if important:
        lines.append(f"**Active notable alerts:** {len(important)}")
        for p in important[:5]:
            lines.append(f"• **{p.get('event','Alert')}**: {clean(p.get('areaDesc',''), 120)}")
    else:
        lines.append("**Active notable alerts:** None found from NWS Oklahoma statewide alerts.")

    lines.append("")
    lines.append("**City snapshots:**")
    for f in forecasts:
        lines.append(f"• {f}")

    lines.append("")
    lines.append("Sources: NWS active alerts, NWS point forecasts, SPC Day 1/Day 2 outlook text, SPC GIS, SPC RSS.")
    message = "\n".join(lines)
    if len(message) > 1900:
        message = message[:1850] + "\n\n...brief truncated to fit Discord."
    return message



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
    if not post_discord(BRIEF_WEBHOOK_URL, content=build_brief_message()):
        log.warning("Manual brief was not sent; leaving trigger file for retry")
        return
    try:
        os.remove(TRIGGER_BRIEF_FILE)
        log.info("Manual brief trigger consumed and removed")
    except FileNotFoundError:
        pass
    except Exception as e:
        log.warning("Could not remove manual trigger file: %s", e)

def maybe_send_daily_brief(state):
    now = datetime.now(TZ)
    today = now.date().isoformat()
    if now.hour == BRIEF_HOUR and now.minute >= BRIEF_MINUTE and state.get("last_brief_date") != today:
        msg = build_brief_message()
        if post_discord(BRIEF_WEBHOOK_URL, content=msg):
            state["last_brief_date"] = today
            log.info("Daily brief sent for %s", today)
        else:
            log.warning("Daily brief was not sent")


def send_startup_message_once(state):
    if not SEND_STARTUP_MESSAGE or state.get("startup_sent"):
        return
    msg = (
        "✅ **Oklahoma Weather Bot Started**\n"
        f"Poll interval: {POLL_SECONDS} seconds\n"
        f"Daily brief: {BRIEF_HOUR:02d}:{BRIEF_MINUTE:02d} {TZ.key}\n"
        "Version: v2.4.3"
    )
    if post_discord(BRIEF_WEBHOOK_URL, content=msg):
        state["startup_sent"] = True
        log.info("Startup message sent")


def main():
    log.info("Starting Oklahoma Weather Discord Bot v2.3")
    state = load_state()
    send_startup_message_once(state)
    if TEST_BRIEF_ON_START:
        log.info("TEST_BRIEF_ON_START enabled, sending test brief")
        post_discord(BRIEF_WEBHOOK_URL, content=build_brief_message())
    save_state(state)
    while True:
        try:
            send_new_nws_alerts(state)
            send_new_spc_items(state)
            maybe_send_manual_brief(state)
            maybe_send_daily_brief(state)
            save_state(state)
        except Exception as e:
            log.exception("Loop error: %s", e)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()

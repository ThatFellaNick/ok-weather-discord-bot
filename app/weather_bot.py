import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
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
SEVERE_THUNDERSTORM_WARNING_MODE = os.getenv("SEVERE_THUNDERSTORM_WARNING_MODE", "all").lower()
STATE_FILE = os.getenv("STATE_FILE", "/data/state.json")
LOG_FILE = os.getenv("LOG_FILE", "/data/weather.log")
SEND_STARTUP_MESSAGE = os.getenv("SEND_STARTUP_MESSAGE", "true").lower() == "true"
TEST_BRIEF_ON_START = os.getenv("TEST_BRIEF_ON_START", "false").lower() == "true"
TRIGGER_BRIEF_FILE = os.getenv("TRIGGER_BRIEF_FILE", "/data/trigger_brief")
TRIGGER_ALERT_TEST_FILE = os.getenv("TRIGGER_ALERT_TEST_FILE", "/data/trigger_alert_test")
AFD_OFFICES = [office.strip().upper() for office in os.getenv("AFD_OFFICES", "OUN,TSA").split(",") if office.strip()]
INCLUDE_BRIEF_IMAGES = os.getenv("INCLUDE_BRIEF_IMAGES", "true").lower() == "true"
RADAR_STATIONS = [station.strip().upper() for station in os.getenv("RADAR_STATIONS", "KTLX,KINX,KFDR").split(",") if station.strip()]

NWS_ALERTS_OK = "https://api.weather.gov/alerts/active?area=OK"
NWS_PRODUCT_LATEST = "https://api.weather.gov/products/types/{product_type}/locations/{office}/latest"
SPC_RSS = "https://www.spc.noaa.gov/products/spcrss.xml"
SPC_DAY1_TXT = "https://www.spc.noaa.gov/products/outlook/day1otlk.txt"
SPC_DAY2_TXT = "https://www.spc.noaa.gov/products/outlook/day2otlk.txt"
SPC_DAY1_MAP = "https://www.spc.noaa.gov/products/outlook/day1otlk.gif"
SPC_DAY2_MAP = "https://www.spc.noaa.gov/products/outlook/day2otlk.gif"
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
    text = str(text).replace("Â°", "°")
    text = re.sub(r"\s+", " ", str(text)).strip()
    return text[:max_len] + ("..." if len(text) > max_len else "")


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
        county_text = f"{count} Oklahoma counties" if count else "Oklahoma counties"
        return f"**{event}{watch_number(props)}**: {county_text}, expires {format_local_time(props.get('expires'))}"
    return f"**{event}**: {clean(props.get('areaDesc', ''), 120)}"


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


def alert_severity_label(event, severity, urgency="", certainty=""):
    parts = [part for part in (severity, urgency, certainty) if part]
    if not parts:
        return "Unavailable"
    return " / ".join(parts)


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


def build_alert_embed(props, *, title=None, description=None):
    event = props.get("event", "Weather Alert")
    severity = props.get("severity", "")
    urgency = props.get("urgency", "")
    certainty = props.get("certainty", "")
    area = clean(props.get("areaDesc", ""), 700)
    headline = clean(props.get("headline", event), 250)
    desc = clean(description if description is not None else props.get("description", ""), 900)
    instr = clean(props.get("instruction", ""), 650)

    embed = {
        "title": title or f"{event}{watch_number(props)}",
        "description": f"**{headline}**" + (f"\n\n{desc}" if desc else ""),
        "color": alert_color(event, severity),
        "url": props.get("@id") or props.get("id") or "https://alerts.weather.gov",
        "fields": [],
    }
    add_embed_field(embed, "Affected area", area or "Oklahoma", False)
    add_embed_field(embed, "Severity", alert_severity_label(event, severity, urgency, certainty), True)
    add_embed_field(embed, "Timing", alert_footer(props), True)
    if instr:
        add_embed_field(embed, "Instruction", instr, False)
    return embed


def strongest_alert_color(alerts):
    priority = {
        0xFF0000: 5,
        0xB00020: 4,
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
        embed = build_alert_embed(props)
        if post_discord(ALERT_WEBHOOK_URL, content=f"**New Oklahoma weather alert:** {event}", embeds=[embed]):
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
            content=f"âš¡ **SPC item mentioning Oklahoma**\n**{title}**\n{summary}\n{entry.get('link', '')}",
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
    for office in AFD_OFFICES[:4]:
        try:
            product = fetch_latest_product("AFD", office)
            note = parse_afd_notes(product.get("productText", ""))
            if note:
                notes.append({"office": office, "text": note, "url": product.get("@id", "")})
        except Exception as e:
            log.warning("Failed to fetch AFD for %s: %s", office, e)
    return notes


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
        line = f"{name}: {first.get('name','Today')} {first.get('temperature','?')}°{first.get('temperatureUnit','F')}, {clean(first.get('shortForecast',''), 42)}"
        if second:
            line += f" | {second.get('name','Tonight')} {second.get('temperature','?')}°{second.get('temperatureUnit','F')}, {clean(second.get('shortForecast',''), 42)}"
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


def risk_color(risk):
    return RISK_COLORS.get(risk, 0x607D8B)


def radar_image_url():
    if not RADAR_STATIONS:
        return ""
    cache_key = datetime.now(TZ).strftime("%Y%m%d%H%M")
    return f"https://radar.weather.gov/ridge/standard/{RADAR_STATIONS[0]}_loop.gif?v={cache_key}"


def add_embed_field(embed, name, value, inline=False):
    value = clean(value, 1000) or "Unavailable"
    embed.setdefault("fields", []).append({"name": name, "value": value, "inline": inline})


def spc_embed(day, map_url):
    embed = {
        "title": f"SPC {day.get('day', 'Outlook')}",
        "description": f"Highest Oklahoma signal: **{day.get('risk', 'Unavailable')}**",
        "color": risk_color(day.get("risk", "Unavailable")),
        "url": day.get("url"),
        "fields": [],
    }
    add_embed_field(embed, "Oklahoma probabilities", format_probabilities(day), False)
    if day.get("summary"):
        add_embed_field(embed, "SPC national context", day["summary"], False)
    if should_show_text_risk_lines(day):
        for risk_line in day.get("risk_lines", [])[:1]:
            add_embed_field(embed, "Text risk line", risk_line, False)
    if INCLUDE_BRIEF_IMAGES:
        embed["image"] = {"url": map_url}
    return embed


def build_brief_data():
    alerts = fetch_active_ok_alerts()
    important = []
    for f in alerts:
        p = f.get("properties", {})
        if p.get("event") in IMPORTANT_EVENTS or p.get("severity") in {"Extreme", "Severe"}:
            important.append(p)

    day1 = merged_spc_day(fetch_spc_outlook(SPC_DAY1_TXT, "Day 1"), fetch_spc_gis_summary("Day 1"))
    day2 = merged_spc_day(fetch_spc_outlook(SPC_DAY2_TXT, "Day 2"), fetch_spc_gis_summary("Day 2"))
    forecasts = [city_forecast_summary(name, lat, lon) for name, (lat, lon) in CITY_POINTS.items()]
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


def build_brief_embeds(data=None):
    data = data or build_brief_data()
    day1 = data["day1"]
    day2 = data["day2"]
    important = data["important"]
    alert_props = [feature.get("properties", {}) for feature in data.get("alerts", [])]
    forecasts = data["forecasts"]
    notes = data["forecaster_notes"]

    overview = {
        "title": "Oklahoma Weather Brief",
        "description": f"_{data['now']}_\n\n**Bottom line:**\n{bottom_line(day1, important)}",
        "color": strongest_alert_color(alert_props) if important else risk_color(day1.get("risk", "Unavailable")),
        "fields": [],
    }
    if important:
        alert_lines = [brief_alert_line(p) for p in important[:5]]
        add_embed_field(overview, f"Active notable alerts: {len(important)}", bullet_list(alert_lines), False)
    else:
        add_embed_field(overview, "Active notable alerts", "None found from NWS Oklahoma statewide alerts.", False)
    timing = expected_timing(data)
    if timing:
        add_embed_field(overview, "Expected timing / focus", timing, False)
    add_embed_field(overview, "City snapshots", bullet_list(forecasts), False)

    embeds = [overview, spc_embed(day1, SPC_DAY1_MAP), spc_embed(day2, SPC_DAY2_MAP)]

    if notes:
        notes_embed = {"title": "Forecaster Notes", "color": 0x4A90E2, "fields": []}
        for note in notes[:4]:
            add_embed_field(notes_embed, f"NWS {note['office']}", note["text"], False)
        embeds.append(notes_embed)

    if INCLUDE_BRIEF_IMAGES and important:
        radar = radar_image_url()
        if radar:
            embeds.append({
                "title": f"{RADAR_STATIONS[0]} Radar",
                "description": "Active notable alerts are in effect.",
                "color": strongest_alert_color(alert_props),
                "image": {"url": radar},
                "url": "https://radar.weather.gov/",
            })

    embeds.append({
        "title": "Sources",
        "description": "NWS active alerts, NWS point forecasts, NWS forecast discussions, SPC Day 1/Day 2 outlook text, SPC GIS, SPC RSS.",
        "color": 0x607D8B,
    })
    return embeds[:10]


def build_brief_message(data=None):
    data = data or build_brief_data()
    important = data["important"]
    day1 = data["day1"]
    day2 = data["day2"]
    forecasts = data["forecasts"]
    forecaster_notes = data["forecaster_notes"]
    timing = expected_timing(data)

    lines = ["🌦️ **Oklahoma Weather Brief**", f"_{data['now']}_", ""]
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
            lines.append(f"• {brief_alert_line(p)}")
    else:
        lines.append("**Active notable alerts:** None found from NWS Oklahoma statewide alerts.")

    if timing:
        lines.append("")
        lines.append("**Expected timing / focus:**")
        lines.append(timing)

    lines.append("")
    lines.append("**City snapshots:**")
    for f in forecasts:
        lines.append(f"• {f}")

    if forecaster_notes:
        lines.append("")
        lines.append("**Forecaster notes:**")
        for note in forecaster_notes[:3]:
            lines.append(f"• **NWS {note['office']}**: {note['text']}")

    lines.append("")
    lines.append("Sources: NWS active alerts, NWS point forecasts, NWS forecast discussions, SPC Day 1/Day 2 outlook text, SPC GIS, SPC RSS.")
    message = "\n".join(lines)
    if len(message) > 1900:
        message = message[:1850] + "\n\n...brief truncated to fit Discord."
    return message


def post_brief():
    data = build_brief_data()
    content = f"🌦️ **Oklahoma Weather Brief** — {data['now']}"
    return post_discord(BRIEF_WEBHOOK_URL, content=content, embeds=build_brief_embeds(data))

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
        "headline": "Test alert card for Oklahoma weather bot",
        "description": "This test uses the same card layout as real NWS alerts. No radar image is attached, so Discord cannot show a stale cached radar loop.",
        "instruction": "No action needed. This is only a webhook and formatting test.",
        "areaDesc": "Oklahoma test area",
        "severity": "Severe",
        "urgency": "Immediate",
        "certainty": "Observed",
        "sent": now.isoformat(),
        "effective": now.isoformat(),
        "expires": (now + timedelta(minutes=30)).isoformat(),
        "@id": "https://alerts.weather.gov",
    }
    ok = post_discord(
        ALERT_WEBHOOK_URL,
        content="**Alert webhook test**",
        embeds=[build_alert_embed(test_props, title="Oklahoma Weather Bot Alert Test")],
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


def send_startup_message_once(state):
    if not SEND_STARTUP_MESSAGE or state.get("startup_sent"):
        return
    msg = (
        "âœ… **Oklahoma Weather Bot Started**\n"
        f"Poll interval: {POLL_SECONDS} seconds\n"
        f"Daily brief: {BRIEF_HOUR:02d}:{BRIEF_MINUTE:02d} {TZ.key}\n"
        "Version: v2.4.3"
    )
    if post_discord(BRIEF_WEBHOOK_URL, content=msg):
        state["startup_sent"] = True
        log.info("Startup message sent")


def log_config_summary():
    log.info(
        "Config: brief_webhook=%s alert_webhook=%s severe_thunderstorm_warning_mode=%s",
        "configured" if BRIEF_WEBHOOK_URL else "missing",
        "configured" if ALERT_WEBHOOK_URL else "missing",
        SEVERE_THUNDERSTORM_WARNING_MODE,
    )


def main():
    log.info("Starting Oklahoma Weather Discord Bot v2.3")
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
            save_state(state)
        except Exception as e:
            log.exception("Loop error: %s", e)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()


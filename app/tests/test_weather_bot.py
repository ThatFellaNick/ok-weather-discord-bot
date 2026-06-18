import importlib.util
import os
import sys
import types
import unittest
from datetime import datetime as real_datetime
from pathlib import Path


def load_weather_bot():
    test_data = Path(__file__).resolve().parents[2] / ".test-data"
    test_data.mkdir(exist_ok=True)
    os.environ["LOG_FILE"] = str(test_data / "weather.log")
    os.environ["STATE_FILE"] = str(test_data / "state.json")

    sys.modules.setdefault("feedparser", types.SimpleNamespace(parse=lambda url: types.SimpleNamespace(entries=[])))
    sys.modules.setdefault("requests", types.SimpleNamespace(get=None, post=None))
    dateutil_module = types.ModuleType("dateutil")
    parser_module = types.ModuleType("dateutil.parser")
    parser_module.parse = lambda value: value
    dateutil_module.parser = parser_module
    sys.modules.setdefault("dateutil", dateutil_module)
    sys.modules.setdefault("dateutil.parser", parser_module)

    path = Path(__file__).resolve().parents[1] / "weather_bot.py"
    spec = importlib.util.spec_from_file_location("weather_bot", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


weather_bot = load_weather_bot()


class SpcParsingTests(unittest.TestCase):
    def test_parse_spc_outlook_ignores_geographic_high_word(self):
        text = """
        DAY 1 CONVECTIVE OUTLOOK
        ...THERE IS A SLIGHT RISK OF SEVERE THUNDERSTORMS ACROSS PARTS OF OKLAHOMA...
        ...SUMMARY...
        Severe storms are possible across Oklahoma and the High Plains.
        """

        outlook = weather_bot.parse_spc_outlook(text, "Day 1", "https://example.test")

        self.assertEqual(outlook["risk"], "Slight")

    def test_highest_dn_feature_uses_largest_dn_value(self):
        features = [
            {"attributes": {"dn": 0.05, "label": "0.05"}},
            {"attributes": {"dn": 0.15, "label": "0.15"}},
            {"attributes": {"dn": 0.02, "label": "0.02"}},
        ]

        feature = weather_bot.highest_dn_feature(features)

        self.assertEqual(feature["dn"], 0.15)
        self.assertEqual(weather_bot.probability_label(feature), "15%")

    def test_format_probabilities_includes_conditional_intensity(self):
        outlook = {
            "probabilities": {"tornado": "5%", "hail": "15%", "wind": "15%"},
            "intensity": {"tornado": "CIG1", "wind": "CIG2"},
        }

        line = weather_bot.format_probabilities(outlook)

        self.assertIn("Tornado: 5% (CIG1)", line)
        self.assertIn("Hail: 15%", line)
        self.assertIn("Wind: 15% (CIG2)", line)

    def test_text_risk_lines_hidden_when_gis_source_is_used(self):
        self.assertFalse(weather_bot.should_show_text_risk_lines({"source": "SPC GIS"}))
        self.assertTrue(weather_bot.should_show_text_risk_lines({"source": "SPC text"}))

    def test_clean_html_strips_spc_feed_markup(self):
        html = (
            'SPC 0100Z Day 1 Outlook <br /><a href="https://www.spc.noaa.gov/products/outlook/day1otlk.html">'
            '<img alt="Day 1 Outlook Image" src="https://www.spc.noaa.gov/products/outlook/day1otlk.png" />'
            "</a><pre>...THERE IS A SLIGHT RISK OF SEVERE THUNDERSTORMS ACROSS OKLAHOMA...</pre>"
        )

        text = weather_bot.clean_html(html)

        self.assertIn("SPC 0100Z Day 1 Outlook", text)
        self.assertIn("SLIGHT RISK", text)
        self.assertNotIn("<br", text)
        self.assertNotIn("<a", text)
        self.assertNotIn("<img", text)

    def test_spc_items_post_as_embed_cards(self):
        entry = {
            "id": "spc-day1",
            "title": "SPC Jun 8, 2026 0100 UTC Day 1 Convective Outlook",
            "summary": (
                'SPC 0100Z Day 1 Outlook <br /><a href="https://www.spc.noaa.gov/products/outlook/day1otlk.html">'
                '<img alt="Day 1 Outlook Image" src="https://www.spc.noaa.gov/products/outlook/day1otlk.png" />'
                "</a><pre>...THERE IS A SLIGHT RISK OF SEVERE THUNDERSTORMS ACROSS OKLAHOMA...</pre>"
            ),
            "link": "https://www.spc.noaa.gov/products/outlook/day1otlk_0100.html",
        }
        calls = []
        old_fetch = weather_bot.fetch_spc_entries
        old_post = weather_bot.post_discord
        weather_bot.fetch_spc_entries = lambda: [entry]
        weather_bot.post_discord = lambda *args, **kwargs: calls.append((args, kwargs)) or True
        try:
            weather_bot.send_new_spc_items({"seen_spc": []})
        finally:
            weather_bot.fetch_spc_entries = old_fetch
            weather_bot.post_discord = old_post

        self.assertEqual(len(calls), 1)
        args, kwargs = calls[0]
        self.assertEqual(kwargs["content"], f"🌩️ **SPC Item:** {entry['title']} - near OKLAHOMA")
        self.assertEqual(kwargs["embeds"][0]["title"], f"🌩️ {entry['title']}")
        self.assertEqual(kwargs["embeds"][0]["url"], entry["link"])
        self.assertEqual(kwargs["embeds"][0]["image"]["url"], "https://www.spc.noaa.gov/products/outlook/day1otlk.png")
        self.assertIn("SLIGHT RISK", kwargs["embeds"][0]["description"])
        self.assertNotIn("<pre>", kwargs["embeds"][0]["description"])

    def test_spc_item_image_url_accepts_relative_feed_images(self):
        entry = {
            "summary": (
                'SPC Day 2 Outlook <br /><a href="/products/outlook/day2otlk.html">'
                '<img alt="Day 2 Outlook Image" src="/products/outlook/day2otlk.png" />'
                "</a>"
            )
        }

        image_url = weather_bot.spc_item_image_url(entry)

        self.assertEqual(image_url, "https://www.spc.noaa.gov/products/outlook/day2otlk.png")

    def test_spc_watch_summary_uses_primary_threats_not_full_product(self):
        entry = {
            "title": "SPC Severe Thunderstorm Watch 316",
            "summary": (
                "WW 316 SEVERE TSTM OK TX 112050Z - 120300Z URGENT - IMMEDIATE BROADCAST REQUESTED "
                "Primary threats include... Scattered large hail and isolated very large hail events to 2 inches in diameter possible. "
                "Scattered damaging gusts to 70 mph possible. SUMMARY... Thunderstorms will likely develop along a cold front across Oklahoma."
            ),
            "link": "https://example.test/watch",
        }

        embed = weather_bot.build_spc_item_embed(entry)

        self.assertIn("large hail", embed["description"])
        self.assertIn("70 mph", embed["description"])
        self.assertNotIn("URGENT - IMMEDIATE BROADCAST REQUESTED", embed["description"])
        self.assertLess(len(embed["description"]), 360)

    def test_spc_item_content_includes_extracted_location(self):
        entry = {
            "title": "SPC MD 1098",
            "summary": "SUMMARY... Risk of large hail and damaging winds spreading eastward across central/northeastern Oklahoma and southeast Kansas.",
        }

        content = weather_bot.spc_item_content(entry)

        self.assertEqual(content, "🌩️ **SPC Item:** SPC MD 1098 - near central/northeastern Oklahoma and southeast Kansas")

    def test_spc_status_report_without_oklahoma_location_is_ignored(self):
        entry = {
            "id": "spc-watch-336-status",
            "title": "SPC Tornado Watch 336 Status Reports",
            "summary": (
                "WW 0336 Status Updates STATUS REPORT ON WW 336 SEVERE WEATHER THREAT CONTINUES "
                "RIGHT OF A LINE FROM 20 NNW MGW TO 30 SSW DUJ TO 25 ESE BFD TO 45 NE BFD. "
                "For additional information see mesoscale discussion 1145. "
                "NWS Storm Prediction Center Norman OK ATTN...WFO...PBZ...CTP"
            ),
            "link": "https://example.test/status",
        }
        calls = []
        old_fetch = weather_bot.fetch_spc_entries
        old_post = weather_bot.post_discord
        weather_bot.fetch_spc_entries = lambda: [entry]
        weather_bot.post_discord = lambda *args, **kwargs: calls.append((args, kwargs)) or True
        try:
            weather_bot.send_new_spc_items({"seen_spc": []})
        finally:
            weather_bot.fetch_spc_entries = old_fetch
            weather_bot.post_discord = old_post

        self.assertEqual(calls, [])

    def test_spc_items_dedupe_when_feed_id_changes(self):
        first_entry = {
            "id": "rss-id-1",
            "title": "SPC Jun 17, 2026 2000 UTC Day 1 Convective Outlook",
            "summary": "SUMMARY... Severe storms are possible across western Oklahoma this evening.",
            "link": "https://www.spc.noaa.gov/products/outlook/day1otlk_2000.html",
        }
        second_entry = dict(first_entry, id="rss-id-2")
        calls = []
        state = {"seen_spc": []}
        old_fetch = weather_bot.fetch_spc_entries
        old_post = weather_bot.post_discord
        weather_bot.post_discord = lambda *args, **kwargs: calls.append((args, kwargs)) or True
        try:
            weather_bot.fetch_spc_entries = lambda: [first_entry]
            weather_bot.send_new_spc_items(state)
            weather_bot.fetch_spc_entries = lambda: [second_entry]
            weather_bot.send_new_spc_items(state)
        finally:
            weather_bot.fetch_spc_entries = old_fetch
            weather_bot.post_discord = old_post

        self.assertEqual(len(calls), 1)
        self.assertIn(first_entry["link"], state["seen_spc"])
        self.assertIn(f"title:{first_entry['title'].lower()}", state["seen_spc"])

    def test_spc_items_ignore_norman_ok_office_header(self):
        entry = {
            "id": "spc-day1-no-ok",
            "title": "SPC Jun 8, 2026 1300 UTC Day 1 Convective Outlook",
            "summary": (
                'SPC 1300Z Day 1 Outlook <br /><a href="https://www.spc.noaa.gov/products/outlook/day1otlk.html">'
                '<img alt="Day 1 Outlook Image" src="https://www.spc.noaa.gov/products/outlook/day1otlk.png" />'
                "</a><pre>Day 1 Convective Outlook NWS Storm Prediction Center Norman OK "
                "0703 AM CDT Mon Jun 08 2026 Valid 081300Z - 091200Z "
                "...THERE IS A SLIGHT RISK OF SEVERE THUNDERSTORMS THIS AFTERNOON AND EVENING "
                "FROM NORTHEAST COLORADO AND SOUTHEAST WYOMING INTO PARTS OF NEBRASKA AND KANSAS..."
                "</pre>"
            ),
            "link": "https://www.spc.noaa.gov/products/outlook/day1otlk_1300.html",
        }
        calls = []
        old_fetch = weather_bot.fetch_spc_entries
        old_post = weather_bot.post_discord
        weather_bot.fetch_spc_entries = lambda: [entry]
        weather_bot.post_discord = lambda *args, **kwargs: calls.append((args, kwargs)) or True
        try:
            weather_bot.send_new_spc_items({"seen_spc": []})
        finally:
            weather_bot.fetch_spc_entries = old_fetch
            weather_bot.post_discord = old_post

        self.assertEqual(calls, [])

    def test_spc_fire_weather_items_ignore_norman_ok_office_header(self):
        entry = {
            "id": "spc-fire-day1-no-ok",
            "title": "SPC Day 1 Fire Weather Outlook",
            "summary": (
                'SPC Day 1 Fire Weather Outlook <br /><a href="https://www.spc.noaa.gov/products/fire_wx/fwdy1.html">'
                '<img alt="Day 1 Fire Weather Outlook Image" src="https://www.spc.noaa.gov/products/fire_wx/day1fireotlk.gif" />'
                "</a><pre>Day 1 Fire Weather Outlook NWS Storm Prediction Center Norman OK "
                "1140 AM CDT Mon Jun 08 2026 Valid 081700Z - 091200Z "
                "...CRITICAL FIRE WEATHER AREA FOR PORTIONS OF THE SOUTHWEST...GREAT BASIN...CENTRAL ROCKIES... "
                "...Northwestern NM into southern CO... Mainly dry thunderstorms are possible this afternoon."
                "</pre>"
            ),
            "link": "https://www.spc.noaa.gov/products/fire_wx/fwdy1.html",
        }
        calls = []
        old_fetch = weather_bot.fetch_spc_entries
        old_post = weather_bot.post_discord
        weather_bot.fetch_spc_entries = lambda: [entry]
        weather_bot.post_discord = lambda *args, **kwargs: calls.append((args, kwargs)) or True
        try:
            weather_bot.send_new_spc_items({"seen_spc": []})
        finally:
            weather_bot.fetch_spc_entries = old_fetch
            weather_bot.post_discord = old_post

        self.assertEqual(calls, [])

    def test_severe_thunderstorm_warning_posts_by_default(self):
        props = {
            "event": "Severe Thunderstorm Warning",
            "severity": "Severe",
            "headline": "Severe Thunderstorm Warning",
            "description": "60 mph wind gusts and quarter size hail.",
            "instruction": "",
        }

        self.assertTrue(weather_bot.should_send_alert(props))

    def test_alert_post_content_includes_compact_area(self):
        props = {
            "areaDesc": "Tulsa, OK; Rogers, OK; Wagoner, OK; Mayes, OK",
        }

        content = weather_bot.alert_post_content("Special Weather Statement", props)

        self.assertEqual(content, "**Special Weather Statement** - in/near 4 areas: Tulsa, OK; Rogers, OK; +2 more")

    def test_tornado_alert_post_content_includes_area(self):
        props = {
            "areaDesc": "Cleveland, OK; Oklahoma, OK; McClain, OK",
        }

        content = weather_bot.alert_post_content("Tornado Warning", props)

        self.assertEqual(content, "🚨🌪️ **TORNADO WARNING:** in/near 3 areas: Cleveland, OK; Oklahoma, OK; +1 more")

    def test_severe_thunderstorm_warning_embed_is_high_priority_with_radar(self):
        props = {
            "event": "Severe Thunderstorm Warning",
            "severity": "Severe",
            "urgency": "Immediate",
            "certainty": "Observed",
            "headline": "Severe Thunderstorm Warning",
            "description": "70 mph wind gusts and quarter size hail.",
            "instruction": "Move indoors.",
            "areaDesc": "Tulsa, OK",
            "@id": "https://example.test/severe",
        }
        geometry = {"type": "Point", "coordinates": [-95.99, 36.15]}

        embed = weather_bot.build_alert_embed(props, geometry=geometry)

        self.assertTrue(embed["title"].startswith("⚠️⛈️"))
        self.assertIn("SEVERE STORM WARNING", embed["description"])
        self.assertEqual(embed["color"], 0xFF9900)
        self.assertEqual(embed["fields"][0]["name"], "🚨 Importance")
        self.assertEqual(embed["fields"][1]["name"], "📍 Affected area")
        self.assertIn("/KINX_loop.gif", embed["image"]["url"])

    def test_tornado_warning_embed_is_highest_priority_with_radar(self):
        props = {
            "event": "Tornado Warning",
            "severity": "Extreme",
            "urgency": "Immediate",
            "certainty": "Observed",
            "headline": "Tornado Warning",
            "description": "Confirmed tornado near Norman.",
            "instruction": "Take shelter now.",
            "areaDesc": "Cleveland, OK",
            "@id": "https://example.test/tornado",
        }
        geometry = {"type": "Point", "coordinates": [-97.44, 35.22]}

        embed = weather_bot.build_alert_embed(props, geometry=geometry)

        self.assertTrue(embed["title"].startswith("🚨🌪️"))
        self.assertIn("TAKE SHELTER NOW", embed["description"])
        self.assertIn("Highest priority alert", embed["fields"][0]["value"])
        self.assertEqual(embed["color"], 0xB00020)
        self.assertIn("/KTLX_loop.gif", embed["image"]["url"])

    def test_watch_alert_embed_compacts_area_and_description(self):
        props = {
            "event": "Severe Thunderstorm Watch",
            "severity": "Severe",
            "urgency": "Future",
            "certainty": "Possible",
            "headline": "Severe Thunderstorm Watch issued until 9 PM CDT",
            "description": (
                "THE NATIONAL WEATHER SERVICE HAS ISSUED SEVERE THUNDERSTORM WATCH 314 IN EFFECT UNTIL 9 PM "
                "FOR THE FOLLOWING AREAS IN OKLAHOMA THIS WATCH INCLUDES 19 COUNTIES. "
                "Primary threats include... Scattered large hail and isolated very large hail events to 2 inches possible. "
                "Scattered damaging wind gusts to 70 mph possible."
            ),
            "areaDesc": "Adair, OK; Cherokee, OK; Craig, OK; Creek, OK; Delaware, OK; Mayes, OK; McIntosh, OK; Muskogee, OK; Nowata, OK; Okfuskee, OK",
            "@id": "https://example.test/watch",
        }

        embed = weather_bot.build_alert_embed(props)

        self.assertIn("large hail", embed["description"])
        self.assertIn("70 mph", embed["description"])
        self.assertNotIn("THE NATIONAL WEATHER SERVICE", embed["description"])
        self.assertIn("10 areas:", embed["fields"][0]["value"])
        self.assertIn("+2 more", embed["fields"][0]["value"])

    def test_parse_afd_notes_prefers_key_messages(self):
        text = """
        Area Forecast Discussion
        .KEY MESSAGES...
        Severe storms are possible this evening across western Oklahoma.

        .SHORT TERM
        Additional discussion follows.
        &&
        """

        self.assertIn("Severe storms", weather_bot.parse_afd_notes(text))

    def test_afd_notes_are_trimmed_to_key_bullets(self):
        section = "- Severe storms possible tonight. - Hot again Tuesday. - Rain chances continue. - Extra detail."

        note = weather_bot.summarize_afd_section(section)

        self.assertIn("• Severe storms possible tonight.", note)
        self.assertIn("• Hot again Tuesday.", note)
        self.assertNotIn("Extra detail", note)

    def test_clean_repairs_mojibake_degree_symbol(self):
        self.assertEqual(weather_bot.clean("73Â°F"), "73°F")

    def test_build_brief_embeds_includes_forecaster_notes(self):
        data = {
            "alerts": [],
            "important": [],
            "day1": {"day": "Day 1", "risk": "Slight", "probabilities": {}, "intensity": {}, "url": "https://example.test", "summary": "", "risk_lines": [], "source": "SPC GIS"},
            "day2": {"day": "Day 2", "risk": "Marginal", "probabilities": {}, "intensity": {}, "url": "https://example.test", "summary": "", "risk_lines": [], "source": "SPC GIS"},
            "forecasts": ["OKC: forecast"],
            "forecaster_notes": [{"office": "OUN", "text": "Severe storms possible.", "url": ""}],
            "now": "Monday, June 1 at 9:00 PM",
        }

        embeds = weather_bot.build_brief_embeds(data)

        self.assertTrue(any(embed["title"] == "📝 Forecaster Notes" for embed in embeds))
        self.assertTrue(any("image" in embed for embed in embeds if "SPC" in embed["title"]))
        city_field = embeds[0]["fields"][1]["value"]
        self.assertTrue(city_field.startswith("• OKC"))


    def test_build_brief_embeds_uses_png_spc_maps(self):
        data = {
            "alerts": [],
            "important": [],
            "day1": {"day": "Day 1", "risk": "Slight", "probabilities": {}, "intensity": {}, "url": "https://example.test", "summary": "", "risk_lines": [], "source": "SPC GIS"},
            "day2": {"day": "Day 2", "risk": "Marginal", "probabilities": {}, "intensity": {}, "url": "https://example.test", "summary": "", "risk_lines": [], "source": "SPC GIS"},
            "forecasts": ["OKC: forecast"],
            "forecaster_notes": [],
            "now": "Monday, June 1 at 9:00 PM",
        }

        embeds = weather_bot.build_brief_embeds(data)

        self.assertEqual(embeds[1]["image"]["url"], "https://www.spc.noaa.gov/products/outlook/day1otlk.png")
        self.assertEqual(embeds[2]["image"]["url"], "https://www.spc.noaa.gov/products/outlook/day2otlk.png")

    def test_overview_uses_strongest_alert_color_when_alerts_exist(self):
        data = {
            "alerts": [{"properties": {"event": "Tornado Warning", "severity": "Extreme"}}],
            "important": [{"event": "Tornado Warning", "severity": "Extreme", "areaDesc": "Canadian, OK"}],
            "day1": {"day": "Day 1", "risk": "Slight", "probabilities": {}, "intensity": {}, "url": "https://example.test", "summary": "National text.", "risk_lines": [], "source": "SPC GIS"},
            "day2": {"day": "Day 2", "risk": "Marginal", "probabilities": {}, "intensity": {}, "url": "https://example.test", "summary": "", "risk_lines": [], "source": "SPC GIS"},
            "forecasts": ["OKC: forecast"],
            "forecaster_notes": [],
            "now": "Monday, June 1 at 9:00 PM",
        }

        embeds = weather_bot.build_brief_embeds(data)

        self.assertEqual(embeds[0]["color"], 0xB00020)
        self.assertEqual(embeds[1]["fields"][1]["name"], "🌎 SPC national context")
        self.assertTrue(any(embed.get("color") == 0xB00020 for embed in embeds if embed["title"].endswith("Radar")))

    def test_watch_alert_line_summarizes_count_and_expiration(self):
        props = {
            "event": "Severe Thunderstorm Watch",
            "headline": "Severe Thunderstorm Watch 412",
            "areaDesc": "Alfalfa, OK; Garfield, OK; Grant, OK",
            "expires": "2026-06-02T03:00:00-05:00",
        }

        line = weather_bot.brief_alert_line(props)

        self.assertIn("Severe Thunderstorm Watch #412", line)
        self.assertIn("3 Oklahoma counties", line)
        self.assertIn("expires", line)

    def test_expected_timing_uses_forecaster_notes(self):
        data = {
            "forecaster_notes": [{"text": "Storms possible this evening. Dry later in the week."}],
            "day1": {"summary": ""},
            "day2": {"summary": ""},
        }

        timing = weather_bot.expected_timing(data)

        self.assertIn("this evening", timing)

    def test_afternoon_severe_brief_posts_once_after_scheduled_time(self):
        class FixedDateTime:
            @classmethod
            def now(cls, tz):
                return real_datetime(2026, 6, 8, 15, 31, tzinfo=tz)

        calls = []
        old_datetime = weather_bot.datetime
        old_enabled = weather_bot.AFTERNOON_SEVERE_BRIEF_ENABLED
        old_post = weather_bot.post_brief
        weather_bot.datetime = FixedDateTime
        weather_bot.AFTERNOON_SEVERE_BRIEF_ENABLED = True
        weather_bot.post_brief = lambda **kwargs: calls.append(kwargs) or True
        state = {"last_afternoon_severe_brief_date": None}
        try:
            weather_bot.maybe_send_afternoon_severe_brief(state)
            weather_bot.maybe_send_afternoon_severe_brief(state)
        finally:
            weather_bot.datetime = old_datetime
            weather_bot.AFTERNOON_SEVERE_BRIEF_ENABLED = old_enabled
            weather_bot.post_brief = old_post

        self.assertEqual(len(calls), 1)
        self.assertEqual(state["last_afternoon_severe_brief_date"], "2026-06-08")
        self.assertEqual(calls[0]["title"], "⛈️ Oklahoma Rest-of-Day Severe Weather Brief")
        self.assertEqual(calls[0]["bottom_line_label"], "Rest-of-day severe weather")

    def test_afternoon_severe_brief_respects_disabled_flag(self):
        calls = []
        old_enabled = weather_bot.AFTERNOON_SEVERE_BRIEF_ENABLED
        old_post = weather_bot.post_brief
        weather_bot.AFTERNOON_SEVERE_BRIEF_ENABLED = False
        weather_bot.post_brief = lambda **kwargs: calls.append(kwargs) or True
        try:
            weather_bot.maybe_send_afternoon_severe_brief({"last_afternoon_severe_brief_date": None})
        finally:
            weather_bot.AFTERNOON_SEVERE_BRIEF_ENABLED = old_enabled
            weather_bot.post_brief = old_post

        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()

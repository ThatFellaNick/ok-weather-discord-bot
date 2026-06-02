import importlib.util
import os
import sys
import types
import unittest
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

    def test_severe_thunderstorm_warning_posts_by_default(self):
        props = {
            "event": "Severe Thunderstorm Warning",
            "severity": "Severe",
            "headline": "Severe Thunderstorm Warning",
            "description": "60 mph wind gusts and quarter size hail.",
            "instruction": "",
        }

        self.assertTrue(weather_bot.should_send_alert(props))

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
            "important": [],
            "day1": {"day": "Day 1", "risk": "Slight", "probabilities": {}, "intensity": {}, "url": "https://example.test", "summary": "", "risk_lines": [], "source": "SPC GIS"},
            "day2": {"day": "Day 2", "risk": "Marginal", "probabilities": {}, "intensity": {}, "url": "https://example.test", "summary": "", "risk_lines": [], "source": "SPC GIS"},
            "forecasts": ["OKC: forecast"],
            "forecaster_notes": [{"office": "OUN", "text": "Severe storms possible.", "url": ""}],
            "now": "Monday, June 1 at 9:00 PM",
        }

        embeds = weather_bot.build_brief_embeds(data)

        self.assertTrue(any(embed["title"] == "Forecaster Notes" for embed in embeds))
        self.assertTrue(any("image" in embed for embed in embeds if embed["title"].startswith("SPC")))
        city_field = embeds[0]["fields"][1]["value"]
        self.assertTrue(city_field.startswith("• OKC"))


if __name__ == "__main__":
    unittest.main()

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


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_TEMP = tempfile.TemporaryDirectory(prefix="stern-monk-action-icon-import-")
os.environ.setdefault("MONK_DB_PATH", str(Path(MODULE_TEMP.name) / "import.db"))
os.environ.setdefault("MONK_CHANNEL_ID", "123456789")

import main  # noqa: E402


EXPECTED_ICONS = {
    1: ("green", "action-1-green.png", "ACTION_EMOJI_1", "🟢"),
    3: ("blue", "action-3-blue.png", "ACTION_EMOJI_3", "🔵"),
    5: ("yellow", "action-5-yellow.png", "ACTION_EMOJI_5", "🟡"),
    10: ("red", "action-10-red.png", "ACTION_EMOJI_10", "🔴"),
    100: ("purple", "action-100-purple.png", "ACTION_EMOJI_100", "🟣"),
}


class ActionIconTests(unittest.TestCase):
    def test_icon_files_are_mobile_sized_transparent_pngs(self) -> None:
        icon_root = PROJECT_ROOT / "assets" / "town_life" / "action_icons"
        for _, filename, _, _ in EXPECTED_ICONS.values():
            raw = (icon_root / filename).read_bytes()
            self.assertEqual(raw[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(
                (int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")),
                (128, 128),
            )
            color_type = raw[25]
            self.assertTrue(
                color_type in (4, 6) or b"tRNS" in raw,
                f"{filename} must preserve transparency",
            )

    def test_manifest_matches_count_color_and_environment_mapping(self) -> None:
        manifest_path = (
            PROJECT_ROOT
            / "assets"
            / "town_life"
            / "action_icons"
            / "action-icon-manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        actual = {
            int(item["count"]): (
                item["color"],
                item["filename"],
                item["railway_variable"],
            )
            for item in manifest["icons"]
        }
        expected = {
            count: (color, filename, variable)
            for count, (color, filename, variable, _) in EXPECTED_ICONS.items()
        }
        self.assertEqual(actual, expected)

    def test_default_button_emoji_mapping_is_color_first(self) -> None:
        for count, (_, _, variable, fallback) in EXPECTED_ICONS.items():
            if not os.environ.get(variable):
                self.assertEqual(str(main.ACTION_COUNT_EMOJIS[count]), fallback)

    def test_custom_discord_emoji_can_be_loaded_from_railway_variable(self) -> None:
        variable = "ACTION_EMOJI_TEST"
        previous = os.environ.get(variable)
        try:
            os.environ[variable] = "<:action_1_green:123456789012345678>"
            emoji = main.configured_action_emoji(variable, "🟢")
            self.assertEqual(emoji.name, "action_1_green")
            self.assertEqual(emoji.id, 123456789012345678)
        finally:
            if previous is None:
                os.environ.pop(variable, None)
            else:
                os.environ[variable] = previous

    def test_life_action_buttons_use_the_expected_color_categories(self) -> None:
        views = (
            main.FishingRouteView(1001),
            main.CrystalRouteView(1001),
        )
        expected_by_suffix = {
            "×1": 1,
            "×3": 3,
            "×5": 5,
            "×10": 10,
            "｜100體": 100,
        }
        matched_counts: set[int] = set()
        for view in views:
            for child in view.children:
                label = str(getattr(child, "label", "") or "")
                for suffix, count in expected_by_suffix.items():
                    if label.endswith(suffix):
                        self.assertEqual(
                            str(getattr(child, "emoji", "")),
                            str(main.ACTION_COUNT_EMOJIS[count]),
                            label,
                        )
                        matched_counts.add(count)
        self.assertEqual(matched_counts, set(EXPECTED_ICONS))


if __name__ == "__main__":
    unittest.main()

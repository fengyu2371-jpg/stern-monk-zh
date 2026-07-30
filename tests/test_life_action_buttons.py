from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path


MODULE_TEMP = tempfile.TemporaryDirectory(prefix="stern-monk-life-button-import-")
os.environ.setdefault("MONK_DB_PATH", str(Path(MODULE_TEMP.name) / "import.db"))
os.environ.setdefault("MONK_CHANNEL_ID", "123456789")

import main  # noqa: E402


class LifeActionButtonTests(unittest.TestCase):
    def test_life_profession_buttons_do_not_use_emoji(self) -> None:
        views = (
            main.FishingRouteView(1001),
            main.FishingActionView(1001, "fish"),
            main.FishingActionView(1001, "forage"),
            main.CrystalRouteView(1001),
        )
        for view in views:
            for child in view.children:
                self.assertIsNone(
                    getattr(child, "emoji", None),
                    str(getattr(child, "label", "") or ""),
                )

    def test_fishing_attempt_buttons_use_compact_text(self) -> None:
        expected_labels = {
            "1 次",
            "5 次",
            "10 次",
            "100 體",
            "返回選擇地點",
        }
        for action in ("fish", "forage"):
            labels = {
                str(getattr(child, "label", "") or "")
                for child in main.FishingActionView(1001, action).children
            }
            self.assertEqual(labels, expected_labels)


if __name__ == "__main__":
    unittest.main()

import sqlite3
import tempfile
import unittest
from pathlib import Path

from academy_db import AcademyDatabase


class PlaceOperatorDatabaseTests(unittest.TestCase):
    def test_new_place_stores_operator_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db = AcademyDatabase(Path(temp_dir) / "monk.db")
            db.initialize()
            db.save_profile(
                user_id=123,
                student_name="辰巳",
                preferred_name="巽",
                house="燭羽院",
                major="魔物",
                enrollment_year="2026",
                introduction="",
                companion_name="",
            )
            place_id = db.create_place(
                user_id=123,
                name="月光商店",
                place_type="商店",
                district="東街",
                description="測試店鋪",
                operator_name="巽與蜂月燐",
                source_kind="新登記",
                status="營業中",
                allow_oracle=True,
                is_public=True,
            )
            place = db.get_user_place(
                user_id=123,
                place_id=place_id,
            )
            self.assertEqual(
                place["operator_name"],
                "巽與蜂月燐",
            )

    def test_legacy_place_is_backfilled_from_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "legacy.db"
            conn = sqlite3.connect(path)
            conn.executescript(
                """
                CREATE TABLE student_profiles (
                    user_id TEXT PRIMARY KEY,
                    student_name TEXT NOT NULL,
                    preferred_name TEXT NOT NULL,
                    house TEXT NOT NULL,
                    major TEXT NOT NULL DEFAULT '',
                    enrollment_year TEXT NOT NULL DEFAULT '',
                    introduction TEXT NOT NULL DEFAULT '',
                    companion_name TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                INSERT INTO student_profiles (
                    user_id, student_name, preferred_name, house,
                    created_at, updated_at
                )
                VALUES ('123', '辰巳', '巽', '燭羽院', 'now', 'now');

                CREATE TABLE student_places (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    place_type TEXT NOT NULL,
                    district TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    source_kind TEXT NOT NULL DEFAULT '新登記',
                    status TEXT NOT NULL DEFAULT '使用中',
                    allow_oracle INTEGER NOT NULL DEFAULT 1,
                    is_public INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                INSERT INTO student_places (
                    user_id, name, place_type, created_at, updated_at
                )
                VALUES ('123', '舊商店', '商店', 'now', 'now');
                """
            )
            conn.commit()
            conn.close()

            db = AcademyDatabase(path)
            db.initialize()
            place = db.list_user_places(123)[0]
            self.assertEqual(place["operator_name"], "巽")


class PlaceOperatorUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (
            Path(__file__).resolve().parents[1] / "monk_bot.py"
        ).read_text(encoding="utf-8")

    def test_student_page_has_direct_add_place_button(self) -> None:
        self.assertIn(
            'label="新增地點"',
            self.source,
        )
        self.assertIn(
            "class StudentHubView(UserOwnedView)",
            self.source,
        )

    def test_place_modal_has_operator_field(self) -> None:
        self.assertIn(
            "operator_name = discord.ui.TextInput",
            self.source,
        )
        self.assertIn(
            'self.operator_name.label = "店主／經營者"',
            self.source,
        )
        self.assertIn(
            'self.operator_name.label = "居住者"',
            self.source,
        )

    def test_public_place_display_uses_operator_name(self) -> None:
        self.assertIn(
            "place.get('operator_name')",
            self.source,
        )


if __name__ == "__main__":
    unittest.main()

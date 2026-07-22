import tempfile
import unittest
from datetime import date
from pathlib import Path

from academy_db import AcademyDatabase, month_week_info


class WeekInfoTests(unittest.TestCase):
    def test_month_week_labels(self) -> None:
        first = month_week_info(date(2026, 7, 1))
        fourth = month_week_info(date(2026, 7, 22))
        fifth = month_week_info(date(2026, 7, 31))

        self.assertEqual(first.label, "7-1")
        self.assertEqual(first.start_date.isoformat(), "2026-07-01")
        self.assertEqual(first.end_date.isoformat(), "2026-07-07")
        self.assertEqual(fourth.label, "7-4")
        self.assertEqual(fourth.end_date.isoformat(), "2026-07-28")
        self.assertEqual(fifth.label, "7-5")
        self.assertEqual(fifth.end_date.isoformat(), "2026-07-31")


class AcademyDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = AcademyDatabase(Path(self.temp_dir.name) / "monk.db")
        self.db.initialize()
        self.db.save_profile(
            user_id=123,
            student_name="測試學生",
            preferred_name="學生",
            house="星泉院",
            major="魔藥",
            enrollment_year="2026",
            introduction="測試簡介",
            companion_name="同行者",
        )
        self.db.save_preferences(
            user_id=123,
            liked_themes="雨天、旅行",
            avoided_topics="血腥",
            creative_keywords="圖書館、熱可可",
            preferred_scenes="商店街",
            allow_place_context=True,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_profile_and_preferences_round_trip(self) -> None:
        bundle = self.db.get_profile_bundle(123)

        self.assertEqual(bundle["preferred_name"], "學生")
        self.assertEqual(bundle["preferences"]["creative_keywords"], "圖書館、熱可可")

    def test_place_and_oracle_round_trip(self) -> None:
        self.db.create_place(
            user_id=123,
            name="月光書店",
            place_type="書店",
            district="學院城東街",
            description="夜間營業。",
            source_kind="舊企劃遷入",
            status="營業中",
            allow_oracle=False,
            is_public=True,
        )

        self.assertEqual(len(self.db.list_user_places(123)), 1)
        self.assertEqual(len(self.db.list_public_places("書店")), 1)
        self.assertEqual(len(self.db.list_oracle_places(123)), 1)
        self.assertEqual(
            self.db.list_oracle_places(123)[0]["allow_oracle"],
            1,
        )

        place = self.db.list_user_places(123)[0]
        hidden = self.db.update_place_visibility(
            user_id=123,
            place_id=place["id"],
            is_public=False,
        )
        self.assertEqual(hidden["is_public"], 0)
        self.assertEqual(self.db.list_public_places("書店"), [])

        shown = self.db.update_place_visibility(
            user_id=123,
            place_id=place["id"],
            is_public=True,
        )
        self.assertEqual(shown["is_public"], 1)
        self.assertEqual(len(self.db.list_public_places("書店")), 1)

        self.assertIsNone(
            self.db.update_place_visibility(
                user_id=999,
                place_id=place["id"],
                is_public=False,
            )
        )

        week = month_week_info(date(2026, 7, 22))
        page = self.db.create_oracle(
            user_id=123,
            week=week,
            oracle_text="本週測試神諭。",
            used_keywords="圖書館",
            used_place_names="月光書店",
        )

        self.assertEqual(page["week_label"], "7-4")
        self.assertEqual(page["status"], "未完成")

        second_page = self.db.create_oracle(
            user_id=123,
            week=week,
            oracle_text="同週第二頁神諭。",
            used_keywords="熱可可",
            used_place_names="",
        )
        self.assertNotEqual(page["id"], second_page["id"])
        self.assertEqual(
            self.db.count_oracles_by_week(123, week.key),
            2,
        )
        self.assertEqual(
            self.db.get_oracle_by_week(123, week.key)["id"],
            second_page["id"],
        )

        self.assertTrue(
            self.db.delete_oracle(
                page_id=second_page["id"],
                user_id=123,
            )
        )
        self.assertEqual(
            self.db.count_oracles_by_week(123, week.key),
            1,
        )
        self.assertFalse(
            self.db.delete_oracle(
                page_id=second_page["id"],
                user_id=999,
            )
        )

        updated = self.db.set_oracle_status(
            page_id=page["id"],
            user_id=123,
            status="已完成",
        )
        self.assertEqual(updated["status"], "已完成")
        self.assertIsNotNone(updated["completed_at"])

    def test_profile_delete_cascades(self) -> None:
        week = month_week_info(date(2026, 7, 1))
        self.db.create_oracle(
            user_id=123,
            week=week,
            oracle_text="神諭。",
            used_keywords="",
            used_place_names="",
        )

        self.assertTrue(self.db.delete_profile(123))
        self.assertEqual(self.db.list_oracles(123), [])


class OracleUnlimitedMigrationTests(unittest.TestCase):
    def test_legacy_weekly_unique_constraint_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "legacy.db"
            connection = __import__("sqlite3").connect(path)
            connection.executescript(
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
                VALUES ('123', '學生', '學生', '星泉院', 'now', 'now');

                CREATE TABLE oracle_pages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    week_key TEXT NOT NULL,
                    week_label TEXT NOT NULL,
                    year INTEGER NOT NULL,
                    month INTEGER NOT NULL,
                    week_index INTEGER NOT NULL,
                    period_start TEXT NOT NULL,
                    period_end TEXT NOT NULL,
                    oracle_text TEXT NOT NULL,
                    used_keywords TEXT NOT NULL DEFAULT '',
                    used_place_names TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '未完成',
                    completed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, week_key)
                );

                INSERT INTO oracle_pages (
                    user_id, week_key, week_label, year, month,
                    week_index, period_start, period_end, oracle_text,
                    created_at, updated_at
                )
                VALUES (
                    '123', '2026-07-4', '7-4', 2026, 7, 4,
                    '2026-07-22', '2026-07-28', '舊神諭', 'now', 'now'
                );
                """
            )
            connection.commit()
            connection.close()

            db = AcademyDatabase(path)
            db.initialize()
            week = month_week_info(date(2026, 7, 22))
            new_page = db.create_oracle(
                user_id=123,
                week=week,
                oracle_text="遷移後的新神諭",
                used_keywords="",
                used_place_names="",
            )

            self.assertIsNotNone(new_page)
            self.assertEqual(
                db.count_oracles_by_week(123, week.key),
                2,
            )


class OracleDeletionUsageTests(unittest.TestCase):
    def test_deleting_page_does_not_refund_draw_usage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db = AcademyDatabase(Path(temp_dir) / "monk.db")
            db.initialize()
            db.save_profile(
                user_id=123,
                student_name="學生",
                preferred_name="學生",
                house="星泉院",
                major="",
                enrollment_year="",
                introduction="",
                companion_name="",
            )
            week = month_week_info(date(2026, 7, 22))
            db.try_reserve_usage(
                user_id=123,
                usage_scope="oracle_week",
                period_key=week.key,
                limit=3,
            )
            page = db.create_oracle(
                user_id=123,
                week=week,
                oracle_text="測試神諭",
                used_keywords="",
                used_place_names="",
            )

            self.assertTrue(
                db.delete_oracle(
                    page_id=page["id"],
                    user_id=123,
                )
            )
            self.assertEqual(
                db.get_usage_count(
                    user_id=123,
                    usage_scope="oracle_week",
                    period_key=week.key,
                ),
                1,
            )


if __name__ == "__main__":
    unittest.main()

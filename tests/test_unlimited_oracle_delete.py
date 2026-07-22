import ast
import tempfile
import unittest
from datetime import date
from pathlib import Path

from academy_db import AcademyDatabase, month_week_info


class PersistentUsageLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "monk.db"
        self.db = AcademyDatabase(self.db_path)
        self.db.initialize()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_oracle_allows_three_reservations_per_week(self) -> None:
        week = month_week_info(date(2026, 7, 22))

        self.assertEqual(
            self.db.try_reserve_usage(
                user_id=123,
                usage_scope="oracle_week",
                period_key=week.key,
                limit=3,
            ),
            1,
        )
        self.assertEqual(
            self.db.try_reserve_usage(
                user_id=123,
                usage_scope="oracle_week",
                period_key=week.key,
                limit=3,
            ),
            2,
        )
        self.assertEqual(
            self.db.try_reserve_usage(
                user_id=123,
                usage_scope="oracle_week",
                period_key=week.key,
                limit=3,
            ),
            3,
        )
        self.assertIsNone(
            self.db.try_reserve_usage(
                user_id=123,
                usage_scope="oracle_week",
                period_key=week.key,
                limit=3,
            )
        )

    def test_confession_allows_one_reservation_per_day(self) -> None:
        self.assertEqual(
            self.db.try_reserve_usage(
                user_id=123,
                usage_scope="confession_day",
                period_key="2026-07-22",
                limit=1,
            ),
            1,
        )
        self.assertIsNone(
            self.db.try_reserve_usage(
                user_id=123,
                usage_scope="confession_day",
                period_key="2026-07-22",
                limit=1,
            )
        )
        self.assertEqual(
            self.db.try_reserve_usage(
                user_id=123,
                usage_scope="confession_day",
                period_key="2026-07-23",
                limit=1,
            ),
            1,
        )

    def test_users_have_separate_limits(self) -> None:
        for user_id in (123, 456):
            self.assertEqual(
                self.db.try_reserve_usage(
                    user_id=user_id,
                    usage_scope="oracle_week",
                    period_key="2026-07-4",
                    limit=3,
                ),
                1,
            )

    def test_release_refunds_failed_api_attempt(self) -> None:
        self.db.try_reserve_usage(
            user_id=123,
            usage_scope="oracle_week",
            period_key="2026-07-4",
            limit=3,
        )
        self.assertEqual(
            self.db.release_usage(
                user_id=123,
                usage_scope="oracle_week",
                period_key="2026-07-4",
            ),
            0,
        )
        self.assertEqual(
            self.db.get_usage_count(
                user_id=123,
                usage_scope="oracle_week",
                period_key="2026-07-4",
            ),
            0,
        )

    def test_usage_survives_database_reopen(self) -> None:
        self.db.try_reserve_usage(
            user_id=123,
            usage_scope="confession_day",
            period_key="2026-07-22",
            limit=1,
        )

        reopened = AcademyDatabase(self.db_path)
        reopened.initialize()

        self.assertEqual(
            reopened.get_usage_count(
                user_id=123,
                usage_scope="confession_day",
                period_key="2026-07-22",
            ),
            1,
        )


class LimitAndOwnershipCodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (
            Path(__file__).resolve().parents[1] / "monk_bot.py"
        ).read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_oracle_handler_uses_weekly_limit(self) -> None:
        self.assertIn(
            "limit=SETTINGS.oracle_weekly_limit",
            self.source,
        )
        self.assertIn(
            "usage_scope=ORACLE_USAGE_SCOPE",
            self.source,
        )

    def test_confession_handler_uses_daily_limit(self) -> None:
        self.assertIn(
            "limit=SETTINGS.ai_daily_limit",
            self.source,
        )
        self.assertIn(
            "usage_scope=CONFESSION_USAGE_SCOPE",
            self.source,
        )
        self.assertIn("taipei_today().isoformat()", self.source)

    def test_local_confession_boundaries_run_before_usage_reservation(self) -> None:
        handler = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "_handle_confession"
        )
        rendered = ast.unparse(handler)
        self.assertLess(
            rendered.index("gorilla_nickname_reply"),
            rendered.index("try_reserve_usage"),
        )
        self.assertLess(
            rendered.index("confession_boundary_reply"),
            rendered.index("try_reserve_usage"),
        )

    def test_delete_does_not_release_oracle_usage(self) -> None:
        delete_class = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "OracleDeleteConfirmView"
        )
        rendered = ast.unparse(delete_class)
        self.assertIn("delete_oracle", rendered)
        self.assertNotIn("release_usage", rendered)

    def test_personal_views_use_owner_guard(self) -> None:
        guarded = (
            "EnrollmentSetupView",
            "StudentHubView",
            "TownHubView",
            "TeachingHubView",
            "OracleHubView",
            "OracleBookView",
            "OracleDeleteConfirmView",
            "PlaceVisibilityEditorView",
            "PlaceVisibilityPickerView",
        )
        for class_name in guarded:
            self.assertIn(
                f"class {class_name}(UserOwnedView)",
                self.source,
            )

    def test_owner_guard_rejects_other_players(self) -> None:
        self.assertIn(
            "這是其他學生的修士面板",
            self.source,
        )
        self.assertIn(
            "不能代替對方操作",
            self.source,
        )


if __name__ == "__main__":
    unittest.main()

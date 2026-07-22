import ast
import tempfile
import unittest
from pathlib import Path

from academy_db import AcademyDatabase


class PlayerPanelDatabaseTests(unittest.TestCase):
    def test_panel_record_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db = AcademyDatabase(Path(temp_dir) / "monk.db")
            db.initialize()
            db.save_player_panel(
                user_id=123,
                channel_id=456,
                message_id=789,
            )
            panel = db.get_player_panel(123)
            self.assertEqual(panel["channel_id"], "456")
            self.assertEqual(panel["message_id"], "789")
            self.assertEqual(len(db.list_player_panels()), 1)
            self.assertTrue(db.delete_player_panel(123))
            self.assertIsNone(db.get_player_panel(123))


class OnePanelPerPlayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (
            Path(__file__).resolve().parents[1] / "monk_bot.py"
        ).read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_entry_has_single_player_panel_button(self) -> None:
        self.assertIn(
            'label="建立／開啟我的面板"',
            self.source,
        )
        self.assertIn(
            "fetch_saved_player_panel(interaction.user.id)",
            self.source,
        )
        self.assertIn(
            "ACADEMY_DB.save_player_panel(",
            self.source,
        )

    def test_player_panel_is_public_and_owner_guarded(self) -> None:
        self.assertIn(
            "class PlayerPanelHomeView(UserOwnedView)",
            self.source,
        )
        self.assertIn(
            "這是其他學生的修士面板",
            self.source,
        )
        self.assertIn(
            "不能代替對方操作",
            self.source,
        )

    def test_timeout_is_ten_minutes(self) -> None:
        self.assertIn(
            "PLAYER_PANEL_TIMEOUT_SECONDS = 600",
            self.source,
        )
        self.assertIn(
            "10 分鐘未操作而鎖定",
            self.source,
        )
        self.assertIn(
            "class LockedPlayerPanelView(UserOwnedView)",
            self.source,
        )

    def test_public_store_data_remains_available(self) -> None:
        self.assertIn(
            "ACADEMY_DB.list_public_places()",
            self.source,
        )
        self.assertIn(
            "place.get('operator_name')",
            self.source,
        )
        self.assertIn(
            "**經營者／居住者**",
            self.source,
        )
        self.assertIn(
            "class PlacesView(UserOwnedView)",
            self.source,
        )

    def test_modal_results_edit_existing_panel(self) -> None:
        self.assertIn(
            "edit_player_panel_from_modal",
            self.source,
        )
        for class_name in (
            "OraclePreferencesModal",
            "EnrollmentModal",
            "PlaceModal",
        ):
            node = next(
                item
                for item in self.tree.body
                if isinstance(item, ast.ClassDef)
                and item.name == class_name
            )
            self.assertIn(
                "edit_player_panel_from_modal",
                ast.unparse(node),
            )

    def test_command_count_remains_two(self) -> None:
        names = []
        for node in ast.walk(self.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                func = decorator.func
                if (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "tree"
                    and func.attr == "command"
                ):
                    for keyword in decorator.keywords:
                        if (
                            keyword.arg == "name"
                            and isinstance(keyword.value, ast.Constant)
                        ):
                            names.append(keyword.value.value)
        self.assertEqual(
            sorted(names),
            sorted(["建立修士面板", "修士狀態"]),
        )


if __name__ == "__main__":
    unittest.main()

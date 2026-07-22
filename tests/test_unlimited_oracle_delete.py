import ast
import unittest
from pathlib import Path


class UnlimitedOracleAndDeleteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bot_source = (
            Path(__file__).resolve().parents[1] / "monk_bot.py"
        ).read_text(encoding="utf-8")
        cls.db_source = (
            Path(__file__).resolve().parents[1] / "academy_db.py"
        ).read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.bot_source)

    def test_weekly_unique_constraint_is_removed(self) -> None:
        self.assertNotIn(
            "UNIQUE(user_id, week_key)",
            self.db_source,
        )
        self.assertIn(
            "_migrate_oracle_pages_for_unlimited_draws",
            self.db_source,
        )

    def test_each_click_creates_a_new_page(self) -> None:
        handler = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "_handle_current_week_oracle"
        )
        calls = [
            node
            for node in ast.walk(handler)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_oracle"
        ]
        self.assertEqual(len(calls), 1)
        self.assertNotIn("existing_page", ast.unparse(handler))
        self.assertIn("draw_number", ast.unparse(handler))

    def test_delete_button_and_confirmation_exist(self) -> None:
        self.assertIn('label="刪除此頁"', self.bot_source)
        self.assertIn('label="確認刪除"', self.bot_source)
        self.assertIn("ACADEMY_DB.delete_oracle(", self.bot_source)

    def test_panel_command_count_remains_two(self) -> None:
        command_names = []
        for node in ast.walk(self.tree):
            if not isinstance(
                node,
                (ast.FunctionDef, ast.AsyncFunctionDef),
            ):
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
                            command_names.append(keyword.value.value)

        self.assertEqual(
            sorted(command_names),
            sorted(["建立修士面板", "修士狀態"]),
        )


if __name__ == "__main__":
    unittest.main()

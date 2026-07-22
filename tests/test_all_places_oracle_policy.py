import ast
import unittest
from pathlib import Path


class AllPlacesOraclePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bot_source = (
            Path(__file__).resolve().parents[1] / "monk_bot.py"
        ).read_text(encoding="utf-8")
        cls.db_source = (
            Path(__file__).resolve().parents[1] / "academy_db.py"
        ).read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.bot_source)

    def test_no_oracle_place_permission_controls(self) -> None:
        self.assertNotIn("神諭可用：是", self.bot_source)
        self.assertNotIn("神諭可用：否", self.bot_source)
        self.assertNotIn(
            "允許神諭使用你登記的地點",
            self.bot_source,
        )

    def test_all_owned_places_are_loaded_for_oracle(self) -> None:
        self.assertIn(
            "all_places = ACADEMY_DB.list_oracle_places(",
            self.bot_source,
        )
        self.assertNotIn(
            "WHERE user_id = ? AND allow_oracle = 1",
            self.db_source,
        )

    def test_visibility_only_controls_public_listing(self) -> None:
        self.assertIn('label="公開設定"', self.bot_source)
        self.assertIn("update_place_visibility", self.bot_source)
        self.assertIn("可作神諭素材**：是", self.bot_source)

    def test_command_count_remains_two(self) -> None:
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
            sorted(["學生資料", "修士狀態"]),
        )


if __name__ == "__main__":
    unittest.main()

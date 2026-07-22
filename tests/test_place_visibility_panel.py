import ast
import unittest
from pathlib import Path


class PlaceVisibilityPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (
            Path(__file__).resolve().parents[1] / "monk_bot.py"
        ).read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_visibility_is_inside_panel_not_new_command(self) -> None:
        self.assertIn('label="公開設定"', self.source)
        self.assertIn("class PlaceVisibilityEditorView", self.source)
        self.assertNotIn('name="地點公開設定"', self.source)

    def test_visibility_uses_owned_database_update(self) -> None:
        self.assertIn("ACADEMY_DB.update_place_visibility(", self.source)
        self.assertIn("user_id=self.owner_id", self.source)

    def test_public_command_count_remains_two(self) -> None:
        command_names = []
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
                            command_names.append(keyword.value.value)

        self.assertEqual(
            sorted(command_names),
            sorted(["建立修士面板", "修士狀態"]),
        )


if __name__ == "__main__":
    unittest.main()

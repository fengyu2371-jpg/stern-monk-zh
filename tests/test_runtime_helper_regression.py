import ast
import unittest
from pathlib import Path


class RuntimeHelperRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (
            Path(__file__).resolve().parents[1] / "monk_bot.py"
        ).read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.function_names = {
            node.name
            for node in cls.tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def test_panel_embed_helper_exists(self) -> None:
        self.assertIn("monk_embed", self.function_names)

    def test_teaching_helpers_exist(self) -> None:
        for name in (
            "knowledge_source_label",
            "roleplay_lines",
            "render_local_reply",
            "random_line",
        ):
            self.assertIn(name, self.function_names)

    def test_date_is_imported_for_enrollment_default_year(self) -> None:
        self.assertIn("from datetime import date", self.source)
        self.assertIn("date.today().year", self.source)

    def test_create_panel_uses_existing_embed_helper(self) -> None:
        command = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "create_monk_panel"
        )
        rendered = ast.unparse(command)
        self.assertIn("main_panel_embed(", rendered)


if __name__ == "__main__":
    unittest.main()

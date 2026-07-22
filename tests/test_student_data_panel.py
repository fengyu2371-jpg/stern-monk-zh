import ast
import unittest
from pathlib import Path


class StudentDataPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (
            Path(__file__).resolve().parents[1] / "monk_bot.py"
        ).read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_panel_command_exists(self) -> None:
        self.assertIn('name="建立學生資料面板"', self.source)

    def test_persistent_view_has_fixed_custom_id(self) -> None:
        self.assertIn("class StudentDataPanelView", self.source)
        self.assertIn("super().__init__(timeout=None)", self.source)
        self.assertIn(
            'custom_id="stern_monk:student_data:view_my_profile"',
            self.source,
        )

    def test_persistent_view_is_registered_on_startup(self) -> None:
        self.assertIn(
            "self.add_view(StudentDataPanelView())",
            self.source,
        )

    def test_panel_response_is_ephemeral(self) -> None:
        panel_class = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "StudentDataPanelView"
        )
        callback = next(
            node
            for node in panel_class.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "view_my_profile"
        )
        ephemeral_values = []
        for node in ast.walk(callback):
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg == "ephemeral":
                        ephemeral_values.append(
                            isinstance(keyword.value, ast.Constant)
                            and keyword.value.value is True
                        )
        self.assertTrue(ephemeral_values)
        self.assertTrue(all(ephemeral_values))

    def test_private_menu_checks_owner(self) -> None:
        self.assertIn(
            "class StudentPrivateMenuView(UserOwnedView)",
            self.source,
        )


if __name__ == "__main__":
    unittest.main()

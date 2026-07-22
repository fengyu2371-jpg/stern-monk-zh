import ast
import unittest
from pathlib import Path


class MonkMainPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (
            Path(__file__).resolve().parents[1] / "monk_bot.py"
        ).read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_panel_command_exists(self) -> None:
        self.assertIn('name="建立修士面板"', self.source)

    def test_main_panel_is_persistent(self) -> None:
        self.assertIn("class MonkMainPanelView", self.source)
        self.assertIn("super().__init__(timeout=None)", self.source)
        for custom_id in (
            "stern_monk:main:student",
            "stern_monk:main:town",
            "stern_monk:main:oracle",
            "stern_monk:main:teaching",
            "stern_monk:main:confession",
        ):
            self.assertIn(custom_id, self.source)

    def test_main_panel_is_registered_on_startup(self) -> None:
        self.assertIn(
            "self.add_view(MonkMainPanelView())",
            self.source,
        )

    def test_main_buttons_exist(self) -> None:
        for label in (
            'label="學生資料"',
            'label="城下町"',
            'label="神諭冊"',
            'label="教學"',
            'label="告解"',
        ):
            self.assertIn(label, self.source)

    def test_private_views_check_owner(self) -> None:
        for class_name in (
            "EnrollmentSetupView",
            "StudentHubView",
            "TownHubView",
            "TeachingHubView",
            "OracleHubView",
        ):
            self.assertIn(
                f"class {class_name}(UserOwnedView)",
                self.source,
            )

    def test_slash_command_count_is_two(self) -> None:
        command_names = []
        for node in self.tree.body:
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            for decorator in node.decorator_list:
                if (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and isinstance(decorator.func.value, ast.Name)
                    and decorator.func.value.id == "tree"
                    and decorator.func.attr == "command"
                ):
                    for keyword in decorator.keywords:
                        if (
                            keyword.arg == "name"
                            and isinstance(keyword.value, ast.Constant)
                        ):
                            command_names.append(keyword.value.value)

        self.assertEqual(
            command_names,
            ["建立修士面板", "修士狀態"],
        )


if __name__ == "__main__":
    unittest.main()

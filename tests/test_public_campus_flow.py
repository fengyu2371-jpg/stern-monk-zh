import ast
import unittest
from pathlib import Path


class SinglePanelPublicFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (
            Path(__file__).resolve().parents[1] / "monk_bot.py"
        ).read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def _method(self, class_name: str, method_name: str) -> ast.AsyncFunctionDef:
        class_node = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == class_name
        )
        return next(
            node
            for node in class_node.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == method_name
        )

    def test_main_buttons_claim_and_edit_same_message(self) -> None:
        for method_name in ("student_data", "town", "oracle", "teaching"):
            rendered = ast.unparse(
                self._method("MonkMainPanelView", method_name)
            )
            self.assertIn("claim_panel_session", rendered)
            self.assertIn("response.edit_message", rendered)
            self.assertNotIn("response.send_message", rendered)

    def test_owner_guard_blocks_other_players_with_private_warning(self) -> None:
        self.assertIn("不能代替操作", self.source)
        self.assertIn("session.owner_name", self.source)
        self.assertIn("ephemeral=True", self.source)

    def test_owner_views_are_session_guarded(self) -> None:
        guarded_classes = (
            "EnrollmentSetupView",
            "StudentHubView",
            "MyPlacesHubView",
            "PlaceRegistrationOptionsView",
            "PlaceVisibilityPickerView",
            "PlaceVisibilityEditorView",
            "PlacesView",
            "OracleHubView",
            "OracleBookView",
            "OracleDeleteConfirmView",
            "TeachingHubView",
        )
        for class_name in guarded_classes:
            self.assertIn(
                f"class {class_name}(UserOwnedView)",
                self.source,
            )

    def test_ten_minute_timeout_restores_main_panel(self) -> None:
        self.assertIn("PANEL_SESSION_TIMEOUT_SECONDS = 600", self.source)
        self.assertIn(
            "上一個操作畫面因 10 分鐘未操作而鎖定",
            self.source,
        )
        self.assertIn("view=MonkMainPanelView()", self.source)

    def test_return_button_exists(self) -> None:
        self.assertIn("class ReturnToMainPanelButton", self.source)
        self.assertIn('label="返回修士面板"', self.source)

    def test_modals_update_active_panel(self) -> None:
        for class_name in (
            "OraclePreferencesModal",
            "EnrollmentModal",
            "PlaceModal",
        ):
            class_node = next(
                node
                for node in self.tree.body
                if isinstance(node, ast.ClassDef)
                and node.name == class_name
            )
            self.assertIn(
                "edit_active_panel_from_modal",
                ast.unparse(class_node),
            )

        teaching_handler = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "_handle_teaching_question"
        )
        self.assertIn(
            "edit_active_panel_from_modal",
            ast.unparse(teaching_handler),
        )

    def test_confession_is_still_private(self) -> None:
        handler = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "_handle_confession"
        )
        rendered = ast.unparse(handler)
        self.assertIn("ephemeral=True", rendered)
        self.assertIn("clear_panel_session", rendered)

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

import ast
import unittest
from pathlib import Path


class PublicVisibleOwnerGuardTests(unittest.TestCase):
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

    def test_main_panel_personal_sections_are_public_visible(self) -> None:
        for method_name in ("town", "oracle", "teaching"):
            rendered = ast.unparse(
                self._method("MonkMainPanelView", method_name)
            )
            self.assertNotIn("ephemeral=True", rendered)

        student = ast.unparse(
            self._method("MonkMainPanelView", "student_data")
        )
        # 未入學表單仍私密；已有學籍後的 StudentHubView 分支公開。
        self.assertIn("ephemeral=True", student)
        normalized = "".join(student.split())
        self.assertIn(
            "view=StudentHubView(interaction.user.id))",
            normalized,
        )
        self.assertNotIn(
            "view=StudentHubView(interaction.user.id),ephemeral=True)",
            normalized,
        )

    def test_owner_guard_still_blocks_other_players(self) -> None:
        self.assertIn(
            "這份資料屬於其他學生，不能代替操作。",
            self.source,
        )
        guarded_classes = (
            "StudentHubView",
            "MyPlacesHubView",
            "PlaceRegistrationOptionsView",
            "PlaceVisibilityPickerView",
            "PlaceVisibilityEditorView",
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

    def test_oracle_generation_edits_original_message(self) -> None:
        self.assertIn("interaction.edit_original_response", self.source)
        self.assertIn("神諭生成中", self.source)
        handler = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "_handle_current_week_oracle"
        )
        rendered = ast.unparse(handler)
        self.assertIn("response.edit_message", rendered)
        self.assertIn("edit_original_response", rendered)
        self.assertNotIn("followup.send", rendered)
        self.assertNotIn("response.defer", rendered)

    def test_my_places_view_all_edits_not_popup(self) -> None:
        rendered = ast.unparse(
            self._method("MyPlacesHubView", "view_all_private")
        )
        self.assertIn("edit_message", rendered)
        self.assertNotIn("send_message", rendered)

    def test_sensitive_parts_remain_private(self) -> None:
        confession = ast.unparse(
            self._method("MonkMainPanelView", "confession")
        )
        self.assertIn("send_modal", confession)

        delete_profile = ast.unparse(
            self._method("StudentHubView", "delete_profile")
        )
        self.assertIn("ephemeral=True", delete_profile)

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

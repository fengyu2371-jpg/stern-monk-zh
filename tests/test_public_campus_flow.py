import ast
import unittest
from pathlib import Path


class PublicCampusFlowTests(unittest.TestCase):
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

    def test_my_places_page_contains_direct_actions(self) -> None:
        self.assertIn("class MyPlacesHubView(UserOwnedView)", self.source)
        for label in (
            'label="新增地點"',
            'label="公開設定"',
            'label="查看全部（私密）"',
            'label="重新整理"',
        ):
            self.assertIn(label, self.source)

    def test_public_place_page_does_not_list_private_names(self) -> None:
        function = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "public_my_places_embed"
        )
        rendered = ast.unparse(function)
        self.assertIn("if bool(place.get('is_public'))", rendered)
        self.assertIn("private_count", rendered)
        self.assertNotIn("student_places_embed", rendered)

    def test_main_student_page_is_public_when_profile_exists(self) -> None:
        method = self._method("MonkMainPanelView", "student_data")
        rendered = ast.unparse(method)
        public_branch = rendered.rsplit(
            "await interaction.response.send_message",
            1,
        )[-1]
        self.assertNotIn("ephemeral=True", public_branch)

    def test_main_town_oracle_and_teaching_are_public(self) -> None:
        for method_name in ("town", "oracle", "teaching"):
            rendered = ast.unparse(
                self._method("MonkMainPanelView", method_name)
            )
            self.assertNotIn("ephemeral=True", rendered)

    def test_confession_stays_modal_private_flow(self) -> None:
        rendered = ast.unparse(
            self._method("MonkMainPanelView", "confession")
        )
        self.assertIn("send_modal", rendered)

    def test_modification_and_deletion_remain_private(self) -> None:
        for method_name in ("edit_profile", "delete_profile"):
            rendered = ast.unparse(
                self._method("StudentHubView", method_name)
            )
            self.assertIn("ephemeral=True", rendered)

    def test_owner_guard_still_protects_personal_buttons(self) -> None:
        self.assertIn(
            "這份資料屬於其他學生，不能代替操作。",
            self.source,
        )
        for class_name in (
            "StudentHubView",
            "MyPlacesHubView",
            "PlaceRegistrationOptionsView",
            "PlaceVisibilityPickerView",
            "OracleHubView",
            "OracleBookView",
        ):
            self.assertIn(
                f"class {class_name}(UserOwnedView)",
                self.source,
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

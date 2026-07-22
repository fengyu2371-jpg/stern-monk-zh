import ast
import unittest
from pathlib import Path


class StudentCommandFiveMinuteCloseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (
            Path(__file__).resolve().parents[1] / "monk_bot.py"
        ).read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_student_data_is_direct_command(self) -> None:
        self.assertIn(
            'name="學生資料"',
            self.source,
        )
        self.assertIn(
            'description="查看並管理自己的學籍、地點與神諭設定"',
            self.source,
        )
        self.assertNotIn(
            'name="建立修士面板"',
            self.source,
        )

    def test_command_sends_student_page_directly(self) -> None:
        command = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "student_data_command"
        )
        rendered = ast.unparse(command)
        self.assertIn("student_dashboard_embed", rendered)
        self.assertIn("EnrollmentSetupView", rendered)
        self.assertIn("edit_original_response", rendered)
        self.assertIn("save_player_panel", rendered)

    def test_other_players_cannot_operate(self) -> None:
        self.assertIn(
            "這是其他學生的資料面板",
            self.source,
        )
        self.assertIn(
            "不能代替對方操作",
            self.source,
        )

    def test_five_minute_timeout_removes_view(self) -> None:
        self.assertIn(
            "PLAYER_PANEL_TIMEOUT_SECONDS = 300",
            self.source,
        )
        session = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "PlayerPanelSession"
        )
        rendered = ast.unparse(session)
        self.assertIn("message.edit(view=None)", rendered)
        self.assertNotIn("LockedPlayerPanelView", rendered)

    def test_closed_panel_does_not_auto_reactivate(self) -> None:
        owned = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "UserOwnedView"
        )
        rendered = ast.unparse(owned)
        self.assertIn("操作入口已關閉", rendered)
        self.assertNotIn("activate_player_panel", rendered)

    def test_commands_are_student_data_and_status(self) -> None:
        names = []
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
                            names.append(keyword.value.value)

        self.assertEqual(
            sorted(names),
            sorted(["學生資料", "修士狀態"]),
        )


if __name__ == "__main__":
    unittest.main()

import ast
import unittest
from pathlib import Path


class BotPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.source = (cls.root / "monk_bot.py").read_text(
            encoding="utf-8"
        )
        cls.tree = ast.parse(cls.source)

    def test_teaching_code_has_no_openai_router_or_rules_context(self) -> None:
        knowledge_source = (self.root / "knowledge.py").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("MONK_AI_INSTRUCTIONS", self.source)
        self.assertNotIn("RULES_CONTEXT", self.source)
        self.assertNotIn("async def ask_openai(question", self.source)
        self.assertNotIn("ai_fallback", knowledge_source)
        self.assertNotIn("build_rules_context", knowledge_source)

    def test_openai_confession_is_only_called_by_handler(self) -> None:
        callers: list[str] = []
        for function in (
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.AsyncFunctionDef)
        ):
            if any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "ask_openai_confession"
                for node in ast.walk(function)
            ):
                callers.append(function.name)

        self.assertEqual(callers, ["_handle_confession"])

    def test_oracle_openai_is_only_called_by_handler(self) -> None:
        callers: list[str] = []
        for function in (
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.AsyncFunctionDef)
        ):
            if any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "generate_oracle"
                for node in ast.walk(function)
            ):
                callers.append(function.name)

        self.assertEqual(callers, ["_handle_current_week_oracle"])

    def test_confession_response_is_stored_for_openai_logs(self) -> None:
        confession_function = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "ask_openai_confession"
        )
        response_call = next(
            node
            for node in ast.walk(confession_function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create"
        )
        store_keyword = next(
            keyword
            for keyword in response_call.keywords
            if keyword.arg == "store"
        )

        self.assertIsInstance(store_keyword.value, ast.Constant)
        self.assertIs(store_keyword.value.value, True)

    def test_teaching_lookup_passes_no_api_callback(self) -> None:
        teaching_calls = [
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "answer_question"
        ]

        self.assertEqual(len(teaching_calls), 1)
        self.assertEqual(len(teaching_calls[0].args), 2)
        self.assertEqual(teaching_calls[0].keywords, [])

    def test_character_setting_remains_in_panel_project(self) -> None:
        self.assertIn("全院制霸", self.source)
        self.assertIn("尊重赤木學長", self.source)

    def test_gorilla_nickname_is_handled_locally(self) -> None:
        self.assertIn("gorilla_nickname_reply(question)", self.source)
        self.assertIn("gorilla_nickname_reply(content)", self.source)


class CampusFeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.source = (cls.root / "monk_bot.py").read_text(
            encoding="utf-8"
        )

    def test_student_and_campus_features_are_buttons_not_commands(self) -> None:
        for old_command_name in (
            "入學登記",
            "我的學籍",
            "地點登記",
            "學院街區",
            "本週神諭",
            "神諭冊",
            "修士告解",
            "修士教學",
            "問修士",
        ):
            self.assertNotIn(
                f'name="{old_command_name}"',
                self.source,
            )

        for class_name in (
            "EnrollmentSetupView",
            "TownHubView",
            "OracleHubView",
            "TeachingHubView",
            "ConfessionModal",
        ):
            self.assertIn(class_name, self.source)

    def test_database_is_not_church_db(self) -> None:
        config_source = (
            self.root / "config.py"
        ).read_text(encoding="utf-8")

        self.assertIn("/app/storage/monk.db", config_source)
        self.assertNotIn("church.db", config_source)


if __name__ == "__main__":
    unittest.main()

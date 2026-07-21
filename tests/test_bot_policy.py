import ast
import unittest
from pathlib import Path


class BotPolicyTests(unittest.TestCase):
    def test_teaching_code_has_no_openai_router_or_rules_context(self) -> None:
        root = Path(__file__).resolve().parents[1]
        bot_source = (root / "monk_bot.py").read_text(
            encoding="utf-8"
        )
        knowledge_source = (root / "knowledge.py").read_text(encoding="utf-8")

        self.assertNotIn("MONK_AI_INSTRUCTIONS", bot_source)
        self.assertNotIn("RULES_CONTEXT", bot_source)
        self.assertNotIn("async def ask_openai(question", bot_source)
        self.assertNotIn("answer_question(KNOWLEDGE, 問題,", bot_source)
        self.assertNotIn("ai_fallback", knowledge_source)
        self.assertNotIn("build_rules_context", knowledge_source)

    def test_openai_confession_is_only_called_by_confession_command(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "monk_bot.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        callers: list[str] = []

        for function in (
            node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)
        ):
            if any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "ask_openai_confession"
                for node in ast.walk(function)
            ):
                callers.append(function.name)

        self.assertEqual(callers, ["monk_confession"])

    def test_confession_response_is_stored_for_openai_logs(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "monk_bot.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        confession_function = next(
            node
            for node in tree.body
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
            keyword for keyword in response_call.keywords if keyword.arg == "store"
        )

        self.assertIsInstance(store_keyword.value, ast.Constant)
        self.assertIs(store_keyword.value.value, True)

    def test_teaching_lookup_passes_no_api_callback(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "monk_bot.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        teaching_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "answer_question"
        ]

        self.assertEqual(len(teaching_calls), 1)
        self.assertEqual(len(teaching_calls[0].args), 2)
        self.assertEqual(teaching_calls[0].keywords, [])


if __name__ == "__main__":
    unittest.main()

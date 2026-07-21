import unittest
from types import SimpleNamespace

from openai_support import reasoning_options, response_diagnostics


class ReasoningOptionsTests(unittest.TestCase):
    def test_uses_minimal_reasoning_for_gpt_5_nano(self) -> None:
        self.assertEqual(
            reasoning_options("gpt-5-nano"),
            {"reasoning": {"effort": "minimal"}},
        )

    def test_supports_gpt_5_nano_snapshot(self) -> None:
        self.assertEqual(
            reasoning_options("gpt-5-nano-2025-08-07"),
            {"reasoning": {"effort": "minimal"}},
        )

    def test_does_not_send_reasoning_to_other_models(self) -> None:
        self.assertEqual(reasoning_options("gpt-4.1-mini"), {})


class ResponseDiagnosticsTests(unittest.TestCase):
    def test_reports_incomplete_reason_without_output_content(self) -> None:
        response = SimpleNamespace(
            status="incomplete",
            incomplete_details=SimpleNamespace(reason="max_tokens"),
            output=[SimpleNamespace(type="reasoning")],
            usage=SimpleNamespace(
                output_tokens=800,
                output_tokens_details=SimpleNamespace(reasoning_tokens=800),
            ),
        )

        diagnostics = response_diagnostics(response)

        self.assertIn("status=incomplete", diagnostics)
        self.assertIn("incomplete_reason=max_tokens", diagnostics)
        self.assertIn("reasoning_tokens=800", diagnostics)
        self.assertNotIn("玩家告解", diagnostics)


if __name__ == "__main__":
    unittest.main()

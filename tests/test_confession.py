import unittest

from confession import (
    CONFESSION_AI_INSTRUCTIONS,
    MAX_CONFESSION_REPLY_CHARS,
    build_confession_input,
    confession_safety_identifier,
    normalize_confession_reply,
)


class ConfessionPromptTests(unittest.TestCase):
    def test_instructions_treat_confessional_as_literal_world_setting(self) -> None:
        self.assertIn("真實場景", CONFESSION_AI_INSTRUCTIONS)
        self.assertIn("禊月堂的告解室", CONFESSION_AI_INSTRUCTIONS)
        self.assertIn("不要解釋成比喻", CONFESSION_AI_INSTRUCTIONS)
        self.assertIn("不得加上", CONFESSION_AI_INSTRUCTIONS)
        self.assertIn("我把朋友的飲料喝掉了", CONFESSION_AI_INSTRUCTIONS)
        self.assertIn("我的罪是愛上你", CONFESSION_AI_INSTRUCTIONS)
        self.assertIn("隊長型修士", CONFESSION_AI_INSTRUCTIONS)
        self.assertIn("赤木剛憲", CONFESSION_AI_INSTRUCTIONS)
        self.assertIn("安西神父", CONFESSION_AI_INSTRUCTIONS)
        self.assertIn("全院制霸", CONFESSION_AI_INSTRUCTIONS)
        self.assertIn("尊重赤木學長", CONFESSION_AI_INSTRUCTIONS)
        self.assertIn("不必把自己說成犯了大錯", CONFESSION_AI_INSTRUCTIONS)

    def test_wraps_player_content_without_game_knowledge(self) -> None:
        prompt = build_confession_input(
            "我今天忘記上課",
            player_name="測試學生",
            trial_or_official="試行版告解",
            sin_result_or_none="無；本次不變更正式罪惡值",
        )

        self.assertIn("我今天忘記上課", prompt)
        self.assertIn("玩家名稱：測試學生", prompt)
        self.assertIn("目前模式：試行版告解", prompt)
        self.assertIn("正式罪惡值變化：無；本次不變更正式罪惡值", prompt)
        self.assertNotIn("正式知識庫", prompt)

    def test_escapes_dynamic_fields(self) -> None:
        prompt = build_confession_input(
            "我按了 <按鈕>",
            player_name="A&B",
            trial_or_official="試行版告解",
            sin_result_or_none="無",
        )

        self.assertIn("A&amp;B", prompt)
        self.assertIn("&lt;按鈕&gt;", prompt)

    def test_safety_identifier_is_stable_and_hides_raw_id(self) -> None:
        first = confession_safety_identifier(123456789)
        second = confession_safety_identifier(123456789)

        self.assertEqual(first, second)
        self.assertNotIn("123456789", first)


class ConfessionReplyTests(unittest.TestCase):
    def test_normalizes_whitespace(self) -> None:
        self.assertEqual(
            normalize_confession_reply("已經聽見。\n\n先處理。\n再休息。\n\n事情仍能補救。"),
            "已經聽見。\n\n先處理。 再休息。\n\n事情仍能補救。",
        )

    def test_rejects_empty_reply(self) -> None:
        with self.assertRaises(RuntimeError):
            normalize_confession_reply("   \n")

    def test_limits_reply_to_configured_character_cap(self) -> None:
        reply = normalize_confession_reply("修" * 500)

        self.assertEqual(len(reply), MAX_CONFESSION_REPLY_CHARS)
        self.assertTrue(reply.endswith("…"))


if __name__ == "__main__":
    unittest.main()

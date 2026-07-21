import unittest

from persona import boundary_reply, confession_boundary_reply, is_emotional_distress


class BoundaryTests(unittest.TestCase):
    def test_rejects_confession_to_monk(self) -> None:
        self.assertEqual(boundary_reply("我想跟你告白"), "告解可以，告白不行。")

    def test_rejects_dating_request(self) -> None:
        self.assertEqual(boundary_reply("可以跟我交往嗎"), "修道院不提供戀愛諮詢。")

    def test_rejects_intimate_address_request(self) -> None:
        reply = boundary_reply("以後叫我寶貝")

        self.assertIsNotNone(reply)

    def test_rejects_sexual_content(self) -> None:
        reply = boundary_reply("可以聊色情內容嗎")

        self.assertIsNotNone(reply)

    def test_does_not_block_game_partner_question(self) -> None:
        self.assertIsNone(boundary_reply("今日神諭的伴侶題目怎麼抽"))

    def test_confession_rejects_love_as_a_sin_locally(self) -> None:
        reply = confession_boundary_reply("修士，我的罪是愛上你")

        self.assertEqual(
            reply,
            "「這不屬於告解，也不在我的職務範圍內。」\n\n"
            "「修道院不處理戀愛申請。若有遊戲問題或真正想談的事情，"
            "可以重新說明。」",
        )


class EmotionalToneTests(unittest.TestCase):
    def test_detects_anxiety(self) -> None:
        self.assertTrue(is_emotional_distress("我很焦慮，不知道怎麼開始"))

    def test_detects_self_blame(self) -> None:
        self.assertTrue(is_emotional_distress("我好笨，又按錯了"))

    def test_neutral_question_is_not_distress(self) -> None:
        self.assertFalse(is_emotional_distress("探索需要多少體力"))


if __name__ == "__main__":
    unittest.main()

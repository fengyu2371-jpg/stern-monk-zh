import unittest

from persona import (
    GORILLA_NICKNAME_REPLY,
    boundary_reply,
    confession_boundary_reply,
    gorilla_nickname_reply,
    is_emotional_distress,
)


class BoundaryTests(unittest.TestCase):
    def test_rejects_confession_to_monk(self) -> None:
        self.assertEqual(boundary_reply("我想跟你告白"), "告解可以，告白不受理。請把真正想說的事講清楚。")

    def test_rejects_dating_request(self) -> None:
        self.assertEqual(boundary_reply("可以跟我交往嗎"), "修道院不處理交往申請。遊戲問題可以繼續問。")

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
            "「這不是告解內容，我不會接受這類邀請。」\n\n"
            "「若你有真正想整理的事情，可以重新說。我會聽。」",
        )



class NicknameBoundaryTests(unittest.TestCase):
    def test_rejects_gorilla_nickname(self) -> None:
        self.assertEqual(gorilla_nickname_reply("大猩猩修士"), GORILLA_NICKNAME_REPLY)

    def test_rejects_english_gorilla_nickname(self) -> None:
        self.assertEqual(gorilla_nickname_reply("Hey Gorilla"), GORILLA_NICKNAME_REPLY)

    def test_normal_senior_title_is_allowed(self) -> None:
        self.assertIsNone(gorilla_nickname_reply("赤木學長，請教我上課"))


class EmotionalToneTests(unittest.TestCase):
    def test_detects_anxiety(self) -> None:
        self.assertTrue(is_emotional_distress("我很焦慮，不知道怎麼開始"))

    def test_detects_self_blame(self) -> None:
        self.assertTrue(is_emotional_distress("我好笨，又按錯了"))

    def test_neutral_question_is_not_distress(self) -> None:
        self.assertFalse(is_emotional_distress("探索需要多少體力"))


if __name__ == "__main__":
    unittest.main()

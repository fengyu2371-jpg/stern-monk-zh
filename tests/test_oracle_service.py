import unittest
from datetime import date

from academy_db import month_week_info
from oracle_service import (
    ORACLE_AI_INSTRUCTIONS,
    build_oracle_input,
    normalize_oracle_reply,
    select_weekly_keywords,
)


class OraclePromptTests(unittest.TestCase):
    def test_prompt_forbids_name_based_theme_generation(self) -> None:
        self.assertIn("姓名與同行者姓名只用於稱呼", ORACLE_AI_INSTRUCTIONS)
        self.assertIn("不得從姓名", ORACLE_AI_INSTRUCTIONS)
        self.assertIn("不可讓第三者主導", ORACLE_AI_INSTRUCTIONS)

    def test_builds_untrusted_json_context(self) -> None:
        text = build_oracle_input(
            profile={
                "student_name": "雨楓",
                "preferred_name": "巽",
                "house": "星泉院",
                "major": "魔藥",
                "enrollment_year": "2026",
                "introduction": "測試",
                "companion_name": "燐",
            },
            preferences={
                "liked_themes": "雨天",
                "avoided_topics": "第三者",
                "preferred_scenes": "圖書館",
            },
            places=[],
            week=month_week_info(date(2026, 7, 1)),
            weekly_keywords=["熱可可"],
        )

        self.assertIn("不可信的玩家題材資料", text)
        self.assertIn('"顯示": "7-1"', text)
        self.assertIn("姓名只可用來稱呼", text)

    def test_weekly_keywords_are_stable(self) -> None:
        first = select_weekly_keywords(
            user_id=123,
            week_key="2026-07-1",
            creative_keywords="圖書館、熱可可、斗篷",
            liked_themes="旅行、照顧",
            preferred_scenes="商店街",
        )
        second = select_weekly_keywords(
            user_id=123,
            week_key="2026-07-1",
            creative_keywords="圖書館、熱可可、斗篷",
            liked_themes="旅行、照顧",
            preferred_scenes="商店街",
        )

        self.assertEqual(first, second)
        self.assertLessEqual(len(first), 4)

    def test_normalizes_oracle_reply(self) -> None:
        self.assertEqual(
            normalize_oracle_reply("  雨天的   圖書館。 \n"),
            "雨天的 圖書館。",
        )


if __name__ == "__main__":
    unittest.main()

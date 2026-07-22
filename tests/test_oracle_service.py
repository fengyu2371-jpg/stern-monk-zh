import unittest
from datetime import date

from academy_db import month_week_info
from oracle_service import (
    ORACLE_AI_INSTRUCTIONS,
    build_oracle_input,
    normalize_oracle_reply,
    select_weekly_keywords,
    select_weekly_places,
)


class OraclePromptTests(unittest.TestCase):
    def test_prompt_keeps_core_boundaries(self) -> None:
        self.assertIn("姓名只供稱呼", ORACLE_AI_INSTRUCTIONS)
        self.assertIn("兩人為核心", ORACLE_AI_INSTRUCTIONS)
        self.assertIn("只輸出正文", ORACLE_AI_INSTRUCTIONS)
        self.assertLess(len(ORACLE_AI_INSTRUCTIONS), 500)

    def test_builds_compact_non_json_context(self) -> None:
        text = build_oracle_input(
            profile={
                "student_name": "雨楓",
                "preferred_name": "巽",
                "house": "星泉院",
                "major": "魔藥",
                "enrollment_year": "2026",
                "introduction": "這段不應送出",
                "companion_name": "燐",
            },
            preferences={
                "liked_themes": "雨天",
                "avoided_topics": "",
                "preferred_scenes": "",
            },
            places=[],
            week=month_week_info(date(2026, 7, 1)),
            weekly_keywords=["熱可可"],
        )

        self.assertIn("學生稱呼：巽", text)
        self.assertIn("同行者：燐", text)
        self.assertIn("主修：魔藥", text)
        self.assertIn("喜歡：雨天", text)
        self.assertIn("關鍵字：熱可可", text)
        self.assertNotIn("{", text)
        self.assertNotIn('"週次"', text)
        self.assertNotIn("2026-07-1", text)
        self.assertNotIn("入學年份", text)
        self.assertNotIn("這段不應送出", text)
        self.assertNotIn("避免：", text)

    def test_location_is_limited_and_shortened(self) -> None:
        text = build_oracle_input(
            profile={
                "preferred_name": "巽",
                "companion_name": "",
                "major": "",
            },
            preferences={},
            places=[
                {
                    "name": "第一地點",
                    "place_type": "商店",
                    "district": "東街",
                    "description": "甲" * 200,
                },
                {
                    "name": "第二地點",
                    "place_type": "住處",
                    "district": "西街",
                    "description": "不應出現",
                },
            ],
            week=month_week_info(date(2026, 7, 1)),
            weekly_keywords=[],
        )

        self.assertIn("第一地點", text)
        self.assertNotIn("第二地點", text)
        self.assertNotIn("不應出現", text)
        self.assertLess(len(text), 250)

    def test_weekly_keywords_are_stable_and_limited_to_three(self) -> None:
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
        self.assertLessEqual(len(first), 3)

    def test_weekly_places_are_limited_to_one(self) -> None:
        selected = select_weekly_places(
            user_id=123,
            week_key="2026-07-1",
            places=[{"id": 1}, {"id": 2}, {"id": 3}],
        )
        self.assertEqual(len(selected), 1)

    def test_normalizes_oracle_reply(self) -> None:
        self.assertEqual(
            normalize_oracle_reply("  雨天的   圖書館。 \n"),
            "雨天的 圖書館。",
        )


if __name__ == "__main__":
    unittest.main()

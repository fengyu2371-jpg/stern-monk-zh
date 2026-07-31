from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path


MODULE_TEMP = tempfile.TemporaryDirectory(prefix="stern-monk-oracle-import-")
os.environ.setdefault("MONK_DB_PATH", str(Path(MODULE_TEMP.name) / "import.db"))
os.environ.setdefault("MONK_CHANNEL_ID", "123456789")

import main  # noqa: E402


class OracleOtherShopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="stern-monk-oracle-")
        self.db = main.AcademyDatabase(Path(self.temp_dir.name) / "academy.db")
        self.db.initialize()
        for user_id, name in ((1001, "訪客"), (2002, "店家學生")):
            self.db.save_profile(
                user_id=user_id,
                student_name=name,
                preferred_name=name,
                house="星泉",
                major="魔法史",
                enrollment_year="2026",
                introduction="",
                companion_name="同行者",
            )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def create_place(
        self,
        *,
        user_id: int,
        name: str,
        place_type: str = "商店",
        is_public: bool,
    ) -> None:
        self.db.create_place(
            user_id=user_id,
            name=name,
            place_type=place_type,
            district="中央廣場",
            description="測試用地點",
            operator_name="店主角色",
            source_kind="新登記",
            status="使用中",
            allow_oracle=True,
            is_public=is_public,
        )

    def test_oracle_pool_keeps_own_private_and_only_other_public_places(self) -> None:
        self.create_place(user_id=1001, name="自己的私人店", is_public=False)
        self.create_place(user_id=2002, name="別人的公開店", is_public=True)
        self.create_place(user_id=2002, name="別人的私人店", is_public=False)

        places = self.db.list_oracle_places(1001)
        names = [place["name"] for place in places]

        self.assertEqual(names, ["自己的私人店", "別人的公開店"])
        self.assertEqual(places[1]["owner_name"], "店家學生")

    def test_other_shop_button_excludes_visitor_and_non_shop_places(self) -> None:
        self.create_place(user_id=1001, name="自己的公開店", is_public=True)
        self.create_place(user_id=2002, name="別人的公開店", is_public=True)
        self.create_place(
            user_id=2002,
            name="別人的公開住處",
            place_type="住處",
            is_public=True,
        )

        original_db = main.ACADEMY_DB
        main.ACADEMY_DB = self.db
        try:
            places = main.list_other_public_shop_places(1001)
        finally:
            main.ACADEMY_DB = original_db

        self.assertEqual([place["name"] for place in places], ["別人的公開店"])

    def test_required_shop_prompt_preserves_owner_and_operator(self) -> None:
        week = main.month_week_info()
        prompt = main.build_oracle_input(
            profile={
                "user_id": "1001",
                "preferred_name": "訪客",
                "companion_name": "同行者",
                "major": "魔法史",
            },
            preferences={
                "liked_themes": "日常",
                "avoided_topics": "",
            },
            places=[
                {
                    "user_id": "2002",
                    "name": "月光書店",
                    "place_type": "書店",
                    "district": "舊城區",
                    "description": "深夜仍亮著燈的舊書店",
                    "owner_name": "店家學生",
                    "operator_name": "書店老闆",
                }
            ],
            week=week,
            weekly_keywords=["雨夜"],
            required_place=True,
        )

        self.assertIn("指定地點（必須使用）：月光書店", prompt)
        self.assertIn("其他學生「店家學生」的公開地點", prompt)
        self.assertIn("店主／經營者：書店老闆", prompt)

    def test_other_shop_button_is_available_in_hub_and_book(self) -> None:
        hub_labels = {
            str(getattr(child, "label", "") or "")
            for child in main.OracleHubView(1001).children
        }
        page = {
            "id": 1,
            "week_key": "2026-07-5",
            "week_label": "7-5",
            "period_start": "2026-07-29",
            "period_end": "2026-07-31",
            "oracle_text": "測試神諭",
            "used_keywords": "",
            "used_place_names": "",
            "status": "未完成",
            "completed_at": None,
        }
        book_labels = {
            str(getattr(child, "label", "") or "")
            for child in main.OracleBookView(1001, [page]).children
        }

        self.assertIn("去其他店看看", hub_labels)
        self.assertIn("去其他店看看", book_labels)


if __name__ == "__main__":
    unittest.main()

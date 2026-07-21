import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from knowledge import (
    NO_OFFICIAL_DATA,
    KnowledgeBase,
    KnowledgeLoadError,
    answer_question,
)


def tutorial(tutorial_id: str, title: str, keywords: list[str]) -> dict:
    return {
        "id": tutorial_id,
        "title": title,
        "keywords": keywords,
        "summary": f"{title}摘要",
        "details": [f"{title}正式規則"],
        "related_commands": ["/修士教學"],
        "warnings": [f"{title}注意事項"],
        "source_files": ["main.py"],
        "needs_review": False,
        "monk_openings": [f"{title}開場一", f"{title}開場二"],
        "monk_endings": [f"{title}判詞一", f"{title}判詞二"],
    }


class KnowledgeLoadingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.tutorials_path = self.root / "tutorials_zh_tw.json"
        self.faq_path = self.root / "faq_zh_tw.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_data(self, tutorials: list[dict], faqs: list[dict]) -> None:
        self.tutorials_path.write_text(
            json.dumps(tutorials, ensure_ascii=False), encoding="utf-8"
        )
        self.faq_path.write_text(
            json.dumps(faqs, ensure_ascii=False), encoding="utf-8"
        )

    def test_loads_valid_json_arrays(self) -> None:
        item = tutorial("classes", "上課", ["上課"])
        faq = {
            "question_patterns": ["一天能上幾次課"],
            "answer": "固定 FAQ 答案",
            "related_tutorial_id": "classes",
            "source_files": ["main.py"],
            "needs_review": False,
        }
        self.write_data([item], [faq])

        knowledge = KnowledgeBase.from_files(self.tutorials_path, self.faq_path)

        self.assertEqual(knowledge.tutorial_by_id["classes"]["title"], "上課")
        self.assertEqual(len(knowledge.faqs), 1)

    def test_rejects_invalid_json(self) -> None:
        self.tutorials_path.write_text("[{", encoding="utf-8")
        self.faq_path.write_text("[]", encoding="utf-8")

        with self.assertRaises(KnowledgeLoadError):
            KnowledgeBase.from_files(self.tutorials_path, self.faq_path)

    def test_rejects_faq_with_unknown_tutorial_id(self) -> None:
        item = tutorial("classes", "上課", ["上課"])
        faq = {
            "question_patterns": ["一天能上幾次課"],
            "answer": "固定 FAQ 答案",
            "related_tutorial_id": "missing",
            "source_files": ["main.py"],
            "needs_review": False,
        }
        self.write_data([item], [faq])

        with self.assertRaises(KnowledgeLoadError):
            KnowledgeBase.from_files(self.tutorials_path, self.faq_path)

    def test_project_knowledge_files_load(self) -> None:
        project_root = Path(__file__).resolve().parents[1]

        knowledge = KnowledgeBase.from_files(
            project_root / "data" / "tutorials_zh_tw.json",
            project_root / "data" / "faq_zh_tw.json",
        )

        self.assertGreaterEqual(len(knowledge.tutorials), 17)
        self.assertGreaterEqual(len(knowledge.faqs), 80)


class KnowledgeMatchingTests(unittest.TestCase):
    def setUp(self) -> None:
        tutorials = [
            tutorial("classes", "上課", ["上課", "課程"]),
            tutorial("wand", "魔杖取得", ["魔杖"]),
            tutorial("wand_upgrade", "魔杖強化", ["強化魔杖", "裂痕"]),
        ]
        faqs = [
            {
                "question_patterns": ["上課規則是什麼", "一天能上幾次課"],
                "answer": "固定 FAQ：上課答案",
                "related_tutorial_id": "classes",
                "source_files": ["main.py"],
                "needs_review": False,
            }
        ]
        self.knowledge = KnowledgeBase(tutorials, faqs)

    def test_faq_has_priority_over_tutorial_keyword(self) -> None:
        match = self.knowledge.find_local("請問上課規則是什麼？")

        self.assertIsNotNone(match)
        self.assertEqual(match.kind, "faq")
        self.assertEqual(match.record["answer"], "固定 FAQ：上課答案")

    def test_tutorial_keyword_is_used_after_faq_miss(self) -> None:
        match = self.knowledge.find_local("我的魔杖出現裂痕怎麼辦")

        self.assertIsNotNone(match)
        self.assertEqual(match.kind, "tutorial")
        self.assertEqual(match.tutorial["id"], "wand_upgrade")

    def test_unknown_question_has_no_local_match(self) -> None:
        self.assertIsNone(self.knowledge.find_local("今晚月亮是什麼顏色"))

    def test_local_answer_uses_fixed_knowledge(self) -> None:
        result = asyncio.run(answer_question(self.knowledge, "一天能上幾次課"))

        self.assertEqual(result.source, "faq")
        self.assertEqual(result.text, "固定 FAQ：上課答案")

    def test_unknown_question_uses_required_message(self) -> None:
        result = asyncio.run(
            answer_question(self.knowledge, "今晚月亮是什麼顏色")
        )

        self.assertEqual(result.source, "none")
        self.assertEqual(result.text, NO_OFFICIAL_DATA)


if __name__ == "__main__":
    unittest.main()

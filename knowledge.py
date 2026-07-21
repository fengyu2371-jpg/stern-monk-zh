from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


NO_OFFICIAL_DATA = "目前沒有正式資料"

TUTORIAL_FIELDS = {
    "id",
    "title",
    "keywords",
    "summary",
    "details",
    "related_commands",
    "warnings",
    "source_files",
    "needs_review",
    "monk_openings",
    "monk_endings",
}
FAQ_FIELDS = {
    "question_patterns",
    "answer",
    "related_tutorial_id",
    "source_files",
    "needs_review",
}


class KnowledgeLoadError(RuntimeError):
    """本地知識庫無法安全載入。"""


@dataclass(frozen=True)
class KnowledgeMatch:
    kind: Literal["faq", "tutorial"]
    record: dict[str, Any]
    tutorial: dict[str, Any]
    score: int


@dataclass(frozen=True)
class AnswerResult:
    text: str
    source: Literal["faq", "tutorial", "none"]
    match: KnowledgeMatch | None = None


def _read_json_list(path: Path, label: str) -> list[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except FileNotFoundError as exc:
        raise KnowledgeLoadError(f"找不到{label}：{path}") from exc
    except json.JSONDecodeError as exc:
        raise KnowledgeLoadError(
            f"{label} JSON 格式錯誤：{path}，第 {exc.lineno} 行"
        ) from exc

    if not isinstance(data, list):
        raise KnowledgeLoadError(f"{label}最外層必須是陣列：{path}")
    if not all(isinstance(item, dict) for item in data):
        raise KnowledgeLoadError(f"{label}每一筆資料都必須是物件：{path}")
    return data


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: Any, *, allow_empty: bool = True) -> bool:
    return (
        isinstance(value, list)
        and (allow_empty or bool(value))
        and all(_non_empty_string(item) for item in value)
    )


def _validate_tutorials(tutorials: list[dict[str, Any]]) -> None:
    seen_ids: set[str] = set()
    for index, item in enumerate(tutorials):
        missing = TUTORIAL_FIELDS - item.keys()
        if missing:
            raise KnowledgeLoadError(
                f"教學第 {index + 1} 筆缺少欄位：{', '.join(sorted(missing))}"
            )

        tutorial_id = item["id"]
        if not _non_empty_string(tutorial_id):
            raise KnowledgeLoadError(f"教學第 {index + 1} 筆的 id 不可空白")
        if tutorial_id in seen_ids:
            raise KnowledgeLoadError(f"教學 id 重複：{tutorial_id}")
        seen_ids.add(tutorial_id)

        for field in ("title", "summary"):
            if not _non_empty_string(item[field]):
                raise KnowledgeLoadError(f"教學 {tutorial_id} 的 {field} 不可空白")
        for field in (
            "keywords",
            "details",
            "related_commands",
            "warnings",
            "source_files",
        ):
            if not _string_list(item[field]):
                raise KnowledgeLoadError(f"教學 {tutorial_id} 的 {field} 必須是字串陣列")
        for field in ("monk_openings", "monk_endings"):
            if not _string_list(item[field], allow_empty=False):
                raise KnowledgeLoadError(f"教學 {tutorial_id} 的 {field} 不可為空")
        if not isinstance(item["needs_review"], bool):
            raise KnowledgeLoadError(f"教學 {tutorial_id} 的 needs_review 必須是布林值")


def _validate_faqs(
    faqs: list[dict[str, Any]], tutorial_ids: set[str]
) -> None:
    for index, item in enumerate(faqs):
        missing = FAQ_FIELDS - item.keys()
        if missing:
            raise KnowledgeLoadError(
                f"FAQ 第 {index + 1} 筆缺少欄位：{', '.join(sorted(missing))}"
            )
        if not _string_list(item["question_patterns"], allow_empty=False):
            raise KnowledgeLoadError(f"FAQ 第 {index + 1} 筆的 question_patterns 不可為空")
        if not _non_empty_string(item["answer"]):
            raise KnowledgeLoadError(f"FAQ 第 {index + 1} 筆的 answer 不可空白")
        related_id = item["related_tutorial_id"]
        if related_id not in tutorial_ids:
            raise KnowledgeLoadError(
                f"FAQ 第 {index + 1} 筆引用不存在的教學：{related_id}"
            )
        if not _string_list(item["source_files"]):
            raise KnowledgeLoadError(f"FAQ 第 {index + 1} 筆的 source_files 必須是字串陣列")
        if not isinstance(item["needs_review"], bool):
            raise KnowledgeLoadError(f"FAQ 第 {index + 1} 筆的 needs_review 必須是布林值")


_NORMALIZE_PATTERN = re.compile(r"[\s？?！!，,。.、：:；;「」『』（）()【】\[\]`*_]+")


def normalize_text(text: str) -> str:
    return _NORMALIZE_PATTERN.sub("", text.strip().casefold())


def _candidate_score(question: str, candidate: str) -> int:
    normalized_candidate = normalize_text(candidate)
    if not normalized_candidate:
        return 0
    if question == normalized_candidate:
        return 100_000 + len(normalized_candidate)
    if normalized_candidate in question:
        return 10_000 + len(normalized_candidate)
    if len(question) >= 2 and question in normalized_candidate:
        return 1_000 + len(question)
    return 0


class KnowledgeBase:
    def __init__(
        self,
        tutorials: list[dict[str, Any]],
        faqs: list[dict[str, Any]],
    ) -> None:
        _validate_tutorials(tutorials)
        tutorial_ids = {str(item["id"]) for item in tutorials}
        _validate_faqs(faqs, tutorial_ids)

        self.tutorials = tutorials
        self.faqs = faqs
        self.tutorial_by_id = {str(item["id"]): item for item in tutorials}

    @classmethod
    def from_files(cls, tutorials_path: Path, faq_path: Path) -> "KnowledgeBase":
        tutorials = _read_json_list(tutorials_path, "教學知識庫")
        faqs = _read_json_list(faq_path, "FAQ 知識庫")
        return cls(tutorials, faqs)

    def find_faq(self, question: str) -> KnowledgeMatch | None:
        normalized_question = normalize_text(question)
        best: KnowledgeMatch | None = None

        for faq in self.faqs:
            score = max(
                (_candidate_score(normalized_question, pattern) for pattern in faq["question_patterns"]),
                default=0,
            )
            if score <= 0:
                continue
            tutorial = self.tutorial_by_id[str(faq["related_tutorial_id"])]
            match = KnowledgeMatch("faq", faq, tutorial, score)
            if best is None or match.score > best.score:
                best = match
        return best

    def find_tutorial(self, question: str) -> KnowledgeMatch | None:
        normalized_question = normalize_text(question)
        best: KnowledgeMatch | None = None
        best_rank = (0, 0, 0, 0)

        for tutorial in self.tutorials:
            candidates = [tutorial["title"], *tutorial["keywords"]]
            score = max(
                (_candidate_score(normalized_question, candidate) for candidate in candidates),
                default=0,
            )
            if score <= 0:
                continue
            match = KnowledgeMatch("tutorial", tutorial, tutorial, score)
            # 同分時，具有較長明確關鍵字的主題優先於泛用短詞主題。
            # 例如「魔杖出現裂痕」應落到強化教學，而不是魔杖取得。
            normalized_title = normalize_text(tutorial["title"])
            matched_specific_keywords = sum(
                1
                for keyword in tutorial["keywords"]
                if normalize_text(keyword) in normalized_question
                and normalize_text(keyword) not in normalized_title
            )
            specificity = max(len(normalize_text(item)) for item in candidates)
            rank = (
                score,
                matched_specific_keywords,
                specificity,
                len(normalized_title),
            )
            if best is None or rank > best_rank:
                best = match
                best_rank = rank
        return best

    def find_local(self, question: str) -> KnowledgeMatch | None:
        # 固定 FAQ 明確優先；FAQ 完全無命中才查教學關鍵字。
        return self.find_faq(question) or self.find_tutorial(question)

def render_knowledge_answer(match: KnowledgeMatch, *, concise: bool = False) -> str:
    if match.kind == "faq":
        return str(match.record["answer"]).strip()

    tutorial = match.tutorial
    parts = [str(tutorial["summary"]).strip()]
    details = tutorial["details"][:1] if concise else tutorial["details"]
    parts.extend(f"• {line}" for line in details)
    if tutorial["warnings"] and not concise:
        parts.append("注意事項：")
        parts.extend(f"• {line}" for line in tutorial["warnings"])
    return "\n".join(parts)


async def answer_question(
    knowledge: KnowledgeBase,
    question: str,
) -> AnswerResult:
    match = knowledge.find_local(question)
    if match is not None:
        return AnswerResult(render_knowledge_answer(match), match.kind, match)

    return AnswerResult(NO_OFFICIAL_DATA, "none")

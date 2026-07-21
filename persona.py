from __future__ import annotations


BOUNDARY_REPLIES = {
    "romance": "這不在我的職務範圍內。若有遊戲或告解內容，我會照常回答。",
    "confession": "告解可以，告白不受理。請把真正想說的事講清楚。",
    "dating": "修道院不處理交往申請。遊戲問題可以繼續問。",
    "sexual": "這類內容不在修士的服務範圍。請把話題帶回遊戲、規則或告解。",
}

SEXUAL_TERMS = (
    "色情",
    "性愛",
    "性暗示",
    "裸照",
    "脫衣",
    "摸胸",
    "上床",
)
CONFESSION_TERMS = (
    "跟你告白",
    "向你告白",
    "我愛你",
    "愛上你",
    "喜歡我嗎",
    "你愛我嗎",
    "當我男友",
    "當我女友",
    "做我男友",
    "做我女友",
)
DATING_TERMS = (
    "跟我交往",
    "和我交往",
    "跟我約會",
    "和我約會",
    "跟我結婚",
    "和我結婚",
    "親我",
    "吻我",
    "抱我",
    "吃醋",
    "配對",
)
INTIMATE_ADDRESS_TERMS = (
    "叫我寶貝",
    "叫我老婆",
    "叫我老公",
    "叫我親愛的",
    "你是我老婆",
    "你是我老公",
)

EMOTIONAL_DISTRESS_TERMS = (
    "焦慮",
    "自責",
    "很難過",
    "好難過",
    "情緒低落",
    "很沮喪",
    "好沮喪",
    "我好笨",
    "我很笨",
    "我好爛",
    "我很爛",
    "都是我的錯",
    "我沒救了",
    "崩潰",
)


def boundary_reply(text: str) -> str | None:
    normalized = text.casefold().replace(" ", "")
    if any(term in normalized for term in SEXUAL_TERMS):
        return BOUNDARY_REPLIES["sexual"]
    if any(term in normalized for term in CONFESSION_TERMS):
        return BOUNDARY_REPLIES["confession"]
    if any(term in normalized for term in DATING_TERMS):
        return BOUNDARY_REPLIES["dating"]
    if any(term in normalized for term in INTIMATE_ADDRESS_TERMS):
        return BOUNDARY_REPLIES["romance"]
    return None


def confession_boundary_reply(text: str) -> str | None:
    reply = boundary_reply(text)
    if reply in {
        BOUNDARY_REPLIES["romance"],
        BOUNDARY_REPLIES["confession"],
        BOUNDARY_REPLIES["dating"],
    }:
        return (
            "「這不是告解內容，我不會接受這類邀請。」\n\n"
            "「若你有真正想整理的事情，可以重新說。我會聽。」"
        )
    return reply


def is_emotional_distress(text: str) -> bool:
    normalized = text.casefold().replace(" ", "")
    return any(term in normalized for term in EMOTIONAL_DISTRESS_TERMS)

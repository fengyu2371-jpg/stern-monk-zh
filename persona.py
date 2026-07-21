from __future__ import annotations

import re


BOUNDARY_REPLIES = {
    "romance": "告白與親密互動不受理。請把內容帶回遊戲、規則或真正的告解。",
    "confession": "告白不受理。若要告解，請直接說明真正需要整理的事情。",
    "dating": "交往、約會與結婚申請不受理。遊戲問題可以繼續問。",
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

# 這些詞一旦出現，直接在本地攔截，不送入 OpenAI API。
DIRECT_ROMANCE_TERMS = (
    "我愛你",
    "愛上你",
    "愛你",
    "我喜歡你",
    "好喜歡你",
    "很喜歡你",
    "最喜歡你",
    "暗戀你",
    "喜歡修士",
    "喜歡赤木學長",
    "喜歡赤木修士",
    "愛修士",
    "愛赤木學長",
    "愛赤木修士",
    "我的罪是喜歡你",
    "我的罪是愛上你",
    "想跟你在一起",
    "想和你在一起",
    "想當你的戀人",
    "想當你男友",
    "想當你女友",
    "想當你老婆",
    "想當你老公",
    "嫁給我",
    "娶我",
)

CONFESSION_TERMS = (
    "跟你告白",
    "向你告白",
    "對你告白",
    "告白修士",
    "跟修士告白",
    "向修士告白",
    "喜歡我嗎",
    "你喜歡我嗎",
    "你愛我嗎",
    "你會愛我嗎",
    "當我男友",
    "當我女友",
    "當我男朋友",
    "當我女朋友",
    "當我老公",
    "當我老婆",
    "做我男友",
    "做我女友",
    "做我男朋友",
    "做我女朋友",
)

DATING_TERMS = (
    "跟我交往",
    "和我交往",
    "跟你交往",
    "和你交往",
    "跟我約會",
    "和我約會",
    "跟你約會",
    "和你約會",
    "跟我結婚",
    "和我結婚",
    "跟你結婚",
    "和你結婚",
    "親我",
    "吻我",
    "抱我",
    "想親你",
    "想吻你",
    "想抱你",
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
    "你是我的戀人",
)

# 避免把「我喜歡你的教學」這類非戀愛稱讚誤判成告白。
NON_ROMANTIC_PRAISE_TERMS = (
    "喜歡你的教學",
    "喜歡你的回答",
    "喜歡你的說明",
    "喜歡你的風格",
    "喜歡你的設定",
    "喜歡你的功能",
    "喜歡你這個角色設定",
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

GORILLA_NICKNAME_TERMS = (
    "大猩猩",
    "猩猩學長",
    "猩猩修士",
    "gorilla",
)

GORILLA_NICKNAME_REPLY = (
    "尊重赤木學長，請不要喊他「大猩猩」。"
    "若有教學、規則或告解內容，請直接說明。"
)


def _normalize_boundary_text(text: str) -> str:
    normalized = text.casefold()
    return re.sub(r"[\s，。！？!?、：:；;「」『』（）()【】\[\]…~～._-]+", "", normalized)


def boundary_reply(text: str) -> str | None:
    normalized = _normalize_boundary_text(text)

    if any(term in normalized for term in SEXUAL_TERMS):
        return BOUNDARY_REPLIES["sexual"]

    if any(term in normalized for term in NON_ROMANTIC_PRAISE_TERMS):
        return None

    if any(term in normalized for term in DIRECT_ROMANCE_TERMS):
        return BOUNDARY_REPLIES["confession"]

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
        return "「這不是告解內容。告白與親密邀請一律不受理。」"
    return reply


def is_emotional_distress(text: str) -> bool:
    normalized = _normalize_boundary_text(text)
    return any(term in normalized for term in EMOTIONAL_DISTRESS_TERMS)


def gorilla_nickname_reply(text: str) -> str | None:
    normalized = _normalize_boundary_text(text)
    if any(term in normalized for term in GORILLA_NICKNAME_TERMS):
        return GORILLA_NICKNAME_REPLY
    return None

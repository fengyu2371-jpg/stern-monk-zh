from __future__ import annotations

import json
import logging
import random
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from openai import AsyncOpenAI

from academy_db import AcademyDatabase, month_week_info
from confession import (
    CONFESSION_AI_INSTRUCTIONS,
    build_confession_input,
    confession_safety_identifier,
    normalize_confession_reply,
)
from config import Settings, is_allowed_channel
from knowledge import (
    NO_OFFICIAL_DATA,
    KnowledgeBase,
    KnowledgeMatch,
    answer_question,
    render_knowledge_answer,
)
from openai_support import reasoning_options, response_diagnostics
from oracle_service import (
    generate_oracle,
    select_weekly_keywords,
    select_weekly_places,
)
from persona import (
    boundary_reply,
    confession_boundary_reply,
    gorilla_nickname_reply,
    is_emotional_distress,
)


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SETTINGS = Settings.from_env()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("stern-monk")


MONK_INTRODUCTION = (
    "赤木修士，全名赤木剛憲。高校時期，他曾是籃球隊隊長，目標是稱霸全國；"
    "後來沒有走上籃球職涯，反而進入魔法大學，將那份隊長精神帶進學分競賽。\n\n"
    "在禊月堂，他受到安西神父感召：團隊不是讓一個人包辦所有得分，而是讓每個人的特性成為勝利的齒輪。"
    "於是他成為修士，也成為所有魔法大學生的萬年學長。\n\n"
    "如今，他引導後輩繼續追求『全院制霸』：不替人逃避問題，也不在學生失敗時把人丟下。\n\n"
    "**學院提醒：尊重赤木學長，請不要喊他「大猩猩」。**"
)


def load_json(filename: str) -> dict[str, Any]:
    path = DATA_DIR / filename

    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except FileNotFoundError as exc:
        raise RuntimeError(f"找不到資料檔案：{path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"JSON 格式錯誤：{path}，第 {exc.lineno} 行") from exc

    if not isinstance(data, dict):
        raise RuntimeError(f"資料檔案最外層必須是物件：{path}")

    return data


DIALOGUE = load_json("dialogue.json")
KNOWLEDGE = KnowledgeBase.from_files(
    DATA_DIR / "tutorials_zh_tw.json",
    DATA_DIR / "faq_zh_tw.json",
)
ACADEMY_DB = AcademyDatabase(SETTINGS.monk_db_path)

openai_client: AsyncOpenAI | None = None
if SETTINGS.confession_ai_available or SETTINGS.oracle_ai_available:
    openai_client = AsyncOpenAI(api_key=SETTINGS.openai_api_key)
elif SETTINGS.ai_enabled:
    if not SETTINGS.openai_api_key:
        logger.warning("AI_ENABLED=true，但沒有設定 OPENAI_API_KEY；AI 功能將停用。")
    else:
        logger.info("AI 總開關已啟用，但告解 AI 目前為關閉。")


# 第一版採記憶體計數，Railway 重啟或重新部署後會歸零。
_ai_usage: dict[str, dict[int, int]] = defaultdict(dict)


def monk_embed(
    title: str,
    description: str,
    *,
    color: int = 0x2B2D31,
) -> discord.Embed:
    return discord.Embed(
        title=title,
        description=description,
        color=color,
    )


def knowledge_source_label(match: KnowledgeMatch) -> str:
    source_type = "固定 FAQ" if match.kind == "faq" else "固定教學"
    return f"知識庫來源：{source_type}｜{match.tutorial['title']}"


def roleplay_lines(match: KnowledgeMatch) -> tuple[str, str]:
    tutorial = match.tutorial
    return (
        random.choice(tutorial["monk_openings"]),
        random.choice(tutorial["monk_endings"]),
    )


def render_local_reply(
    match: KnowledgeMatch,
    *,
    concise: bool = False,
    gentle: bool = False,
) -> str:
    answer = render_knowledge_answer(match, concise=concise)
    source = f"_{knowledge_source_label(match)}_"
    if gentle:
        return f"{answer}\n\n先照正確做法處理；若畫面仍不同，保留截圖詢問管理員。\n\n{source}"

    opening, ending = roleplay_lines(match)
    return f"{opening}\n\n{answer}\n\n{ending}\n\n{source}"


def random_line(category: str, fallback: str) -> str:
    lines = DIALOGUE.get(category, [])
    if not isinstance(lines, list):
        return fallback

    valid_lines = [line for line in lines if isinstance(line, str) and line.strip()]
    return random.choice(valid_lines) if valid_lines else fallback


def get_today_usage(user_id: int) -> int:
    today_key = date.today().isoformat()
    return _ai_usage[today_key].get(user_id, 0)


def increment_today_usage(user_id: int) -> int:
    today_key = date.today().isoformat()

    # 清掉舊日期，避免記憶體一直累積。
    for old_key in list(_ai_usage.keys()):
        if old_key != today_key:
            del _ai_usage[old_key]

    _ai_usage[today_key][user_id] = _ai_usage[today_key].get(user_id, 0) + 1
    return _ai_usage[today_key][user_id]


async def ask_openai_confession(
    content: str,
    user_id: int,
    player_name: str,
) -> str:
    if openai_client is None or not SETTINGS.confession_ai_available:
        raise RuntimeError("OpenAI 告解尚未啟用。")

    response = await openai_client.responses.create(
        model=SETTINGS.openai_model,
        instructions=CONFESSION_AI_INSTRUCTIONS,
        input=build_confession_input(
            content,
            player_name=player_name,
            trial_or_official="試行版告解",
            sin_result_or_none="無；本次不變更正式罪惡值",
        ),
        max_output_tokens=SETTINGS.ai_max_output_tokens,
        store=True,
        safety_identifier=confession_safety_identifier(user_id),
        **reasoning_options(SETTINGS.openai_model),
    )

    output_text = response.output_text or ""
    if not output_text.strip():
        logger.warning("OpenAI 告解空輸出：%s", response_diagnostics(response))
    return normalize_confession_reply(output_text)


class WrongMonkChannel(app_commands.CheckFailure):
    pass


class MonkCommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not is_allowed_channel(interaction.channel_id, SETTINGS.monk_channel_id):
            raise WrongMonkChannel()
        return True


class MonkClient(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = MonkCommandTree(self)

    async def setup_hook(self) -> None:
        ACADEMY_DB.initialize()
        logger.info("修士學籍資料庫已初始化：%s", SETTINGS.monk_db_path)

        # 重新註冊固定面板，讓 Railway 重啟後舊訊息上的按鈕仍可使用。
        self.add_view(StudentDataPanelView())
        logger.info("學生資料中心 Persistent View 已註冊。")
        if SETTINGS.guild_id is not None:
            guild = discord.Object(id=SETTINGS.guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info(
                "已同步 %s 個指令到伺服器 %s。", len(synced), SETTINGS.guild_id
            )
        else:
            synced = await self.tree.sync()
            logger.info("未設定 GUILD_ID，已同步 %s 個全域指令。", len(synced))

    async def on_ready(self) -> None:
        if self.user is None:
            return

        await self.change_presence(
            activity=discord.Game(name="帶後輩挑戰全學院制霸"),
        )
        logger.info("修士已上線：%s（%s）", self.user, self.user.id)
        logger.info(
            "AI 教學：永久停用｜AI 告解：%s｜AI 神諭：%s｜模型：%s｜每日每人上限：%s",
            "啟用" if SETTINGS.confession_ai_available else "停用",
            "啟用" if SETTINGS.oracle_ai_available else "停用",
            SETTINGS.openai_model,
            SETTINGS.ai_daily_limit,
        )
        logger.info("修士允許回覆頻道：%s", SETTINGS.monk_channel_id)


client = MonkClient()
tree = client.tree




HOUSE_CHOICES = [
    app_commands.Choice(name="棘鹿院", value="棘鹿院"),
    app_commands.Choice(name="星泉院", value="星泉院"),
    app_commands.Choice(name="灰狼院", value="灰狼院"),
    app_commands.Choice(name="燭羽院", value="燭羽院"),
    app_commands.Choice(name="尚未分院", value="尚未分院"),
]

PLACE_TYPE_CHOICES = [
    app_commands.Choice(name="商店", value="商店"),
    app_commands.Choice(name="校外住處", value="校外住處"),
    app_commands.Choice(name="工作室", value="工作室"),
    app_commands.Choice(name="餐館", value="餐館"),
    app_commands.Choice(name="書店", value="書店"),
    app_commands.Choice(name="魔藥工房", value="魔藥工房"),
    app_commands.Choice(name="診所", value="診所"),
    app_commands.Choice(name="社團據點", value="社團據點"),
    app_commands.Choice(name="其他", value="其他"),
]

PLACE_SOURCE_CHOICES = [
    app_commands.Choice(name="新登記", value="新登記"),
    app_commands.Choice(name="舊企劃遷入", value="舊企劃遷入"),
]


def _yes_no(value: str, default: bool = True) -> bool:
    normalized = value.strip().casefold()
    if normalized in {"否", "不", "false", "no", "0", "不要"}:
        return False
    if normalized in {"是", "true", "yes", "1", "要", "允許"}:
        return True
    return default


def student_profile_embed(profile: dict[str, Any]) -> discord.Embed:
    prefs = profile.get("preferences", {})
    embed = monk_embed(
        "🎓 禊月堂魔法大學｜學生學籍",
        f"**學生姓名**：{profile.get('student_name') or '未填寫'}\n"
        f"**希望稱呼**：{profile.get('preferred_name') or '未填寫'}\n"
        f"**所屬學院**：{profile.get('house') or '尚未分院'}\n"
        f"**主修方向**：{profile.get('major') or '未填寫'}\n"
        f"**入學年份**：{profile.get('enrollment_year') or '未填寫'}\n"
        f"**固定同行者**：{profile.get('companion_name') or '未設定'}\n\n"
        f"**個人簡介**\n{profile.get('introduction') or '未填寫'}",
        color=0x5865F2,
    )
    embed.add_field(
        name="神諭偏好",
        value=(
            f"喜歡：{prefs.get('liked_themes') or '未設定'}\n"
            f"避免：{prefs.get('avoided_topics') or '未設定'}\n"
            f"關鍵字：{prefs.get('creative_keywords') or '未設定'}\n"
            f"偏好場景：{prefs.get('preferred_scenes') or '未設定'}\n"
            f"允許使用個人地點：{'是' if prefs.get('allow_place_context', 1) else '否'}"
        ),
        inline=False,
    )
    return embed


class UserOwnedView(discord.ui.View):
    def __init__(self, owner_id: int, *, timeout: float | None = 900) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "這份資料屬於其他學生，不能代替操作。",
                ephemeral=True,
            )
            return False
        return True


class OraclePreferencesModal(discord.ui.Modal, title="神諭偏好設定"):
    liked_themes = discord.ui.TextInput(
        label="喜歡的題材與氣氛",
        placeholder="雨天、旅行、照顧、魔法學院日常",
        required=False,
        max_length=300,
    )
    avoided_topics = discord.ui.TextInput(
        label="希望避免的題材",
        placeholder="第三者、血腥、分離、爭吵",
        required=False,
        max_length=300,
    )
    creative_keywords = discord.ui.TextInput(
        label="可使用的創作關鍵字",
        placeholder="圖書館、斗篷、熱可可、月光",
        required=False,
        max_length=400,
    )
    preferred_scenes = discord.ui.TextInput(
        label="偏好場景",
        placeholder="商店街、校外住處、旅行、季節活動",
        required=False,
        max_length=300,
    )
    allow_places = discord.ui.TextInput(
        label="允許神諭使用你登記的地點？",
        placeholder="填「是」或「否」",
        default="是",
        required=True,
        max_length=10,
    )

    def __init__(
        self,
        user_id: int,
        existing: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self.user_id = int(user_id)
        existing = existing or {}
        self.liked_themes.default = existing.get("liked_themes", "")
        self.avoided_topics.default = existing.get("avoided_topics", "")
        self.creative_keywords.default = existing.get("creative_keywords", "")
        self.preferred_scenes.default = existing.get("preferred_scenes", "")
        self.allow_places.default = (
            "是" if existing.get("allow_place_context", 1) else "否"
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if ACADEMY_DB.get_profile(self.user_id) is None:
            await interaction.response.send_message(
                "請先使用 `/入學登記` 建立學籍。",
                ephemeral=True,
            )
            return

        ACADEMY_DB.save_preferences(
            user_id=self.user_id,
            liked_themes=str(self.liked_themes.value),
            avoided_topics=str(self.avoided_topics.value),
            creative_keywords=str(self.creative_keywords.value),
            preferred_scenes=str(self.preferred_scenes.value),
            allow_place_context=_yes_no(str(self.allow_places.value)),
        )
        await interaction.response.send_message(
            "神諭偏好已保存。姓名只會用於稱呼，不會被拿來推測神諭主題。",
            ephemeral=True,
        )


class ProfileNextStepView(UserOwnedView):
    @discord.ui.button(
        label="補充神諭偏好",
        style=discord.ButtonStyle.primary,
        emoji="📖",
    )
    async def open_preferences(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        existing = ACADEMY_DB.get_preferences(self.owner_id)
        await interaction.response.send_modal(
            OraclePreferencesModal(self.owner_id, existing)
        )


class EnrollmentModal(discord.ui.Modal, title="禊月堂魔法大學｜入學登記"):
    student_name = discord.ui.TextInput(
        label="學生姓名／角色名稱",
        required=True,
        max_length=50,
    )
    preferred_name = discord.ui.TextInput(
        label="希望大家怎麼稱呼你",
        required=True,
        max_length=50,
    )
    major = discord.ui.TextInput(
        label="主修方向",
        placeholder="魔藥、魔法生物、道具研究、尚未決定",
        required=False,
        max_length=80,
    )
    companion_name = discord.ui.TextInput(
        label="固定同行者／伴侶稱呼（可留白）",
        required=False,
        max_length=50,
    )
    introduction = discord.ui.TextInput(
        label="個人簡介",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=600,
    )

    def __init__(
        self,
        *,
        user_id: int,
        house: str,
        enrollment_year: str,
        existing: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self.user_id = int(user_id)
        self.house = house
        self.enrollment_year = enrollment_year
        existing = existing or {}

        self.student_name.default = existing.get("student_name", "")
        self.preferred_name.default = existing.get("preferred_name", "")
        self.major.default = existing.get("major", "")
        self.companion_name.default = existing.get("companion_name", "")
        self.introduction.default = existing.get("introduction", "")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        ACADEMY_DB.save_profile(
            user_id=self.user_id,
            student_name=str(self.student_name.value),
            preferred_name=str(self.preferred_name.value),
            house=self.house,
            major=str(self.major.value),
            enrollment_year=self.enrollment_year,
            introduction=str(self.introduction.value),
            companion_name=str(self.companion_name.value),
        )

        await interaction.response.send_message(
            embed=monk_embed(
                "✅ 入學資料已保存",
                f"{self.preferred_name.value}，學籍已登記至 **{self.house}**。\n\n"
                "接著可以補充神諭偏好，也可以登記商店、住處或工作室。",
                color=0x3BA55D,
            ),
            view=ProfileNextStepView(self.user_id),
            ephemeral=True,
        )


class DeleteProfileView(UserOwnedView):
    @discord.ui.button(
        label="確認刪除學籍",
        style=discord.ButtonStyle.danger,
        emoji="🗑️",
    )
    async def confirm_delete(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        deleted = ACADEMY_DB.delete_profile(self.owner_id)
        self.stop()
        await interaction.response.edit_message(
            content=(
                "學籍、個人地點與神諭冊已刪除。"
                if deleted
                else "目前沒有可刪除的學籍。"
            ),
            embed=None,
            view=None,
        )

    @discord.ui.button(
        label="取消",
        style=discord.ButtonStyle.secondary,
    )
    async def cancel_delete(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.stop()
        await interaction.response.edit_message(
            content="已取消刪除。",
            embed=None,
            view=None,
        )


class PlaceModal(discord.ui.Modal, title="學院街區｜地點登記"):
    place_name = discord.ui.TextInput(
        label="地點名稱",
        placeholder="不會製藥株式會社／月影公寓三樓",
        required=True,
        max_length=80,
    )
    district = discord.ui.TextInput(
        label="所在區域",
        placeholder="學院城東街／星泉河畔／校外住宅區",
        required=False,
        max_length=80,
    )
    description = discord.ui.TextInput(
        label="地點簡介",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=700,
    )
    status = discord.ui.TextInput(
        label="目前狀態",
        placeholder="營業中／使用中／等待重新開張",
        default="使用中",
        required=True,
        max_length=40,
    )

    def __init__(
        self,
        *,
        user_id: int,
        place_type: str,
        source_kind: str,
        allow_oracle: bool,
        is_public: bool,
    ) -> None:
        super().__init__()
        self.user_id = int(user_id)
        self.place_type = place_type
        self.source_kind = source_kind
        self.allow_oracle = allow_oracle
        self.is_public = is_public

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if ACADEMY_DB.get_profile(self.user_id) is None:
            await interaction.response.send_message(
                "請先完成 `/入學登記`，再登記個人地點。",
                ephemeral=True,
            )
            return

        place_id = ACADEMY_DB.create_place(
            user_id=self.user_id,
            name=str(self.place_name.value),
            place_type=self.place_type,
            district=str(self.district.value),
            description=str(self.description.value),
            source_kind=self.source_kind,
            status=str(self.status.value),
            allow_oracle=self.allow_oracle,
            is_public=self.is_public,
        )

        await interaction.response.send_message(
            embed=monk_embed(
                "🏘️ 地點登記完成",
                f"**{self.place_name.value}** 已加入學院街區資料。\n\n"
                f"類型：{self.place_type}\n"
                f"來源：{self.source_kind}\n"
                f"公開：{'是' if self.is_public else '否'}\n"
                f"允許神諭使用：{'是' if self.allow_oracle else '否'}\n"
                f"地點編號：{place_id}",
                color=0x8B6F47,
            ),
            ephemeral=True,
        )


def place_embed(
    place: dict[str, Any],
    *,
    index: int,
    total: int,
) -> discord.Embed:
    embed = monk_embed(
        f"🏘️ 學院街區｜{place['name']}",
        f"**類型**：{place['place_type']}\n"
        f"**經營者／居住者**：{place.get('owner_name') or '未公開'}\n"
        f"**區域**：{place.get('district') or '未設定'}\n"
        f"**狀態**：{place.get('status') or '未設定'}\n"
        f"**來源**：{place.get('source_kind') or '新登記'}\n\n"
        f"{place.get('description') or '沒有簡介。'}",
        color=0x8B6F47,
    )
    embed.set_footer(text=f"地點 {index + 1}／{total}")
    return embed


class PlacesView(discord.ui.View):
    def __init__(self, places: list[dict[str, Any]]) -> None:
        super().__init__(timeout=900)
        self.places = places
        self.index = 0
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        self.previous_page.disabled = self.index <= 0
        self.next_page.disabled = self.index >= len(self.places) - 1

    def current_embed(self) -> discord.Embed:
        return place_embed(
            self.places[self.index],
            index=self.index,
            total=len(self.places),
        )

    @discord.ui.button(
        label="上一頁",
        style=discord.ButtonStyle.secondary,
        emoji="◀️",
    )
    async def previous_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.index = max(0, self.index - 1)
        self._refresh_buttons()
        await interaction.response.edit_message(
            embed=self.current_embed(),
            view=self,
        )

    @discord.ui.button(
        label="下一頁",
        style=discord.ButtonStyle.secondary,
        emoji="▶️",
    )
    async def next_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.index = min(len(self.places) - 1, self.index + 1)
        self._refresh_buttons()
        await interaction.response.edit_message(
            embed=self.current_embed(),
            view=self,
        )


def oracle_page_embed(
    page: dict[str, Any],
    *,
    index: int,
    total: int,
) -> discord.Embed:
    status_icon = "✅" if page["status"] == "已完成" else "⬜"
    embed = monk_embed(
        f"📖 禊月堂個人神諭冊｜{page['week_label']}",
        f"**期間**：{page['period_start']}～{page['period_end']}\n"
        f"**狀態**：{status_icon} {page['status']}\n\n"
        f"{page['oracle_text']}",
        color=0x7A5AC8,
    )

    if page.get("used_keywords"):
        embed.add_field(
            name="本頁創作關鍵字",
            value=page["used_keywords"],
            inline=False,
        )
    if page.get("used_place_names"):
        embed.add_field(
            name="本頁可能使用的學院地點",
            value=page["used_place_names"],
            inline=False,
        )
    if page.get("completed_at"):
        embed.add_field(
            name="完成紀錄",
            value=page["completed_at"],
            inline=False,
        )

    embed.set_footer(
        text=f"神諭頁 {index + 1}／{total}｜內部週次 {page['week_key']}"
    )
    return embed


class OracleBookView(UserOwnedView):
    def __init__(
        self,
        owner_id: int,
        pages: list[dict[str, Any]],
        *,
        index: int | None = None,
    ) -> None:
        super().__init__(owner_id, timeout=900)
        self.pages = pages
        self.index = len(pages) - 1 if index is None else index
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        self.previous_page.disabled = self.index <= 0
        self.next_page.disabled = self.index >= len(self.pages) - 1
        page = self.pages[self.index]
        self.mark_done.disabled = page["status"] == "已完成"
        self.mark_undone.disabled = page["status"] == "未完成"

    def current_embed(self) -> discord.Embed:
        return oracle_page_embed(
            self.pages[self.index],
            index=self.index,
            total=len(self.pages),
        )

    @discord.ui.button(
        label="上一頁",
        style=discord.ButtonStyle.secondary,
        emoji="◀️",
    )
    async def previous_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.index = max(0, self.index - 1)
        self._refresh_buttons()
        await interaction.response.edit_message(
            embed=self.current_embed(),
            view=self,
        )

    @discord.ui.button(
        label="標記已完成",
        style=discord.ButtonStyle.success,
        emoji="✅",
    )
    async def mark_done(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        page = self.pages[self.index]
        updated = ACADEMY_DB.set_oracle_status(
            page_id=int(page["id"]),
            user_id=self.owner_id,
            status="已完成",
        )
        if updated is None:
            await interaction.response.send_message(
                "找不到這一頁神諭。",
                ephemeral=True,
            )
            return
        self.pages[self.index] = updated
        self._refresh_buttons()
        await interaction.response.edit_message(
            embed=self.current_embed(),
            view=self,
        )

    @discord.ui.button(
        label="標記未完成",
        style=discord.ButtonStyle.primary,
        emoji="⬜",
    )
    async def mark_undone(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        page = self.pages[self.index]
        updated = ACADEMY_DB.set_oracle_status(
            page_id=int(page["id"]),
            user_id=self.owner_id,
            status="未完成",
        )
        if updated is None:
            await interaction.response.send_message(
                "找不到這一頁神諭。",
                ephemeral=True,
            )
            return
        self.pages[self.index] = updated
        self._refresh_buttons()
        await interaction.response.edit_message(
            embed=self.current_embed(),
            view=self,
        )

    @discord.ui.button(
        label="下一頁",
        style=discord.ButtonStyle.secondary,
        emoji="▶️",
    )
    async def next_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.index = min(len(self.pages) - 1, self.index + 1)
        self._refresh_buttons()
        await interaction.response.edit_message(
            embed=self.current_embed(),
            view=self,
        )




def _truncate_text(text: str, limit: int) -> str:
    cleaned = str(text or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[: limit - 1].rstrip()}…"


def student_dashboard_embed(user_id: int) -> discord.Embed:
    profile = ACADEMY_DB.get_profile_bundle(user_id)
    if profile is None:
        return monk_embed(
            "🎓 禊月堂學生資料中心",
            "目前尚未建立學籍。\n\n"
            "請先使用 `/入學登記` 填寫入學資料；完成後再回來按這顆按鈕。",
            color=0x5865F2,
        )

    preferences = profile.get("preferences", {})
    places = ACADEMY_DB.list_user_places(user_id)
    pages = ACADEMY_DB.list_oracles(user_id)
    current_week = month_week_info()
    current_page = ACADEMY_DB.get_oracle_by_week(user_id, current_week.key)

    current_status = (
        current_page["status"]
        if current_page is not None
        else "本週尚未建立"
    )

    embed = monk_embed(
        "🎓 我的學生資料總覽",
        f"**學生姓名**：{profile.get('student_name') or '未填寫'}\n"
        f"**希望稱呼**：{profile.get('preferred_name') or '未填寫'}\n"
        f"**所屬學院**：{profile.get('house') or '尚未分院'}\n"
        f"**主修方向**：{profile.get('major') or '未填寫'}\n"
        f"**入學年份**：{profile.get('enrollment_year') or '未填寫'}\n"
        f"**固定同行者**：{profile.get('companion_name') or '未設定'}",
        color=0x5865F2,
    )

    preference_lines = (
        f"喜歡：{preferences.get('liked_themes') or '未設定'}\n"
        f"避免：{preferences.get('avoided_topics') or '未設定'}\n"
        f"創作關鍵字：{preferences.get('creative_keywords') or '未設定'}\n"
        f"偏好場景：{preferences.get('preferred_scenes') or '未設定'}\n"
        f"允許使用個人地點："
        f"{'是' if preferences.get('allow_place_context', 1) else '否'}"
    )
    embed.add_field(
        name="🔮 神諭偏好",
        value=_truncate_text(preference_lines, 1024),
        inline=False,
    )
    embed.add_field(
        name="🏘️ 學院街區",
        value=f"已登記 **{len(places)}** 個商店、住處或其他地點。",
        inline=True,
    )
    embed.add_field(
        name="📖 神諭冊",
        value=(
            f"目前共有 **{len(pages)}** 頁。\n"
            f"本週 `{current_week.label}`：**{current_status}**"
        ),
        inline=True,
    )
    embed.set_footer(text="此頁為私密資料，只有按下按鈕的本人看得到。")
    return embed


def student_preferences_embed(user_id: int) -> discord.Embed:
    preferences = ACADEMY_DB.get_preferences(user_id)
    if preferences is None:
        return monk_embed(
            "🔮 我的神諭偏好",
            "目前尚未設定神諭偏好。\n\n"
            "可以使用 `/神諭偏好` 補充喜歡的題材、避免內容與創作關鍵字。",
            color=0x7A5AC8,
        )

    return monk_embed(
        "🔮 我的神諭偏好",
        f"**喜歡的題材與氣氛**\n"
        f"{preferences.get('liked_themes') or '未設定'}\n\n"
        f"**希望避免的題材**\n"
        f"{preferences.get('avoided_topics') or '未設定'}\n\n"
        f"**可使用的創作關鍵字**\n"
        f"{preferences.get('creative_keywords') or '未設定'}\n\n"
        f"**偏好場景**\n"
        f"{preferences.get('preferred_scenes') or '未設定'}\n\n"
        f"**允許神諭使用個人地點**："
        f"{'是' if preferences.get('allow_place_context', 1) else '否'}",
        color=0x7A5AC8,
    )


def student_places_embed(user_id: int) -> discord.Embed:
    places = ACADEMY_DB.list_user_places(user_id)
    if not places:
        return monk_embed(
            "🏘️ 我的學院街區地點",
            "目前沒有登記地點。\n\n"
            "可以使用 `/地點登記` 建立商店、校外住處、工作室，"
            "或把過去企劃中的店面遷入學院街區。",
            color=0x8B6F47,
        )

    lines: list[str] = []
    for place in places[:15]:
        lines.append(
            f"**#{place['id']}｜{place['name']}**\n"
            f"{place['place_type']}｜{place['status']}｜"
            f"{'公開' if place['is_public'] else '不公開'}｜"
            f"{'可進神諭' if place['allow_oracle'] else '不進神諭'}"
        )

    remaining = len(places) - 15
    if remaining > 0:
        lines.append(f"……另有 {remaining} 個地點未顯示。")

    return monk_embed(
        "🏘️ 我的學院街區地點",
        _truncate_text("\n\n".join(lines), 4000),
        color=0x8B6F47,
    )


class StudentPrivateMenuView(UserOwnedView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id, timeout=900)

    @discord.ui.button(
        label="學籍總覽",
        style=discord.ButtonStyle.primary,
        emoji="🎓",
    )
    async def show_profile(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.edit_message(
            embed=student_dashboard_embed(self.owner_id),
            view=self,
        )

    @discord.ui.button(
        label="神諭偏好",
        style=discord.ButtonStyle.secondary,
        emoji="🔮",
    )
    async def show_preferences(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.edit_message(
            embed=student_preferences_embed(self.owner_id),
            view=self,
        )

    @discord.ui.button(
        label="我的地點",
        style=discord.ButtonStyle.secondary,
        emoji="🏘️",
    )
    async def show_places(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.edit_message(
            embed=student_places_embed(self.owner_id),
            view=self,
        )

    @discord.ui.button(
        label="開啟神諭冊",
        style=discord.ButtonStyle.success,
        emoji="📖",
    )
    async def open_oracle_book(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        pages = ACADEMY_DB.list_oracles(self.owner_id)
        if not pages:
            await interaction.response.send_message(
                "神諭冊目前是空的。請先使用 `/本週神諭` 建立第一頁。",
                ephemeral=True,
            )
            return

        oracle_view = OracleBookView(self.owner_id, pages)
        await interaction.response.send_message(
            embed=oracle_view.current_embed(),
            view=oracle_view,
            ephemeral=True,
        )


class StudentDataPanelView(discord.ui.View):
    """公開固定面板；每次互動都依按鈕操作者查詢私密資料。"""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="查看我的學生資料",
        style=discord.ButtonStyle.primary,
        emoji="📚",
        custom_id="stern_monk:student_data:view_my_profile",
    )
    async def view_my_profile(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not is_allowed_channel(
            interaction.channel_id,
            SETTINGS.monk_channel_id,
        ):
            await interaction.response.send_message(
                f"學生資料面板只能在 <#{SETTINGS.monk_channel_id}> 使用。",
                ephemeral=True,
            )
            return

        profile = ACADEMY_DB.get_profile_bundle(interaction.user.id)
        if profile is None:
            await interaction.response.send_message(
                embed=student_dashboard_embed(interaction.user.id),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=student_dashboard_embed(interaction.user.id),
            view=StudentPrivateMenuView(interaction.user.id),
            ephemeral=True,
        )


@tree.command(
    name="建立學生資料面板",
    description="由管理員建立固定的學生資料查看按鈕",
)
@app_commands.default_permissions(manage_guild=True)
async def create_student_data_panel(
    interaction: discord.Interaction,
) -> None:
    member = interaction.user
    permissions = getattr(member, "guild_permissions", None)
    if permissions is None or not permissions.manage_guild:
        await interaction.response.send_message(
            "只有具有「管理伺服器」權限的管理員能建立這個面板。",
            ephemeral=True,
        )
        return

    embed = monk_embed(
        "🎓 禊月堂學生資料中心",
        "按下下方按鈕，即可查看自己保存的：\n\n"
        "・入學學籍與希望稱呼\n"
        "・神諭偏好與避免題材\n"
        "・個人商店、住處與工作室\n"
        "・神諭冊頁數與本週完成狀態\n\n"
        "**查詢結果只會私密顯示給按鈕操作者本人。**",
        color=0x5865F2,
    )

    await interaction.response.send_message(
        embed=embed,
        view=StudentDataPanelView(),
    )



@tree.command(
    name="入學登記",
    description="填寫魔法大學入學資料",
)
@app_commands.describe(
    學院="目前所屬學院",
    入學年份="例如 2026；可留白",
)
@app_commands.choices(學院=HOUSE_CHOICES)
async def enroll_student(
    interaction: discord.Interaction,
    學院: app_commands.Choice[str],
    入學年份: app_commands.Range[str, 0, 12] = "",
) -> None:
    existing = ACADEMY_DB.get_profile(interaction.user.id)
    await interaction.response.send_modal(
        EnrollmentModal(
            user_id=interaction.user.id,
            house=學院.value,
            enrollment_year=str(入學年份),
            existing=existing,
        )
    )


@tree.command(
    name="我的學籍",
    description="查看自己的入學資料與神諭偏好",
)
async def my_student_profile(interaction: discord.Interaction) -> None:
    profile = ACADEMY_DB.get_profile_bundle(interaction.user.id)
    if profile is None:
        await interaction.response.send_message(
            "尚未建立學籍。請先使用 `/入學登記`。",
            ephemeral=True,
        )
        return
    await interaction.response.send_message(
        embed=student_profile_embed(profile),
        ephemeral=True,
    )


@tree.command(
    name="修改學籍",
    description="修改自己的魔法大學入學資料",
)
@app_commands.describe(
    學院="更新後的學院；不填則保留原本設定",
    入學年份="更新後的入學年份；不填則保留原本設定",
)
@app_commands.choices(學院=HOUSE_CHOICES)
async def edit_student_profile(
    interaction: discord.Interaction,
    學院: app_commands.Choice[str] | None = None,
    入學年份: app_commands.Range[str, 0, 12] | None = None,
) -> None:
    existing = ACADEMY_DB.get_profile(interaction.user.id)
    if existing is None:
        await interaction.response.send_message(
            "尚未建立學籍。請先使用 `/入學登記`。",
            ephemeral=True,
        )
        return

    await interaction.response.send_modal(
        EnrollmentModal(
            user_id=interaction.user.id,
            house=學院.value if 學院 else existing["house"],
            enrollment_year=(
                str(入學年份)
                if 入學年份 is not None
                else existing["enrollment_year"]
            ),
            existing=existing,
        )
    )


@tree.command(
    name="神諭偏好",
    description="設定神諭創作偏好與避免題材",
)
async def oracle_preferences(interaction: discord.Interaction) -> None:
    if ACADEMY_DB.get_profile(interaction.user.id) is None:
        await interaction.response.send_message(
            "請先使用 `/入學登記` 建立學籍。",
            ephemeral=True,
        )
        return
    existing = ACADEMY_DB.get_preferences(interaction.user.id)
    await interaction.response.send_modal(
        OraclePreferencesModal(interaction.user.id, existing)
    )


@tree.command(
    name="刪除學籍",
    description="刪除自己的學籍、個人地點與神諭冊",
)
async def delete_student_profile(interaction: discord.Interaction) -> None:
    if ACADEMY_DB.get_profile(interaction.user.id) is None:
        await interaction.response.send_message(
            "目前沒有可刪除的學籍。",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        "這會同時刪除你的學籍、神諭偏好、個人地點與神諭頁面。確定要繼續嗎？",
        view=DeleteProfileView(interaction.user.id),
        ephemeral=True,
    )


@tree.command(
    name="地點登記",
    description="把商店、住處或舊企劃地點登記進學院街區",
)
@app_commands.describe(
    類型="商店、住處或其他地點類型",
    來源="新地點或舊企劃遷入",
    允許神諭使用="是否允許每週神諭採用這個地點",
    是否公開="是否出現在公開學院街區",
)
@app_commands.choices(
    類型=PLACE_TYPE_CHOICES,
    來源=PLACE_SOURCE_CHOICES,
)
async def register_place(
    interaction: discord.Interaction,
    類型: app_commands.Choice[str],
    來源: app_commands.Choice[str],
    允許神諭使用: bool = True,
    是否公開: bool = True,
) -> None:
    if ACADEMY_DB.get_profile(interaction.user.id) is None:
        await interaction.response.send_message(
            "請先完成 `/入學登記`，再登記個人地點。",
            ephemeral=True,
        )
        return

    await interaction.response.send_modal(
        PlaceModal(
            user_id=interaction.user.id,
            place_type=類型.value,
            source_kind=來源.value,
            allow_oracle=允許神諭使用,
            is_public=是否公開,
        )
    )


@tree.command(
    name="我的地點",
    description="查看自己登記的商店、住處與工作室",
)
async def my_places(interaction: discord.Interaction) -> None:
    places = ACADEMY_DB.list_user_places(interaction.user.id)
    if not places:
        await interaction.response.send_message(
            "目前沒有登記地點。可以使用 `/地點登記` 建立商店、住處或遷入舊企劃。",
            ephemeral=True,
        )
        return

    lines = []
    for place in places[:20]:
        lines.append(
            f"**#{place['id']}｜{place['name']}**\n"
            f"{place['place_type']}｜{place['status']}｜"
            f"{'公開' if place['is_public'] else '不公開'}｜"
            f"{'可進神諭' if place['allow_oracle'] else '不進神諭'}"
        )

    await interaction.response.send_message(
        embed=monk_embed(
            "🏘️ 我的學院街區地點",
            "\n\n".join(lines),
            color=0x8B6F47,
        ),
        ephemeral=True,
    )


@tree.command(
    name="學院街區",
    description="一頁一頁查看學生公開的商店與校外居住地",
)
@app_commands.describe(類型="可選擇只查看某一類型")
@app_commands.choices(類型=PLACE_TYPE_CHOICES)
async def campus_district(
    interaction: discord.Interaction,
    類型: app_commands.Choice[str] | None = None,
) -> None:
    places = ACADEMY_DB.list_public_places(
        類型.value if 類型 else None
    )
    if not places:
        await interaction.response.send_message(
            "目前沒有符合條件的公開地點。",
            ephemeral=True,
        )
        return

    view = PlacesView(places)
    await interaction.response.send_message(
        embed=view.current_embed(),
        view=view,
    )


@tree.command(
    name="本週神諭",
    description="建立或查看本週神諭頁面",
)
async def current_week_oracle(interaction: discord.Interaction) -> None:
    profile = ACADEMY_DB.get_profile_bundle(interaction.user.id)
    if profile is None:
        await interaction.response.send_message(
            "請先使用 `/入學登記` 建立學籍。",
            ephemeral=True,
        )
        return

    week = month_week_info()
    existing_page = ACADEMY_DB.get_oracle_by_week(
        interaction.user.id,
        week.key,
    )

    if existing_page is None:
        if openai_client is None or not SETTINGS.oracle_ai_available:
            await interaction.response.send_message(
                "本週尚未建立神諭，而且 AI 神諭目前未啟用。"
                "請確認 `AI_ORACLE_ENABLED=true` 與 API Key。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True, ephemeral=True)

        preferences = profile.get("preferences", {})
        all_places = (
            ACADEMY_DB.list_oracle_places(interaction.user.id)
            if preferences.get("allow_place_context", 1)
            else []
        )
        weekly_keywords = select_weekly_keywords(
            user_id=interaction.user.id,
            week_key=week.key,
            creative_keywords=preferences.get("creative_keywords", ""),
            liked_themes=preferences.get("liked_themes", ""),
            preferred_scenes=preferences.get("preferred_scenes", ""),
        )
        weekly_places = select_weekly_places(
            user_id=interaction.user.id,
            week_key=week.key,
            places=all_places,
        )

        try:
            oracle_text = await generate_oracle(
                client=openai_client,
                model=SETTINGS.openai_model,
                max_output_tokens=SETTINGS.oracle_max_output_tokens,
                user_id=interaction.user.id,
                profile=profile,
                preferences=preferences,
                places=weekly_places,
                week=week,
                weekly_keywords=weekly_keywords,
            )
        except Exception:
            logger.exception("OpenAI API 神諭生成失敗")
            await interaction.followup.send(
                "本週神諭生成失敗。請稍後再試，或請管理員查看 Railway 紀錄。",
                ephemeral=True,
            )
            return

        existing_page = ACADEMY_DB.create_oracle(
            user_id=interaction.user.id,
            week=week,
            oracle_text=oracle_text,
            used_keywords="、".join(weekly_keywords),
            used_place_names="、".join(
                place["name"] for place in weekly_places
            ),
        )

        pages = ACADEMY_DB.list_oracles(interaction.user.id)
        index = next(
            i for i, page in enumerate(pages)
            if page["id"] == existing_page["id"]
        )
        view = OracleBookView(
            interaction.user.id,
            pages,
            index=index,
        )
        await interaction.followup.send(
            embed=view.current_embed(),
            view=view,
            ephemeral=True,
        )
        return

    pages = ACADEMY_DB.list_oracles(interaction.user.id)
    index = next(
        i for i, page in enumerate(pages)
        if page["id"] == existing_page["id"]
    )
    view = OracleBookView(
        interaction.user.id,
        pages,
        index=index,
    )
    await interaction.response.send_message(
        embed=view.current_embed(),
        view=view,
        ephemeral=True,
    )


@tree.command(
    name="神諭冊",
    description="一頁一頁查看歷週神諭並標記完成狀態",
)
async def oracle_book(interaction: discord.Interaction) -> None:
    pages = ACADEMY_DB.list_oracles(interaction.user.id)
    if not pages:
        await interaction.response.send_message(
            "神諭冊目前是空的。請先使用 `/本週神諭` 建立第一頁。",
            ephemeral=True,
        )
        return

    view = OracleBookView(interaction.user.id, pages)
    await interaction.response.send_message(
        embed=view.current_embed(),
        view=view,
        ephemeral=True,
    )



@tree.command(
    name="修士介紹",
    description="查看赤木修士成為萬年學長的經歷",
)
async def monk_introduction(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        embed=monk_embed(
            "📜 赤木修士｜萬年學長",
            MONK_INTRODUCTION,
            color=0x8B6F47,
        ),
    )


@tree.command(
    name="新生指南",
    description="讓修士告訴你加入學院後該先做什麼",
)
async def newcomer_guide(interaction: discord.Interaction) -> None:
    tutorial = KNOWLEDGE.tutorial_by_id["new_player_flow"]
    match = KnowledgeMatch("tutorial", tutorial, tutorial, 100_000)
    description = render_local_reply(match)

    await interaction.response.send_message(
        embed=monk_embed("📘 新生指南｜修士版", description, color=0x5865F2),
    )


TEACHING_CHOICES = [
    app_commands.Choice(name=item["title"], value=item["id"])
    for item in KNOWLEDGE.tutorials
]


@tree.command(
    name="修士教學",
    description="選擇一個遊戲系統，查看繁體中文教學",
)
@app_commands.describe(主題="你想查看的教學主題")
@app_commands.choices(主題=TEACHING_CHOICES)
async def monk_tutorial(
    interaction: discord.Interaction,
    主題: app_commands.Choice[str],
) -> None:
    item = KNOWLEDGE.tutorial_by_id.get(主題.value)

    if not isinstance(item, dict):
        await interaction.response.send_message(
            "這份教學目前無法載入。請通知管理員檢查資料檔案。",
            ephemeral=True,
        )
        return

    match = KnowledgeMatch("tutorial", item, item, 100_000)
    description = render_local_reply(match)

    await interaction.response.send_message(
        embed=monk_embed(
            f"📖 修士教學｜{item.get('title', 主題.name)}",
            description,
        ),
    )


@tree.command(
    name="問修士",
    description="只查本地 FAQ 與正式教學，不會使用 AI",
)
@app_commands.describe(問題="例如：我剛加入，應該先上課還是探索？")
@app_commands.checks.cooldown(1, 20.0)
async def ask_monk(
    interaction: discord.Interaction,
    問題: app_commands.Range[str, 2, 200],
) -> None:
    nickname_reply = gorilla_nickname_reply(問題)
    if nickname_reply is not None:
        await interaction.response.send_message(nickname_reply, ephemeral=True)
        return

    refused = boundary_reply(問題)
    if refused is not None:
        await interaction.response.send_message(refused, ephemeral=True)
        return

    # 固定 FAQ 第一順位，固定教學第二順位；兩者都不會使用 API。
    local_result = await answer_question(KNOWLEDGE, 問題)
    if local_result.match is not None:
        match = local_result.match
        await interaction.response.send_message(
            embed=monk_embed(
                f"📚 {match.tutorial['title']}",
                render_local_reply(
                    match,
                    concise=True,
                    gentle=is_emotional_distress(問題),
                ),
                color=0x3BA55D,
            ),
        )
        return

    description = (
        f"{random_line('unknown_question', '「紀錄本裡沒有這題。」')}\n\n"
        f"{NO_OFFICIAL_DATA}\n\n"
        "教學查詢只使用本地正式知識庫，不會呼叫 AI。請查看最新公告或詢問管理員。"
    )

    await interaction.response.send_message(
        embed=monk_embed("📕 修士查不到答案", description, color=0x992D22),
        ephemeral=True,
    )


@tree.command(
    name="修士告解",
    description="向修士進行 AI 告解陪伴；不會修改正式罪惡值",
)
@app_commands.describe(內容="簡短寫下你想告解的內容")
async def monk_confession(
    interaction: discord.Interaction,
    內容: app_commands.Range[str, 2, 300],
) -> None:
    nickname_reply = gorilla_nickname_reply(內容)
    if nickname_reply is not None:
        await interaction.response.send_message(nickname_reply, ephemeral=True)
        return

    refused = confession_boundary_reply(內容)
    if refused is not None:
        await interaction.response.send_message(refused, ephemeral=True)
        return

    if is_emotional_distress(內容):
        opening = "可以慢慢說，先講現在最需要處理的部分。"
        verdict = "先處理眼前能做的一步；需要時，也可以找信任的人一起整理。"
    else:
        opening = random_line("confession_opening", "請說重點，我會聽完。")
        verdict = random_line("confession_verdict", "內容收到。接著把能修正的部分做好。")

    local_description = (
        f"{opening}\n\n"
        f"> {discord.utils.escape_markdown(內容)}\n\n"
        f"{verdict}\n\n"
        "⚠️ **目前是試行版告解**\n"
        "這個指令只進行角色演出，尚未連接神父的正式玩家資料，"
        "因此不會降低罪惡值。正式處理仍請使用神父的告解功能。"
    )

    if openai_client is None or not SETTINGS.confession_ai_available:
        await interaction.response.send_message(
            embed=monk_embed(
                "🕯️ 修士告解室｜本地回覆",
                f"{local_description}\n\n_AI 告解目前未啟用。_",
                color=0x111111,
            ),
            ephemeral=True,
        )
        return

    used = get_today_usage(interaction.user.id)
    if SETTINGS.ai_daily_limit > 0 and used >= SETTINGS.ai_daily_limit:
        await interaction.response.send_message(
            embed=monk_embed(
                "🕯️ 修士告解室｜本地回覆",
                f"{local_description}\n\n_今日 AI 使用次數已用完，先由本地修士回覆。_",
                color=0x111111,
            ),
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True, ephemeral=True)

    try:
        ai_reply = await ask_openai_confession(
            內容,
            interaction.user.id,
            interaction.user.display_name,
        )
    except Exception:
        logger.exception("OpenAI API 告解回覆失敗")
        await interaction.followup.send(
            embed=monk_embed(
                "🕯️ 修士告解室｜本地回覆",
                f"{local_description}\n\n_AI 暫時無法回覆；告解內容未寫入玩家資料。_",
                color=0xFAA61A,
            ),
            ephemeral=True,
        )
        return

    current_usage = increment_today_usage(interaction.user.id)
    remaining = (
        max(0, SETTINGS.ai_daily_limit - current_usage)
        if SETTINGS.ai_daily_limit > 0
        else "不限"
    )
    description = (
        f"{ai_reply}\n\n"
        "⚠️ **告解陪伴不會修改罪惡值或玩家資料。**\n"
        "正式罪惡值處理仍請使用神父的告解功能。\n\n"
        f"_AI 一次性回覆｜今日 AI 使用剩餘：{remaining}_"
    )

    await interaction.followup.send(
        embed=monk_embed("🕯️ 修士告解室｜AI 回覆", description, color=0x111111),
        ephemeral=True,
    )


@tree.command(
    name="修士狀態",
    description="確認修士是否正常運作",
)
async def monk_status(interaction: discord.Interaction) -> None:
    confession_ai_status = "已啟用" if SETTINGS.confession_ai_available else "未啟用"
    oracle_ai_status = "已啟用" if SETTINGS.oracle_ai_available else "未啟用"
    await interaction.response.send_message(
        "修士目前在線，教學與規則查詢可正常使用。\n\n"
        "AI 教學：**永久停用**\n"
        f"AI 告解：**{confession_ai_status}**\n"
        f"AI 神諭：**{oracle_ai_status}**\n"
        f"學籍資料庫：**已啟用**\n"
        f"指定頻道：<#{SETTINGS.monk_channel_id}>",
        ephemeral=True,
    )


@tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
) -> None:
    if isinstance(error, WrongMonkChannel):
        message = (
            f"這裡不是修士教學頻道。請到 <#{SETTINGS.monk_channel_id}> 使用指令。"
        )
    elif isinstance(error, app_commands.CommandOnCooldown):
        seconds = max(1, int(error.retry_after))
        message = f"指令冷卻中，請在 **{seconds} 秒**後再問。資料整理也需要一點時間。"
    else:
        logger.exception("斜線指令執行失敗：%s", error)
        message = "系統發生錯誤，這次不是你的操作問題。請通知管理員查看 Railway 紀錄。"

    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


def main() -> None:
    SETTINGS.validate_runtime()
    client.run(SETTINGS.monk_token, log_handler=None)


if __name__ == "__main__":
    main()

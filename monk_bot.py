from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import date
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from openai import AsyncOpenAI

from academy_db import AcademyDatabase, month_week_info, taipei_today
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


CONFESSION_USAGE_SCOPE = "confession_day"
ORACLE_USAGE_SCOPE = "oracle_week"


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


PANEL_SESSION_TIMEOUT_SECONDS = 600


def main_panel_embed(*, notice: str = "") -> discord.Embed:
    description = (
        "赤木修士正在整理學生資料與本週紀錄。\n\n"
        "請使用下方按鈕進入對應功能：\n"
        "・學生資料：入學登記、學籍、神諭偏好、地點管理\n"
        "・城下町：商店街、校外住處、地點登記\n"
        "・神諭冊：本週神諭與完成紀錄\n"
        "・教學：正式遊戲教學與 FAQ\n"
        "・告解：一次性陪伴，不修改正式罪惡值"
    )
    if notice:
        description += f"\n\n🔒 {notice}"

    embed = monk_embed(
        "◆ 禊月堂修士教室",
        description,
        color=0x8B6F47,
    )
    embed.set_footer(
        text=(
            "按下功能後會直接切換此訊息。"
            "操作畫面只限開啟者使用，10 分鐘未操作會自動鎖定並返回主面板。"
        )
    )
    return embed


class PanelSession:
    def __init__(
        self,
        *,
        owner_id: int,
        owner_name: str,
        message: discord.Message,
    ) -> None:
        self.owner_id = int(owner_id)
        self.owner_name = owner_name
        self.message = message
        self.timeout_task: asyncio.Task[None] | None = None

    def touch(self) -> None:
        task = self.timeout_task
        if task is not None and not task.done():
            task.cancel()
        self.timeout_task = asyncio.create_task(self._expire())

    async def _expire(self) -> None:
        try:
            await asyncio.sleep(PANEL_SESSION_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            return

        current = ACTIVE_PANEL_SESSIONS.get(self.message.id)
        if current is not self:
            return

        clear_panel_session(self, cancel_task=False)
        try:
            await self.message.edit(
                embed=main_panel_embed(
                    notice="上一個操作畫面因 10 分鐘未操作而鎖定。"
                ),
                view=MonkMainPanelView(),
            )
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.exception("修士面板逾時後無法恢復主畫面。")


ACTIVE_PANEL_SESSIONS: dict[int, PanelSession] = {}
ACTIVE_OWNER_SESSIONS: dict[int, PanelSession] = {}


def clear_panel_session(
    session: PanelSession,
    *,
    cancel_task: bool = True,
) -> None:
    if ACTIVE_PANEL_SESSIONS.get(session.message.id) is session:
        ACTIVE_PANEL_SESSIONS.pop(session.message.id, None)
    if ACTIVE_OWNER_SESSIONS.get(session.owner_id) is session:
        ACTIVE_OWNER_SESSIONS.pop(session.owner_id, None)

    task = session.timeout_task
    if (
        cancel_task
        and task is not None
        and not task.done()
        and task is not asyncio.current_task()
    ):
        task.cancel()


def active_session_for_owner(owner_id: int) -> PanelSession | None:
    return ACTIVE_OWNER_SESSIONS.get(int(owner_id))


async def claim_panel_session(
    interaction: discord.Interaction,
) -> PanelSession | None:
    message = interaction.message
    if message is None:
        await interaction.response.send_message(
            "找不到修士面板訊息，請重新建立面板。",
            ephemeral=True,
        )
        return None

    existing = ACTIVE_PANEL_SESSIONS.get(message.id)
    if existing is not None and existing.owner_id != interaction.user.id:
        await interaction.response.send_message(
            f"目前是 **{existing.owner_name}** 的操作畫面。"
            "請等對方返回主面板，或等待 10 分鐘未操作後自動解鎖。",
            ephemeral=True,
        )
        return None

    if existing is None:
        previous = ACTIVE_OWNER_SESSIONS.get(interaction.user.id)
        if previous is not None:
            clear_panel_session(previous)

        existing = PanelSession(
            owner_id=interaction.user.id,
            owner_name=interaction.user.display_name,
            message=message,
        )
        ACTIVE_PANEL_SESSIONS[message.id] = existing
        ACTIVE_OWNER_SESSIONS[interaction.user.id] = existing

    existing.touch()
    return existing


async def edit_active_panel_from_modal(
    interaction: discord.Interaction,
    *,
    owner_id: int,
    embed: discord.Embed,
    view: discord.ui.View,
) -> bool:
    session = active_session_for_owner(owner_id)
    if session is None:
        await interaction.response.send_message(
            "這個操作畫面已鎖定。請從修士主面板重新開始。",
            ephemeral=True,
        )
        return False

    session.touch()
    await interaction.response.defer()
    await session.message.edit(embed=embed, view=view)
    return True


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
    source_label = f"_{knowledge_source_label(match)}_"
    if gentle:
        return (
            f"{answer}\n\n"
            "先照正確做法處理；若畫面仍不同，"
            f"保留截圖詢問管理員。\n\n{source_label}"
        )

    opening, ending = roleplay_lines(match)
    return (
        f"{opening}\n\n{answer}\n\n"
        f"{ending}\n\n{source_label}"
    )


def random_line(category: str, fallback: str) -> str:
    lines = DIALOGUE.get(category, [])
    if not isinstance(lines, list):
        return fallback

    valid_lines = [
        line
        for line in lines
        if isinstance(line, str) and line.strip()
    ]
    return random.choice(valid_lines) if valid_lines else fallback


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
        self.add_view(MonkMainPanelView())
        # 相容 v9 已經貼出的舊學生資料面板。
        self.add_view(StudentDataPanelView())
        logger.info("修士主面板 Persistent View 已註冊。")
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
            "AI 教學：永久停用｜AI 告解：%s｜AI 神諭：%s｜模型：%s｜告解每日上限：%s｜神諭每週上限：%s",
            "啟用" if SETTINGS.confession_ai_available else "停用",
            "啟用" if SETTINGS.oracle_ai_available else "停用",
            SETTINGS.openai_model,
            SETTINGS.ai_daily_limit,
            SETTINGS.oracle_weekly_limit,
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


class ReturnToMainPanelButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="返回修士面板",
            style=discord.ButtonStyle.secondary,
            emoji="↩️",
            row=4,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        view = self.view
        owner_id = getattr(view, "owner_id", interaction.user.id)
        session = active_session_for_owner(owner_id)
        if session is not None:
            clear_panel_session(session)

        await interaction.response.edit_message(
            embed=main_panel_embed(),
            view=MonkMainPanelView(),
        )


class UserOwnedView(discord.ui.View):
    def __init__(
        self,
        owner_id: int,
        *,
        timeout: float | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self.owner_id = int(owner_id)
        self.add_item(ReturnToMainPanelButton())

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if not _component_channel_allowed(interaction):
            await _reject_wrong_component_channel(interaction)
            return False

        session = active_session_for_owner(self.owner_id)
        message = interaction.message
        valid_session = (
            session is not None
            and message is not None
            and session.message.id == message.id
            and ACTIVE_PANEL_SESSIONS.get(message.id) is session
        )
        if not valid_session:
            await interaction.response.send_message(
                "這個操作畫面已鎖定。請從修士主面板重新開始。",
                ephemeral=True,
            )
            return False

        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                f"這是 **{session.owner_name}** 的操作畫面，"
                "不能代替操作。請等對方返回主面板，"
                "或等待 10 分鐘未操作後自動解鎖。",
                ephemeral=True,
            )
            return False

        session.touch()
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

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if ACADEMY_DB.get_profile(self.user_id) is None:
            await interaction.response.send_message(
                "請先從修士主面板的「學生資料」完成入學登記。",
                ephemeral=True,
            )
            return

        ACADEMY_DB.save_preferences(
            user_id=self.user_id,
            liked_themes=str(self.liked_themes.value),
            avoided_topics=str(self.avoided_topics.value),
            creative_keywords=str(self.creative_keywords.value),
            preferred_scenes=str(self.preferred_scenes.value),
            allow_place_context=True,
        )
        await edit_active_panel_from_modal(
            interaction,
            owner_id=self.user_id,
            embed=student_dashboard_embed(self.user_id),
            view=StudentHubView(self.user_id),
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

        await edit_active_panel_from_modal(
            interaction,
            owner_id=self.user_id,
            embed=student_dashboard_embed(self.user_id),
            view=StudentHubView(self.user_id),
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
        session = active_session_for_owner(self.owner_id)
        if session is not None:
            clear_panel_session(session)

        await interaction.response.edit_message(
            content=None,
            embed=main_panel_embed(
                notice=(
                    "學籍、個人地點與神諭冊已刪除。"
                    if deleted
                    else "目前沒有可刪除的學籍。"
                )
            ),
            view=MonkMainPanelView(),
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
        await interaction.response.edit_message(
            content=None,
            embed=student_dashboard_embed(self.owner_id),
            view=StudentHubView(self.owner_id),
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
    operator_name = discord.ui.TextInput(
        label="店主／經營者",
        placeholder="填寫角色名稱；共同經營可填多人",
        required=True,
        max_length=120,
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
        is_public: bool,
    ) -> None:
        super().__init__()
        self.user_id = int(user_id)
        self.place_type = place_type
        self.source_kind = source_kind
        self.is_public = is_public

        profile = ACADEMY_DB.get_profile(self.user_id) or {}
        self.operator_name.default = (
            profile.get("preferred_name")
            or profile.get("student_name")
            or ""
        )

        if self.place_type == "校外住處":
            self.operator_name.label = "居住者"
            self.operator_name.placeholder = "填寫居住角色；共同居住可填多人"
        elif self.place_type in {
            "商店",
            "餐館",
            "書店",
            "魔藥工房",
            "診所",
        }:
            self.operator_name.label = "店主／經營者"
            self.operator_name.placeholder = "填寫店主角色；共同經營可填多人"
        else:
            self.operator_name.label = "負責人／使用者"
            self.operator_name.placeholder = "填寫負責角色；可填多人"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if ACADEMY_DB.get_profile(self.user_id) is None:
            await interaction.response.send_message(
                "請先從修士主面板的「學生資料」完成入學登記，再登記個人地點。",
                ephemeral=True,
            )
            return

        place_id = ACADEMY_DB.create_place(
            user_id=self.user_id,
            name=str(self.place_name.value),
            place_type=self.place_type,
            district=str(self.district.value),
            description=str(self.description.value),
            operator_name=str(self.operator_name.value),
            source_kind=self.source_kind,
            status=str(self.status.value),
            allow_oracle=True,
            is_public=self.is_public,
        )

        await edit_active_panel_from_modal(
            interaction,
            owner_id=self.user_id,
            embed=public_my_places_embed(self.user_id),
            view=MyPlacesHubView(
                self.user_id,
                return_target="student",
            ),
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
        f"**經營者／居住者**："
        f"{place.get('operator_name') or place.get('owner_name') or '未設定'}\n"
        f"**區域**：{place.get('district') or '未設定'}\n"
        f"**狀態**：{place.get('status') or '未設定'}\n"
        f"**來源**：{place.get('source_kind') or '新登記'}\n\n"
        f"{place.get('description') or '沒有簡介。'}",
        color=0x8B6F47,
    )
    embed.set_footer(text=f"地點 {index + 1}／{total}")
    return embed


class PlacesView(UserOwnedView):
    def __init__(
        self,
        owner_id: int,
        places: list[dict[str, Any]],
    ) -> None:
        super().__init__(owner_id)
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

    @discord.ui.button(
        label="刪除此頁",
        style=discord.ButtonStyle.danger,
        emoji="🗑️",
        row=1,
    )
    async def delete_page(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        page = self.pages[self.index]
        await interaction.response.edit_message(
            embed=monk_embed(
                "⚠️ 確認刪除神諭",
                f"即將刪除 **{page['week_label']}** 的這一頁神諭。\\n\\n"
                "刪除後無法復原，也不會退還本週抽取次數。",
                color=0xED4245,
            ),
            view=OracleDeleteConfirmView(
                self.owner_id,
                self.pages,
                self.index,
            ),
        )


class OracleDeleteConfirmView(UserOwnedView):
    def __init__(
        self,
        owner_id: int,
        pages: list[dict[str, Any]],
        index: int,
    ) -> None:
        super().__init__(owner_id, timeout=300)
        self.pages = list(pages)
        self.index = index

    @discord.ui.button(
        label="確認刪除",
        style=discord.ButtonStyle.danger,
        emoji="🗑️",
    )
    async def confirm_delete(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        page = self.pages[self.index]
        deleted = ACADEMY_DB.delete_oracle(
            page_id=int(page["id"]),
            user_id=self.owner_id,
        )
        if not deleted:
            await interaction.response.send_message(
                "找不到這一頁神諭，可能已經被刪除。",
                ephemeral=True,
            )
            return

        self.pages.pop(self.index)
        if not self.pages:
            await interaction.response.edit_message(
                embed=monk_embed(
                    "📖 神諭冊目前是空的",
                    "這一頁已刪除；本週剩餘抽取次數不會因此增加。",
                    color=0x7A5AC8,
                ),
                view=OracleHubView(self.owner_id),
            )
            return

        next_index = min(self.index, len(self.pages) - 1)
        book_view = OracleBookView(
            self.owner_id,
            self.pages,
            index=next_index,
        )
        await interaction.response.edit_message(
            embed=book_view.current_embed(),
            view=book_view,
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
        book_view = OracleBookView(
            self.owner_id,
            self.pages,
            index=self.index,
        )
        await interaction.response.edit_message(
            embed=book_view.current_embed(),
            view=book_view,
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
            "🎓 禊月堂學生資料",
            "目前尚未建立學籍。",
            color=0x5865F2,
        )

    places = ACADEMY_DB.list_user_places(user_id)
    public_places = [
        place for place in places if bool(place.get("is_public"))
    ]
    pages = ACADEMY_DB.list_oracles(user_id)
    current_week = month_week_info()
    current_count = ACADEMY_DB.get_usage_count(
        user_id=user_id,
        usage_scope=ORACLE_USAGE_SCOPE,
        period_key=current_week.key,
    )

    embed = monk_embed(
        f"🎓 學生資料｜{profile.get('preferred_name') or profile.get('student_name') or '未命名學生'}",
        f"**學生姓名**：{profile.get('student_name') or '未填寫'}\n"
        f"**希望稱呼**：{profile.get('preferred_name') or '未填寫'}\n"
        f"**所屬學院**：{profile.get('house') or '尚未分院'}\n"
        f"**主修方向**：{profile.get('major') or '未填寫'}\n"
        f"**入學年份**：{profile.get('enrollment_year') or '未填寫'}\n"
        f"**固定同行者**：{profile.get('companion_name') or '未設定'}",
        color=0x5865F2,
    )

    introduction = _truncate_text(
        profile.get("introduction") or "尚未填寫個人簡介。",
        1024,
    )
    embed.add_field(
        name="個人簡介",
        value=introduction,
        inline=False,
    )
    embed.add_field(
        name="🏘️ 公開地點",
        value=(
            f"公開 **{len(public_places)}** 處｜"
            f"全部登記 **{len(places)}** 處"
        ),
        inline=True,
    )
    embed.add_field(
        name="📖 神諭冊",
        value=(
            f"共有 **{len(pages)}** 頁\n"
            f"本週 `{current_week.label}` 已抽 "
            f"**{current_count}／{SETTINGS.oracle_weekly_limit}** 次"
        ),
        inline=True,
    )
    embed.set_footer(
        text="此學籍為公開展示；修改、刪除與神諭偏好仍只有本人能操作。"
    )
    return embed


def student_preferences_embed(user_id: int) -> discord.Embed:
    preferences = ACADEMY_DB.get_preferences(user_id)
    if preferences is None:
        return monk_embed(
            "🔮 我的神諭偏好",
            "目前尚未設定神諭偏好。\n\n"
            "請從「學生資料」頁面的神諭偏好按鈕補充設定。",
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
        f"{preferences.get('preferred_scenes') or '未設定'}",
        color=0x7A5AC8,
    )


def student_places_embed(user_id: int) -> discord.Embed:
    places = ACADEMY_DB.list_user_places(user_id)
    if not places:
        return monk_embed(
            "🏘️ 我的學院街區地點",
            "目前沒有登記地點。\n\n"
            "請從修士主面板的「城下町」選擇「登記地點」，"
            "或把過去企劃中的店面遷入學院街區。",
            color=0x8B6F47,
        )

    lines: list[str] = []
    for place in places[:15]:
        lines.append(
            f"**#{place['id']}｜{place['name']}**\n"
            f"{place['place_type']}｜{place['status']}｜"
            f"{'公開' if place['is_public'] else '不公開'}｜可進神諭\n"
            f"經營者／居住者："
            f"{place.get('operator_name') or '未設定'}"
        )

    remaining = len(places) - 15
    if remaining > 0:
        lines.append(f"……另有 {remaining} 個地點未顯示。")

    return monk_embed(
        "🏘️ 我的學院街區地點",
        _truncate_text("\n\n".join(lines), 4000),
        color=0x8B6F47,
    )


def public_my_places_embed(user_id: int) -> discord.Embed:
    profile = ACADEMY_DB.get_profile(user_id)
    places = ACADEMY_DB.list_user_places(user_id)
    public_places = [
        place for place in places if bool(place.get("is_public"))
    ]
    private_count = len(places) - len(public_places)
    display_name = (
        (profile or {}).get("preferred_name")
        or (profile or {}).get("student_name")
        or "學生"
    )

    if public_places:
        lines = [
            f"**#{place['id']}｜{place['name']}**\n"
            f"{place['place_type']}｜{place['status']}｜可進神諭\n"
            f"經營者／居住者："
            f"{place.get('operator_name') or display_name}"
            for place in public_places[:12]
        ]
        remaining = len(public_places) - 12
        if remaining > 0:
            lines.append(f"……另有 {remaining} 個公開地點未顯示。")
        public_text = "\n\n".join(lines)
    else:
        public_text = "目前沒有公開地點。"

    embed = monk_embed(
        f"📍 {display_name}的地點",
        _truncate_text(public_text, 3500),
        color=0x8B6F47,
    )
    embed.add_field(
        name="地點統計",
        value=(
            f"公開：**{len(public_places)}** 處\n"
            f"不公開：**{private_count}** 處\n"
            f"合計：**{len(places)}** 處"
        ),
        inline=False,
    )
    embed.set_footer(
        text="不公開地點的名稱不會顯示在這張公開頁面。"
    )
    return embed


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
            embed=public_my_places_embed(self.owner_id),
            view=MyPlacesHubView(
                self.owner_id,
                return_target="student",
            ),
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
            await interaction.response.edit_message(
                embed=monk_embed(
                    "📖 神諭冊目前是空的",
                    "請從主面板的「神諭冊」領取本週神諭。",
                    color=0x7A5AC8,
                ),
                view=self,
            )
            return

        oracle_view = OracleBookView(self.owner_id, pages)
        await interaction.response.edit_message(
            embed=oracle_view.current_embed(),
            view=oracle_view,
        )


class StudentDataPanelView(discord.ui.View):
    """相容舊面板；按下後同樣進入單一訊息工作階段。"""

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

        session = await claim_panel_session(interaction)
        if session is None:
            return

        profile = ACADEMY_DB.get_profile_bundle(interaction.user.id)
        if profile is None:
            await interaction.response.edit_message(
                embed=monk_embed(
                    "🎓 入學登記",
                    "尚未建立學籍。先選擇學院與入學年份，"
                    "再填寫學生資料。",
                    color=0x5865F2,
                ),
                view=EnrollmentSetupView(interaction.user.id),
            )
            return

        await interaction.response.edit_message(
            embed=student_dashboard_embed(interaction.user.id),
            view=StudentHubView(interaction.user.id),
        )



def _component_channel_allowed(interaction: discord.Interaction) -> bool:
    return is_allowed_channel(
        interaction.channel_id,
        SETTINGS.monk_channel_id,
    )


async def _reject_wrong_component_channel(
    interaction: discord.Interaction,
) -> None:
    await interaction.response.send_message(
        f"修士功能面板只能在 <#{SETTINGS.monk_channel_id}> 使用。",
        ephemeral=True,
    )


class EnrollmentSetupView(UserOwnedView):
    def __init__(
        self,
        owner_id: int,
        existing: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(owner_id, timeout=900)
        self.existing = existing or {}
        self.selected_house = self.existing.get("house") or "尚未分院"
        self.selected_year = (
            self.existing.get("enrollment_year")
            or str(date.today().year)
        )

        house_options = [
            discord.SelectOption(
                label=choice.name,
                value=choice.value,
                default=choice.value == self.selected_house,
            )
            for choice in HOUSE_CHOICES
        ]
        self.house_select = discord.ui.Select(
            placeholder="選擇所屬學院",
            min_values=1,
            max_values=1,
            options=house_options,
            row=0,
        )
        self.house_select.callback = self._on_house_selected
        self.add_item(self.house_select)

        year_values = [
            str(year)
            for year in range(date.today().year - 3, date.today().year + 2)
        ]
        if self.selected_year and self.selected_year not in year_values:
            year_values.insert(0, self.selected_year)
        year_values.append("未填寫")

        year_options = [
            discord.SelectOption(
                label=value,
                value="__none__" if value == "未填寫" else value,
                default=(
                    (value == "未填寫" and not self.selected_year)
                    or value == self.selected_year
                ),
            )
            for value in year_values
        ]
        self.year_select = discord.ui.Select(
            placeholder="選擇入學年份",
            min_values=1,
            max_values=1,
            options=year_options,
            row=1,
        )
        self.year_select.callback = self._on_year_selected
        self.add_item(self.year_select)

    async def _on_house_selected(
        self,
        interaction: discord.Interaction,
    ) -> None:
        self.selected_house = self.house_select.values[0]
        await interaction.response.defer()

    async def _on_year_selected(
        self,
        interaction: discord.Interaction,
    ) -> None:
        selected = self.year_select.values[0]
        self.selected_year = "" if selected == "__none__" else selected
        await interaction.response.defer()

    @discord.ui.button(
        label="繼續填寫入學資料",
        style=discord.ButtonStyle.primary,
        emoji="✏️",
        row=2,
    )
    async def continue_enrollment(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.send_modal(
            EnrollmentModal(
                user_id=self.owner_id,
                house=self.selected_house,
                enrollment_year=self.selected_year,
                existing=self.existing,
            )
        )


class StudentHubView(UserOwnedView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id, timeout=900)

    @discord.ui.button(
        label="學籍總覽",
        style=discord.ButtonStyle.primary,
        emoji="🎓",
        row=0,
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
        label="修改學籍",
        style=discord.ButtonStyle.secondary,
        emoji="✏️",
        row=0,
    )
    async def edit_profile(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        existing = ACADEMY_DB.get_profile(self.owner_id)
        if existing is None:
            await interaction.response.edit_message(
                embed=monk_embed(
                    "🎓 入學登記",
                    "請先選擇學院與入學年份，再繼續填寫資料。",
                    color=0x5865F2,
                ),
                view=EnrollmentSetupView(self.owner_id),
            )
            return

        await interaction.response.edit_message(
            embed=monk_embed(
                "✏️ 修改學籍",
                "先確認學院與入學年份，再開啟資料表單。",
                color=0x5865F2,
            ),
            view=EnrollmentSetupView(self.owner_id, existing),
        )

    @discord.ui.button(
        label="神諭偏好",
        style=discord.ButtonStyle.secondary,
        emoji="🔮",
        row=0,
    )
    async def edit_preferences(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        existing = ACADEMY_DB.get_preferences(self.owner_id)
        await interaction.response.send_modal(
            OraclePreferencesModal(self.owner_id, existing)
        )

    @discord.ui.button(
        label="我的地點",
        style=discord.ButtonStyle.secondary,
        emoji="🏘️",
        row=0,
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
        label="新增地點",
        style=discord.ButtonStyle.success,
        emoji="➕",
        row=1,
    )
    async def add_place(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.edit_message(
            embed=monk_embed(
                "🏘️ 新增地點",
                "選擇地點類型與來源，再決定是否公開。"
                "商店登記時可以填寫實際店主或共同經營者。",
                color=0x8B6F47,
            ),
            view=PlaceRegistrationOptionsView(self.owner_id),
        )

    @discord.ui.button(
        label="刪除學籍",
        style=discord.ButtonStyle.danger,
        emoji="🗑️",
        row=1,
    )
    async def delete_profile(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.edit_message(
            content=(
                "這會刪除你的學籍、神諭偏好、個人地點與神諭冊。"
                "確定要繼續嗎？"
            ),
            embed=None,
            view=DeleteProfileView(self.owner_id),
        )


class PlaceRegistrationOptionsView(UserOwnedView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id, timeout=900)
        self.place_type = "商店"
        self.source_kind = "新登記"
        self.is_public = True

        type_options = [
            discord.SelectOption(
                label=choice.name,
                value=choice.value,
                default=choice.value == self.place_type,
            )
            for choice in PLACE_TYPE_CHOICES
        ]
        self.type_select = discord.ui.Select(
            placeholder="選擇地點類型",
            min_values=1,
            max_values=1,
            options=type_options,
            row=0,
        )
        self.type_select.callback = self._on_type_selected
        self.add_item(self.type_select)

        source_options = [
            discord.SelectOption(
                label=choice.name,
                value=choice.value,
                default=choice.value == self.source_kind,
            )
            for choice in PLACE_SOURCE_CHOICES
        ]
        self.source_select = discord.ui.Select(
            placeholder="選擇地點來源",
            min_values=1,
            max_values=1,
            options=source_options,
            row=1,
        )
        self.source_select.callback = self._on_source_selected
        self.add_item(self.source_select)

    async def _on_type_selected(
        self,
        interaction: discord.Interaction,
    ) -> None:
        self.place_type = self.type_select.values[0]
        await interaction.response.defer()

    async def _on_source_selected(
        self,
        interaction: discord.Interaction,
    ) -> None:
        self.source_kind = self.source_select.values[0]
        await interaction.response.defer()

    @discord.ui.button(
        label="公開顯示：是",
        style=discord.ButtonStyle.success,
        emoji="👁️",
        row=2,
    )
    async def toggle_public(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.is_public = not self.is_public
        button.label = f"公開顯示：{'是' if self.is_public else '否'}"
        button.style = (
            discord.ButtonStyle.success
            if self.is_public
            else discord.ButtonStyle.secondary
        )
        await interaction.response.edit_message(view=self)

    @discord.ui.button(
        label="繼續填寫地點資料",
        style=discord.ButtonStyle.primary,
        emoji="✏️",
        row=3,
    )
    async def continue_registration(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if ACADEMY_DB.get_profile(self.owner_id) is None:
            await interaction.response.send_message(
                "請先從主面板的「學生資料」完成入學登記。",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            PlaceModal(
                user_id=self.owner_id,
                place_type=self.place_type,
                source_kind=self.source_kind,
                is_public=self.is_public,
            )
        )




def place_visibility_embed(place: dict[str, Any]) -> discord.Embed:
    visibility = "公開" if place.get("is_public") else "不公開"
    visibility_note = (
        "此地點會出現在其他學生可查看的城下町名單中。"
        if place.get("is_public")
        else "此地點只會保存在你的個人資料中，不會出現在公開名單。"
    )
    return monk_embed(
        f"👁️ 地點公開設定｜{place.get('name', '未命名地點')}",
        f"**類型**：{place.get('place_type') or '未設定'}\n"
        f"**區域**：{place.get('district') or '未設定'}\n"
        f"**目前設定**：{visibility}\n"
        f"**可作神諭素材**：是\n\n"
        f"{visibility_note}",
        color=0x8B6F47,
    )


class PlaceVisibilityEditorView(UserOwnedView):
    def __init__(
        self,
        owner_id: int,
        place: dict[str, Any],
    ) -> None:
        super().__init__(owner_id, timeout=900)
        self.place = place
        self._refresh_button()

    def _refresh_button(self) -> None:
        is_public = bool(self.place.get("is_public"))
        self.toggle_visibility.label = (
            f"公開顯示：{'是' if is_public else '否'}"
        )
        self.toggle_visibility.style = (
            discord.ButtonStyle.success
            if is_public
            else discord.ButtonStyle.secondary
        )

    @discord.ui.button(
        label="公開顯示：是",
        style=discord.ButtonStyle.success,
        emoji="👁️",
    )
    async def toggle_visibility(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        updated = ACADEMY_DB.update_place_visibility(
            user_id=self.owner_id,
            place_id=int(self.place["id"]),
            is_public=not bool(self.place.get("is_public")),
        )
        if updated is None:
            await interaction.response.send_message(
                "找不到這個地點，可能已被刪除。",
                ephemeral=True,
            )
            return

        self.place = updated
        self._refresh_button()
        await interaction.response.edit_message(
            embed=place_visibility_embed(self.place),
            view=self,
        )

    @discord.ui.button(
        label="選擇其他地點",
        style=discord.ButtonStyle.primary,
        emoji="📍",
    )
    async def choose_another(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        places = ACADEMY_DB.list_user_places(self.owner_id)
        if not places:
            await interaction.response.edit_message(
                embed=monk_embed(
                    "👁️ 地點公開設定",
                    "目前沒有可管理的地點。",
                    color=0x8B6F47,
                ),
                view=TownHubView(self.owner_id),
            )
            return

        await interaction.response.edit_message(
            embed=monk_embed(
                "👁️ 地點公開設定",
                "選擇要調整公開狀態的地點。",
                color=0x8B6F47,
            ),
            view=PlaceVisibilityPickerView(self.owner_id, places),
        )


class PlaceVisibilitySelect(discord.ui.Select):
    def __init__(
        self,
        owner_id: int,
        places: list[dict[str, Any]],
    ) -> None:
        self.owner_id = int(owner_id)
        self.places_by_id = {
            str(place["id"]): place for place in places[:25]
        }
        options = [
            discord.SelectOption(
                label=_truncate_text(place["name"], 80),
                value=str(place["id"]),
                description=(
                    f"{place['place_type']}｜"
                    f"{'公開' if place['is_public'] else '不公開'}"
                )[:100],
                emoji="👁️" if place["is_public"] else "🔒",
            )
            for place in places[:25]
        ]
        super().__init__(
            placeholder="選擇要編輯的地點",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        place_id = int(self.values[0])
        place = ACADEMY_DB.get_user_place(
            user_id=self.owner_id,
            place_id=place_id,
        )
        if place is None:
            await interaction.response.send_message(
                "找不到這個地點，請重新開啟公開設定。",
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(
            embed=place_visibility_embed(place),
            view=PlaceVisibilityEditorView(self.owner_id, place),
        )


class PlaceVisibilityPickerView(UserOwnedView):
    def __init__(
        self,
        owner_id: int,
        places: list[dict[str, Any]],
    ) -> None:
        super().__init__(owner_id, timeout=900)
        self.add_item(PlaceVisibilitySelect(owner_id, places))

    @discord.ui.button(
        label="返回城下町",
        style=discord.ButtonStyle.secondary,
        emoji="↩️",
        row=1,
    )
    async def back_to_town(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.edit_message(
            embed=monk_embed(
                "🏘️ 禊月堂城下町",
                "查看公開店鋪與校外住處，或管理自己的地點。",
                color=0x8B6F47,
            ),
            view=TownHubView(self.owner_id),
        )




class MyPlacesHubView(UserOwnedView):
    def __init__(
        self,
        owner_id: int,
        *,
        return_target: str = "town",
    ) -> None:
        super().__init__(owner_id, timeout=900)
        self.return_target = return_target
        self.back_button.label = (
            "返回學生資料"
            if return_target == "student"
            else "返回城下町"
        )

    @discord.ui.button(
        label="新增地點",
        style=discord.ButtonStyle.success,
        emoji="➕",
        row=0,
    )
    async def add_place(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.edit_message(
            embed=monk_embed(
                "🏘️ 新增地點",
                "選擇類型與來源，再決定是否公開。"
                "所有學生地點都能成為自己的神諭素材。",
                color=0x8B6F47,
            ),
            view=PlaceRegistrationOptionsView(self.owner_id),
        )

    @discord.ui.button(
        label="公開設定",
        style=discord.ButtonStyle.secondary,
        emoji="👁️",
        row=0,
    )
    async def visibility_settings(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        places = ACADEMY_DB.list_user_places(self.owner_id)
        if not places:
            await interaction.response.edit_message(
                embed=monk_embed(
                    "👁️ 地點公開設定",
                    "目前沒有可調整的地點，先按「新增地點」。",
                    color=0x8B6F47,
                ),
                view=self,
            )
            return

        note = ""
        if len(places) > 25:
            note = "\n\n目前先顯示前 25 個地點。"

        await interaction.response.edit_message(
            embed=monk_embed(
                "👁️ 地點公開設定",
                "選擇地點後切換公開或不公開。"
                "不公開地點仍可進自己的神諭，但不會顯示給其他玩家。"
                + note,
                color=0x8B6F47,
            ),
            view=PlaceVisibilityPickerView(self.owner_id, places),
        )

    @discord.ui.button(
        label="查看全部（私密）",
        style=discord.ButtonStyle.secondary,
        emoji="🔒",
        row=0,
    )
    async def view_all_private(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.edit_message(
            embed=student_places_embed(self.owner_id),
            view=self,
        )

    @discord.ui.button(
        label="重新整理",
        style=discord.ButtonStyle.primary,
        emoji="🔄",
        row=1,
    )
    async def refresh_places(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.edit_message(
            embed=public_my_places_embed(self.owner_id),
            view=self,
        )

    @discord.ui.button(
        label="返回城下町",
        style=discord.ButtonStyle.secondary,
        emoji="↩️",
        row=1,
    )
    async def back_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if self.return_target == "student":
            await interaction.response.edit_message(
                embed=student_dashboard_embed(self.owner_id),
                view=StudentHubView(self.owner_id),
            )
            return

        await interaction.response.edit_message(
            embed=monk_embed(
                "🏘️ 禊月堂魔法學院城下町",
                "查看學生商店街、校外居住地，"
                "或直接管理自己的店鋪與住所。",
                color=0x8B6F47,
            ),
            view=TownHubView(self.owner_id),
        )


class TownHubView(UserOwnedView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id, timeout=900)

    async def _show_place_list(
        self,
        interaction: discord.Interaction,
        places: list[dict[str, Any]],
        empty_message: str,
    ) -> None:
        if not places:
            await interaction.response.edit_message(
                embed=monk_embed(
                    "🏘️ 城下町",
                    empty_message,
                    color=0x8B6F47,
                ),
                view=TownHubView(self.owner_id),
            )
            return

        view = PlacesView(self.owner_id, places)
        await interaction.response.edit_message(
            embed=view.current_embed(),
            view=view,
        )

    @discord.ui.button(
        label="商店街",
        style=discord.ButtonStyle.success,
        emoji="🛍️",
        row=0,
    )
    async def shops(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        shop_types = {"商店", "餐館", "書店", "魔藥工房", "診所"}
        places = [
            place
            for place in ACADEMY_DB.list_public_places()
            if place["place_type"] in shop_types
        ]
        await self._show_place_list(
            interaction,
            places,
            "城下町目前還沒有公開營業的店鋪。",
        )

    @discord.ui.button(
        label="校外居住地",
        style=discord.ButtonStyle.primary,
        emoji="🏠",
        row=0,
    )
    async def residences(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        places = ACADEMY_DB.list_public_places("校外住處")
        await self._show_place_list(
            interaction,
            places,
            "目前沒有公開的校外居住地。",
        )

    @discord.ui.button(
        label="我的地點",
        style=discord.ButtonStyle.secondary,
        emoji="📍",
        row=0,
    )
    async def my_places(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.edit_message(
            embed=public_my_places_embed(self.owner_id),
            view=MyPlacesHubView(
                self.owner_id,
                return_target="town",
            ),
        )

    @discord.ui.button(
        label="登記地點",
        style=discord.ButtonStyle.secondary,
        emoji="➕",
        row=0,
    )
    async def register_place(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.edit_message(
            embed=monk_embed(
                "🏘️ 城下町｜地點登記",
                "先選擇類型與來源，再決定是否公開。所有學生地點都可作神諭素材。",
                color=0x8B6F47,
            ),
            view=PlaceRegistrationOptionsView(self.owner_id),
        )

    @discord.ui.button(
        label="公開設定",
        style=discord.ButtonStyle.secondary,
        emoji="👁️",
        row=1,
    )
    async def visibility_settings(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        places = ACADEMY_DB.list_user_places(self.owner_id)
        if not places:
            await interaction.response.edit_message(
                embed=monk_embed(
                    "👁️ 地點公開設定",
                    "目前沒有可調整公開狀態的地點。請先登記一個地點。",
                    color=0x8B6F47,
                ),
                view=self,
            )
            return

        note = ""
        if len(places) > 25:
            note = "\n\n目前先顯示前 25 個地點。"

        await interaction.response.edit_message(
            embed=monk_embed(
                "👁️ 地點公開設定",
                "選擇地點後，即可切換公開或不公開。"
                "不公開的地點不會出現在商店街或校外居住地名單。"
                + note,
                color=0x8B6F47,
            ),
            view=PlaceVisibilityPickerView(self.owner_id, places),
        )


async def _send_tutorial(
    interaction: discord.Interaction,
    tutorial_id: str,
    owner_id: int,
) -> None:
    item = KNOWLEDGE.tutorial_by_id.get(tutorial_id)
    if not isinstance(item, dict):
        await interaction.response.edit_message(
            embed=monk_embed(
                "📕 教學無法載入",
                "這份教學目前無法載入。請通知管理員檢查資料檔案。",
                color=0x992D22,
            ),
            view=TeachingHubView(owner_id),
        )
        return

    match = KnowledgeMatch("tutorial", item, item, 100_000)
    await interaction.response.edit_message(
        embed=monk_embed(
            f"📖 修士教學｜{item.get('title', '教學')}",
            render_local_reply(match),
        ),
        view=TeachingHubView(owner_id),
    )


async def _handle_teaching_question(
    interaction: discord.Interaction,
    question: str,
    owner_id: int,
) -> None:
    nickname_reply = gorilla_nickname_reply(question)
    if nickname_reply is not None:
        embed = monk_embed(
            "📚 修士回覆",
            nickname_reply,
            color=0x992D22,
        )
    else:
        refused = boundary_reply(question)
        if refused is not None:
            embed = monk_embed(
                "📚 修士回覆",
                refused,
                color=0x992D22,
            )
        else:
            local_result = await answer_question(KNOWLEDGE, question)
            if local_result.match is not None:
                match = local_result.match
                embed = monk_embed(
                    f"📚 {match.tutorial['title']}",
                    render_local_reply(
                        match,
                        concise=True,
                        gentle=is_emotional_distress(question),
                    ),
                    color=0x3BA55D,
                )
            else:
                description = (
                    f"{random_line('unknown_question', '「紀錄本裡沒有這題。」')}\n\n"
                    f"{NO_OFFICIAL_DATA}\n\n"
                    "教學查詢只使用本地正式知識庫，不會呼叫 AI。"
                    "請查看最新公告或詢問管理員。"
                )
                embed = monk_embed(
                    "📕 修士查不到答案",
                    description,
                    color=0x992D22,
                )

    await edit_active_panel_from_modal(
        interaction,
        owner_id=owner_id,
        embed=embed,
        view=TeachingHubView(owner_id),
    )


class TeachingQuestionModal(discord.ui.Modal, title="向赤木學長詢問教學"):
    question = discord.ui.TextInput(
        label="想查詢的遊戲問題",
        style=discord.TextStyle.paragraph,
        placeholder="例如：我剛加入，應該先上課還是探索？",
        required=True,
        min_length=2,
        max_length=300,
    )

    def __init__(self, owner_id: int) -> None:
        super().__init__()
        self.owner_id = int(owner_id)

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await _handle_teaching_question(
            interaction,
            str(self.question.value),
            self.owner_id,
        )


class TutorialSelect(discord.ui.Select):
    def __init__(self) -> None:
        options = [
            discord.SelectOption(
                label=str(item["title"])[:100],
                value=str(item["id"]),
                description=str(item.get("summary", ""))[:100] or None,
            )
            for item in KNOWLEDGE.tutorials[:25]
        ]
        super().__init__(
            placeholder="選擇一項教學主題",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ) -> None:
        owner_id = getattr(self.view, "owner_id", interaction.user.id)
        await _send_tutorial(
            interaction,
            self.values[0],
            owner_id,
        )


class TeachingHubView(UserOwnedView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id, timeout=900)
        self.add_item(TutorialSelect())

    @discord.ui.button(
        label="輸入問題查詢",
        style=discord.ButtonStyle.primary,
        emoji="🔎",
        row=1,
    )
    async def ask_question(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.send_modal(
            TeachingQuestionModal(self.owner_id)
        )


async def _handle_confession(
    interaction: discord.Interaction,
    content: str,
) -> None:
    session = active_session_for_owner(interaction.user.id)

    nickname_reply = gorilla_nickname_reply(content)
    if nickname_reply is not None:
        await interaction.response.send_message(
            nickname_reply,
            ephemeral=True,
        )
        if session is not None:
            clear_panel_session(session)
        return

    refused = confession_boundary_reply(content)
    if refused is not None:
        await interaction.response.send_message(
            refused,
            ephemeral=True,
        )
        if session is not None:
            clear_panel_session(session)
        return

    if is_emotional_distress(content):
        opening = "可以慢慢說，先講現在最需要處理的部分。"
        verdict = "先處理眼前能做的一步；需要時，也可以找信任的人一起整理。"
    else:
        opening = random_line(
            "confession_opening",
            "請說重點，我會聽完。",
        )
        verdict = random_line(
            "confession_verdict",
            "內容收到。接著把能修正的部分做好。",
        )

    local_description = (
        f"{opening}\n\n"
        f"> {discord.utils.escape_markdown(content)}\n\n"
        f"{verdict}\n\n"
        "⚠️ **目前是試行版告解**\n"
        "這項功能只進行角色陪伴，尚未連接神父的正式玩家資料，"
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
        if session is not None:
            clear_panel_session(session)
        return

    usage_date = taipei_today().isoformat()
    reserved_usage = ACADEMY_DB.try_reserve_usage(
        user_id=interaction.user.id,
        usage_scope=CONFESSION_USAGE_SCOPE,
        period_key=usage_date,
        limit=SETTINGS.ai_daily_limit,
    )
    if reserved_usage is None:
        await interaction.response.send_message(
            embed=monk_embed(
                "🕯️ 修士告解室｜本地回覆",
                f"{local_description}\n\n"
                f"_今日告解已達 {SETTINGS.ai_daily_limit} 次上限，"
                "先由本地修士回覆。_",
                color=0x111111,
            ),
            ephemeral=True,
        )
        if session is not None:
            clear_panel_session(session)
        return

    await interaction.response.defer(
        thinking=True,
        ephemeral=True,
    )

    try:
        ai_reply = await ask_openai_confession(
            content,
            interaction.user.id,
            interaction.user.display_name,
        )
    except Exception:
        ACADEMY_DB.release_usage(
            user_id=interaction.user.id,
            usage_scope=CONFESSION_USAGE_SCOPE,
            period_key=usage_date,
        )
        logger.exception("OpenAI API 告解回覆失敗")
        await interaction.followup.send(
            embed=monk_embed(
                "🕯️ 修士告解室｜本地回覆",
                f"{local_description}\n\n"
                "_AI 暫時無法回覆；告解內容未寫入玩家資料。_",
                color=0xFAA61A,
            ),
            ephemeral=True,
        )
        if session is not None:
            clear_panel_session(session)
        return

    remaining = max(
        0,
        SETTINGS.ai_daily_limit - reserved_usage,
    )
    description = (
        f"{ai_reply}\n\n"
        "⚠️ **告解陪伴不會修改罪惡值或玩家資料。**\n"
        "正式罪惡值處理仍請使用神父的告解功能。\n\n"
        f"_AI 一次性回覆｜今日 AI 使用剩餘：{remaining}_"
    )

    await interaction.followup.send(
        embed=monk_embed(
            "🕯️ 修士告解室｜AI 回覆",
            description,
            color=0x111111,
        ),
        ephemeral=True,
    )
    if session is not None:
        clear_panel_session(session)


class ConfessionModal(discord.ui.Modal, title="禊月堂修士告解室"):
    content = discord.ui.TextInput(
        label="告解內容",
        style=discord.TextStyle.paragraph,
        placeholder="簡短寫下你想整理或坦白的事情。",
        required=True,
        min_length=2,
        max_length=1000,
    )

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await _handle_confession(
            interaction,
            str(self.content.value),
        )


async def _handle_current_week_oracle(
    interaction: discord.Interaction,
) -> None:
    profile = ACADEMY_DB.get_profile_bundle(interaction.user.id)
    if profile is None:
        await interaction.response.edit_message(
            embed=monk_embed(
                "📖 尚未建立學籍",
                "請先返回修士面板，從「學生資料」完成入學登記。",
                color=0x7A5AC8,
            ),
            view=OracleHubView(interaction.user.id),
        )
        return

    if openai_client is None or not SETTINGS.oracle_ai_available:
        await interaction.response.edit_message(
            embed=monk_embed(
                "📖 AI 神諭尚未啟用",
                "請管理員確認 `AI_ORACLE_ENABLED=true` 與 API Key。",
                color=0x7A5AC8,
            ),
            view=OracleHubView(interaction.user.id),
        )
        return

    week = month_week_info()
    reserved_draw = ACADEMY_DB.try_reserve_usage(
        user_id=interaction.user.id,
        usage_scope=ORACLE_USAGE_SCOPE,
        period_key=week.key,
        limit=SETTINGS.oracle_weekly_limit,
    )
    if reserved_draw is None:
        await interaction.response.edit_message(
            embed=monk_embed(
                "📖 本週神諭已抽完",
                f"`{week.label}` 每位學生最多抽取 "
                f"**{SETTINGS.oracle_weekly_limit} 次**。\n\n"
                "刪除神諭只會整理神諭冊，不會退還抽取次數。",
                color=0x7A5AC8,
            ),
            view=OracleHubView(interaction.user.id),
        )
        return

    draw_number = reserved_draw
    selection_key = f"{week.key}:draw:{draw_number}"

    await interaction.response.edit_message(
        embed=monk_embed(
            "📖 神諭生成中",
            "赤木修士正在整理本週素材。請稍候。",
            color=0x7A5AC8,
        ),
        view=None,
    )

    preferences = profile.get("preferences", {})
    all_places = ACADEMY_DB.list_oracle_places(
        interaction.user.id
    )
    weekly_keywords = select_weekly_keywords(
        user_id=interaction.user.id,
        week_key=selection_key,
        creative_keywords=preferences.get(
            "creative_keywords",
            "",
        ),
        liked_themes=preferences.get("liked_themes", ""),
        preferred_scenes=preferences.get(
            "preferred_scenes",
            "",
        ),
    )
    weekly_places = select_weekly_places(
        user_id=interaction.user.id,
        week_key=selection_key,
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
        ACADEMY_DB.release_usage(
            user_id=interaction.user.id,
            usage_scope=ORACLE_USAGE_SCOPE,
            period_key=week.key,
        )
        logger.exception("OpenAI API 神諭生成失敗")
        await interaction.edit_original_response(
            embed=monk_embed(
                "📖 神諭生成失敗",
                "神諭生成失敗。請稍後再試，或請管理員查看 Railway 紀錄。",
                color=0xED4245,
            ),
            view=OracleHubView(interaction.user.id),
        )
        return

    new_page = ACADEMY_DB.create_oracle(
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
        i
        for i, page in enumerate(pages)
        if page["id"] == new_page["id"]
    )
    view = OracleBookView(
        interaction.user.id,
        pages,
        index=index,
    )
    await interaction.edit_original_response(
        embed=view.current_embed(),
        view=view,
    )


class OracleHubView(UserOwnedView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id, timeout=900)

    @discord.ui.button(
        label="抽取新神諭",
        style=discord.ButtonStyle.primary,
        emoji="✨",
        row=0,
    )
    async def current_week(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await _handle_current_week_oracle(interaction)

    @discord.ui.button(
        label="開啟神諭冊",
        style=discord.ButtonStyle.success,
        emoji="📖",
        row=0,
    )
    async def open_book(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        pages = ACADEMY_DB.list_oracles(self.owner_id)
        if not pages:
            await interaction.response.edit_message(
                embed=monk_embed(
                    "📖 神諭冊目前是空的",
                    "請先按「抽取新神諭」建立第一頁。",
                    color=0x7A5AC8,
                ),
                view=OracleHubView(self.owner_id),
            )
            return

        view = OracleBookView(self.owner_id, pages)
        await interaction.response.edit_message(
            embed=view.current_embed(),
            view=view,
        )


class MonkMainPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if not _component_channel_allowed(interaction):
            await _reject_wrong_component_channel(interaction)
            return False
        return True

    @discord.ui.button(
        label="學生資料",
        style=discord.ButtonStyle.primary,
        emoji="🎓",
        custom_id="stern_monk:main:student",
        row=0,
    )
    async def student_data(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        session = await claim_panel_session(interaction)
        if session is None:
            return

        profile = ACADEMY_DB.get_profile_bundle(
            interaction.user.id
        )
        if profile is None:
            await interaction.response.edit_message(
                embed=monk_embed(
                    "🎓 入學登記",
                    "尚未建立學籍。先選擇學院與入學年份，"
                    "再填寫學生資料。",
                    color=0x5865F2,
                ),
                view=EnrollmentSetupView(interaction.user.id),
            )
            return

        await interaction.response.edit_message(
            embed=student_dashboard_embed(interaction.user.id),
            view=StudentHubView(interaction.user.id),
        )

    @discord.ui.button(
        label="城下町",
        style=discord.ButtonStyle.success,
        emoji="🏘️",
        custom_id="stern_monk:main:town",
        row=0,
    )
    async def town(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        session = await claim_panel_session(interaction)
        if session is None:
            return

        await interaction.response.edit_message(
            embed=monk_embed(
                "🏘️ 禊月堂魔法學院城下町",
                "查看公開商店街與校外居住地，"
                "或管理你自己的店鋪與住所。",
                color=0x8B6F47,
            ),
            view=TownHubView(interaction.user.id),
        )

    @discord.ui.button(
        label="神諭冊",
        style=discord.ButtonStyle.primary,
        emoji="📖",
        custom_id="stern_monk:main:oracle",
        row=0,
    )
    async def oracle(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        session = await claim_panel_session(interaction)
        if session is None:
            return

        await interaction.response.edit_message(
            embed=monk_embed(
                "📖 禊月堂個人神諭冊",
                "每位學生每週可抽 3 次神諭，並翻閱、標記或刪除頁面。",
                color=0x7A5AC8,
            ),
            view=OracleHubView(interaction.user.id),
        )

    @discord.ui.button(
        label="教學",
        style=discord.ButtonStyle.secondary,
        emoji="📚",
        custom_id="stern_monk:main:teaching",
        row=1,
    )
    async def teaching(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        session = await claim_panel_session(interaction)
        if session is None:
            return

        await interaction.response.edit_message(
            embed=monk_embed(
                "📚 赤木學長教學櫃臺",
                "從選單挑選正式教學，"
                "或按下「輸入問題查詢」搜尋本地 FAQ。",
                color=0x3BA55D,
            ),
            view=TeachingHubView(interaction.user.id),
        )

    @discord.ui.button(
        label="告解",
        style=discord.ButtonStyle.secondary,
        emoji="🕯️",
        custom_id="stern_monk:main:confession",
        row=1,
    )
    async def confession(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        session = await claim_panel_session(interaction)
        if session is None:
            return

        await interaction.response.send_modal(
            ConfessionModal()
        )


@tree.command(
    name="建立修士面板",
    description="由管理員建立固定的修士功能面板",
)
@app_commands.default_permissions(manage_guild=True)
async def create_monk_panel(
    interaction: discord.Interaction,
) -> None:
    permissions = getattr(
        interaction.user,
        "guild_permissions",
        None,
    )
    if permissions is None or not permissions.manage_guild:
        await interaction.response.send_message(
            "只有具有「管理伺服器」權限的管理員能建立面板。",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        embed=main_panel_embed(),
        view=MonkMainPanelView(),
    )


@tree.command(
    name="修士狀態",
    description="由管理員確認修士服務是否正常",
)
@app_commands.default_permissions(manage_guild=True)
async def monk_status(
    interaction: discord.Interaction,
) -> None:
    confession_ai_status = (
        "已啟用"
        if SETTINGS.confession_ai_available
        else "未啟用"
    )
    oracle_ai_status = (
        "已啟用"
        if SETTINGS.oracle_ai_available
        else "未啟用"
    )
    await interaction.response.send_message(
        "修士目前在線。\n\n"
        "玩家操作方式：**固定功能面板**\n"
        "公開斜線指令數量：**2**\n"
        "AI 教學：**永久停用**\n"
        f"AI 告解：**{confession_ai_status}**\n"
        f"AI 神諭：**{oracle_ai_status}**\n"
        "學籍資料庫：**已啟用**\n"
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

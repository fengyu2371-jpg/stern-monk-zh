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
from persona import boundary_reply, confession_boundary_reply, is_emotional_distress


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SETTINGS = Settings.from_env()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("stern-monk")


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

openai_client: AsyncOpenAI | None = None
if SETTINGS.confession_ai_available:
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
            activity=discord.Game(name="監督學生閱讀教學"),
        )
        logger.info("修士已上線：%s（%s）", self.user, self.user.id)
        logger.info(
            "AI 教學：永久停用｜AI 告解：%s｜模型：%s｜每日每人上限：%s",
            "啟用" if SETTINGS.confession_ai_available else "停用",
            SETTINGS.openai_model,
            SETTINGS.ai_daily_limit,
        )
        logger.info("修士允許回覆頻道：%s", SETTINGS.monk_channel_id)


client = MonkClient()
tree = client.tree


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
            "修士皺起眉。\n\n「這份教學不見了。去叫管理員檢查資料檔案。」",
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
    await interaction.response.send_message(
        "修士目前在線，教學與規則查詢可正常使用。\n\n"
        "AI 教學：**永久停用**\n"
        f"AI 告解：**{confession_ai_status}**\n"
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
        message = f"指令冷卻中，請在 **{seconds} 秒**後再問。紀錄本也需要翻頁時間。"
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

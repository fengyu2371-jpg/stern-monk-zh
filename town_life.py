from __future__ import annotations

import random
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


TAIPEI_TIMEZONE = ZoneInfo("Asia/Taipei")
STAMINA_RESET_HOUR = 0
MAX_TOOL_LEVEL = 5
MAX_CAREER_LEVEL = 5
INITIAL_COINS = 600
INITIAL_STAMINA = 1000
MAX_STAMINA = 1000
INITIAL_SPIRIT = 100
MAX_SPIRIT = 100
MAX_DAILY_FOOD_STAMINA = 600
STAMINA_RECOVERY_PER_MINUTE = 1
TOOL_UPGRADE_SPIRIT_COSTS = (0, 0, 3, 5, 8)
MAINTENANCE_MAIL_KEY = "maintenance_2026_07_29_stamina_potion"
MAINTENANCE_MAIL_TITLE = "城下町維護補償"
MAINTENANCE_MAIL_BODY = "感謝等待本次城下町維護，請收下專用體力藥水。"
MAINTENANCE_MAIL_ITEM_KEY = "maintenance_stamina_potion"
MAINTENANCE_MAIL_QUANTITY = 1


class TownLifeError(RuntimeError):
    """城下町生活職業操作無法完成。"""


TOOL_CONFIG: dict[str, dict[str, Any]] = {
    "farm_tools": {
        "name": "農具組",
        "route": "農牧師",
        "workshop": "農牧工坊",
        "costs": [180, 450, 900, 1800, 3600],
        "materials": [
            {},
            {"stone": 6, "copper_ore": 2},
            {"branch": 8, "iron_ore": 3},
            {"iron_ore": 6, "raw_crystal": 2},
            {"iron_ore": 10, "refined_crystal": 2},
        ],
        "description": "用於播種與收成；等級越高，農作物收成越多。",
    },
    "fishing_rod": {
        "name": "釣具組",
        "route": "漁採師",
        "workshop": "河岸工坊",
        "costs": [220, 500, 1000, 2000, 4000],
        "materials": [
            {},
            {"branch": 6, "copper_ore": 2},
            {"branch": 10, "iron_ore": 3},
            {"iron_ore": 5, "raw_crystal": 2},
            {"iron_ore": 8, "refined_crystal": 2},
        ],
        "description": "用於河岸釣魚；等級越高，稀有魚獲機率越高。",
    },
    "pickaxe": {
        "name": "挖礦工具",
        "route": "魔晶礦師",
        "workshop": "礦坑工坊",
        "costs": [260, 600, 1200, 2400, 4800],
        "materials": [
            {},
            {"stone": 10, "copper_ore": 3},
            {"copper_ore": 8, "iron_ore": 4},
            {"iron_ore": 8, "raw_crystal": 2},
            {"iron_ore": 12, "refined_crystal": 2},
        ],
        "description": "用於礦坑採掘；二級起可進入更深礦層。",
    },
}

CAREER_CONFIG: dict[str, dict[str, Any]] = {
    "farming": {
        "name": "農牧師",
        "description": "耕作、畜牧與農產品採收。",
    },
    "fishing": {
        "name": "漁採師",
        "description": "釣魚、河岸採集與野外素材。",
    },
    "crystal": {
        "name": "魔晶礦師",
        "description": "採礦、魔晶發掘與水晶精煉。",
    },
}

CAREER_LEVEL_THRESHOLDS = {
    1: 0,
    2: 50,
    3: 150,
    4: 350,
    5: 700,
}

CROP_CONFIG: dict[str, dict[str, Any]] = {
    "wheat": {
        "name": "小麥",
        "seed": "wheat_seed",
        "product": "wheat",
        "growth_minutes": 15,
        "base_yield": 2,
        "exp": 8,
    },
    "carrot": {
        "name": "胡蘿蔔",
        "seed": "carrot_seed",
        "product": "carrot",
        "growth_minutes": 30,
        "base_yield": 2,
        "exp": 12,
    },
    "moon_herb": {
        "name": "月光草",
        "seed": "moon_herb_seed",
        "product": "moon_herb",
        "growth_minutes": 60,
        "base_yield": 1,
        "exp": 20,
    },
}

ANIMAL_CONFIG: dict[str, dict[str, Any]] = {
    "chicken": {
        "name": "雞",
        "cost": 600,
        "required_tool_level": 1,
        "product": "egg",
        "product_name": "雞蛋",
        "career_exp": 10,
    },
    "cow": {
        "name": "牛",
        "cost": 1500,
        "required_tool_level": 2,
        "product": "milk",
        "product_name": "牛奶",
        "career_exp": 18,
    },
}

MINING_AREA_CONFIG: dict[str, dict[str, Any]] = {
    "outer_tunnel": {
        "name": "外圍礦道",
        "description": "礦層穩定，適合剛取得挖礦工具的礦師。以石材與銅礦為主。",
        "required_tool_level": 1,
        "required_career_level": 1,
        "base_stamina_cost": 12,
        "minimum_stamina_cost": 8,
        "spirit_cost": 0,
        "items": ["stone", "copper_ore", "iron_ore"],
        "weights": [65, 30, 5],
        "base_exp": 7,
    },
    "iron_depths": {
        "name": "深層鐵脈",
        "description": "岩層較硬，鐵礦密度更高，也可能找到少量魔法水晶原礦。",
        "required_tool_level": 2,
        "required_career_level": 2,
        "base_stamina_cost": 18,
        "minimum_stamina_cost": 14,
        "spirit_cost": 2,
        "items": ["stone", "copper_ore", "iron_ore", "raw_crystal"],
        "weights": [20, 35, 35, 10],
        "base_exp": 12,
    },
    "crystal_cavern": {
        "name": "魔晶洞窟",
        "description": "高級礦坑最深處，魔力濃度高，水晶原礦機率最高。",
        "required_tool_level": 4,
        "required_career_level": 3,
        "base_stamina_cost": 28,
        "minimum_stamina_cost": 22,
        "spirit_cost": 4,
        "items": ["copper_ore", "iron_ore", "raw_crystal"],
        "weights": [15, 30, 55],
        "base_exp": 20,
    },
}


ITEM_CONFIG: dict[str, dict[str, Any]] = {
    # 商店物資
    "wheat_seed": {"name": "小麥種子", "buy": 10, "sell": 0, "category": "seed"},
    "carrot_seed": {"name": "胡蘿蔔種子", "buy": 20, "sell": 0, "category": "seed"},
    "moon_herb_seed": {"name": "月光草種子", "buy": 60, "sell": 0, "category": "seed"},
    "animal_feed": {"name": "飼料", "buy": 15, "sell": 0, "category": "supply"},
    # 農牧
    "wheat": {"name": "小麥", "sell": 30, "category": "farming"},
    "carrot": {"name": "胡蘿蔔", "sell": 45, "category": "farming"},
    "moon_herb": {"name": "月光草", "sell": 100, "category": "farming"},
    "egg": {"name": "雞蛋", "sell": 45, "category": "farming"},
    "milk": {"name": "牛奶", "sell": 110, "category": "farming"},
    # 漁採
    "river_fish": {"name": "河魚", "sell": 55, "category": "fishing"},
    "silver_carp": {"name": "銀鱗鯉", "sell": 100, "category": "fishing"},
    "moon_trout": {"name": "月光鱒", "sell": 220, "category": "fishing"},
    "old_boot": {"name": "泡水舊靴", "sell": 2, "category": "fishing"},
    "wild_berry": {"name": "野莓", "sell": 25, "category": "fishing"},
    "wild_herb": {"name": "野生藥草", "sell": 40, "category": "fishing"},
    "branch": {"name": "硬木枝", "sell": 30, "category": "fishing"},
    # 礦晶
    "stone": {"name": "石材", "sell": 18, "category": "crystal"},
    "copper_ore": {"name": "銅礦", "sell": 65, "category": "crystal"},
    "iron_ore": {"name": "鐵礦", "sell": 120, "category": "crystal"},
    "raw_crystal": {"name": "魔法水晶原礦", "sell": 200, "category": "crystal"},
    "refined_crystal": {"name": "精煉魔法水晶", "sell": 560, "category": "crystal"},
    # 料理（不可直接出售，用於恢復精神力）
    "grilled_fish": {"name": "炭烤河魚", "sell": 0, "category": "food"},
    "carrot_soup": {"name": "胡蘿蔔濃湯", "sell": 0, "category": "food"},
    "farm_breakfast": {"name": "農家早餐", "sell": 0, "category": "food"},
    "moon_trout_steak": {"name": "香煎月光鱒", "sell": 0, "category": "food"},
    "berry_plate": {"name": "野莓果盤", "sell": 0, "category": "food"},
    "roasted_carrot": {"name": "烤胡蘿蔔", "sell": 0, "category": "food"},
    "boiled_egg": {"name": "水煮蛋", "sell": 0, "category": "food"},
    "wheat_bread": {"name": "小麥麵包", "sell": 0, "category": "food"},
    "herb_soup": {"name": "野菜湯", "sell": 0, "category": "food"},
    "milk_egg_stew": {"name": "牛奶燉蛋", "sell": 0, "category": "food"},
    "silver_carp_steak": {"name": "香煎銀鱗鯉", "sell": 0, "category": "food"},
    "moon_trout_platter": {"name": "月光鱒套餐", "sell": 0, "category": "food"},
    "stamina_potion": {"name": "體力藥水", "buy": 250, "sell": 0, "category": "other"},
    "maintenance_stamina_potion": {
        "name": "維護補償體力藥水",
        "sell": 0,
        "category": "other",
    },
}


UPGRADE_MATERIAL_KEYS = {
    "branch",
    "stone",
    "copper_ore",
    "iron_ore",
    "raw_crystal",
    "refined_crystal",
}


# 取得機率較低，或需要高階採集／精煉才能取得的素材。
# 這份名單和升級素材名單刻意分開：同一項物品可以同時屬於兩者。
RARE_MATERIAL_KEYS = {
    "silver_carp",
    "moon_trout",
    "raw_crystal",
    "refined_crystal",
}


FOOD_RECIPE_CONFIG: dict[str, dict[str, Any]] = {
    "grilled_fish": {
        "name": "炭烤河魚",
        "route": "stove",
        "ingredients": {"river_fish": 1, "branch": 1},
        "spirit_restore": 12,
        "stamina_restore": 90,
    },
    "carrot_soup": {
        "name": "胡蘿蔔濃湯",
        "route": "stove",
        "ingredients": {"carrot": 2, "milk": 1},
        "spirit_restore": 25,
        "stamina_restore": 100,
    },
    "farm_breakfast": {
        "name": "農家早餐",
        "route": "stove",
        "ingredients": {"wheat": 1, "egg": 2, "milk": 1},
        "spirit_restore": 40,
        "stamina_restore": 180,
    },
    "moon_trout_steak": {
        "name": "香煎月光鱒",
        "route": "stove",
        "ingredients": {"moon_trout": 1, "moon_herb": 1},
        "spirit_restore": 50,
        "stamina_restore": 250,
    },
    "berry_plate": {
        "name": "野莓果盤",
        "route": "stove",
        "ingredients": {"wild_berry": 2},
        "spirit_restore": 5,
        "stamina_restore": 40,
    },
    "roasted_carrot": {
        "name": "烤胡蘿蔔",
        "route": "stove",
        "ingredients": {"carrot": 2},
        "spirit_restore": 8,
        "stamina_restore": 60,
    },
    "boiled_egg": {
        "name": "水煮蛋",
        "route": "stove",
        "ingredients": {"egg": 1},
        "spirit_restore": 8,
        "stamina_restore": 70,
    },
    "wheat_bread": {
        "name": "小麥麵包",
        "route": "stove",
        "ingredients": {"wheat": 3},
        "spirit_restore": 10,
        "stamina_restore": 120,
    },
    "herb_soup": {
        "name": "野菜湯",
        "route": "stove",
        "ingredients": {"carrot": 1, "wild_herb": 1},
        "spirit_restore": 18,
        "stamina_restore": 140,
    },
    "milk_egg_stew": {
        "name": "牛奶燉蛋",
        "route": "stove",
        "ingredients": {"egg": 2, "milk": 1},
        "spirit_restore": 25,
        "stamina_restore": 180,
    },
    "silver_carp_steak": {
        "name": "香煎銀鱗鯉",
        "route": "stove",
        "ingredients": {"silver_carp": 1, "wild_herb": 1},
        "spirit_restore": 35,
        "stamina_restore": 260,
    },
    "moon_trout_platter": {
        "name": "月光鱒套餐",
        "route": "stove",
        "ingredients": {"moon_trout": 1, "moon_herb": 1},
        "spirit_restore": 50,
        "stamina_restore": 350,
    },
}


POTION_CONFIG: dict[str, dict[str, Any]] = {
    "stamina_potion": {
        "name": "體力藥水",
        "stamina_restore": 250,
    },
    "maintenance_stamina_potion": {
        "name": "維護補償體力藥水",
        "stamina_restore": 250,
    },
}


def taipei_now() -> datetime:
    return datetime.now(TAIPEI_TIMEZONE)


def now_iso() -> str:
    return taipei_now().isoformat(timespec="seconds")


def today_key() -> str:
    return taipei_now().date().isoformat()


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TAIPEI_TIMEZONE)
    return parsed.astimezone(TAIPEI_TIMEZONE)


def career_level_for_exp(exp: int) -> int:
    level = 1
    for candidate, threshold in CAREER_LEVEL_THRESHOLDS.items():
        if int(exp) >= threshold:
            level = candidate
    return min(MAX_CAREER_LEVEL, level)


def item_name(item_key: str) -> str:
    return str(ITEM_CONFIG.get(item_key, {}).get("name") or item_key)


def tool_name(tool_key: str) -> str:
    return str(TOOL_CONFIG.get(tool_key, {}).get("name") or tool_key)


def format_item_requirements(requirements: dict[str, int]) -> str:
    if not requirements:
        return "不需要素材"
    return "、".join(
        f"{item_name(key)}×{int(quantity)}"
        for key, quantity in requirements.items()
    )


def format_remaining(ready_at: str) -> str:
    remaining = parse_time(ready_at) - taipei_now()
    if remaining.total_seconds() <= 0:
        return "可收成"
    minutes = max(1, int((remaining.total_seconds() + 59) // 60))
    if minutes >= 60:
        hours, rest = divmod(minutes, 60)
        return f"約 {hours} 小時 {rest} 分"
    return f"約 {minutes} 分鐘"


class TownLifeDatabase:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 15000;")
        return conn

    def initialize(self) -> None:
        with closing(self.connect()) as conn:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS town_life_players (
                    user_id TEXT PRIMARY KEY,
                    coins INTEGER NOT NULL DEFAULT 600,
                    stamina INTEGER NOT NULL DEFAULT 1000,
                    max_stamina INTEGER NOT NULL DEFAULT 1000,
                    spirit INTEGER NOT NULL DEFAULT 100,
                    max_spirit INTEGER NOT NULL DEFAULT 100,
                    stamina_updated_at TEXT NOT NULL,
                    daily_resource_reset_date TEXT NOT NULL DEFAULT '',
                    last_rest_date TEXT NOT NULL DEFAULT '',
                    food_stamina_date TEXT NOT NULL DEFAULT '',
                    food_stamina_recovered INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS town_life_tools (
                    user_id TEXT NOT NULL,
                    tool_key TEXT NOT NULL,
                    level INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, tool_key)
                );

                CREATE TABLE IF NOT EXISTS town_life_careers (
                    user_id TEXT NOT NULL,
                    career_key TEXT NOT NULL,
                    level INTEGER NOT NULL DEFAULT 1,
                    exp INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, career_key)
                );

                CREATE TABLE IF NOT EXISTS town_life_inventory (
                    user_id TEXT NOT NULL,
                    item_key TEXT NOT NULL,
                    quantity INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, item_key)
                );

                CREATE TABLE IF NOT EXISTS town_life_plots (
                    user_id TEXT NOT NULL,
                    plot_no INTEGER NOT NULL,
                    crop_key TEXT NOT NULL DEFAULT '',
                    planted_at TEXT NOT NULL DEFAULT '',
                    ready_at TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (user_id, plot_no)
                );

                CREATE TABLE IF NOT EXISTS town_life_animals (
                    user_id TEXT NOT NULL,
                    animal_key TEXT NOT NULL,
                    quantity INTEGER NOT NULL DEFAULT 0,
                    last_collect_date TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, animal_key)
                );

                CREATE TABLE IF NOT EXISTS town_life_mailbox (
                    user_id TEXT NOT NULL,
                    mail_key TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    item_key TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    claimed_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, mail_key)
                );

                CREATE TABLE IF NOT EXISTS town_life_system_markers (
                    marker_key TEXT PRIMARY KEY,
                    marker_value TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_town_life_inventory_user
                ON town_life_inventory(user_id);

                CREATE INDEX IF NOT EXISTS idx_town_life_mailbox_user_claimed
                ON town_life_mailbox(user_id, claimed_at);
                """
            )

            # 舊資料庫安全新增精神力欄位，不重建玩家資料表。
            player_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(town_life_players)").fetchall()
            }
            if "spirit" not in player_columns:
                conn.execute(
                    f"ALTER TABLE town_life_players ADD COLUMN spirit INTEGER NOT NULL DEFAULT {INITIAL_SPIRIT}"
                )
            if "max_spirit" not in player_columns:
                conn.execute(
                    f"ALTER TABLE town_life_players ADD COLUMN max_spirit INTEGER NOT NULL DEFAULT {MAX_SPIRIT}"
                )
            if "food_stamina_date" not in player_columns:
                conn.execute(
                    "ALTER TABLE town_life_players ADD COLUMN food_stamina_date TEXT NOT NULL DEFAULT ''"
                )
            if "food_stamina_recovered" not in player_columns:
                conn.execute(
                    "ALTER TABLE town_life_players ADD COLUMN food_stamina_recovered INTEGER NOT NULL DEFAULT 0"
                )
            if "daily_resource_reset_date" not in player_columns:
                conn.execute(
                    "ALTER TABLE town_life_players ADD COLUMN daily_resource_reset_date TEXT NOT NULL DEFAULT ''"
                )
                # 沿用舊體力時間作為首次每日重置標記。若該時間仍停在
                # 昨日，玩家下次操作時會立即取得今天的體力與精神力。
                conn.execute(
                    """
                    UPDATE town_life_players
                    SET daily_resource_reset_date = SUBSTR(stamina_updated_at, 1, 10)
                    WHERE daily_resource_reset_date = ''
                    """
                )

            required_schema = {
                "town_life_players": {
                    "user_id", "coins", "stamina", "max_stamina",
                    "spirit", "max_spirit", "stamina_updated_at",
                    "daily_resource_reset_date",
                    "last_rest_date", "food_stamina_date",
                    "food_stamina_recovered", "created_at", "updated_at",
                },
                "town_life_tools": {
                    "user_id", "tool_key", "level", "updated_at",
                },
                "town_life_careers": {
                    "user_id", "career_key", "level", "exp", "updated_at",
                },
                "town_life_inventory": {
                    "user_id", "item_key", "quantity", "updated_at",
                },
                "town_life_plots": {
                    "user_id", "plot_no", "crop_key", "planted_at",
                    "ready_at",
                },
                "town_life_animals": {
                    "user_id", "animal_key", "quantity",
                    "last_collect_date", "updated_at",
                },
                "town_life_mailbox": {
                    "user_id", "mail_key", "title", "body", "item_key",
                    "quantity", "claimed_at", "created_at",
                },
                "town_life_system_markers": {
                    "marker_key", "marker_value", "updated_at",
                },
            }
            missing_schema: list[str] = []
            for table_name, expected in required_schema.items():
                actual = {
                    str(column["name"])
                    for column in conn.execute(
                        f"PRAGMA table_info({table_name})"
                    ).fetchall()
                }
                missing = sorted(expected - actual)
                if missing:
                    missing_schema.append(
                        f"{table_name}: {', '.join(missing)}"
                    )
            if missing_schema:
                raise RuntimeError(
                    "城下町資料庫 schema 不完整，已停止啟動且未執行資料回填；"
                    "請先備份並確認 migration。"
                    + "；".join(missing_schema)
                )

            # 將舊版較低的體力上限安全提升到目前基礎上限。
            # 保留玩家已消耗的體力量，例如 70/100 會遷移成 970/1000。
            now = now_iso()
            conn.execute(
                """
                UPDATE town_life_players
                SET stamina = MIN(?, stamina + (? - max_stamina)),
                    max_stamina = ?,
                    updated_at = ?
                WHERE max_stamina < ?
                """,
                (MAX_STAMINA, MAX_STAMINA, MAX_STAMINA, now, MAX_STAMINA),
            )
            self._seed_maintenance_compensation(conn)
            conn.commit()

    @staticmethod
    def _insert_mail(
        conn: sqlite3.Connection,
        user_id: int | str,
        mail_key: str,
        title: str,
        body: str,
        item_key: str,
        quantity: int,
        created_at: str,
    ) -> bool:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO town_life_mailbox (
                user_id, mail_key, title, body, item_key,
                quantity, claimed_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, '', ?)
            """,
            (
                str(user_id),
                mail_key,
                title,
                body,
                item_key,
                int(quantity),
                created_at,
            ),
        )
        return cursor.rowcount > 0

    def _seed_maintenance_compensation(self, conn: sqlite3.Connection) -> int:
        marker_key = f"mail_issued:{MAINTENANCE_MAIL_KEY}"
        marker = conn.execute(
            "SELECT marker_key FROM town_life_system_markers WHERE marker_key = ?",
            (marker_key,),
        ).fetchone()
        if marker is not None:
            return 0

        now = now_iso()
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO town_life_mailbox (
                user_id, mail_key, title, body, item_key,
                quantity, claimed_at, created_at
            )
            SELECT user_id, ?, ?, ?, ?, ?, '', ?
            FROM town_life_players
            """,
            (
                MAINTENANCE_MAIL_KEY,
                MAINTENANCE_MAIL_TITLE,
                MAINTENANCE_MAIL_BODY,
                MAINTENANCE_MAIL_ITEM_KEY,
                MAINTENANCE_MAIL_QUANTITY,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO town_life_system_markers (
                marker_key, marker_value, updated_at
            ) VALUES (?, ?, ?)
            """,
            (marker_key, str(max(0, cursor.rowcount)), now),
        )
        return max(0, cursor.rowcount)

    def issue_mail(
        self,
        user_id: int,
        *,
        mail_key: str,
        title: str,
        body: str,
        item_key: str,
        quantity: int,
    ) -> bool:
        if item_key not in ITEM_CONFIG:
            raise TownLifeError("信件獎勵道具不存在。")
        if int(quantity) <= 0:
            raise TownLifeError("信件獎勵數量必須大於零。")
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_player(conn, user_id)
            inserted = self._insert_mail(
                conn,
                user_id,
                mail_key,
                title,
                body,
                item_key,
                quantity,
                now_iso(),
            )
            conn.commit()
        return inserted

    def _ensure_player(self, conn: sqlite3.Connection, user_id: int) -> None:
        uid = str(user_id)
        current_time = taipei_now()
        now = current_time.isoformat(timespec="seconds")
        today = current_time.date().isoformat()
        conn.execute(
            """
            INSERT OR IGNORE INTO town_life_players (
                user_id, coins, stamina, max_stamina, spirit, max_spirit,
                stamina_updated_at, daily_resource_reset_date,
                last_rest_date, food_stamina_date,
                food_stamina_recovered, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', '', 0, ?, ?)
            """,
            (
                uid,
                INITIAL_COINS,
                INITIAL_STAMINA,
                MAX_STAMINA,
                INITIAL_SPIRIT,
                MAX_SPIRIT,
                now,
                today,
                now,
                now,
            ),
        )
        player = conn.execute(
            """
            SELECT daily_resource_reset_date
            FROM town_life_players
            WHERE user_id = ?
            """,
            (uid,),
        ).fetchone()
        if player is not None and str(player["daily_resource_reset_date"] or "") != today:
            # 每日凌晨 00:00（Asia/Taipei）後的第一次讀取或操作，
            # 將兩項資源恢復到玩家當下真正的上限，不寫死基礎數值。
            conn.execute(
                """
                UPDATE town_life_players
                SET stamina = max_stamina,
                    spirit = max_spirit,
                    stamina_updated_at = ?,
                    daily_resource_reset_date = ?,
                    last_rest_date = '',
                    updated_at = ?
                WHERE user_id = ?
                """,
                (now, today, now, uid),
            )
        for tool_key in TOOL_CONFIG:
            conn.execute(
                """
                INSERT OR IGNORE INTO town_life_tools (
                    user_id, tool_key, level, updated_at
                ) VALUES (?, ?, 0, ?)
                """,
                (uid, tool_key, now),
            )
        for career_key in CAREER_CONFIG:
            conn.execute(
                """
                INSERT OR IGNORE INTO town_life_careers (
                    user_id, career_key, level, exp, updated_at
                ) VALUES (?, ?, 1, 0, ?)
                """,
                (uid, career_key, now),
            )
        for plot_no in range(1, 4):
            conn.execute(
                """
                INSERT OR IGNORE INTO town_life_plots (
                    user_id, plot_no, crop_key, planted_at, ready_at
                ) VALUES (?, ?, '', '', '')
                """,
                (uid, plot_no),
            )
        for animal_key in ANIMAL_CONFIG:
            conn.execute(
                """
                INSERT OR IGNORE INTO town_life_animals (
                    user_id, animal_key, quantity, last_collect_date, updated_at
                ) VALUES (?, ?, 0, '', ?)
                """,
                (uid, animal_key, now),
            )

    def _refresh_stamina(self, conn: sqlite3.Connection, user_id: int) -> sqlite3.Row:
        self._ensure_player(conn, user_id)
        uid = str(user_id)
        row = conn.execute(
            "SELECT * FROM town_life_players WHERE user_id = ?",
            (uid,),
        ).fetchone()
        if row is None:
            raise TownLifeError("找不到城下町生活資料。")

        # 每日資源回滿已由 _ensure_player 統一處理。體力另外採離線
        # 結算：同一天每完整一分鐘恢復一點。到達上限時會丟棄
        # 多餘時間，避免滿體囤積恢復量。
        current_time = taipei_now()
        updated_at = parse_time(str(row["stamina_updated_at"]))
        if updated_at <= current_time:
            elapsed_minutes = int(
                (current_time - updated_at).total_seconds() // 60
            )
            if elapsed_minutes > 0:
                stamina = int(row["stamina"])
                maximum = int(row["max_stamina"])
                recovered = min(
                    max(0, maximum - stamina),
                    elapsed_minutes * STAMINA_RECOVERY_PER_MINUTE,
                )
                refreshed_stamina = stamina + recovered
                if refreshed_stamina >= maximum:
                    refreshed_at = current_time
                else:
                    refreshed_at = updated_at + timedelta(minutes=elapsed_minutes)
                now = current_time.isoformat(timespec="seconds")
                conn.execute(
                    """
                    UPDATE town_life_players
                    SET stamina = ?, stamina_updated_at = ?, updated_at = ?
                    WHERE user_id = ?
                    """,
                    (
                        refreshed_stamina,
                        refreshed_at.isoformat(timespec="seconds"),
                        now,
                        uid,
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM town_life_players WHERE user_id = ?",
                    (uid,),
                ).fetchone()
        return row

    def _spend_stamina(self, conn: sqlite3.Connection, user_id: int, amount: int) -> int:
        row = self._refresh_stamina(conn, user_id)
        stamina = int(row["stamina"])
        if stamina < amount:
            raise TownLifeError(f"體力不足。這次需要 {amount} 點，目前只有 {stamina} 點。")
        left = stamina - amount
        now = now_iso()
        conn.execute(
            """
            UPDATE town_life_players
            SET stamina = ?, stamina_updated_at = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (left, now, now, str(user_id)),
        )
        return left

    def _spend_spirit(self, conn: sqlite3.Connection, user_id: int, amount: int) -> int:
        self._ensure_player(conn, user_id)
        row = conn.execute(
            "SELECT spirit FROM town_life_players WHERE user_id = ?",
            (str(user_id),),
        ).fetchone()
        spirit = int(row["spirit"] if row is not None else 0)
        amount = max(0, int(amount))
        if spirit < amount:
            raise TownLifeError(
                f"精神力不足。這次需要 {amount} 點，目前只有 {spirit} 點。"
                "可先休息、食用料理，或等隔日重置。"
            )
        left = spirit - amount
        now = now_iso()
        conn.execute(
            """
            UPDATE town_life_players
            SET spirit = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (left, now, str(user_id)),
        )
        return left

    def _restore_spirit(self, conn: sqlite3.Connection, user_id: int, amount: int) -> int:
        self._ensure_player(conn, user_id)
        row = conn.execute(
            "SELECT spirit, max_spirit FROM town_life_players WHERE user_id = ?",
            (str(user_id),),
        ).fetchone()
        if row is None:
            raise TownLifeError("找不到城下町生活資料。")
        current = int(row["spirit"])
        maximum = int(row["max_spirit"])
        restored = min(maximum - current, max(0, int(amount)))
        if restored <= 0:
            return 0
        now = now_iso()
        conn.execute(
            "UPDATE town_life_players SET spirit = ?, updated_at = ? WHERE user_id = ?",
            (current + restored, now, str(user_id)),
        )
        return restored

    @staticmethod
    def _inventory_quantity(conn: sqlite3.Connection, user_id: int, item_key: str) -> int:
        row = conn.execute(
            """
            SELECT quantity FROM town_life_inventory
            WHERE user_id = ? AND item_key = ?
            """,
            (str(user_id), item_key),
        ).fetchone()
        return int(row["quantity"] if row is not None else 0)

    @staticmethod
    def _change_inventory(
        conn: sqlite3.Connection,
        user_id: int,
        item_key: str,
        delta: int,
    ) -> int:
        uid = str(user_id)
        current_row = conn.execute(
            """
            SELECT quantity FROM town_life_inventory
            WHERE user_id = ? AND item_key = ?
            """,
            (uid, item_key),
        ).fetchone()
        current = int(current_row["quantity"] if current_row is not None else 0)
        updated = current + int(delta)
        if updated < 0:
            raise TownLifeError(f"{item_name(item_key)}數量不足。")
        now = now_iso()
        if updated == 0:
            conn.execute(
                "DELETE FROM town_life_inventory WHERE user_id = ? AND item_key = ?",
                (uid, item_key),
            )
        else:
            conn.execute(
                """
                INSERT INTO town_life_inventory (
                    user_id, item_key, quantity, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, item_key)
                DO UPDATE SET quantity = excluded.quantity, updated_at = excluded.updated_at
                """,
                (uid, item_key, updated, now),
            )
        return updated

    @staticmethod
    def _add_career_exp(
        conn: sqlite3.Connection,
        user_id: int,
        career_key: str,
        amount: int,
    ) -> tuple[int, int]:
        uid = str(user_id)
        row = conn.execute(
            """
            SELECT level, exp FROM town_life_careers
            WHERE user_id = ? AND career_key = ?
            """,
            (uid, career_key),
        ).fetchone()
        current_exp = int(row["exp"] if row is not None else 0)
        new_exp = current_exp + max(0, int(amount))
        new_level = career_level_for_exp(new_exp)
        now = now_iso()
        conn.execute(
            """
            INSERT INTO town_life_careers (
                user_id, career_key, level, exp, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, career_key)
            DO UPDATE SET level = excluded.level, exp = excluded.exp, updated_at = excluded.updated_at
            """,
            (uid, career_key, new_level, new_exp, now),
        )
        return new_level, new_exp

    def get_snapshot(self, user_id: int) -> dict[str, Any]:
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            player_row = self._refresh_stamina(conn, user_id)
            tools = {
                row["tool_key"]: int(row["level"])
                for row in conn.execute(
                    "SELECT tool_key, level FROM town_life_tools WHERE user_id = ?",
                    (str(user_id),),
                ).fetchall()
            }
            careers = {
                row["career_key"]: {
                    "level": int(row["level"]),
                    "exp": int(row["exp"]),
                }
                for row in conn.execute(
                    "SELECT career_key, level, exp FROM town_life_careers WHERE user_id = ?",
                    (str(user_id),),
                ).fetchall()
            }
            inventory = {
                row["item_key"]: int(row["quantity"])
                for row in conn.execute(
                    """
                    SELECT item_key, quantity FROM town_life_inventory
                    WHERE user_id = ? AND quantity > 0
                    ORDER BY item_key
                    """,
                    (str(user_id),),
                ).fetchall()
            }
            plots = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT plot_no, crop_key, planted_at, ready_at
                    FROM town_life_plots WHERE user_id = ? ORDER BY plot_no
                    """,
                    (str(user_id),),
                ).fetchall()
            ]
            animals = {
                row["animal_key"]: {
                    "quantity": int(row["quantity"]),
                    "last_collect_date": str(row["last_collect_date"]),
                }
                for row in conn.execute(
                    """
                    SELECT animal_key, quantity, last_collect_date
                    FROM town_life_animals WHERE user_id = ?
                    """,
                    (str(user_id),),
                ).fetchall()
            }
            mailbox = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT mail_key, title, body, item_key, quantity,
                           claimed_at, created_at
                    FROM town_life_mailbox
                    WHERE user_id = ?
                    ORDER BY CASE WHEN claimed_at = '' THEN 0 ELSE 1 END,
                             created_at DESC, mail_key
                    """,
                    (str(user_id),),
                ).fetchall()
            ]
            conn.commit()
        return {
            "player": dict(player_row),
            "tools": tools,
            "careers": careers,
            "inventory": inventory,
            "plots": plots,
            "animals": animals,
            "mailbox": mailbox,
        }

    def claim_all_mail(self, user_id: int) -> dict[str, Any]:
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_player(conn, user_id)
            rows = conn.execute(
                """
                SELECT mail_key, item_key, quantity
                FROM town_life_mailbox
                WHERE user_id = ? AND claimed_at = ''
                ORDER BY created_at, mail_key
                """,
                (str(user_id),),
            ).fetchall()
            if not rows:
                raise TownLifeError("信箱目前沒有尚未領取的附件。")

            rewards: dict[str, int] = {}
            for row in rows:
                item_key = str(row["item_key"])
                quantity = int(row["quantity"])
                if item_key not in ITEM_CONFIG or quantity <= 0:
                    raise TownLifeError("信箱內有無法辨識的附件，請聯絡管理員。")
                rewards[item_key] = rewards.get(item_key, 0) + quantity

            for item_key, quantity in rewards.items():
                self._change_inventory(conn, user_id, item_key, quantity)

            claimed_at = now_iso()
            conn.execute(
                """
                UPDATE town_life_mailbox
                SET claimed_at = ?
                WHERE user_id = ? AND claimed_at = ''
                """,
                (claimed_at, str(user_id)),
            )
            conn.commit()
        return {
            "claimed_count": len(rows),
            "rewards": rewards,
            "claimed_at": claimed_at,
        }

    def buy_or_upgrade_tool(self, user_id: int, tool_key: str) -> dict[str, Any]:
        if tool_key not in TOOL_CONFIG:
            raise TownLifeError("找不到這項工具。")
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_player(conn, user_id)
            row = conn.execute(
                "SELECT level FROM town_life_tools WHERE user_id = ? AND tool_key = ?",
                (str(user_id), tool_key),
            ).fetchone()
            level = int(row["level"] if row is not None else 0)
            if level >= MAX_TOOL_LEVEL:
                raise TownLifeError(f"{tool_name(tool_key)}已經升到最高等級。")

            info = TOOL_CONFIG[tool_key]
            cost = int(info["costs"][level])
            materials = {
                str(key): int(quantity)
                for key, quantity in dict(info["materials"][level]).items()
            }
            player = self._refresh_stamina(conn, user_id)
            coins = int(player["coins"])
            if coins < cost:
                raise TownLifeError(f"麻瓜幣不足。需要 {cost}，目前只有 {coins}。")

            missing = {
                key: required - self._inventory_quantity(conn, user_id, key)
                for key, required in materials.items()
                if self._inventory_quantity(conn, user_id, key) < required
            }
            spirit_cost = int(TOOL_UPGRADE_SPIRIT_COSTS[level])
            if spirit_cost > 0:
                self._spend_spirit(conn, user_id, spirit_cost)

            if missing:
                raise TownLifeError(
                    "升級素材不足，還缺：" + format_item_requirements(missing) + "。"
                )

            for item_key, quantity in materials.items():
                self._change_inventory(conn, user_id, item_key, -quantity)

            new_level = level + 1
            now = now_iso()
            conn.execute(
                "UPDATE town_life_players SET coins = ?, updated_at = ? WHERE user_id = ?",
                (coins - cost, now, str(user_id)),
            )
            conn.execute(
                """
                UPDATE town_life_tools SET level = ?, updated_at = ?
                WHERE user_id = ? AND tool_key = ?
                """,
                (new_level, now, str(user_id), tool_key),
            )
            conn.commit()
        return {
            "tool_key": tool_key,
            "level": new_level,
            "cost": cost,
            "materials": materials,
            "spirit_cost": spirit_cost,
        }

    def buy_supply(self, user_id: int, item_key: str, quantity: int) -> dict[str, Any]:
        item = ITEM_CONFIG.get(item_key)
        unit_price = int(item.get("buy", 0)) if item else 0
        if unit_price <= 0 or quantity <= 0:
            raise TownLifeError("這項物資目前不能購買。")
        total = unit_price * int(quantity)
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            player = self._refresh_stamina(conn, user_id)
            coins = int(player["coins"])
            if coins < total:
                raise TownLifeError(f"麻瓜幣不足。需要 {total}，目前只有 {coins}。")
            self._change_inventory(conn, user_id, item_key, quantity)
            now = now_iso()
            conn.execute(
                "UPDATE town_life_players SET coins = ?, updated_at = ? WHERE user_id = ?",
                (coins - total, now, str(user_id)),
            )
            conn.commit()
        return {"item_key": item_key, "quantity": quantity, "cost": total}

    def plant_crop(self, user_id: int, crop_key: str) -> dict[str, Any]:
        crop = CROP_CONFIG.get(crop_key)
        if crop is None:
            raise TownLifeError("找不到這種作物。")
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_player(conn, user_id)
            tool_row = conn.execute(
                "SELECT level FROM town_life_tools WHERE user_id = ? AND tool_key = 'farm_tools'",
                (str(user_id),),
            ).fetchone()
            tool_level = int(tool_row["level"] if tool_row is not None else 0)
            if tool_level <= 0:
                raise TownLifeError("要先到對應工坊購買農具組，才能播種。")

            empty_plots = conn.execute(
                """
                SELECT plot_no FROM town_life_plots
                WHERE user_id = ? AND crop_key = '' ORDER BY plot_no
                """,
                (str(user_id),),
            ).fetchall()
            seed_key = str(crop["seed"])
            seed_qty = self._inventory_quantity(conn, user_id, seed_key)
            plant_count = min(len(empty_plots), seed_qty)
            if not empty_plots:
                raise TownLifeError("三塊田都已經種植，先等待收成。")
            if seed_qty <= 0:
                raise TownLifeError(f"沒有{item_name(seed_key)}，先去購買種子。")
            stamina_cost = plant_count * max(1, 4 - ((tool_level - 1) // 2))
            self._spend_stamina(conn, user_id, stamina_cost)
            spirit_cost = 0
            started = taipei_now()
            ready = started + timedelta(minutes=int(crop["growth_minutes"]))
            for plot in empty_plots[:plant_count]:
                conn.execute(
                    """
                    UPDATE town_life_plots
                    SET crop_key = ?, planted_at = ?, ready_at = ?
                    WHERE user_id = ? AND plot_no = ?
                    """,
                    (
                        crop_key,
                        started.isoformat(timespec="seconds"),
                        ready.isoformat(timespec="seconds"),
                        str(user_id),
                        int(plot["plot_no"]),
                    ),
                )
            self._change_inventory(conn, user_id, seed_key, -plant_count)
            conn.commit()
        return {
            "crop_key": crop_key,
            "planted": plant_count,
            "ready_at": ready.isoformat(timespec="seconds"),
            "stamina_cost": stamina_cost,
            "spirit_cost": spirit_cost,
        }

    def harvest_ready_crops(self, user_id: int) -> dict[str, Any]:
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_player(conn, user_id)
            tool_row = conn.execute(
                "SELECT level FROM town_life_tools WHERE user_id = ? AND tool_key = 'farm_tools'",
                (str(user_id),),
            ).fetchone()
            tool_level = int(tool_row["level"] if tool_row is not None else 0)
            if tool_level <= 0:
                raise TownLifeError("尚未擁有農具組。")
            rows = conn.execute(
                """
                SELECT plot_no, crop_key, ready_at FROM town_life_plots
                WHERE user_id = ? AND crop_key <> '' ORDER BY plot_no
                """,
                (str(user_id),),
            ).fetchall()
            ready_rows = [row for row in rows if parse_time(str(row["ready_at"])) <= taipei_now()]
            if not ready_rows:
                raise TownLifeError("目前沒有成熟的作物。")
            stamina_cost = max(1, len(ready_rows) * 2)
            spirit_cost = 0
            self._spend_stamina(conn, user_id, stamina_cost)
            rewards: dict[str, int] = {}
            exp_gain = 0
            for row in ready_rows:
                crop = CROP_CONFIG[str(row["crop_key"])]
                bonus = random.randint(0, max(0, tool_level - 1))
                quantity = int(crop["base_yield"]) + bonus
                product = str(crop["product"])
                self._change_inventory(conn, user_id, product, quantity)
                rewards[product] = rewards.get(product, 0) + quantity
                exp_gain += int(crop["exp"])
                conn.execute(
                    """
                    UPDATE town_life_plots
                    SET crop_key = '', planted_at = '', ready_at = ''
                    WHERE user_id = ? AND plot_no = ?
                    """,
                    (str(user_id), int(row["plot_no"])),
                )
            level, exp = self._add_career_exp(conn, user_id, "farming", exp_gain)
            conn.commit()
        return {
            "rewards": rewards,
            "exp_gain": exp_gain,
            "level": level,
            "exp": exp,
            "stamina_cost": stamina_cost,
            "spirit_cost": spirit_cost,
        }

    def buy_animal(self, user_id: int, animal_key: str) -> dict[str, Any]:
        animal = ANIMAL_CONFIG.get(animal_key)
        if animal is None:
            raise TownLifeError("找不到這種動物。")
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_player(conn, user_id)
            tool_row = conn.execute(
                "SELECT level FROM town_life_tools WHERE user_id = ? AND tool_key = 'farm_tools'",
                (str(user_id),),
            ).fetchone()
            tool_level = int(tool_row["level"] if tool_row is not None else 0)
            required = int(animal["required_tool_level"])
            if tool_level < required:
                raise TownLifeError(f"農具組需要達到 Lv.{required}，才有能力照顧{animal['name']}。")
            player = self._refresh_stamina(conn, user_id)
            coins = int(player["coins"])
            cost = int(animal["cost"])
            if coins < cost:
                raise TownLifeError(f"麻瓜幣不足。購買{animal['name']}需要 {cost}。")
            current_row = conn.execute(
                """
                SELECT quantity FROM town_life_animals
                WHERE user_id = ? AND animal_key = ?
                """,
                (str(user_id), animal_key),
            ).fetchone()
            current = int(current_row["quantity"] if current_row is not None else 0)
            if current >= 10:
                raise TownLifeError(f"目前最多只能飼養 10 隻{animal['name']}。")
            now = now_iso()
            conn.execute(
                "UPDATE town_life_players SET coins = ?, updated_at = ? WHERE user_id = ?",
                (coins - cost, now, str(user_id)),
            )
            conn.execute(
                """
                UPDATE town_life_animals
                SET quantity = ?, updated_at = ?
                WHERE user_id = ? AND animal_key = ?
                """,
                (current + 1, now, str(user_id), animal_key),
            )
            conn.commit()
        return {
            "animal_key": animal_key,
            "product": str(animal["product"]),
            "quantity": current + 1,
            "cost": cost,
        }

    def collect_animal_product(self, user_id: int, animal_key: str) -> dict[str, Any]:
        animal = ANIMAL_CONFIG.get(animal_key)
        if animal is None:
            raise TownLifeError("找不到這種動物。")
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_player(conn, user_id)
            row = conn.execute(
                """
                SELECT quantity, last_collect_date FROM town_life_animals
                WHERE user_id = ? AND animal_key = ?
                """,
                (str(user_id), animal_key),
            ).fetchone()
            quantity = int(row["quantity"] if row is not None else 0)
            last_collect = str(row["last_collect_date"] if row is not None else "")
            if quantity <= 0:
                raise TownLifeError(f"牧場裡還沒有{animal['name']}。")
            if last_collect == today_key():
                raise TownLifeError(f"今天已經採收過{animal['product_name']}，明天再來。")
            feed_qty = self._inventory_quantity(conn, user_id, "animal_feed")
            if feed_qty < quantity:
                raise TownLifeError(
                    f"需要 {quantity} 份飼料才能照顧全部{animal['name']}，目前只有 {feed_qty} 份。"
                )
            spirit_cost = 0
            self._change_inventory(conn, user_id, "animal_feed", -quantity)
            self._change_inventory(conn, user_id, str(animal["product"]), quantity)
            exp_gain = int(animal["career_exp"]) * quantity
            level, exp = self._add_career_exp(conn, user_id, "farming", exp_gain)
            now = now_iso()
            conn.execute(
                """
                UPDATE town_life_animals
                SET last_collect_date = ?, updated_at = ?
                WHERE user_id = ? AND animal_key = ?
                """,
                (today_key(), now, str(user_id), animal_key),
            )
            conn.commit()
        return {
            "animal_key": animal_key,
            "product": str(animal["product"]),
            "quantity": quantity,
            "exp_gain": exp_gain,
            "level": level,
            "exp": exp,
            "spirit_cost": spirit_cost,
        }

    def forage(
        self,
        user_id: int,
        attempts: int = 1,
        *,
        stamina_budget: int | None = None,
    ) -> dict[str, Any]:
        requested = int(attempts)
        maximum_attempts = 100 if stamina_budget is not None else 10
        if requested < 1 or requested > maximum_attempts:
            raise TownLifeError(
                f"採集批次必須介於 1 到 {maximum_attempts} 次。"
            )
        budget = (
            None
            if stamina_budget is None
            else max(1, int(stamina_budget))
        )

        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_player(conn, user_id)
            player = self._refresh_stamina(conn, user_id)
            stamina_per_attempt = 6
            completed = min(
                requested,
                int(player["stamina"]) // stamina_per_attempt,
                (
                    requested
                    if budget is None
                    else budget // stamina_per_attempt
                ),
            )
            if completed <= 0:
                if int(player["stamina"]) < stamina_per_attempt:
                    self._spend_stamina(conn, user_id, stamina_per_attempt)
                raise TownLifeError("這次設定的體力預算不足以完成一次採集。")
            stamina_cost = stamina_per_attempt * completed
            self._spend_stamina(conn, user_id, stamina_cost)
            spirit_cost = 0
            rewards: dict[str, int] = {}
            item_key = ""
            for _ in range(completed):
                item_key = random.choices(
                    ["wild_berry", "wild_herb", "branch"],
                    weights=[50, 30, 20],
                    k=1,
                )[0]
                quantity = random.randint(1, 2)
                rewards[item_key] = rewards.get(item_key, 0) + quantity
            for reward_key, reward_quantity in rewards.items():
                self._change_inventory(
                    conn,
                    user_id,
                    reward_key,
                    reward_quantity,
                )
            level, exp = self._add_career_exp(
                conn,
                user_id,
                "fishing",
                5 * completed,
            )
            conn.commit()
        return {
            "item_key": item_key,
            "quantity": rewards[item_key],
            "rewards": rewards,
            "attempts_requested": requested,
            "attempts_completed": completed,
            "stamina_budget": budget,
            "level": level,
            "exp": exp,
            "stamina_cost": stamina_cost,
            "spirit_cost": spirit_cost,
        }

    def fish(
        self,
        user_id: int,
        attempts: int = 1,
        *,
        stamina_budget: int | None = None,
    ) -> dict[str, Any]:
        requested = int(attempts)
        maximum_attempts = 100 if stamina_budget is not None else 10
        if requested < 1 or requested > maximum_attempts:
            raise TownLifeError(
                f"釣魚批次必須介於 1 到 {maximum_attempts} 次。"
            )
        budget = (
            None
            if stamina_budget is None
            else max(1, int(stamina_budget))
        )

        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_player(conn, user_id)
            tool_row = conn.execute(
                "SELECT level FROM town_life_tools WHERE user_id = ? AND tool_key = 'fishing_rod'",
                (str(user_id),),
            ).fetchone()
            tool_level = int(tool_row["level"] if tool_row is not None else 0)
            if tool_level <= 0:
                raise TownLifeError("要先到對應工坊購買釣具組。")
            stamina_per_attempt = max(5, 10 - tool_level)
            player = self._refresh_stamina(conn, user_id)
            completed = min(
                requested,
                int(player["stamina"]) // stamina_per_attempt,
                (
                    requested
                    if budget is None
                    else budget // stamina_per_attempt
                ),
            )
            if completed <= 0:
                if int(player["stamina"]) < stamina_per_attempt:
                    self._spend_stamina(conn, user_id, stamina_per_attempt)
                raise TownLifeError("這次設定的體力預算不足以完成一次釣魚。")
            stamina_cost = stamina_per_attempt * completed
            self._spend_stamina(conn, user_id, stamina_cost)
            spirit_cost = 0
            if tool_level == 1:
                items, weights = ["river_fish", "silver_carp", "old_boot"], [72, 18, 10]
            elif tool_level == 2:
                items, weights = ["river_fish", "silver_carp", "moon_trout", "old_boot"], [58, 28, 5, 9]
            else:
                items, weights = ["river_fish", "silver_carp", "moon_trout", "old_boot"], [45, 35, 15 + tool_level, 5]
            rewards: dict[str, int] = {}
            exp_gain = 0
            item_key = ""
            for _ in range(completed):
                item_key = random.choices(items, weights=weights, k=1)[0]
                quantity = 1 + (
                    1
                    if tool_level >= 4 and random.random() < 0.25
                    else 0
                )
                rewards[item_key] = rewards.get(item_key, 0) + quantity
                exp_gain += (
                    4
                    if item_key == "old_boot"
                    else (12 if item_key == "moon_trout" else 8)
                )
            for reward_key, reward_quantity in rewards.items():
                self._change_inventory(
                    conn,
                    user_id,
                    reward_key,
                    reward_quantity,
                )
            level, exp = self._add_career_exp(
                conn,
                user_id,
                "fishing",
                exp_gain,
            )
            conn.commit()
        return {
            "item_key": item_key,
            "quantity": rewards[item_key],
            "rewards": rewards,
            "attempts_requested": requested,
            "attempts_completed": completed,
            "stamina_budget": budget,
            "stamina_cost": stamina_cost,
            "spirit_cost": spirit_cost,
            "level": level,
            "exp": exp,
        }

    def mine(
        self,
        user_id: int,
        area_key: str = "outer_tunnel",
        attempts: int = 1,
        *,
        stamina_budget: int | None = None,
    ) -> dict[str, Any]:
        area = MINING_AREA_CONFIG.get(area_key)
        if area is None:
            raise TownLifeError("找不到這個礦區。")
        requested = int(attempts)
        maximum_attempts = 100 if stamina_budget is not None else 5
        if requested < 1 or requested > maximum_attempts:
            raise TownLifeError(
                f"挖礦批次必須介於 1 到 {maximum_attempts} 次。"
            )
        budget = (
            None
            if stamina_budget is None
            else max(1, int(stamina_budget))
        )

        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_player(conn, user_id)
            tool_row = conn.execute(
                "SELECT level FROM town_life_tools WHERE user_id = ? AND tool_key = 'pickaxe'",
                (str(user_id),),
            ).fetchone()
            career_row = conn.execute(
                """
                SELECT level FROM town_life_careers
                WHERE user_id = ? AND career_key = 'crystal'
                """,
                (str(user_id),),
            ).fetchone()
            tool_level = int(tool_row["level"] if tool_row is not None else 0)
            career_level = int(career_row["level"] if career_row is not None else 1)
            required_tool = int(area["required_tool_level"])
            required_career = int(area["required_career_level"])

            if tool_level <= 0:
                raise TownLifeError("要先到對應工坊購買挖礦工具。")
            if tool_level < required_tool or career_level < required_career:
                raise TownLifeError(
                    f"{area['name']}需要挖礦工具 Lv.{required_tool}，"
                    f"並且魔晶礦師達到 Lv.{required_career}。"
                )

            efficiency = max(0, tool_level - required_tool)
            stamina_cost = max(
                int(area["minimum_stamina_cost"]),
                int(area["base_stamina_cost"]) - efficiency,
            )
            spirit_per_attempt = int(area["spirit_cost"])
            player = self._refresh_stamina(conn, user_id)
            affordable_by_stamina = int(player["stamina"]) // stamina_cost
            affordable_by_budget = (
                requested
                if budget is None
                else budget // stamina_cost
            )
            affordable_by_spirit = (
                requested
                if spirit_per_attempt <= 0
                else int(player["spirit"]) // spirit_per_attempt
            )
            completed = min(
                requested,
                affordable_by_stamina,
                affordable_by_budget,
                affordable_by_spirit,
            )
            if completed <= 0:
                if affordable_by_stamina <= 0:
                    self._spend_stamina(conn, user_id, stamina_cost)
                if affordable_by_spirit <= 0:
                    self._spend_spirit(conn, user_id, spirit_per_attempt)
                raise TownLifeError("這次設定的體力預算不足以完成一次挖礦。")
            stamina_cost *= completed
            spirit_cost = spirit_per_attempt * completed
            self._spend_stamina(conn, user_id, stamina_cost)
            self._spend_spirit(conn, user_id, spirit_cost)

            items = list(area["items"])
            weights = list(area["weights"])
            rewards: dict[str, int] = {}
            exp_gain = 0
            item_key = ""
            for _ in range(completed):
                item_key = random.choices(items, weights=weights, k=1)[0]
                quantity = 1
                if item_key == "stone":
                    quantity += random.randint(0, max(1, tool_level // 2))
                elif item_key in {"copper_ore", "iron_ore"} and tool_level >= 4:
                    if random.random() < 0.25:
                        quantity += 1
                elif item_key == "raw_crystal" and tool_level >= 5:
                    if random.random() < 0.15:
                        quantity += 1
                rewards[item_key] = rewards.get(item_key, 0) + quantity
                exp_gain += int(area["base_exp"])
                if item_key == "iron_ore":
                    exp_gain += 2
                elif item_key == "raw_crystal":
                    exp_gain += 5

            for reward_key, reward_quantity in rewards.items():
                self._change_inventory(
                    conn,
                    user_id,
                    reward_key,
                    reward_quantity,
                )
            level, exp = self._add_career_exp(
                conn,
                user_id,
                "crystal",
                exp_gain,
            )
            conn.commit()
        return {
            "area_key": area_key,
            "area_name": str(area["name"]),
            "item_key": item_key,
            "quantity": rewards[item_key],
            "rewards": rewards,
            "attempts_requested": requested,
            "attempts_completed": completed,
            "stamina_budget": budget,
            "stamina_cost": stamina_cost,
            "spirit_cost": spirit_cost,
            "level": level,
            "exp": exp,
        }

    def refine_crystal(self, user_id: int) -> dict[str, Any]:
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_player(conn, user_id)
            career_row = conn.execute(
                """
                SELECT level FROM town_life_careers
                WHERE user_id = ? AND career_key = 'crystal'
                """,
                (str(user_id),),
            ).fetchone()
            career_level = int(career_row["level"] if career_row is not None else 1)
            if career_level < 2:
                raise TownLifeError("魔晶礦師需要達到 Lv.2 才能進行水晶精煉。")
            if self._inventory_quantity(conn, user_id, "raw_crystal") < 2:
                raise TownLifeError("精煉需要 2 個魔法水晶原礦。")
            if self._inventory_quantity(conn, user_id, "iron_ore") < 1:
                raise TownLifeError("精煉還需要 1 個鐵礦作為穩定材料。")
            self._spend_stamina(conn, user_id, 8)
            spirit_cost = 3
            self._spend_spirit(conn, user_id, spirit_cost)
            self._change_inventory(conn, user_id, "raw_crystal", -2)
            self._change_inventory(conn, user_id, "iron_ore", -1)
            self._change_inventory(conn, user_id, "refined_crystal", 1)
            level, exp = self._add_career_exp(conn, user_id, "crystal", 25)
            conn.commit()
        return {
            "quantity": 1,
            "level": level,
            "exp": exp,
            "stamina_cost": 8,
            "spirit_cost": spirit_cost,
        }

    def _next_upgrade_material_reserve(
        self,
        conn: sqlite3.Connection,
        user_id: int,
    ) -> dict[str, int]:
        """保留三套工具各自下一級所需的升級素材。"""
        uid = str(user_id)
        reserve: dict[str, int] = {}
        rows = conn.execute(
            "SELECT tool_key, level FROM town_life_tools WHERE user_id = ?",
            (uid,),
        ).fetchall()
        levels = {str(row["tool_key"]): int(row["level"]) for row in rows}
        for tool_key, info in TOOL_CONFIG.items():
            level = int(levels.get(tool_key, 0))
            if level >= MAX_TOOL_LEVEL:
                continue
            materials = dict(info["materials"][level])
            for item_key, quantity in materials.items():
                key = str(item_key)
                reserve[key] = reserve.get(key, 0) + int(quantity)
        return reserve

    def _restore_food_stamina(
        self,
        conn: sqlite3.Connection,
        user_id: int,
        requested: int,
    ) -> tuple[int, int, int]:
        player = self._refresh_stamina(conn, user_id)
        uid = str(user_id)
        today = today_key()
        stored_date = str(player["food_stamina_date"] or "")
        recovered_today = int(player["food_stamina_recovered"] or 0)
        if stored_date != today:
            recovered_today = 0
        daily_remaining = max(0, MAX_DAILY_FOOD_STAMINA - recovered_today)
        current = int(player["stamina"])
        maximum = int(player["max_stamina"])
        restored = min(max(0, int(requested)), daily_remaining, maximum - current)
        now = now_iso()
        conn.execute(
            """
            UPDATE town_life_players
            SET stamina = ?, food_stamina_date = ?,
                food_stamina_recovered = ?, stamina_updated_at = ?, updated_at = ?
            WHERE user_id = ?
            """,
            (current + restored, today, recovered_today + restored, now, now, uid),
        )
        return restored, current + restored, daily_remaining - restored

    def cook_food(self, user_id: int, recipe_key: str) -> dict[str, Any]:
        recipe = FOOD_RECIPE_CONFIG.get(recipe_key)
        if recipe is None:
            raise TownLifeError("找不到這道料理。")
        ingredients = {
            str(key): int(quantity)
            for key, quantity in dict(recipe["ingredients"]).items()
        }
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_player(conn, user_id)
            missing = {
                key: required - self._inventory_quantity(conn, user_id, key)
                for key, required in ingredients.items()
                if self._inventory_quantity(conn, user_id, key) < required
            }
            if missing:
                raise TownLifeError(
                    "料理材料不足，還缺：" + format_item_requirements(missing) + "。"
                )
            for item_key, quantity in ingredients.items():
                self._change_inventory(conn, user_id, item_key, -quantity)
            self._change_inventory(conn, user_id, recipe_key, 1)
            conn.commit()
        return {
            "recipe_key": recipe_key,
            "quantity": 1,
            "ingredients": ingredients,
            "spirit_restore": int(recipe["spirit_restore"]),
            "stamina_restore": int(recipe.get("stamina_restore", 0)),
        }

    def eat_food(self, user_id: int, food_key: str) -> dict[str, Any]:
        recipe = FOOD_RECIPE_CONFIG.get(food_key)
        if recipe is None:
            raise TownLifeError("這項物品不是可以食用的料理。")
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_player(conn, user_id)
            if self._inventory_quantity(conn, user_id, food_key) <= 0:
                raise TownLifeError(f"背包裡沒有{item_name(food_key)}。")

            player = self._refresh_stamina(conn, user_id)
            spirit_full = int(player["spirit"]) >= int(player["max_spirit"])
            stamina_full = int(player["stamina"]) >= int(player["max_stamina"])
            same_day = str(player["food_stamina_date"] or "") == today_key()
            stamina_recovered_today = (
                int(player["food_stamina_recovered"] or 0) if same_day else 0
            )
            stamina_daily_remaining = max(
                0,
                MAX_DAILY_FOOD_STAMINA - stamina_recovered_today,
            )
            stamina_cap_full = stamina_daily_remaining <= 0
            if spirit_full and (stamina_full or stamina_cap_full):
                raise TownLifeError("體力與精神力目前不需要再補充，先把料理留著。")
            if (
                int(recipe.get("stamina_restore", 0)) > 0
                and not stamina_full
                and stamina_cap_full
            ):
                raise TownLifeError(
                    f"今天由料理恢復的體力已達 {MAX_DAILY_FOOD_STAMINA} 點上限。"
                    "這份料理目前只會恢復精神力，系統已替你保留，沒有消耗。"
                )

            spirit_restored = self._restore_spirit(
                conn, user_id, int(recipe["spirit_restore"])
            )
            stamina_restored, stamina, stamina_daily_remaining = self._restore_food_stamina(
                conn, user_id, int(recipe.get("stamina_restore", 0))
            )
            if spirit_restored <= 0 and stamina_restored <= 0:
                raise TownLifeError(
                    "精神力與可由料理恢復的體力都已經全滿。"
                )
            self._change_inventory(conn, user_id, food_key, -1)
            row = conn.execute(
                "SELECT spirit, max_spirit, max_stamina FROM town_life_players WHERE user_id = ?",
                (str(user_id),),
            ).fetchone()
            conn.commit()
        return {
            "food_key": food_key,
            "restored": spirit_restored,
            "spirit_restored": spirit_restored,
            "stamina_restored": stamina_restored,
            "spirit": int(row["spirit"]),
            "max_spirit": int(row["max_spirit"]),
            "stamina": stamina,
            "max_stamina": int(row["max_stamina"]),
            "stamina_daily_remaining": stamina_daily_remaining,
        }

    def rest_spirit(self, user_id: int) -> dict[str, Any]:
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            player = self._refresh_stamina(conn, user_id)
            current = int(player["spirit"])
            maximum = int(player["max_spirit"])
            if current >= maximum:
                raise TownLifeError("精神力已滿，不需要休息。")
            today = today_key()
            marker = str(player["last_rest_date"] or "")
            used = 0
            if marker.startswith(today + ":"):
                try:
                    used = int(marker.split(":", 1)[1])
                except ValueError:
                    used = 0
            if used >= 2:
                raise TownLifeError("今天已經休息兩次，明天再休息。")
            restored = min(25, maximum - current)
            used += 1
            now = now_iso()
            conn.execute(
                """
                UPDATE town_life_players
                SET spirit = ?, last_rest_date = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (current + restored, f"{today}:{used}", now, str(user_id)),
            )
            conn.commit()
        return {
            "restored": restored,
            "spirit": current + restored,
            "max_spirit": maximum,
            "remaining_uses": 2 - used,
        }

    def use_stamina_potion(self, user_id: int, item_key: str = "stamina_potion") -> dict[str, Any]:
        potion = POTION_CONFIG.get(item_key)
        if potion is None:
            raise TownLifeError("這項物品不是可使用的體力藥水。")
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_player(conn, user_id)
            if self._inventory_quantity(conn, user_id, item_key) <= 0:
                raise TownLifeError(f"背包裡沒有{item_name(item_key)}。")
            player = self._refresh_stamina(conn, user_id)
            current = int(player["stamina"])
            maximum = int(player["max_stamina"])
            if current >= maximum:
                raise TownLifeError("目前體力已滿，先把藥水留著。")
            restored = min(int(potion["stamina_restore"]), maximum - current)
            now = now_iso()
            conn.execute(
                """
                UPDATE town_life_players
                SET stamina = ?, stamina_updated_at = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (current + restored, now, now, str(user_id)),
            )
            self._change_inventory(conn, user_id, item_key, -1)
            conn.commit()
        return {
            "item_key": item_key,
            "stamina_restored": restored,
            "stamina": current + restored,
            "max_stamina": maximum,
        }

    def sell_items(self, user_id: int, category: str) -> dict[str, Any]:
        if category not in {"farming", "fishing", "crystal", "all"}:
            raise TownLifeError("找不到這個出售分類。")
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_player(conn, user_id)
            reserve = self._next_upgrade_material_reserve(conn, user_id)
            rows = conn.execute(
                """
                SELECT item_key, quantity FROM town_life_inventory
                WHERE user_id = ? AND quantity > 0
                """,
                (str(user_id),),
            ).fetchall()
            sold: dict[str, int] = {}
            protected: dict[str, int] = {}
            total = 0
            for row in rows:
                key = str(row["item_key"])
                quantity = int(row["quantity"])
                item = ITEM_CONFIG.get(key, {})
                sell_price = int(item.get("sell", 0))
                item_category = str(item.get("category", ""))
                if sell_price <= 0:
                    continue
                if category != "all" and item_category != category:
                    continue

                keep = min(quantity, int(reserve.get(key, 0))) if key in UPGRADE_MATERIAL_KEYS else 0
                sell_quantity = max(0, quantity - keep)
                if keep > 0:
                    protected[key] = keep
                if sell_quantity <= 0:
                    continue

                total += sell_price * sell_quantity
                sold[key] = sell_quantity
                self._change_inventory(conn, user_id, key, -sell_quantity)
            if total <= 0:
                if protected:
                    raise TownLifeError(
                        "目前只有受保護的升級素材，系統已保留下一階段工具需求。"
                    )
                raise TownLifeError("這個分類目前沒有可出售的物資。")
            player = self._refresh_stamina(conn, user_id)
            coins = int(player["coins"])
            now = now_iso()
            conn.execute(
                "UPDATE town_life_players SET coins = ?, updated_at = ? WHERE user_id = ?",
                (coins + total, now, str(user_id)),
            )
            conn.commit()
        return {"sold": sold, "protected": protected, "coins": total}

    def sell_item(
        self,
        user_id: int,
        item_key: str,
        quantity: int | None,
    ) -> dict[str, Any]:
        """Sell one selected item while preserving future tool-upgrade materials.

        Passing ``None`` sells every currently sellable copy. A numeric request is
        exact: it is rejected when fewer copies are available, so a stale button
        can never silently sell a different quantity than its label promised.
        """
        item = ITEM_CONFIG.get(item_key)
        if item is None:
            raise TownLifeError("找不到這項物品。")
        sell_price = int(item.get("sell", 0))
        if sell_price <= 0:
            raise TownLifeError(f"{item_name(item_key)}不可出售。")
        if quantity is not None and int(quantity) <= 0:
            raise TownLifeError("出售數量必須大於零。")

        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_player(conn, user_id)
            owned = self._inventory_quantity(conn, user_id, item_key)
            reserve = self._next_upgrade_material_reserve(conn, user_id)
            protected = (
                min(owned, int(reserve.get(item_key, 0)))
                if item_key in UPGRADE_MATERIAL_KEYS
                else 0
            )
            sellable = max(0, owned - protected)
            if sellable <= 0:
                if protected > 0:
                    raise TownLifeError(
                        f"{item_name(item_key)}目前都屬於受保護的升級素材，"
                        "沒有可出售的數量。"
                    )
                raise TownLifeError(f"背包裡沒有可出售的{item_name(item_key)}。")

            sell_quantity = sellable if quantity is None else int(quantity)
            if sell_quantity > sellable:
                raise TownLifeError(
                    f"{item_name(item_key)}目前可出售 {sellable} 個，"
                    f"不足以出售 {sell_quantity} 個。"
                )

            total = sell_price * sell_quantity
            self._change_inventory(conn, user_id, item_key, -sell_quantity)
            player = self._refresh_stamina(conn, user_id)
            coins = int(player["coins"])
            now = now_iso()
            conn.execute(
                "UPDATE town_life_players SET coins = ?, updated_at = ? WHERE user_id = ?",
                (coins + total, now, str(user_id)),
            )
            conn.commit()

        return {
            "item_key": item_key,
            "quantity": sell_quantity,
            "unit_price": sell_price,
            "coins": total,
            "protected": protected,
        }

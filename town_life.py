from __future__ import annotations

import random
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


TAIPEI_TIMEZONE = ZoneInfo("Asia/Taipei")
STAMINA_REGEN_MINUTES = 10
MAX_TOOL_LEVEL = 5
MAX_CAREER_LEVEL = 5
INITIAL_COINS = 600
INITIAL_STAMINA = 100
MAX_STAMINA = 100


class TownLifeError(RuntimeError):
    """城下町生活職業操作無法完成。"""


TOOL_CONFIG: dict[str, dict[str, Any]] = {
    "farm_tools": {
        "name": "農具組",
        "route": "農牧師",
        "costs": [180, 450, 900, 1800, 3600],
        "description": "用於播種與收成；等級越高，農作物收成越多。",
    },
    "fishing_rod": {
        "name": "釣具組",
        "route": "漁採師",
        "costs": [220, 500, 1000, 2000, 4000],
        "description": "用於河岸釣魚；等級越高，稀有魚獲機率越高。",
    },
    "pickaxe": {
        "name": "挖礦工具",
        "route": "魔晶礦師",
        "costs": [260, 600, 1200, 2400, 4800],
        "description": "用於礦坑採掘；二級起有機會發現魔法水晶。",
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
                    stamina INTEGER NOT NULL DEFAULT 100,
                    max_stamina INTEGER NOT NULL DEFAULT 100,
                    stamina_updated_at TEXT NOT NULL,
                    last_rest_date TEXT NOT NULL DEFAULT '',
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

                CREATE INDEX IF NOT EXISTS idx_town_life_inventory_user
                ON town_life_inventory(user_id);
                """
            )
            conn.commit()

    def _ensure_player(self, conn: sqlite3.Connection, user_id: int) -> None:
        uid = str(user_id)
        now = now_iso()
        conn.execute(
            """
            INSERT OR IGNORE INTO town_life_players (
                user_id, coins, stamina, max_stamina, stamina_updated_at,
                last_rest_date, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, '', ?, ?)
            """,
            (uid, INITIAL_COINS, INITIAL_STAMINA, MAX_STAMINA, now, now, now),
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

        current = int(row["stamina"])
        maximum = int(row["max_stamina"])
        updated_at = parse_time(str(row["stamina_updated_at"]))
        elapsed = taipei_now() - updated_at
        recovered = max(0, int(elapsed.total_seconds() // (STAMINA_REGEN_MINUTES * 60)))
        if recovered > 0:
            now = now_iso()
            if current >= maximum:
                new_value = maximum
                new_stamp = taipei_now()
            else:
                new_value = min(maximum, current + recovered)
                if new_value >= maximum:
                    new_stamp = taipei_now()
                else:
                    consumed_minutes = recovered * STAMINA_REGEN_MINUTES
                    new_stamp = updated_at + timedelta(minutes=consumed_minutes)
            conn.execute(
                """
                UPDATE town_life_players
                SET stamina = ?, stamina_updated_at = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (new_value, new_stamp.isoformat(timespec="seconds"), now, uid),
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
            conn.commit()
        return {
            "player": dict(player_row),
            "tools": tools,
            "careers": careers,
            "inventory": inventory,
            "plots": plots,
            "animals": animals,
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
            cost = int(TOOL_CONFIG[tool_key]["costs"][level])
            player = self._refresh_stamina(conn, user_id)
            coins = int(player["coins"])
            if coins < cost:
                raise TownLifeError(f"金幣不足。需要 {cost}，目前只有 {coins}。")
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
        return {"tool_key": tool_key, "level": new_level, "cost": cost}

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
                raise TownLifeError(f"金幣不足。需要 {total}，目前只有 {coins}。")
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
                raise TownLifeError("要先到工具商店購買農具組，才能播種。")

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
            self._spend_stamina(conn, user_id, max(1, len(ready_rows) * 2))
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
        return {"rewards": rewards, "exp_gain": exp_gain, "level": level, "exp": exp}

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
                raise TownLifeError(f"金幣不足。購買{animal['name']}需要 {cost}。")
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
        return {"animal_key": animal_key, "quantity": current + 1, "cost": cost}

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
        }

    def forage(self, user_id: int) -> dict[str, Any]:
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_player(conn, user_id)
            self._spend_stamina(conn, user_id, 6)
            item_key = random.choices(
                ["wild_berry", "wild_herb", "branch"],
                weights=[50, 30, 20],
                k=1,
            )[0]
            quantity = random.randint(1, 2)
            self._change_inventory(conn, user_id, item_key, quantity)
            level, exp = self._add_career_exp(conn, user_id, "fishing", 5)
            conn.commit()
        return {"item_key": item_key, "quantity": quantity, "level": level, "exp": exp}

    def fish(self, user_id: int) -> dict[str, Any]:
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_player(conn, user_id)
            tool_row = conn.execute(
                "SELECT level FROM town_life_tools WHERE user_id = ? AND tool_key = 'fishing_rod'",
                (str(user_id),),
            ).fetchone()
            tool_level = int(tool_row["level"] if tool_row is not None else 0)
            if tool_level <= 0:
                raise TownLifeError("要先到工具商店購買釣具組。")
            stamina_cost = max(5, 10 - tool_level)
            self._spend_stamina(conn, user_id, stamina_cost)
            if tool_level == 1:
                items, weights = ["river_fish", "silver_carp", "old_boot"], [72, 18, 10]
            elif tool_level == 2:
                items, weights = ["river_fish", "silver_carp", "moon_trout", "old_boot"], [58, 28, 5, 9]
            else:
                items, weights = ["river_fish", "silver_carp", "moon_trout", "old_boot"], [45, 35, 15 + tool_level, 5]
            item_key = random.choices(items, weights=weights, k=1)[0]
            quantity = 1 + (1 if tool_level >= 4 and random.random() < 0.25 else 0)
            self._change_inventory(conn, user_id, item_key, quantity)
            exp_gain = 4 if item_key == "old_boot" else (12 if item_key == "moon_trout" else 8)
            level, exp = self._add_career_exp(conn, user_id, "fishing", exp_gain)
            conn.commit()
        return {
            "item_key": item_key,
            "quantity": quantity,
            "stamina_cost": stamina_cost,
            "level": level,
            "exp": exp,
        }

    def mine(self, user_id: int) -> dict[str, Any]:
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_player(conn, user_id)
            tool_row = conn.execute(
                "SELECT level FROM town_life_tools WHERE user_id = ? AND tool_key = 'pickaxe'",
                (str(user_id),),
            ).fetchone()
            tool_level = int(tool_row["level"] if tool_row is not None else 0)
            if tool_level <= 0:
                raise TownLifeError("要先到工具商店購買挖礦工具。")
            stamina_cost = max(6, 12 - tool_level)
            self._spend_stamina(conn, user_id, stamina_cost)
            items = ["stone", "copper_ore", "iron_ore"]
            weights = [60, 30, 10]
            if tool_level >= 2:
                items.append("raw_crystal")
                weights.append(4 + tool_level * 3)
            item_key = random.choices(items, weights=weights, k=1)[0]
            quantity = 1
            if item_key == "stone":
                quantity += random.randint(0, max(1, tool_level // 2))
            elif tool_level >= 4 and random.random() < 0.2:
                quantity += 1
            self._change_inventory(conn, user_id, item_key, quantity)
            exp_gain = 15 if item_key == "raw_crystal" else (10 if item_key == "iron_ore" else 7)
            level, exp = self._add_career_exp(conn, user_id, "crystal", exp_gain)
            conn.commit()
        return {
            "item_key": item_key,
            "quantity": quantity,
            "stamina_cost": stamina_cost,
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
            self._change_inventory(conn, user_id, "raw_crystal", -2)
            self._change_inventory(conn, user_id, "iron_ore", -1)
            self._change_inventory(conn, user_id, "refined_crystal", 1)
            level, exp = self._add_career_exp(conn, user_id, "crystal", 25)
            conn.commit()
        return {"quantity": 1, "level": level, "exp": exp}

    def sell_items(self, user_id: int, category: str) -> dict[str, Any]:
        if category not in {"farming", "fishing", "crystal", "all"}:
            raise TownLifeError("找不到這個出售分類。")
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._ensure_player(conn, user_id)
            rows = conn.execute(
                """
                SELECT item_key, quantity FROM town_life_inventory
                WHERE user_id = ? AND quantity > 0
                """,
                (str(user_id),),
            ).fetchall()
            sold: dict[str, int] = {}
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
                total += sell_price * quantity
                sold[key] = quantity
                conn.execute(
                    "DELETE FROM town_life_inventory WHERE user_id = ? AND item_key = ?",
                    (str(user_id), key),
                )
            if total <= 0:
                raise TownLifeError("這個分類目前沒有可出售的物資。")
            player = self._refresh_stamina(conn, user_id)
            coins = int(player["coins"])
            now = now_iso()
            conn.execute(
                "UPDATE town_life_players SET coins = ?, updated_at = ? WHERE user_id = ?",
                (coins + total, now, str(user_id)),
            )
            conn.commit()
        return {"sold": sold, "coins": total}

    def daily_rest(self, user_id: int) -> dict[str, Any]:
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            player = self._refresh_stamina(conn, user_id)
            if str(player["last_rest_date"]) == today_key():
                raise TownLifeError("今天已經休息過一次。體力仍會每 10 分鐘自然回復 1 點。")
            current = int(player["stamina"])
            maximum = int(player["max_stamina"])
            recovered = min(40, maximum - current)
            if recovered <= 0:
                raise TownLifeError("目前體力已滿，不需要消耗今天的休息次數。")
            now = now_iso()
            conn.execute(
                """
                UPDATE town_life_players
                SET stamina = ?, stamina_updated_at = ?, last_rest_date = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (current + recovered, now, today_key(), now, str(user_id)),
            )
            conn.commit()
        return {"recovered": recovered, "stamina": current + recovered}

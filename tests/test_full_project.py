from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_TEMP = tempfile.TemporaryDirectory(prefix="stern-monk-import-")
os.environ["MONK_DB_PATH"] = str(Path(MODULE_TEMP.name) / "import.db")
os.environ["MONK_CHANNEL_ID"] = "123456789"

import main  # noqa: E402
import town_life  # noqa: E402
from town_life import (  # noqa: E402
    ANIMAL_CONFIG,
    FOOD_RECIPE_CONFIG,
    ITEM_CONFIG,
    MAX_TOOL_LEVEL,
    TOOL_CONFIG,
    TownLifeDatabase,
    TownLifeError,
)


class FakeResponse:
    def __init__(self) -> None:
        self.done = False
        self.messages: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.edits: list[dict[str, object]] = []

    def is_done(self) -> bool:
        return self.done

    async def send_message(self, *args: object, **kwargs: object) -> None:
        self.done = True
        self.messages.append((args, kwargs))

    async def edit_message(self, **kwargs: object) -> None:
        self.done = True
        self.edits.append(kwargs)

    async def defer(self, **kwargs: object) -> None:
        self.done = True


class FakeFollowup:
    def __init__(self) -> None:
        self.messages: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def send(self, *args: object, **kwargs: object) -> None:
        self.messages.append((args, kwargs))


class FakeInteraction:
    def __init__(
        self,
        *,
        user_id: int = 1001,
        message: object | None = None,
        original_message: object | None = None,
    ) -> None:
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self.original_edits: list[dict[str, object]] = []
        self.user = SimpleNamespace(
            id=int(user_id),
            display_name="測試玩家",
        )
        self.message = message
        self.original_message = original_message
        self.channel_id = 123456789

    async def edit_original_response(self, **kwargs: object) -> object | None:
        self.original_edits.append(kwargs)
        return self.original_message


class FakeMessage:
    def __init__(
        self,
        message_id: int = 987654321,
        channel_id: int = 123456789,
    ) -> None:
        self.id = int(message_id)
        self.channel = SimpleNamespace(id=int(channel_id))
        self.edits: list[dict[str, object]] = []

    async def edit(self, **kwargs: object) -> None:
        self.edits.append(kwargs)


class DatabaseCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="stern-monk-test-")
        self.db = TownLifeDatabase(Path(self.temp_dir.name) / "test.db")
        self.db.initialize()
        self.original_db = main.TOWN_LIFE_DB
        main.TOWN_LIFE_DB = self.db
        self.user_id = 1001
        self.db.get_snapshot(self.user_id)

    def tearDown(self) -> None:
        main.TOWN_LIFE_DB = self.original_db
        self.temp_dir.cleanup()

    def transaction(self, *statements: tuple[str, tuple[object, ...]]) -> None:
        with closing(self.db.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self.db._ensure_player(conn, self.user_id)
            for sql, params in statements:
                conn.execute(sql, params)
            conn.commit()

    def set_player(self, **values: object) -> None:
        assignments = ", ".join(f"{key} = ?" for key in values)
        self.transaction(
            (
                f"UPDATE town_life_players SET {assignments} WHERE user_id = ?",
                (*values.values(), str(self.user_id)),
            )
        )

    def set_tool(self, tool_key: str, level: int) -> None:
        self.transaction(
            (
                "UPDATE town_life_tools SET level = ? WHERE user_id = ? AND tool_key = ?",
                (level, str(self.user_id), tool_key),
            )
        )

    def set_career(self, career_key: str, level: int, exp: int = 0) -> None:
        self.transaction(
            (
                "UPDATE town_life_careers SET level = ?, exp = ? "
                "WHERE user_id = ? AND career_key = ?",
                (level, exp, str(self.user_id), career_key),
            )
        )

    def add_item(self, item_key: str, quantity: int) -> None:
        with closing(self.db.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self.db._ensure_player(conn, self.user_id)
            self.db._change_inventory(conn, self.user_id, item_key, quantity)
            conn.commit()


class ProjectStaticTests(DatabaseCase):
    def test_syntax_and_public_commands(self) -> None:
        compile((PROJECT_ROOT / "main.py").read_text(encoding="utf-8"), "main.py", "exec")
        compile(
            (PROJECT_ROOT / "town_life.py").read_text(encoding="utf-8"),
            "town_life.py",
            "exec",
        )
        self.assertEqual(
            [command.name for command in main.tree.get_commands()],
            ["學生資料", "城下町", "今日穿搭推薦", "下載目前備份", "修士狀態"],
        )
        source = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
        self.assertNotIn('name="我的"', source)
        self.assertNotIn("公開斜線指令數量：**6**", source)

    def test_railway_and_requirements(self) -> None:
        railway = (PROJECT_ROOT / "railway.toml").read_text(encoding="utf-8")
        requirements = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn('startCommand = "python main.py"', railway)
        self.assertIn("discord.py>=2.5,<3.0", requirements)
        self.assertIn("openai>=1.0,<3.0", requirements)
        self.assertIn("audioop-lts", requirements)

    def test_legacy_priest_tutorial_removed(self) -> None:
        source = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
        for marker in (
            "TeachingHubView",
            "KNOWLEDGE",
            "TEACHING_CHOICES",
            "_send_tutorial",
            "AI 教學",
        ):
            self.assertNotIn(marker, source)
        self.assertFalse((PROJECT_ROOT / "data" / "tutorials_zh_tw.json").exists())
        self.assertFalse((PROJECT_ROOT / "data" / "faq_zh_tw.json").exists())
        self.assertTrue((PROJECT_ROOT / "data" / "dialogue.json").is_file())

    def test_all_item_assets_or_safe_fallback(self) -> None:
        item_root = PROJECT_ROOT / "assets" / "town_life" / "items"
        present = {path.stem for path in item_root.glob("*.png")}
        expected = set(ITEM_CONFIG) | set(TOOL_CONFIG)
        self.assertEqual(expected, present)
        manifest = json.loads((item_root / "item-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(
            {Path(item["filename"]).stem for item in manifest["items"]},
            expected,
        )
        validation = json.loads((item_root / "validation.json").read_text(encoding="utf-8"))
        self.assertEqual(int(validation["count"]), len(expected))
        self.assertEqual(
            {Path(item["filename"]).stem for item in validation["files"]},
            expected,
        )
        for path in item_root.glob("*.png"):
            self.assertEqual(path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        original = main.TOWN_LIFE_ITEM_ASSET_ROOT
        with tempfile.TemporaryDirectory(prefix="missing-assets-") as empty:
            main.TOWN_LIFE_ITEM_ASSET_ROOT = Path(empty)
            try:
                embed = main.monk_embed("測試", "缺圖仍應可顯示")
                returned = main._town_life_embed_with_item_thumbnail(embed, "egg")
                self.assertIsNone(returned.thumbnail.url)
                self.assertEqual(main.town_life_item_attachments("egg"), [])
            finally:
                main.TOWN_LIFE_ITEM_ASSET_ROOT = original

    def test_scene_assets_and_attachment_names(self) -> None:
        for key in ("farming", "ranch", "fishing", "crystal", "stove"):
            path = main.TOWN_LIFE_ASSET_ROOT / main.TOWN_LIFE_ROUTE_IMAGES[key]
            raw = path.read_bytes()
            self.assertEqual(raw[:4], b"RIFF")
            self.assertEqual(raw[8:12], b"WEBP")
            files = main.town_life_route_attachments(key)
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].filename, path.name)
            files[0].close()

    def test_main_views_construct_and_callbacks_are_bound(self) -> None:
        views = [
            main.TownLifeHubView(self.user_id),
            main.ToolShopView(self.user_id),
            main.FarmRouteView(self.user_id),
            main.RanchView(self.user_id),
            main.FishingRouteView(self.user_id),
            main.CrystalRouteView(self.user_id),
            main.StoveView(self.user_id),
            main.InventoryMarketView(self.user_id),
            main.MailboxView(self.user_id),
            *(main.WorkshopView(self.user_id, route) for route in ("farming", "fishing", "crystal")),
        ]
        for view in views:
            self.assertTrue(view.children)
            for child in view.children:
                self.assertIsNotNone(child.callback)

    def test_batch_action_buttons_are_available(self) -> None:
        fishing_labels = {
            str(getattr(child, "label", ""))
            for child in main.FishingRouteView(self.user_id).children
        }
        self.assertTrue(
            {
                "釣魚×1",
                "釣魚×5",
                "釣魚×10",
                "釣魚｜100體",
                "採集×1",
                "採集×5",
                "採集×10",
                "採集｜100體",
            }.issubset(fishing_labels)
        )

        mining_labels = {
            str(getattr(child, "label", ""))
            for child in main.CrystalRouteView(self.user_id).children
        }
        self.assertTrue(
            {
                "外圍×1",
                "外圍×3",
                "外圍×5",
                "外圍｜100體",
                "深層×1",
                "深層×3",
                "深層×5",
                "深層｜100體",
                "洞窟×1",
                "洞窟×3",
                "洞窟×5",
                "洞窟｜100體",
            }.issubset(mining_labels)
        )

    def test_startup_hook_without_discord_network(self) -> None:
        original_academy = main.ACADEMY_DB
        main.ACADEMY_DB = main.AcademyDatabase(Path(self.temp_dir.name) / "startup.db")

        async def run() -> None:
            with mock.patch.object(main.client.tree, "sync", new=mock.AsyncMock(return_value=[])):
                await main.client.setup_hook()

        try:
            asyncio.run(run())
            self.assertTrue(self.db.path.is_file())
        finally:
            main.ACADEMY_DB = original_academy


class PlayerAndTransactionTests(DatabaseCase):
    def test_new_and_existing_player_data(self) -> None:
        snapshot = self.db.get_snapshot(self.user_id)
        self.assertEqual(int(snapshot["player"]["coins"]), 600)
        self.assertEqual(int(snapshot["player"]["stamina"]), 1000)
        self.assertEqual(int(snapshot["player"]["spirit"]), 100)
        self.set_player(coins=4321, stamina=777, spirit=45)
        self.db.initialize()
        preserved = self.db.get_snapshot(self.user_id)
        self.assertEqual(int(preserved["player"]["coins"]), 4321)
        self.assertEqual(int(preserved["player"]["stamina"]), 777)
        self.assertEqual(int(preserved["player"]["spirit"]), 45)

    def test_cross_day_resets_stamina_but_not_spirit(self) -> None:
        yesterday = (town_life.taipei_now() - timedelta(days=1)).isoformat(timespec="seconds")
        self.set_player(stamina=5, spirit=17, stamina_updated_at=yesterday)
        snapshot = self.db.get_snapshot(self.user_id)
        self.assertEqual(int(snapshot["player"]["stamina"]), 1000)
        self.assertEqual(int(snapshot["player"]["spirit"]), 17)
        with closing(self.db.connect()) as conn:
            row = conn.execute(
                "SELECT stamina, spirit FROM town_life_players WHERE user_id = ?",
                (str(self.user_id),),
            ).fetchone()
        self.assertEqual((int(row["stamina"]), int(row["spirit"])), (1000, 17))

    def test_stamina_recovers_one_point_per_complete_minute(self) -> None:
        current_time = town_life.taipei_now().replace(
            hour=12,
            minute=0,
            second=30,
            microsecond=0,
        )
        updated_at = current_time - timedelta(minutes=5, seconds=30)
        self.set_player(stamina=100, stamina_updated_at=updated_at.isoformat())

        with mock.patch.object(town_life, "taipei_now", return_value=current_time):
            snapshot = self.db.get_snapshot(self.user_id)

        self.assertEqual(snapshot["player"]["stamina"], 105)
        self.assertEqual(
            snapshot["player"]["stamina_updated_at"],
            (current_time - timedelta(seconds=30)).isoformat(timespec="seconds"),
        )

    def test_full_stamina_does_not_bank_recovery_time(self) -> None:
        current_time = town_life.taipei_now().replace(
            hour=12,
            minute=0,
            second=0,
            microsecond=0,
        )
        self.set_player(
            stamina=1000,
            stamina_updated_at=(current_time - timedelta(hours=2)).isoformat(),
        )

        with mock.patch.object(town_life, "taipei_now", return_value=current_time):
            snapshot = self.db.get_snapshot(self.user_id)

        self.assertEqual(snapshot["player"]["stamina"], 1000)
        self.assertEqual(
            snapshot["player"]["stamina_updated_at"],
            current_time.isoformat(timespec="seconds"),
        )

    def test_stamina_potion_restarts_natural_recovery_timer(self) -> None:
        current_time = town_life.taipei_now().replace(
            hour=12,
            minute=0,
            second=0,
            microsecond=0,
        )
        self.set_player(
            stamina=500,
            stamina_updated_at=(current_time - timedelta(minutes=5)).isoformat(),
        )
        self.add_item("stamina_potion", 1)

        with mock.patch.object(town_life, "taipei_now", return_value=current_time):
            result = self.db.use_stamina_potion(self.user_id)

        self.assertEqual(result["stamina"], 755)
        with closing(self.db.connect()) as conn:
            row = conn.execute(
                "SELECT stamina_updated_at FROM town_life_players WHERE user_id = ?",
                (str(self.user_id),),
            ).fetchone()
        self.assertEqual(
            row["stamina_updated_at"],
            current_time.isoformat(timespec="seconds"),
        )

    def test_maintenance_mail_is_seeded_once_for_existing_players(self) -> None:
        marker_key = f"mail_issued:{town_life.MAINTENANCE_MAIL_KEY}"
        self.transaction(
            (
                "DELETE FROM town_life_mailbox WHERE user_id = ?",
                (str(self.user_id),),
            ),
            (
                "DELETE FROM town_life_system_markers WHERE marker_key = ?",
                (marker_key,),
            ),
        )

        self.db.initialize()
        first = self.db.get_snapshot(self.user_id)["mailbox"]
        self.db.initialize()
        second = self.db.get_snapshot(self.user_id)["mailbox"]

        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertEqual(first[0]["mail_key"], town_life.MAINTENANCE_MAIL_KEY)
        self.assertEqual(first[0]["item_key"], "maintenance_stamina_potion")
        self.assertEqual(first[0]["quantity"], 1)
        self.assertEqual(first[0]["claimed_at"], "")

        later_user = self.user_id + 1
        self.db.get_snapshot(later_user)
        self.assertEqual(self.db.get_snapshot(later_user)["mailbox"], [])

    def test_mail_claim_is_atomic_and_cannot_be_repeated(self) -> None:
        inserted = self.db.issue_mail(
            self.user_id,
            mail_key="test_reward",
            title="測試補償",
            body="測試信件",
            item_key="maintenance_stamina_potion",
            quantity=1,
        )
        duplicate = self.db.issue_mail(
            self.user_id,
            mail_key="test_reward",
            title="測試補償",
            body="測試信件",
            item_key="maintenance_stamina_potion",
            quantity=1,
        )
        self.assertTrue(inserted)
        self.assertFalse(duplicate)

        result = self.db.claim_all_mail(self.user_id)
        snapshot = self.db.get_snapshot(self.user_id)

        self.assertEqual(result["claimed_count"], 1)
        self.assertEqual(result["rewards"], {"maintenance_stamina_potion": 1})
        self.assertEqual(snapshot["inventory"]["maintenance_stamina_potion"], 1)
        self.assertTrue(snapshot["mailbox"][0]["claimed_at"])
        with self.assertRaises(TownLifeError):
            self.db.claim_all_mail(self.user_id)
        self.assertEqual(
            self.db.get_snapshot(self.user_id)["inventory"]["maintenance_stamina_potion"],
            1,
        )

    def test_mail_claim_database_failure_rolls_back_attachment_and_status(self) -> None:
        self.db.issue_mail(
            self.user_id,
            mail_key="rollback_reward",
            title="回滾測試",
            body="測試信件",
            item_key="maintenance_stamina_potion",
            quantity=1,
        )

        with mock.patch.object(
            self.db,
            "_change_inventory",
            side_effect=sqlite3.OperationalError("forced failure"),
        ):
            with self.assertRaises(sqlite3.OperationalError):
                self.db.claim_all_mail(self.user_id)

        snapshot = self.db.get_snapshot(self.user_id)
        self.assertEqual(
            snapshot["inventory"].get("maintenance_stamina_potion", 0),
            0,
        )
        self.assertEqual(snapshot["mailbox"][0]["claimed_at"], "")

    def test_chicken_purchase_contract_and_balance(self) -> None:
        self.set_player(coins=5000)
        self.set_tool("farm_tools", 1)
        before = self.db.get_snapshot(self.user_id)
        result = self.db.buy_animal(self.user_id, "chicken")
        after = self.db.get_snapshot(self.user_id)
        self.assertEqual(result["product"], "egg")
        self.assertEqual(int(result["cost"]), int(ANIMAL_CONFIG["chicken"]["cost"]))
        self.assertEqual(int(after["animals"]["chicken"]["quantity"]), 1)
        self.assertEqual(
            int(after["player"]["coins"]),
            int(before["player"]["coins"]) - int(ANIMAL_CONFIG["chicken"]["cost"]),
        )

    def test_cow_purchase_contract_and_balance(self) -> None:
        self.set_player(coins=5000)
        self.set_tool("farm_tools", 2)
        result = self.db.buy_animal(self.user_id, "cow")
        snapshot = self.db.get_snapshot(self.user_id)
        self.assertEqual(result["product"], "milk")
        self.assertEqual(int(snapshot["animals"]["cow"]["quantity"]), 1)
        self.assertEqual(
            int(snapshot["player"]["coins"]),
            5000 - int(ANIMAL_CONFIG["cow"]["cost"]),
        )

    def test_animal_unlock_coin_and_limit_failures_do_not_mutate(self) -> None:
        self.set_player(coins=5000)
        with self.assertRaises(TownLifeError):
            self.db.buy_animal(self.user_id, "chicken")
        self.set_tool("farm_tools", 1)
        with self.assertRaises(TownLifeError):
            self.db.buy_animal(self.user_id, "cow")
        self.set_player(coins=0)
        with self.assertRaises(TownLifeError):
            self.db.buy_animal(self.user_id, "chicken")
        self.set_player(coins=5000)
        self.transaction(
            (
                "UPDATE town_life_animals SET quantity = 10 "
                "WHERE user_id = ? AND animal_key = 'chicken'",
                (str(self.user_id),),
            )
        )
        before = self.db.get_snapshot(self.user_id)
        with self.assertRaises(TownLifeError):
            self.db.buy_animal(self.user_id, "chicken")
        after = self.db.get_snapshot(self.user_id)
        self.assertEqual(before["player"]["coins"], after["player"]["coins"])
        self.assertEqual(after["animals"]["chicken"]["quantity"], 10)

    def test_forced_database_failure_rolls_back_animal_and_coins(self) -> None:
        self.set_player(coins=5000)
        self.set_tool("farm_tools", 2)
        before = self.db.get_snapshot(self.user_id)
        self.transaction(
            (
                "CREATE TRIGGER fail_animal_update BEFORE UPDATE ON town_life_animals "
                "BEGIN SELECT RAISE(ABORT, 'forced test failure'); END",
                (),
            )
        )
        with self.assertRaises(sqlite3.DatabaseError):
            self.db.buy_animal(self.user_id, "cow")
        after = self.db.get_snapshot(self.user_id)
        self.assertEqual(before["player"]["coins"], after["player"]["coins"])
        self.assertEqual(before["animals"]["cow"], after["animals"]["cow"])

    def _prepare_animal_collection(self, animal_key: str, quantity: int) -> None:
        self.add_item("animal_feed", quantity)
        self.transaction(
            (
                "UPDATE town_life_animals SET quantity = ?, last_collect_date = '' "
                "WHERE user_id = ? AND animal_key = ?",
                (quantity, str(self.user_id), animal_key),
            )
        )

    def test_collect_eggs(self) -> None:
        self._prepare_animal_collection("chicken", 2)
        result = self.db.collect_animal_product(self.user_id, "chicken")
        snapshot = self.db.get_snapshot(self.user_id)
        self.assertEqual(result["product"], "egg")
        self.assertEqual(int(result["quantity"]), 2)
        self.assertEqual(int(snapshot["inventory"]["egg"]), 2)
        self.assertNotIn("animal_feed", snapshot["inventory"])
        with self.assertRaises(TownLifeError):
            self.db.collect_animal_product(self.user_id, "chicken")

    def test_collect_milk(self) -> None:
        self._prepare_animal_collection("cow", 3)
        result = self.db.collect_animal_product(self.user_id, "cow")
        snapshot = self.db.get_snapshot(self.user_id)
        self.assertEqual(result["product"], "milk")
        self.assertEqual(int(snapshot["inventory"]["milk"]), 3)

    def test_all_tools_level_zero_to_five_and_max(self) -> None:
        self.set_player(coins=1_000_000, spirit=100, max_spirit=100)
        for key in ITEM_CONFIG:
            self.add_item(key, 500)
        for tool_key in TOOL_CONFIG:
            self.set_tool(tool_key, 0)
            for expected_level in range(1, MAX_TOOL_LEVEL + 1):
                result = self.db.buy_or_upgrade_tool(self.user_id, tool_key)
                self.assertEqual(int(result["level"]), expected_level)
            before = self.db.get_snapshot(self.user_id)
            with self.assertRaises(TownLifeError):
                self.db.buy_or_upgrade_tool(self.user_id, tool_key)
            after = self.db.get_snapshot(self.user_id)
            self.assertEqual(before["tools"][tool_key], after["tools"][tool_key])

    def test_max_tool_button_is_disabled(self) -> None:
        self.set_tool("farm_tools", MAX_TOOL_LEVEL)
        view = main.WorkshopView(self.user_id, "farming")
        button = next(child for child in view.children if "最高等級" in str(getattr(child, "label", "")))
        self.assertTrue(button.disabled)

    def test_tool_upgrade_resource_failures_rollback(self) -> None:
        self.set_player(coins=0, spirit=0)
        before = self.db.get_snapshot(self.user_id)
        with self.assertRaises(TownLifeError):
            self.db.buy_or_upgrade_tool(self.user_id, "pickaxe")
        after = self.db.get_snapshot(self.user_id)
        self.assertEqual(before["player"]["coins"], after["player"]["coins"])
        self.assertEqual(before["tools"], after["tools"])

    def test_crop_purchase_plant_and_harvest(self) -> None:
        self.set_player(coins=5000, stamina=1000, spirit=100)
        self.set_tool("farm_tools", 1)
        bought = self.db.buy_supply(self.user_id, "wheat_seed", 5)
        self.assertEqual(int(bought["quantity"]), 5)
        planted = self.db.plant_crop(self.user_id, "wheat")
        self.assertEqual(int(planted["planted"]), 3)
        self.transaction(
            (
                "UPDATE town_life_plots SET ready_at = ? "
                "WHERE user_id = ? AND crop_key <> ''",
                ("2000-01-01T00:00:00+08:00", str(self.user_id)),
            )
        )
        harvested = self.db.harvest_ready_crops(self.user_id)
        self.assertTrue(harvested["rewards"])
        self.assertTrue(all(not plot["crop_key"] for plot in self.db.get_snapshot(self.user_id)["plots"]))

    def test_fishing_forage_and_mining_requirements(self) -> None:
        with self.assertRaises(TownLifeError):
            self.db.fish(self.user_id)
        self.assertIn(self.db.forage(self.user_id)["item_key"], {"wild_berry", "wild_herb", "branch"})
        self.set_tool("fishing_rod", 1)
        self.assertIn(self.db.fish(self.user_id)["item_key"], {"river_fish", "silver_carp", "old_boot"})
        with self.assertRaises(TownLifeError):
            self.db.mine(self.user_id, "outer_tunnel")
        self.set_tool("pickaxe", 1)
        self.assertIn(self.db.mine(self.user_id, "outer_tunnel")["item_key"], ITEM_CONFIG)
        with self.assertRaises(TownLifeError):
            self.db.mine(self.user_id, "crystal_cavern")
        self.set_tool("pickaxe", 4)
        self.set_career("crystal", 3)
        self.assertIn(self.db.mine(self.user_id, "crystal_cavern")["item_key"], ITEM_CONFIG)

    def test_forage_batch_aggregates_rewards_and_costs(self) -> None:
        self.set_player(stamina=1000, stamina_updated_at=town_life.now_iso())
        with (
            mock.patch("town_life.random.choices", return_value=["wild_berry"]),
            mock.patch("town_life.random.randint", return_value=2),
        ):
            result = self.db.forage(self.user_id, 10)

        snapshot = self.db.get_snapshot(self.user_id)
        self.assertEqual(int(result["attempts_completed"]), 10)
        self.assertEqual(int(result["stamina_cost"]), 60)
        self.assertEqual(result["rewards"], {"wild_berry": 20})
        self.assertEqual(int(snapshot["inventory"]["wild_berry"]), 20)
        self.assertEqual(int(snapshot["player"]["stamina"]), 940)
        self.assertEqual(int(snapshot["careers"]["fishing"]["exp"]), 50)

    def test_batch_partially_completes_when_stamina_is_low(self) -> None:
        self.set_player(stamina=17, stamina_updated_at=town_life.now_iso())
        with (
            mock.patch("town_life.random.choices", return_value=["branch"]),
            mock.patch("town_life.random.randint", return_value=1),
        ):
            result = self.db.forage(self.user_id, 10)

        snapshot = self.db.get_snapshot(self.user_id)
        self.assertEqual(int(result["attempts_requested"]), 10)
        self.assertEqual(int(result["attempts_completed"]), 2)
        self.assertEqual(int(result["stamina_cost"]), 12)
        self.assertEqual(int(snapshot["player"]["stamina"]), 5)
        self.assertEqual(int(snapshot["inventory"]["branch"]), 2)

    def test_hundred_stamina_budget_never_overspends(self) -> None:
        self.set_player(stamina=1000, stamina_updated_at=town_life.now_iso())
        with (
            mock.patch("town_life.random.choices", return_value=["wild_herb"]),
            mock.patch("town_life.random.randint", return_value=1),
        ):
            result = self.db.forage(
                self.user_id,
                100,
                stamina_budget=100,
            )

        self.assertEqual(int(result["attempts_completed"]), 16)
        self.assertEqual(int(result["stamina_cost"]), 96)
        self.assertLessEqual(int(result["stamina_cost"]), 100)
        self.assertEqual(result["stamina_budget"], 100)

    def test_fishing_batch_keeps_individual_drop_and_exp_rules(self) -> None:
        self.set_player(stamina=1000, stamina_updated_at=town_life.now_iso())
        self.set_tool("fishing_rod", 1)
        drops = [
            ["river_fish"],
            ["old_boot"],
            ["silver_carp"],
            ["river_fish"],
            ["old_boot"],
        ]
        with mock.patch("town_life.random.choices", side_effect=drops):
            result = self.db.fish(self.user_id, 5)

        snapshot = self.db.get_snapshot(self.user_id)
        self.assertEqual(
            result["rewards"],
            {"river_fish": 2, "old_boot": 2, "silver_carp": 1},
        )
        self.assertEqual(int(result["stamina_cost"]), 45)
        self.assertEqual(int(snapshot["careers"]["fishing"]["exp"]), 32)

    def test_mining_batch_is_limited_by_spirit(self) -> None:
        self.set_player(
            stamina=1000,
            spirit=5,
            stamina_updated_at=town_life.now_iso(),
        )
        self.set_tool("pickaxe", 4)
        self.set_career("crystal", 3)
        with (
            mock.patch("town_life.random.choices", return_value=["iron_ore"]),
            mock.patch("town_life.random.random", return_value=1.0),
        ):
            result = self.db.mine(self.user_id, "iron_depths", 5)

        snapshot = self.db.get_snapshot(self.user_id)
        self.assertEqual(int(result["attempts_completed"]), 2)
        self.assertEqual(int(result["spirit_cost"]), 4)
        self.assertEqual(int(snapshot["player"]["spirit"]), 1)
        self.assertEqual(int(snapshot["inventory"]["iron_ore"]), 2)

    def test_batch_database_failure_rolls_back_everything(self) -> None:
        self.set_player(stamina=1000, stamina_updated_at=town_life.now_iso())
        before = self.db.get_snapshot(self.user_id)
        with (
            mock.patch("town_life.random.choices", return_value=["wild_berry"]),
            mock.patch("town_life.random.randint", return_value=2),
            mock.patch.object(
                self.db,
                "_add_career_exp",
                side_effect=sqlite3.OperationalError("forced batch failure"),
            ),
        ):
            with self.assertRaises(sqlite3.OperationalError):
                self.db.forage(self.user_id, 10)

        after = self.db.get_snapshot(self.user_id)
        self.assertEqual(after["player"]["stamina"], before["player"]["stamina"])
        self.assertEqual(after["inventory"], before["inventory"])
        self.assertEqual(after["careers"], before["careers"])

    def test_refine_missing_and_success(self) -> None:
        self.set_career("crystal", 2)
        with self.assertRaises(TownLifeError):
            self.db.refine_crystal(self.user_id)
        self.add_item("raw_crystal", 2)
        self.add_item("iron_ore", 1)
        result = self.db.refine_crystal(self.user_id)
        self.assertEqual(int(result["quantity"]), 1)
        self.assertEqual(int(self.db.get_snapshot(self.user_id)["inventory"]["refined_crystal"]), 1)

    def test_all_twelve_recipes_and_food_use(self) -> None:
        self.set_player(stamina=1, spirit=1)
        for recipe in FOOD_RECIPE_CONFIG.values():
            for key, quantity in recipe["ingredients"].items():
                self.add_item(str(key), int(quantity) + 20)
        for recipe_key in FOOD_RECIPE_CONFIG:
            result = self.db.cook_food(self.user_id, recipe_key)
            self.assertEqual(result["recipe_key"], recipe_key)
            self.assertEqual(int(result["quantity"]), 1)
        eaten = self.db.eat_food(self.user_id, "grilled_fish")
        self.assertGreater(int(eaten["stamina_restored"]) + int(eaten["spirit_restored"]), 0)

    def test_food_is_not_consumed_when_daily_stamina_limit_is_full(self) -> None:
        self.set_player(
            stamina=1,
            spirit=50,
            stamina_updated_at=town_life.now_iso(),
            food_stamina_date=town_life.today_key(),
            food_stamina_recovered=town_life.MAX_DAILY_FOOD_STAMINA,
        )
        self.add_item("grilled_fish", 1)
        before = self.db.get_snapshot(self.user_id)

        with self.assertRaisesRegex(TownLifeError, "已替你保留"):
            self.db.eat_food(self.user_id, "grilled_fish")

        after = self.db.get_snapshot(self.user_id)
        self.assertEqual(after["inventory"]["grilled_fish"], 1)
        self.assertEqual(after["player"]["stamina"], before["player"]["stamina"])
        self.assertEqual(after["player"]["spirit"], before["player"]["spirit"])

    def test_food_can_use_the_remaining_daily_stamina_allowance(self) -> None:
        self.set_player(
            stamina=1,
            spirit=50,
            stamina_updated_at=town_life.now_iso(),
            food_stamina_date=town_life.today_key(),
            food_stamina_recovered=town_life.MAX_DAILY_FOOD_STAMINA - 10,
        )
        self.add_item("grilled_fish", 1)

        result = self.db.eat_food(self.user_id, "grilled_fish")

        self.assertEqual(result["stamina_restored"], 10)
        self.assertEqual(result["spirit_restored"], 12)
        self.assertEqual(result["stamina_daily_remaining"], 0)
        self.assertEqual(
            self.db.get_snapshot(self.user_id)["inventory"].get("grilled_fish", 0),
            0,
        )

    def test_food_still_restores_spirit_when_stamina_itself_is_full(self) -> None:
        self.set_player(
            stamina=1000,
            spirit=50,
            stamina_updated_at=town_life.now_iso(),
            food_stamina_date=town_life.today_key(),
            food_stamina_recovered=town_life.MAX_DAILY_FOOD_STAMINA,
        )
        self.add_item("grilled_fish", 1)

        result = self.db.eat_food(self.user_id, "grilled_fish")

        self.assertEqual(result["stamina_restored"], 0)
        self.assertEqual(result["spirit_restored"], 12)
        self.assertEqual(
            self.db.get_snapshot(self.user_id)["inventory"].get("grilled_fish", 0),
            0,
        )


class InterfaceTests(DatabaseCase, unittest.IsolatedAsyncioTestCase):
    def test_player_panel_views_share_one_canonical_timeout(self) -> None:
        self.assertEqual(main.PLAYER_PANEL_TIMEOUT_SECONDS, 300)
        for view in (
            main.StudentHubView(self.user_id),
            main.TownHubView(self.user_id),
            main.OracleHubView(self.user_id),
            main.PlayerPanelHomeView(self.user_id),
            main.PlayerPanelOutfitView(self.user_id),
            main.PlayerPanelOutfitResultView(self.user_id),
        ):
            self.assertIsNone(view.timeout)

    def test_player_panel_contains_outfit_entry_and_return_route(self) -> None:
        home = main.PlayerPanelHomeView(self.user_id)
        self.assertTrue(
            any(child.label == "今日穿搭" for child in home.children)
        )

        outfit = main.PlayerPanelOutfitView(self.user_id)
        self.assertTrue(
            any(
                isinstance(child, main.OutfitDirectionSelect)
                for child in outfit.children
            )
        )
        self.assertTrue(
            any(child.label == "返回主面板" for child in outfit.children)
        )

    def test_restart_lock_screen_explains_why_old_panel_closed(self) -> None:
        embed = main.locked_operation_embed(restarted=True)
        self.assertIn("舊面板已鎖定", embed.title)
        self.assertIn("Bot 已重新啟動", embed.description)
        self.assertIn("/學生資料", embed.description)
        self.assertIn("/城下町", embed.description)

    async def test_player_panel_session_expiry_removes_all_components(self) -> None:
        message = FakeMessage()
        session = main.PlayerPanelSession(
            owner_id=self.user_id,
            owner_name="測試玩家",
            message=message,
        )
        main.ACTIVE_PLAYER_PANELS[self.user_id] = session
        try:
            with mock.patch.object(
                main.asyncio,
                "sleep",
                new=mock.AsyncMock(),
            ):
                await session._expire()

            self.assertIsNone(main.current_player_panel(self.user_id))
            self.assertEqual(len(message.edits), 1)
            self.assertIsNone(message.edits[0]["view"])
            self.assertEqual(message.edits[0]["attachments"], [])
            self.assertIn("操作畫面已鎖定", message.edits[0]["embed"].title)
        finally:
            main.ACTIVE_PLAYER_PANELS.pop(self.user_id, None)

    async def test_home_and_inventory_refresh_the_lockable_message_reference(
        self,
    ) -> None:
        for view in (
            main.PlayerPanelHomeView(self.user_id),
            main.InventoryMarketView(self.user_id),
        ):
            with self.subTest(view=type(view).__name__):
                stale_reference = FakeMessage(message_id=555)
                current_message = FakeMessage(message_id=555)
                session = main.PlayerPanelSession(
                    owner_id=self.user_id,
                    owner_name="舊名稱",
                    message=stale_reference,
                )
                main.ACTIVE_PLAYER_PANELS[self.user_id] = session
                interaction = FakeInteraction(
                    user_id=self.user_id,
                    message=current_message,
                )

                try:
                    with mock.patch.object(
                        main.ACADEMY_DB,
                        "get_player_panel",
                        return_value={"message_id": "555"},
                    ):
                        accepted = await view.interaction_check(interaction)

                    self.assertTrue(accepted)
                    self.assertIs(session.message, current_message)
                    self.assertEqual(session.owner_name, "測試玩家")

                    timer = session.timeout_task
                    if timer is not None:
                        timer.cancel()
                        await asyncio.sleep(0)
                    session.timeout_task = None

                    with mock.patch.object(
                        main.asyncio,
                        "sleep",
                        new=mock.AsyncMock(),
                    ):
                        await session._expire()

                    self.assertEqual(stale_reference.edits, [])
                    self.assertEqual(len(current_message.edits), 1)
                    self.assertIsNone(current_message.edits[0]["view"])
                    self.assertIn(
                        "操作畫面已鎖定",
                        current_message.edits[0]["embed"].title,
                    )
                finally:
                    main.clear_player_panel_session(session)

    async def test_opening_new_panel_visibly_locks_previous_panel(self) -> None:
        previous_message = FakeMessage(message_id=111)
        new_message = FakeMessage(message_id=222)
        interaction = FakeInteraction(
            user_id=self.user_id,
            original_message=new_message,
        )

        with (
            mock.patch.object(
                main,
                "fetch_saved_player_panel",
                new=mock.AsyncMock(return_value=previous_message),
            ),
            mock.patch.object(
                main.ACADEMY_DB,
                "save_player_panel",
            ) as save_panel,
            mock.patch.object(
                main,
                "activate_player_panel",
            ) as activate_panel,
        ):
            returned = await main.open_player_panel_page(
                interaction,
                embed=main.monk_embed("新面板", "測試"),
                view=main.PlayerPanelHomeView(self.user_id),
            )

        self.assertIs(returned, new_message)
        save_panel.assert_called_once()
        activate_panel.assert_called_once()
        self.assertEqual(len(previous_message.edits), 1)
        locked = previous_message.edits[0]
        self.assertIsNone(locked["view"])
        self.assertEqual(locked["attachments"], [])
        self.assertIn("舊面板已鎖定", locked["embed"].title)
        self.assertIn("已開啟新的操作面板", locked["embed"].description)

    async def test_panel_lock_uses_direct_bot_edit_without_history_fetch(self) -> None:
        stale_interaction_reference = FakeMessage(message_id=135)
        bot_editable_message = FakeMessage(message_id=135)
        get_partial_message = mock.Mock(return_value=bot_editable_message)
        fetch_message = mock.AsyncMock(
            side_effect=AssertionError(
                "鎖定面板不應依賴讀取訊息歷史權限"
            )
        )
        stale_interaction_reference.channel = SimpleNamespace(
            id=123456789,
            get_partial_message=get_partial_message,
            fetch_message=fetch_message,
        )

        locked = await main.lock_player_panel_message(
            stale_interaction_reference,
            owner_name="測試玩家",
            replaced=True,
        )

        self.assertTrue(locked)
        get_partial_message.assert_called_once_with(135)
        fetch_message.assert_not_awaited()
        self.assertEqual(stale_interaction_reference.edits, [])
        self.assertEqual(len(bot_editable_message.edits), 1)
        self.assertIsNone(bot_editable_message.edits[0]["view"])

    async def test_saved_panel_builds_partial_message_without_history_fetch(
        self,
    ) -> None:
        partial_message = FakeMessage(message_id=138)
        channel = mock.MagicMock(spec=main.discord.TextChannel)
        channel.get_partial_message.return_value = partial_message
        channel.fetch_message = mock.AsyncMock(
            side_effect=AssertionError(
                "取得舊面板不應依賴讀取訊息歷史權限"
            )
        )

        with (
            mock.patch.object(
                main.ACADEMY_DB,
                "get_player_panel",
                return_value={
                    "channel_id": "123456789",
                    "message_id": "138",
                },
            ),
            mock.patch.object(
                main.client,
                "get_channel",
                return_value=channel,
            ),
        ):
            result = await main.fetch_saved_player_panel(self.user_id)

        self.assertIs(result, partial_message)
        channel.get_partial_message.assert_called_once_with(138)
        channel.fetch_message.assert_not_awaited()

    async def test_failed_timeout_lock_self_repairs_on_next_touch(self) -> None:
        message = FakeMessage(message_id=136)
        session = main.PlayerPanelSession(
            owner_id=self.user_id,
            owner_name="測試玩家",
            message=message,
        )
        main.ACTIVE_PLAYER_PANELS[self.user_id] = session
        try:
            with (
                mock.patch.object(
                    main.asyncio,
                    "sleep",
                    new=mock.AsyncMock(),
                ),
                mock.patch.object(
                    main,
                    "lock_player_panel_message",
                    new=mock.AsyncMock(return_value=False),
                ),
            ):
                await session._expire()

            self.assertTrue(session.expired)
            self.assertIs(main.current_player_panel(self.user_id), session)

            interaction = FakeInteraction(
                user_id=self.user_id,
                message=message,
            )
            view = main.TownLifeHubView(self.user_id)
            with mock.patch.object(
                main.ACADEMY_DB,
                "get_player_panel",
                return_value={"message_id": "136"},
            ):
                accepted = await view.interaction_check(interaction)

            self.assertFalse(accepted)
            self.assertIsNone(main.current_player_panel(self.user_id))
            self.assertEqual(len(message.edits), 1)
            self.assertIsNone(message.edits[0]["view"])
            self.assertIn(
                "操作畫面已鎖定",
                message.edits[0]["embed"].title,
            )
        finally:
            current = main.current_player_panel(self.user_id)
            if current is not None:
                main.clear_player_panel_session(current)

    async def test_missing_session_visibly_locks_matching_saved_panel(self) -> None:
        message = FakeMessage(message_id=137)
        interaction = FakeInteraction(
            user_id=self.user_id,
            message=message,
        )
        view = main.TownLifeHubView(self.user_id)
        main.ACTIVE_PLAYER_PANELS.pop(self.user_id, None)

        with mock.patch.object(
            main.ACADEMY_DB,
            "get_player_panel",
            return_value={"message_id": "137"},
        ):
            accepted = await view.interaction_check(interaction)

        self.assertFalse(accepted)
        self.assertEqual(len(message.edits), 1)
        self.assertIsNone(message.edits[0]["view"])
        self.assertIn("舊面板已鎖定", message.edits[0]["embed"].title)

    async def test_stale_panel_self_locks_when_player_touches_it(self) -> None:
        stale_message = FakeMessage(message_id=246)
        interaction = FakeInteraction(
            user_id=self.user_id,
            message=stale_message,
        )
        view = main.PlayerPanelHomeView(self.user_id)

        with mock.patch.object(
            main.ACADEMY_DB,
            "get_player_panel",
            return_value={"message_id": "999"},
        ):
            accepted = await view.interaction_check(interaction)

        self.assertFalse(accepted)
        self.assertEqual(len(stale_message.edits), 1)
        self.assertIsNone(stale_message.edits[0]["view"])
        self.assertIn(
            "舊面板已鎖定",
            stale_message.edits[0]["embed"].title,
        )
        self.assertTrue(interaction.response.messages)

    async def test_new_panel_prefers_active_session_when_db_lookup_is_unavailable(
        self,
    ) -> None:
        previous_message = FakeMessage(message_id=333)
        previous_session = main.PlayerPanelSession(
            owner_id=self.user_id,
            owner_name="測試玩家",
            message=previous_message,
        )
        main.ACTIVE_PLAYER_PANELS[self.user_id] = previous_session
        new_message = FakeMessage(message_id=444)
        interaction = FakeInteraction(
            user_id=self.user_id,
            original_message=new_message,
        )

        try:
            with (
                mock.patch.object(
                    main,
                    "fetch_saved_player_panel",
                    new=mock.AsyncMock(
                        side_effect=AssertionError(
                            "有目前工作階段時不應依賴資料庫查詢"
                        )
                    ),
                ) as fetch_saved,
                mock.patch.object(
                    main.ACADEMY_DB,
                    "save_player_panel",
                ),
                mock.patch.object(
                    main,
                    "activate_player_panel",
                ),
            ):
                await main.open_player_panel_page(
                    interaction,
                    embed=main.monk_embed("新面板", "測試"),
                    view=main.PlayerPanelHomeView(self.user_id),
                )

            fetch_saved.assert_not_awaited()
            self.assertEqual(len(previous_message.edits), 1)
            self.assertIsNone(previous_message.edits[0]["view"])
            self.assertIn(
                "舊面板已鎖定",
                previous_message.edits[0]["embed"].title,
            )
        finally:
            main.ACTIVE_PLAYER_PANELS.pop(self.user_id, None)

    async def test_failed_new_panel_creation_does_not_lock_previous_panel(self) -> None:
        previous_message = FakeMessage(message_id=111)
        interaction = FakeInteraction(user_id=self.user_id)
        interaction.edit_original_response = mock.AsyncMock(
            side_effect=RuntimeError("Discord render failed"),
        )

        with (
            mock.patch.object(
                main,
                "fetch_saved_player_panel",
                new=mock.AsyncMock(return_value=previous_message),
            ),
            mock.patch.object(
                main.ACADEMY_DB,
                "save_player_panel",
            ) as save_panel,
            mock.patch.object(
                main,
                "activate_player_panel",
            ) as activate_panel,
        ):
            with self.assertRaisesRegex(RuntimeError, "render failed"):
                await main.open_player_panel_page(
                    interaction,
                    embed=main.monk_embed("新面板", "測試"),
                    view=main.PlayerPanelHomeView(self.user_id),
                )

        self.assertEqual(previous_message.edits, [])
        save_panel.assert_not_called()
        activate_panel.assert_not_called()

    async def test_expired_confession_modal_is_rejected_without_processing(self) -> None:
        message_id = 123456
        modal = main.ConfessionModal(
            user_id=self.user_id,
            source_message_id=message_id,
        )
        interaction = FakeInteraction(user_id=self.user_id)
        main.ACTIVE_PLAYER_PANELS.pop(self.user_id, None)

        with (
            mock.patch.object(
                main.ACADEMY_DB,
                "get_player_panel",
                return_value=None,
            ),
            mock.patch.object(
                main,
                "_handle_confession",
                new=mock.AsyncMock(),
            ) as handler,
        ):
            await modal.on_submit(interaction)

        handler.assert_not_awaited()
        self.assertTrue(interaction.response.messages)
        self.assertIn(
            "已關閉或已被新面板取代",
            str(interaction.response.messages[0][0][0]),
        )

    async def test_expired_outfit_modal_does_not_consume_daily_usage(self) -> None:
        flow_view = main.OutfitDirectionView(self.user_id)
        flow_view.close_flow()
        modal = main.OutfitKeywordModal(
            user_id=self.user_id,
            direction_key="neutral",
            source_message=None,
            flow_view=flow_view,
        )
        interaction = FakeInteraction(user_id=self.user_id)

        with mock.patch.object(
            main.ACADEMY_DB,
            "try_reserve_usage",
            side_effect=AssertionError("逾時表單不應扣除使用次數"),
        ):
            await modal.on_submit(interaction)

        self.assertTrue(interaction.response.messages)
        self.assertIn(
            "沒有扣除今日使用次數",
            str(interaction.response.messages[0][0][0]),
        )

    async def test_expired_panel_outfit_modal_does_not_consume_usage(self) -> None:
        flow_view = main.PlayerPanelOutfitView(self.user_id)
        source_message = FakeMessage(message_id=777)
        modal = main.OutfitKeywordModal(
            user_id=self.user_id,
            direction_key="neutral",
            source_message=source_message,
            flow_view=flow_view,
        )
        interaction = FakeInteraction(user_id=self.user_id)

        with (
            mock.patch.object(
                main,
                "validate_modal_player_panel",
                new=mock.AsyncMock(return_value=None),
            ),
            mock.patch.object(
                main.ACADEMY_DB,
                "try_reserve_usage",
                side_effect=AssertionError(
                    "失效的面板穿搭表單不應扣除使用次數"
                ),
            ),
        ):
            await modal.on_submit(interaction)

        self.assertFalse(flow_view.active)

    async def test_panel_outfit_result_keeps_return_to_home_button(self) -> None:
        flow_view = main.PlayerPanelOutfitView(self.user_id)
        source_message = FakeMessage(message_id=778)
        modal = main.OutfitKeywordModal(
            user_id=self.user_id,
            direction_key="neutral",
            source_message=source_message,
            flow_view=flow_view,
        )
        interaction = FakeInteraction(user_id=self.user_id)
        direction = main.choose_outfit_direction("neutral")
        recommendation = main.outfit_fallback(
            direction=direction,
            keywords="",
        )

        with (
            mock.patch.object(
                main,
                "validate_modal_player_panel",
                new=mock.AsyncMock(return_value=object()),
            ),
            mock.patch.object(
                main.ACADEMY_DB,
                "try_reserve_usage",
                return_value=1,
            ),
            mock.patch.object(
                main,
                "generate_outfit_recommendation",
                new=mock.AsyncMock(
                    return_value=(recommendation, True)
                ),
            ),
        ):
            await modal.on_submit(interaction)

        self.assertFalse(flow_view.active)
        self.assertEqual(len(source_message.edits), 1)
        result_view = source_message.edits[0]["view"]
        self.assertIsInstance(
            result_view,
            main.PlayerPanelOutfitResultView,
        )
        self.assertTrue(
            any(
                child.label == "返回主面板"
                for child in result_view.children
            )
        )

    async def test_ai_confession_keeps_player_panel_unchanged(self) -> None:
        interaction = FakeInteraction(user_id=self.user_id)
        settings = SimpleNamespace(
            confession_ai_available=True,
            ai_daily_limit=3,
        )
        with (
            mock.patch.object(main, "openai_client", object()),
            mock.patch.object(main, "SETTINGS", settings),
            mock.patch.object(
                main.ACADEMY_DB,
                "try_reserve_usage",
                return_value=1,
            ),
            mock.patch.object(
                main,
                "ask_openai_confession",
                new=mock.AsyncMock(return_value="請先整理今天能完成的一步。"),
            ),
        ):
            await main._handle_confession(
                interaction,
                "我想整理一下今天發生的事情。",
            )

        self.assertTrue(interaction.response.done)
        self.assertEqual(interaction.response.edits, [])
        self.assertEqual(len(interaction.followup.messages), 1)
        self.assertTrue(interaction.followup.messages[0][1]["ephemeral"])

    def test_ranch_embed_explains_location_and_next_action(self) -> None:
        self.set_player(coins=5000)
        self.set_tool("farm_tools", 2)
        self.add_item("animal_feed", 3)
        self.transaction(
            (
                "UPDATE town_life_animals SET quantity = 2, last_collect_date = '' "
                "WHERE user_id = ? AND animal_key = 'chicken'",
                (str(self.user_id),),
            ),
            (
                "UPDATE town_life_animals SET quantity = 1, last_collect_date = '' "
                "WHERE user_id = ? AND animal_key = 'cow'",
                (str(self.user_id),),
            ),
        )

        embed = main.ranch_embed(self.user_id)
        self.assertIn("城下町 › 農牧師 › 畜牧場", embed.description)
        self.assertIn("**下一步**", embed.description)
        self.assertIn("「收雞蛋 ×2」", embed.description)
        self.assertIn("「擠牛奶 ×1」", embed.description)
        self.assertIn("雞 2／10｜可收雞蛋 ×2", embed.description)
        self.assertIn("牛 1／10｜可收牛奶 ×1", embed.description)
        self.assertEqual(embed.image.url, "attachment://ranch.webp")

        compact = main.ranch_embed(
            self.user_id,
            notice="測試完成。",
            item_key="egg",
        )
        self.assertIsNone(compact.image.url)
        self.assertEqual(compact.thumbnail.url, "attachment://egg.png")
        self.assertIn("✅ 測試完成。", compact.description)

        self.set_player(coins=100)
        self.set_tool("farm_tools", 1)
        self.transaction(
            (
                "UPDATE town_life_animals SET quantity = 0, last_collect_date = '' "
                "WHERE user_id = ?",
                (str(self.user_id),),
            )
        )
        insufficient_coins = main.ranch_embed(self.user_id)
        self.assertIn("先準備 600 麻瓜幣", insufficient_coins.description)

    def test_ranch_view_buttons_explain_requirements_and_daily_state(self) -> None:
        locked = main.RanchView(self.user_id)
        self.assertTrue(locked.buy_chicken.disabled)
        self.assertEqual(locked.buy_chicken.label, "買雞｜需農具 Lv.1")
        self.assertTrue(locked.buy_cow.disabled)
        self.assertEqual(locked.buy_cow.label, "買牛｜需農具 Lv.2")
        self.assertTrue(locked.collect_eggs.disabled)
        self.assertEqual(locked.collect_eggs.label, "雞蛋｜尚無雞")
        self.assertTrue(any(child.label == "城下町首頁" for child in locked.children))

        self.set_player(coins=5000)
        self.set_tool("farm_tools", 2)
        self.add_item("animal_feed", 3)
        self.transaction(
            (
                "UPDATE town_life_animals SET quantity = 2, last_collect_date = '' "
                "WHERE user_id = ? AND animal_key = 'chicken'",
                (str(self.user_id),),
            ),
            (
                "UPDATE town_life_animals SET quantity = 1, last_collect_date = '' "
                "WHERE user_id = ? AND animal_key = 'cow'",
                (str(self.user_id),),
            ),
        )
        ready = main.RanchView(self.user_id)
        self.assertFalse(ready.collect_eggs.disabled)
        self.assertEqual(ready.collect_eggs.label, "收雞蛋 ×2")
        self.assertFalse(ready.collect_milk.disabled)
        self.assertEqual(ready.collect_milk.label, "擠牛奶 ×1")
        self.assertEqual(ready.buy_feed.label, "買飼料 ×10｜150")

        self.transaction(
            (
                "UPDATE town_life_animals SET last_collect_date = ? "
                "WHERE user_id = ? AND animal_key = 'chicken'",
                (town_life.today_key(), str(self.user_id)),
            ),
            (
                "DELETE FROM town_life_inventory "
                "WHERE user_id = ? AND item_key = 'animal_feed'",
                (str(self.user_id),),
            ),
        )
        unavailable = main.RanchView(self.user_id)
        self.assertTrue(unavailable.collect_eggs.disabled)
        self.assertEqual(unavailable.collect_eggs.label, "雞蛋｜今日已收")
        self.assertTrue(unavailable.collect_milk.disabled)
        self.assertEqual(unavailable.collect_milk.label, "牛奶｜缺飼料 1")

    async def test_same_view_mutation_gate_blocks_second_click(self) -> None:
        view = main.RanchView(self.user_id)
        first = FakeInteraction()
        second = FakeInteraction()
        self.assertTrue(await view.begin_town_life_action(first))
        self.assertFalse(await view.begin_town_life_action(second))
        self.assertTrue(second.response.messages)
        self.assertIn("上一筆操作正在處理", str(second.response.messages[0][0][0]))

    async def test_failed_transaction_releases_view_gate(self) -> None:
        view = main.RanchView(self.user_id)
        interaction = FakeInteraction()
        await view._buy_animal(interaction, "cow")
        self.assertFalse(view._town_life_action_started)

    async def test_post_commit_render_error_reports_completed_transaction(self) -> None:
        view = main.RanchView(self.user_id)
        view._town_life_action_started = True
        view.mark_town_life_action_committed()
        interaction = FakeInteraction()
        await view.on_error(
            interaction,
            RuntimeError("forced render failure"),
            view.children[0],
        )
        message = str(interaction.response.messages[0][0][0])
        self.assertIn("交易資料已經完成更新", message)
        self.assertIn("不要在舊畫面重複操作", message)

    async def test_collect_screen_uses_product_not_feed(self) -> None:
        self.add_item("animal_feed", 1)
        self.transaction(
            (
                "UPDATE town_life_animals SET quantity = 1, last_collect_date = '' "
                "WHERE user_id = ? AND animal_key = 'chicken'",
                (str(self.user_id),),
            )
        )
        view = main.RanchView(self.user_id)
        interaction = FakeInteraction()
        await view._collect(interaction, "chicken")
        edit = interaction.response.edits[0]
        self.assertEqual(edit["embed"].thumbnail.url, "attachment://egg.png")
        self.assertIsNone(edit["embed"].image.url)
        self.assertIn("**下一步**", edit["embed"].description)
        filenames = [file.filename for file in edit["attachments"]]
        self.assertIn("egg.png", filenames)
        self.assertNotIn("animal_feed.png", filenames)
        self.assertNotIn("ranch.webp", filenames)
        for file in edit["attachments"]:
            file.close()

    async def test_purchase_screen_is_compact_and_guides_the_player(self) -> None:
        self.set_player(coins=5000)
        self.set_tool("farm_tools", 1)
        view = main.RanchView(self.user_id)
        interaction = FakeInteraction()

        await view._buy_animal(interaction, "chicken")

        edit = interaction.response.edits[0]
        self.assertIsNone(edit["embed"].image.url)
        self.assertEqual(edit["embed"].thumbnail.url, "attachment://egg.png")
        self.assertIn("日後可採收雞蛋", edit["embed"].description)
        self.assertIn("先按「買飼料 ×10」", edit["embed"].description)
        filenames = [file.filename for file in edit["attachments"]]
        self.assertEqual(filenames, ["egg.png"])
        refreshed = edit["view"]
        self.assertTrue(refreshed.collect_eggs.disabled)
        self.assertEqual(refreshed.collect_eggs.label, "雞蛋｜缺飼料 1")
        for file in edit["attachments"]:
            file.close()

    def test_empty_backpack_has_no_empty_item_select(self) -> None:
        view = main.InventoryMarketView(self.user_id)
        self.assertFalse(any(isinstance(child, main.InventoryItemSelect) for child in view.children))
        self.assertTrue(view.previous_page.disabled)
        self.assertTrue(view.next_page.disabled)
        self.assertTrue(view.details.disabled)

    def test_mailbox_embed_and_view_show_unclaimed_compensation(self) -> None:
        self.db.issue_mail(
            self.user_id,
            mail_key="interface_reward",
            title="城下町維護補償",
            body="感謝等待維護。",
            item_key="maintenance_stamina_potion",
            quantity=1,
        )

        embed = main.mailbox_embed(self.user_id)
        view = main.MailboxView(self.user_id)

        self.assertIn("**待領信件**：1 封", embed.description)
        self.assertIn("維護補償體力藥水×1", embed.description)
        self.assertEqual(
            embed.thumbnail.url,
            "attachment://maintenance_stamina_potion.png",
        )
        claim_button = next(
            child
            for child in view.children
            if isinstance(child, main.discord.ui.Button)
            and str(child.label).startswith("領取全部")
        )
        self.assertFalse(claim_button.disabled)
        self.assertEqual(claim_button.label, "領取全部｜1 封")

    def test_backpack_disables_food_when_daily_stamina_limit_is_full(self) -> None:
        self.set_player(
            stamina=1,
            spirit=50,
            stamina_updated_at=town_life.now_iso(),
            food_stamina_date=town_life.today_key(),
            food_stamina_recovered=town_life.MAX_DAILY_FOOD_STAMINA,
        )
        self.add_item("grilled_fish", 1)
        snapshot = self.db.get_snapshot(self.user_id)

        view = main.InventoryMarketView(
            self.user_id,
            selected_item_key="grilled_fish",
            category="food",
            snapshot=snapshot,
        )
        eat_button = next(
            child
            for child in view.children
            if isinstance(child, main.discord.ui.Button)
            and child.label == "今日回體已達上限"
        )
        self.assertTrue(eat_button.disabled)

        embed = main.inventory_market_embed(
            self.user_id,
            selected_item_key="grilled_fish",
            category="food",
            snapshot=snapshot,
        )
        self.assertIn("**今日料理可回體**：0／600", embed.description)

    def test_backpack_multiple_pages_and_categories(self) -> None:
        food_keys = list(FOOD_RECIPE_CONFIG)[:6]
        for key in food_keys:
            self.add_item(key, 1)
        for key in ("river_fish", "stone"):
            self.add_item(key, 1)
        snapshot = self.db.get_snapshot(self.user_id)
        sequence = main._inventory_page_sequence(self.user_id, inventory=snapshot["inventory"])
        self.assertIn(("food", 1), sequence)
        self.assertTrue(any(category == "fishing" for category, _ in sequence))
        self.assertTrue(any(category == "crystal" for category, _ in sequence))
        page_two = main.InventoryMarketView(
            self.user_id,
            category="food",
            page=1,
            snapshot=snapshot,
        )
        self.assertFalse(page_two.previous_page.disabled)
        item_select = next(
            child for child in page_two.children if isinstance(child, main.InventoryItemSelect)
        )
        self.assertLessEqual(len(item_select.options), main.INVENTORY_PAGE_SIZE)


if __name__ == "__main__":
    unittest.main(verbosity=2)

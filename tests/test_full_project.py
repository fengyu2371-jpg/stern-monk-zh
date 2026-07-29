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
    def __init__(self) -> None:
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self.original_edits: list[dict[str, object]] = []

    async def edit_original_response(self, **kwargs: object) -> None:
        self.original_edits.append(kwargs)


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
            ["學生資料", "我的", "城下町", "今日穿搭推薦", "下載目前備份", "修士狀態"],
        )

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
            *(main.WorkshopView(self.user_id, route) for route in ("farming", "fishing", "crystal")),
        ]
        for view in views:
            self.assertTrue(view.children)
            for child in view.children:
                self.assertIsNotNone(child.callback)

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


class InterfaceTests(DatabaseCase, unittest.IsolatedAsyncioTestCase):
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
        filenames = [file.filename for file in edit["attachments"]]
        self.assertIn("egg.png", filenames)
        self.assertNotIn("animal_feed.png", filenames)
        for file in edit["attachments"]:
            file.close()

    def test_empty_backpack_has_no_empty_item_select(self) -> None:
        view = main.InventoryMarketView(self.user_id)
        self.assertFalse(any(isinstance(child, main.InventoryItemSelect) for child in view.children))
        self.assertTrue(view.previous_page.disabled)
        self.assertTrue(view.next_page.disabled)
        self.assertTrue(view.details.disabled)

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

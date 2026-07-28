# stern-monk-zh-tw v29｜城下町生活職業第一版

這是修士 Bot 的 Railway 部署包。本版以原有 v28.2 城下町、學籍、店鋪與神諭功能為基底，新增獨立的城下町生活職業系統。

## 部署檔案

```text
main.py
town_life.py
requirements.txt
railway.toml
data/
  faq_zh_tw.json
  tutorials_zh_tw.json
  dialogue.json
assets/
  districts/
    town-overview.webp
    riverside-market.webp
    central-square.webp
    muggle-life.webp
    artisan-street.webp
    magic-commercial.webp
    old-town.webp
    academy-avenue.webp
```

部署時請完整上傳整個資料夾。不要只替換 `main.py`，否則會缺少 `town_life.py` 與城下町圖片。

部署前建議先使用 `/下載目前備份` 取得目前資料庫快照。

## 入口

```text
/學生資料
→ 城下町
→ 生活職業
```

## 三條職業路線

三條路線不會永久綁定，玩家可以同時培養。

### 1. 農牧師

- 購買與升級農具組。
- 三塊固定農田。
- 購買小麥、胡蘿蔔、月光草種子。
- 播種時自動填滿空地，成熟後一次收成。
- 農具等級越高，收成數量越多，播種體力消耗也會下降。
- 可飼養雞與牛。
- 雞需要農具組 Lv.1；牛需要農具組 Lv.2。
- 動物每天消耗飼料，採收雞蛋或牛奶。

作物成熟時間：

```text
小麥：15 分鐘
胡蘿蔔：30 分鐘
月光草：60 分鐘
```

### 2. 漁採師

- 購買與升級釣具組。
- 河岸釣魚會取得河魚、銀鱗鯉、月光鱒或泡水舊靴。
- 釣具等級越高，稀有魚獲機率越高，體力消耗越低。
- 野外採集不需要工具，可取得野莓、野生藥草與硬木枝。
- 沒有工具的新玩家也能先靠採集累積金幣。

### 3. 魔晶礦師

- 購買與升級挖礦工具。
- 可採得石材、銅礦與鐵礦。
- 挖礦工具 Lv.2 起，可能發現魔法水晶原礦。
- 魔晶礦師 Lv.2 起，可使用：

```text
魔法水晶原礦 ×2
鐵礦 ×1
```

精煉成：

```text
精煉魔法水晶 ×1
```

## 工具與經濟

玩家初始擁有：

```text
600 金幣
100／100 體力
```

第一級工具價格：

```text
農具組：180 金幣
釣具組：220 金幣
挖礦工具：260 金幣
```

每項工具最高 Lv.5。玩家可在河岸市集出售農牧、漁採與礦晶物資，也可以一次出售全部可售物資。種子與飼料不會被出售。

## 體力

- 體力上限 100。
- 每 10 分鐘自然回復 1 點。
- 每日可休息一次，回復最多 40 點。
- 體力已滿時不會消耗當日休息次數。

## 資料庫

本版會在原本的 SQLite 資料庫內新增以下獨立資料表：

```text
town_life_players
town_life_tools
town_life_careers
town_life_inventory
town_life_plots
town_life_animals
```

不會修改或清除原有：

```text
student_profiles
student_places
oracle_pages
player_panels
```

## 原有功能保留

- 城下町分區導覽與圖片。
- 依區域查看公開店鋪。
- 公開住處。
- 玩家地點登記、搬遷、公開設定與論壇封面。
- 學籍、神諭冊、今日穿搭與修士教學。
- `/下載目前備份`。

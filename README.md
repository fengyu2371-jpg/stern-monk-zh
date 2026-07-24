# stern-monk-zh-tw v28.2｜城下町入口總覽圖版

這是修士 Bot 的 Railway 部署精簡版。

## 部署檔案

```text
main.py
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

部署時請整包上傳。**不要只替換 main.py**，否則分區導覽會找不到圖片。

## 指令

```text
/學生資料
/今日穿搭推薦
/下載目前備份
/修士狀態
```

`/下載目前備份` 僅提供擁有「管理伺服器」權限的管理員使用。

## v28 新增與調整

### 1. Railway 分區導覽圖片

城下町入口的「商店街」改為「分區找店」。玩家可以先查看：

```text
城下町總覽
河岸市集
中央廣場
麻瓜生活區
工匠街
魔法商業區
舊城區
學院大道
```

分區圖片直接從 Railway 專案內的 `assets/districts/` 載入，再由 Bot 作為 Discord 訊息附件顯示。

這些圖片只作為「區域導覽圖」；個別玩家店鋪仍優先顯示玩家綁定之論壇貼文中的封面圖片。

### 2. 依區域查看店鋪

新的瀏覽流程：

```text
城下町
→ 分區找店
→ 選擇區域
→ 查看本區店鋪
→ 上一家／下一家
→ 開啟店鋪貼文
```

分區頁會顯示該區目前公開店鋪數量。

### 3. 地點管理排版整理

地點管理卡片改為分欄顯示：

```text
基本資料
位置與營業狀態
公開與店鋪貼文
```

按鈕調整為較直覺的動作名稱：

```text
修改資料
搬遷區域
改為公開／改為不公開
設定店鋪貼文／更換店鋪貼文
開啟店鋪貼文
解除貼文綁定
回到我的地點
刪除地點
```

### 4. 店鋪封面與論壇連結

玩家仍可在城下町論壇自行建立貼文，再於地點管理頁貼上論壇貼文或圖片訊息連結。

Bot 會依序嘗試讀取：

1. 玩家指定的訊息。
2. 論壇貼文第一則訊息。
3. 貼文最早的 50 則訊息。

找到圖片後會顯示於地點管理卡片與公開店鋪卡片。

## Discord Developer Portal

店鋪封面需要讀取論壇訊息附件，請確認：

```text
Bot
→ Privileged Gateway Intents
→ Message Content Intent：開啟
```

v28 的 `main.py` 已包含：

```python
intents.message_content = True
```

後台開關與程式碼必須同時啟用。

## 資料庫

v28 沿用 v27 的欄位：

```text
shop_guild_id
shop_thread_id
shop_cover_message_id
```

沒有新增資料庫欄位，既有學生、地點與店鋪綁定資料不會被清除。


## v28.2 調整

城下町主入口卡片現在也會顯示 `town-overview.webp`。
從學生面板進入城下町，以及從分區、店鋪、地點管理返回城下町時，都會重新附上總覽圖片。
圖片仍使用 v28.1 的 1000 × 400 輕量素材。

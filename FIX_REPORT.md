# 致命／高嚴重程度修正紀錄

本修正版以使用者提供的新 `main.py` 為主版本，只處理前次報告中列為「致命」或「高」的項目。未變更遊戲規則、資料庫欄位意義、道具 ID、斜線指令名稱或圖片路徑，也未移除功能。

## `main.py`

### `AcademyDatabase._migrate_oracle_pages_for_unlimited_draws`

- 在改建舊 `oracle_pages` 前先核對全部來源欄位。
- 偵測到舊的 `oracle_pages_limited_backup` 時停止，不擅自覆蓋或合併。
- 以 SQLite savepoint 包住改名、建表、複製、筆數核對、刪除備份表與索引建立。
- 任一步失敗會回滾成原本的 `oracle_pages`，避免留下空新表與孤立備份表。

### `AcademyDatabase.initialize`

- 保留原有已知欄位補丁。
- 在資料回填前檢查學籍、偏好、地點、神諭、計數器與面板表的必要欄位。
- 若遇到無法安全推定的舊 schema，停止啟動並列出缺欄，不自動遷移正式玩家資料。

### `_report_interaction_error`、`SafeModal`、`UserOwnedView.on_error`、`OutfitDirectionView.on_error`

- View、Button、Select、Modal 的未預期例外會寫入 Railway log。
- 若互動仍可回覆，會向玩家顯示 ephemeral 錯誤提示，降低只看到「此交互失敗」而沒有紀錄的情況。

### `validate_modal_player_panel`、`edit_player_panel_from_modal`

- Modal 開啟時保存來源訊息 ID。
- `OraclePreferencesModal`、`EnrollmentModal`、`PlaceModal`、`EditPlaceModal`、`ShopLinkModal` 在任何資料寫入前，核對使用者、資料庫目前面板、記憶體 session 與來源訊息 ID。
- 舊面板逾時或被新面板取代後送出的表單不再寫入資料。
- 店鋪連結在完成網路讀取後、寫入資料庫前會再核對一次，避免等待期間面板已被替換。

### `_open_student_data_panel`、`student_data_command`、`my_panel_command`

- 抽出未套用 Discord 指令裝飾器的共用函式。
- `/我的` 不再嘗試呼叫 `app_commands.Command` 物件，改為呼叫共用函式。
- `/我的` 與 `/學生資料` 先 defer，再讀取資料與建立面板。

### `town_life_command`、`open_player_panel_page`

- `/城下町` 在建立含資料庫快照的 embed 前先 defer。
- `open_player_panel_page` 改為只在尚未回覆時 defer，避免重複 response。

### 背包函式與 `InventoryMarketView`

- `_inventory_keys`、`_inventory_page_count`、`_inventory_page_sequence`、`_inventory_initial_state`、`inventory_market_embed` 可共用同一份 snapshot。
- `InventoryMarketView` 一次建構只讀取一次 snapshot，再傳給選單、分頁與 embed。
- 進入背包與背包內操作會先 defer；後續依狀態使用原訊息編輯或 follow-up。
- 空分類、詳細說明、料理、藥水、出售與返回功能均保留。

## `town_life.py`

### `TownLifeDatabase.initialize`

- 保留原有精神力與料理回體欄位補丁。
- 在任何玩家體力回填前檢查城下町所有必要欄位。
- 無法安全推定的舊 schema 會停止啟動並列出缺欄，不修改玩家數值。

### `TownLifeDatabase._restore_spirit`、`TownLifeDatabase.eat_food`

- 精神力已滿時 `_restore_spirit` 回傳 `0`，不再提前中止整個料理交易。
- 料理會分別計算精神力與體力恢復量；只有兩者都不能恢復時才拒絕並回滾。
- 料理仍在成功後才扣除，既有每日料理回體上限與體力藥水規則不變。

## 已執行的隔離驗證

- 全部 Python 檔案語法編譯。
- 使用 `discord.py 2.7.1` 實際 import。
- pyflakes 未發現 undefined name 或漏匯入；保留的警告只有原專案重複 import 與一個未使用區域變數，屬整理項目。
- 全部主要 View 實際序列化，未發現超過 25 元件、Select 超過 25 選項或 row 超限。
- 預期舊神諭表 migration 保留 1/1 筆資料。
- 缺欄的神諭表 migration 中止後，原表仍保留 1/1 筆，未留下 backup 表。
- 強制工具升級失敗時，麻瓜幣、精神力、材料與工具等級全部回滾。
- 台灣時間 00:00 跨日重置體力、精神力與休息次數。
- 精神力滿、體力不足時可正常食用料理補體力。
- `/我的` callback 可建立新面板。
- 舊來源訊息的 Modal 被拒絕。
- 背包 View 單次建構只取一次 snapshot 且可序列化。

Discord 真實閘道、Railway Volume 與五分鐘實際計時仍需依 `DEPLOYMENT_CHECKLIST.md` 做部署前測試。

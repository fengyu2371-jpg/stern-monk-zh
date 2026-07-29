# 修士 Bot／城下町生活系統完整審查報告

審查日期：2026-07-29  
基準來源：`stern-monk-zh-ranch-fix.zip`  
測試方式：本機 Python 3.14、discord.py 2.7.1、OpenAI 2.50.0、臨時 SQLite；未登入 Discord、Railway，也未載入正式玩家資料庫。

## 結論

- Python 語法、真實 import、Bot `setup_hook()`、6 個 Slash command 註冊均通過。
- 28 項可重跑測試全部通過；19 個資料庫回傳 dict／呼叫端欄位契約全部一致。
- 雞、牛的購買、採收、價格、農具門檻與每種最多 10 隻均正常。
- 所有城下町寫入交易都在 `BEGIN IMMEDIATE` 交易內執行，只在成功路徑 `commit()`；錯誤或例外會由連線關閉回滾。
- 沒有修改資料庫結構、既有玩家資料、動物價格、工具價格、配方、解鎖門檻或城下町平衡。
- 專案不含任何 `.db`、`.db-wal` 或 `.db-shm`。

## 1. 畜牧場錯誤根因

舊版流程為：

1. `TownLifeDatabase.buy_animal()` 在同一筆 SQLite transaction 中扣除麻瓜幣、增加動物並 `commit()`。
2. 函式只回傳 `animal_key`、`quantity`、`cost`。
3. `RanchView._buy_animal()` 隨後讀取 `result["product"]`。
4. 因缺少欄位而拋出 `KeyError: 'product'`，Discord 顯示通用錯誤。

因此舊版確實可能出現「畫面報錯，但購買已完成」：例外發生在資料庫成功提交之後，不會回滾已完成的購買。

提供的 `ranch-fix` 基準包已先補上 `product`；本次重新驗證並保留：

```python
{
    "animal_key": animal_key,
    "product": str(animal["product"]),
    "quantity": current + 1,
    "cost": cost,
}
```

- 雞：`product == "egg"`
- 牛：`product == "milk"`

本機無 Railway 專案權限，無法讀取線上 log；但舊程式控制流程可確定 Railway traceback 的核心例外會是 `RanchView._buy_animal()` 讀取回傳 dict 時的 `KeyError: 'product'`。

## 2. 本次修改

### `town_life.py`

- 保留並驗證 `buy_animal()` 的 `product` 回傳欄位。
- 修正 `_refresh_stamina()`：跨日只重置體力與每日休息標記，不再錯誤補滿精神力。
- 移除未被呼叫的舊 `daily_rest()` 相容方法；該方法原本可能先提交跨日重置，再用 `TownLifeError` 回覆，容易造成「顯示錯誤但資料已更新」的語意混淆。

### `main.py`

- `RanchView._buy_animal()` 使用 `egg`／`milk` 更新 Embed 與附件。
- `RanchView._collect()` 不再錯用 `animal_feed` 圖片，改用實際採收產品 `egg`／`milk`。
- 所有 19 個城下町交易呼叫共用單次操作鎖；同一張 View 快速重複點擊只接受第一筆。
- 交易因可預期的 `TownLifeError` 失敗時解除操作鎖，玩家可修正資源後重試。
- 資料庫成功回傳後標記為已提交；若後續 Discord 畫面更新失敗，明確告知「交易資料已完成」，並要求重新輸入 `/城下町`，不再顯示成交易失敗。
- 一般未知例外提示也改為提醒先重開 `/城下町` 確認，避免立即重複交易。
- 工具達 Lv.5 時，工坊按鈕改為「已達最高等級」並停用。
- 精神力說明改為符合現行程式：料理／每日休息可恢復；目前由工具升級、挖礦與精煉消耗，其他職業行動維持既有消耗 0。
- 移除未使用的 `first_inventory_item_key()`。
- 原本只 `pass` 的三個 Discord 最佳努力例外處理改為 `logger.debug(..., exc_info=True)`。
- `/修士狀態` 的公開指令數由過時的 5 修正為實際 6，並移除已刪除功能的「AI 教學」狀態文案。

### 新生教學：採用 A「完整刪除」

舊教學僅由個人面板的「教學」按鈕進入，使用獨立的 `TeachingHubView`、Modal、Select、知識庫載入器及兩份 JSON；沒有 Slash command、玩家資料表，也不被城下町、學籍、神諭、穿搭或告解資料流程依賴。

已移除：

- 個人面板「教學」按鈕。
- `TeachingHubView`、`TutorialSelect`、`TeachingQuestionModal`。
- 教學查詢、教學渲染與 KnowledgeBase 專屬程式。
- `data/tutorials_zh_tw.json`。
- `data/faq_zh_tw.json`。

告解共用的界線檢查、情緒辨識與角色回覆仍保留。修士告解世界觀內原本合法存在的教堂／神父敘事未刪除。

### 圖片

掃描後原先發現 4 個已實作料理 key 缺少 PNG：

- `herb_soup`
- `milk_egg_stew`
- `silver_carp_steak`
- `moon_trout_platter`

已補上同一套深藍／金框像素風 128×128 RGBA 圖片，並同步更新 `item-manifest.json` 與 `validation.json`。

最終結果：

- 8 張城下町分區 WebP。
- 5 張生活場景 WebP。
- 37 張道具／工具 PNG。
- 共 50 張圖片；缺失 0、大小寫衝突 0、內容完全重複 0、Windows 本機路徑 0。
- 37 個工具／物品 key 與 37 張 PNG、manifest、validation 完全一致。
- `attachment://檔名` 與 `discord.File(filename=...)` 一致。
- 人工模擬圖片缺失時，Embed 會略過圖片並留下 warning，不會讓頁面失效。

### 部署與文件

- `requirements.txt` 新增 Python 3.13+ 條件式 `audioop-lts`，避免新版 Python 因標準庫移除 `audioop` 而無法 import `discord.py`。
- 更新 `README.md` 與 `DEPLOYMENT_CHECKLIST.md`：移除舊教學檔、補上第五張場景、12 道料理、跨日精神力規則、連點與交易後畫面失敗檢查。
- 新增 `tests/test_full_project.py`，可重複執行完整本機測試。

## 3. 雞與牛測試結果

| 項目 | 雞 | 牛 |
|---|---:|---:|
| 購買價格 | 600（未改） | 1500（未改） |
| 農具門檻 | Lv.1（未改） | Lv.2（未改） |
| 回傳產品 | `egg` | `milk` |
| 購買後動物數量 | 正確 +1 | 正確 +1 |
| 購買後麻瓜幣 | 正確扣款一次 | 正確扣款一次 |
| 採收產品 | 雞蛋，數量等於雞數 | 牛奶，數量等於牛數 |
| 採收圖片 | `egg.png` | `milk.png` |
| 飼料 | 每隻消耗 1 份 | 每隻消耗 1 份 |
| 每日重複採收 | 阻擋且不變更資料 | 阻擋且不變更資料 |
| 最多 10 隻 | 通過 | 通過 |
| 門檻／幣不足 | 回滾、不扣款 | 回滾、不扣款 |

另以 SQLite trigger 強制讓動物 UPDATE 失敗，確認前一步麻瓜幣 UPDATE 也完整回滾。

## 4. 背包與工坊

### 背包

- 全空背包：不建立空的物品 Select；上一頁、下一頁、詳細說明均正確停用。
- 多頁：以 6 種料理建立兩頁，第二頁可返回；每頁最多 5 個物品。
- 跨分類：農牧、漁採、礦晶、料理／其他分類順序正確。
- 分類：種子與飼料歸「其他物資」；作物／蛋／奶歸農牧；魚與採集物歸漁採；礦物歸礦晶；料理歸料理。
- 詳細說明：圖片存在時附件一致；圖片缺失時無圖回覆，不破圖、不拋例外。

### 三個工坊

- 農牧、河岸、礦坑工坊 View 均可建立，callback 均已綁定。
- 三種工具從 Lv.0 實際升至 Lv.5；費用、素材與精神力沿用原設定。
- Lv.5 按鈕停用；第六次升級由資料層再次阻擋。
- 麻瓜幣、素材或精神力不足時不扣除其他資源。
- 12 道料理均可製作，材料正確扣除、料理正確增加。
- 魔晶精煉的職業門檻、素材不足與成功流程通過。

## 5. SQLite transaction 與重複操作

- 所有寫入方法使用 `BEGIN IMMEDIATE`。
- 成功分支只有完成所有驗證及寫入後才 `commit()`。
- `TownLifeError` 或非預期 SQLite 例外發生時，連線離開 `closing()` 且未 commit，SQLite 會回滾。
- 強制中途失敗測試證明麻瓜幣、動物、素材、精神力、體力與工具等級不會留下半套變更。
- 同一張 View 的交易按鈕／Select 共用操作鎖；成功後舊 View 保持鎖定，失敗才解鎖。
- 個人面板原有「只允許最新面板操作」檢查仍保留。

殘餘部署條件：請維持單一 Railway Bot replica。依本次「不改資料庫結構」限制，沒有新增跨程序 idempotency key；若同一 Bot Token 同時啟動多個 replica，應先停止多餘 instance。

## 6. 回傳欄位契約

靜態掃描 `main.py` 的 19 個城下町資料庫呼叫點，逐一比對所有 `result["..."]`／`result.get(...)` 與 `town_life.py` 回傳 dict：

- 不一致：0
- 缺少欄位：0
- 其他和 `product` 相同類型問題：未再發現

另發現並修正一個「回傳欄位正確，但圖片 key 使用錯誤」：採收蛋／奶後原本顯示 `animal_feed`。

## 7. 測試清單

| # | 測試 | 結果 |
|---:|---|---|
| 1 | Python 語法編譯 | 通過 |
| 2 | 真實 import（discord.py／OpenAI） | 通過 |
| 3 | Bot `setup_hook()`（mock Discord sync） | 通過 |
| 4 | 6 個 Slash command 註冊 | 通過 |
| 5 | 主要城下町 View 建立與 callback 綁定 | 通過 |
| 6 | Railway 設定與必要依賴 | 通過 |
| 7 | 新生教學完整移除 | 通過 |
| 8 | 50 張圖片格式、路徑、大小寫與附件檔名 | 通過 |
| 9 | 37 個工具／物品 key、PNG、manifest、validation | 通過 |
| 10 | 圖片缺失 fallback | 通過 |
| 11 | 新玩家初始資料 | 通過 |
| 12 | 舊玩家重新 initialize 保留資料 | 通過 |
| 13 | 跨日體力重置、精神力保留 | 通過 |
| 14 | 購買雞 | 通過 |
| 15 | 購買牛 | 通過 |
| 16 | 動物門檻／幣不足／10 隻上限 | 通過 |
| 17 | 強制 DB 中途失敗 rollback | 通過 |
| 18 | 收雞蛋與每日限制 | 通過 |
| 19 | 擠牛奶與飼料扣除 | 通過 |
| 20 | 三種工具 Lv.0→Lv.5 | 通過 |
| 21 | Lv.5 工坊按鈕停用 | 通過 |
| 22 | 工具資源不足 rollback | 通過 |
| 23 | 種子購買、播種、成熟收成 | 通過 |
| 24 | 採集、釣魚、工具不足 | 通過 |
| 25 | 三礦區工具／職業門檻 | 通過 |
| 26 | 魔晶精煉失敗與成功 | 通過 |
| 27 | 12 道料理製作與食用 | 通過 |
| 28 | 空背包、多頁、跨分類、詳細說明 | 通過 |
| 29 | 同 View 重複點擊鎖 | 通過 |
| 30 | 交易失敗後解鎖 | 通過 |
| 31 | commit 後 Discord 畫面失敗的正確提示 | 通過 |
| 32 | 19 個資料介面契約 | 通過 |
| 33 | 未使用 import／重複定義／silent pass／print | 通過 |

`unittest` 實際案例數為 28；表格另外拆列同一案例內的靜態檢查與 19 點契約掃描。最終執行結果：`Ran 28 tests ... OK`。

## 8. 未線上執行的項目

- 未登入 Discord 測試真實按鈕點擊、訊息編輯或附件上傳。
- 未讀取 Railway live logs。
- 未使用正式 `monk.db` 或正式 Volume 副本。

這些項目需在 staging 依 `DEPLOYMENT_CHECKLIST.md` 手動確認。其餘資料層、View 建立、回傳契約、圖片與失敗流程均已由本機測試實際執行，不是僅做理論判讀。

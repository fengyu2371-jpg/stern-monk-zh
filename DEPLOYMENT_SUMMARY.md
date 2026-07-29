# Railway 部署摘要

## 是否需要修改 Volume

不需要。請沿用目前掛載正式 `monk.db` 的 Railway Volume，確認 `MONK_DB_PATH` 仍指向該持久化路徑。

## 是否需要更動資料庫

不需要手動改表、不需要 migration 指令、不需要清空或重建資料。這次沒有更動資料庫 schema、價格、門檻或玩家資料。

部署前仍應先：

1. 暫停正式 Bot。
2. 使用 `/下載目前備份` 或 Railway Volume 快照備份 `monk.db`。
3. 在備份副本執行 `PRAGMA quick_check;`，結果應為 `ok`。
4. 確認 Railway 只啟動一個 Bot replica。

## 部署內容

請部署整個資料夾，不要只替換 `main.py`。必要內容包含：

- `main.py`
- `town_life.py`
- `requirements.txt`
- `railway.toml`
- `data/dialogue.json`
- `assets/districts/`
- `assets/town_life/`

舊 `data/tutorials_zh_tw.json` 與 `data/faq_zh_tw.json` 已移除，不應再上傳。

`requirements.txt` 新增 Python 3.13+ 的 `audioop-lts`；Railway 重新 build 時會自動安裝。

## 部署後優先手動測試

1. 查看 Railway log，確認資料庫初始化、6 個指令同步及 import 無錯誤。
2. 新玩家與舊玩家各開一次 `/城下町`。
3. 各買一隻雞、牛，確認只扣款一次，並顯示 `egg.png`／`milk.png`。
4. 收一次雞蛋、牛奶，確認顯示產品圖而不是飼料圖。
5. 快速連點購買按鈕，確認第二次顯示「上一筆操作正在處理」。
6. 開啟空背包、料理多頁及詳細說明。
7. 進入農牧、河岸、礦坑三個工坊；抽查滿等工具按鈕已停用。
8. 暫時在 staging 移走一張圖片，確認頁面仍可操作且 log 只有缺圖 warning。
9. 模擬 Discord 訊息編輯失敗，確認提示為「交易資料已完成」，不會引導玩家立即重試。

若部署後數值或資料表筆數異常，立即停止 Bot，保留異常資料庫副本，再使用部署前備份回復；不要在正式資料庫上反覆嘗試修表。

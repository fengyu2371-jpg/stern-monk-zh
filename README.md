# stern-monk-zh-tw v4｜正式知識庫版

禊月堂繁體中文「修士」Discord Bot。

## 回答流程

1. 先查 `data/faq_zh_tw.json`（不使用 API）
2. FAQ 無命中時查 `data/tutorials_zh_tw.json`（不使用 API）
3. 兩份固定知識庫都無命中時，固定回覆「目前沒有正式資料」
4. 教學與規則查詢沒有 OpenAI 呼叫路徑，正式知識庫不會送往 API
5. 只有 `/修士告解` 可以使用 OpenAI API

## 目前功能

- `/新生指南`
- `/修士教學`
- `/問修士`
- `/修士告解`（AI 一次性陪伴；失敗時回退本地回覆，不修改正式罪惡值）
- `/修士狀態`

這些都是修士自己的教學、規則查詢、角色演出與狀態指令；不包含或代理神父 Bot 的遊戲操作指令。

## 本地知識庫

- `data/tutorials_zh_tw.json`：正式教學、關鍵字、規則、注意事項與修士主題台詞
- `data/faq_zh_tw.json`：固定 FAQ 問法與正式回答
- `knowledge.py`：JSON 驗證、FAQ 優先配對、教學配對及未知問題固定回覆
- `persona.py`：互動界線與情緒低落偵測
- `PERSONA.md`：修士正式人格規範

啟動時若 JSON 缺檔、格式錯誤、欄位不完整、教學 ID 重複，或 FAQ 引用了不存在的教學，程式會直接停止並顯示資料錯誤，不會帶著不完整規則上線。

## Railway 環境變數

```env
MONK_TOKEN=修士的 Discord Bot Token
GUILD_ID=禊月堂的 Discord 伺服器 ID
MONK_CHANNEL_ID=修士唯一允許回覆的 Discord 頻道 ID

AI_ENABLED=true
AI_CONFESSION_ENABLED=true
OPENAI_API_KEY=你的 OpenAI API Key
OPENAI_MODEL=gpt-5-nano
AI_DAILY_LIMIT=5
AI_MAX_OUTPUT_TOKENS=180
BOT_LANGUAGE=zh-TW
```

`AI_DAILY_LIMIT=0` 代表不限制，但不建議在正式環境使用。

`AI_ENABLED` 是告解 AI 的總開關，`AI_CONFESSION_ENABLED` 是告解功能開關。教學與規則查詢在程式中沒有 OpenAI 呼叫路徑；即使兩個開關都是 `true`，也只有 `/修士告解` 會使用 API。

`MONK_CHANNEL_ID` 是必要設定。修士的所有斜線指令只能在這個頻道執行；其他頻道只會收到私人導引訊息，不會查詢知識庫或呼叫 OpenAI。請在 Discord 開發者模式中複製目標頻道 ID，再加入 Railway Variables。

所有 Railway 變數會由 `config.py` 集中解析。布林值或數字格式錯誤時程式會停止啟動，避免帶著錯誤設定上線；Token 與 API Key 不會寫入紀錄。

目前每日次數使用記憶體計算，Railway 重新部署或重啟後會歸零。這個上限只套用於 `/修士告解`；`/問修士` 不會呼叫 AI。

告解使用獨立提示詞，不會取得遊戲知識庫，也不能回答遊戲數值或規則。告解回覆在 Discord 中僅指令使用者可見；送往 Responses API 時設定 `store=True`，因此玩家告解輸入與修士輸出會保存並顯示在所屬 OpenAI Project 的 Responses Logs。API 無法使用或今日次數已滿時，會改用原本的本地告解回覆。

AI 告解會收到玩家顯示名稱、告解內容、目前模式與正式罪惡值結果四個欄位，並以「已聽見告解 → 整理重點與下一步 → 平穩結語」三段式回覆。動態欄位會先跳脫處理，回覆保留段落並限制在最多 220 字。

告解正文以禊月堂世界觀為準：修士確實位於教堂告解室內主持告解，不會把告解解讀成比喻、模擬或一般客服對話。試行版與罪惡值限制只顯示在 Discord 回覆下方，不混入 AI 的告解正文。

告解提示內含「偷喝朋友飲料」「忘記上課三次」「藉告解告白」三組管理員核定範例。一般告解採三段中文引號；戀愛越界採兩段拒絕，並在本地直接處理、不送往 OpenAI API。

使用 `gpt-5-nano` 時，程式會固定採用 `minimal` 推理強度，避免短回覆的 token 額度全部消耗在推理而沒有可見文字。若 API 仍回傳空內容，Railway 紀錄只會顯示回應狀態、未完成原因及 token 統計；告解輸入與模型輸出則依 `store=True` 保存於 OpenAI Responses Logs。

## 測試

測試只使用暫存 JSON 與純記憶體假 AI，不啟動 Discord、不呼叫 OpenAI，也不連接任何資料庫：

```bash
python -m unittest discover -s tests -v
```


## 回覆保證

- 固定 FAQ 永遠優先於關鍵字教學，兩者都不使用 API。
- 回覆數值與規則只取自知識庫，不由修士或 AI 自行補寫。
- 教學與規則查詢永遠不呼叫 OpenAI；未知問題一律回覆「目前沒有正式資料」。
- 修士不讀寫 `church.db`，也不修改玩家數值、背包、身分組或活動狀態。
- 戀愛、曖昧、調情、成人內容與配對要求會在本地直接拒絕，不會送往 API。
- 問題含焦慮、自責或情緒低落語句時，回答會停用吐槽並改用平穩提醒。
- AI 告解只提供一次性陪伴，不會寫入玩家資料，也不能處理正式罪惡值。
- 一般問答使用精簡知識庫內容；目前最長固定回覆仍控制在約 200 字內。


## v4 隊長型人格調整

- 修士改為嚴肅、負責、重視秩序的隊長型角色。
- 一般問題不再用訓話式語氣。
- 無心失誤以修正與鼓勵為主；只有故意或反覆違規時才提高嚴格程度。
- 告解會肯定願意坦白，但仍要求玩家完成實際補救。
- 保留禁止戀愛、曖昧與成人互動的界線。

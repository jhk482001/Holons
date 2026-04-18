# 功能介紹 — agent_company

> 最後更新：2026-04-16
> 讀者：平台使用者、想知道「這平台能做什麼」的 PM / stakeholder

本文件只講使用者看得到的功能。技術細節請看 `implementation.md`，架構決策請看 `design.md`。

---

## 1. 這是什麼平台

agent_company 是一個**擬人化的多 agent 協作工作台**。你不是在跟聊天機器人聊天，而是在「經營一家由 LLM 組成的虛擬公司」。

核心心智模型：
- 你有多個**員工 (Agent)**，每個有名字、頭像、職位、技能、預算、工時
- 你可以直接跟員工聊天（Dialog Center），或者把多個員工串成**流程 (Workflow)** 讓他們接力完成任務
- 員工可以使用**工具 / MCP server / 知識庫**（統稱 Asset）完成更複雜的工作
- **總機 Lead** 是一個特殊員工，負責接待你的請求並轉派或代答

適合的使用情境：
- 內部內容生產流（編劇 → 對白 → 節奏評審 → 主編）
- 客戶回覆代理（Lead 總機 + 各領域專家）
- 知識庫問答（RAG + 多輪澄清）
- 重複性報告產出（排程觸發的 workflow）

---

## 2. 使用者角色

| 角色 | 身份 | 能做什麼 |
|------|------|---------|
| **admin** | 平台管理員 | 全部功能 + user 管理 + system feature flags + 公用 asset 維護 |
| **user** | 一般使用者 | 建自己的 agent/workflow/schedule，使用已授權的 asset，發 Lead 訊息 |

建立帳號目前由 admin 手動在「設定 → 使用者管理」完成。不開放自助註冊。

---

## 3. 介面導覽

左側固定側邊欄分為幾個區：

```
  agent_company
  ─────────────
  對話中心        ← 跟員工聊天、Lead 總機
  Dashboard      ← 即時監控（agent 負載、成本、時間表）
  員工           ← Agent CRUD + 詳細設定
  團隊           ← Group（把多個 agent 包成單位）
  自動化         ← Workflow 編輯 + Schedules
  紀錄           ← Runs 歷史 + Audit log
  Skill / MCP /  ← Asset Library
    知識庫
  設定           ← 個人偏好 + admin-only 分頁
```

每個區塊底下的分頁（tab）會在該頁面頂部顯示。

---

## 4. 功能列表

### 4.1 對話中心 (`/dialog`)

最常用的入口。左側是員工清單（含 Lead 總機），右側是對話。

- **Lead 總機**：不指定員工時預設跟 Lead 對話。Lead 會判斷要代答或轉派。
- **直接對某員工**：點選員工頭像，訊息直接進到那個員工的隊列。
- **訊息 Markdown 支援**：答案可以含表格、code block、連結。
- **Proxy answer 標記**：Lead 代員工回答時會標示「由 Lead 代答」，員工事後可 retract（收回並重答）。
- **員工設定直接編輯**：在側邊選中員工後，右側可切「對話 / 日曆 / 設定」三個 tab。設定 tab 可以直接改名字、prompt、skills、quotas，不用跳離對話頁。
- **大 bust 肖像**：對話頁每個員工有全身肖像，強化擬人感；小圓頭像則出現在各頁面的卡片 / 列表裡。

### 4.2 Dashboard (`/dashboard`)

即時監控介面，每 10 秒自動 refresh：

- **四張 summary 卡**：活躍員工數 / 佇列總深度 / 今日成本 / 今日 runs 數
- **Agent Timeline (Gantt)**：過去 N 小時各員工的任務時段；支援 1 / 6 / 24 小時與一週切換
- **24 小時負載熱圖**：每個員工 × 每個小時的忙碌程度（顏色越深越忙）
- **Agent 負載卡**：每個員工的圓頭像 + 當前佇列深度 / 上限 + 今日成本 + 即時狀態 badge

### 4.3 員工 (`/agents`)

Agent 管理。列表是卡片網格，每張卡點進去是詳細頁。

**建立 / 編輯 agent：**
- 名稱、職位 (`role_title`)、system prompt、model 選擇
- **Avatar Builder**：可視化選擇 body_type / body / hair / face / facial_hair / accessory；即時預覽
- **Working hours**：設定每週工作時段；worker 會尊重工時不派 task
- **Quotas**：每月 USD 上限 / 每日 request 上限 / 每日 token 上限
- **Skills 管理**：查看已提取的 skill，approve / reject / 編輯
- **Assets 指派**：從 Library 選 skill / tool / mcp / rag 給這個 agent 用

**其他：**
- **可見性** (public / private)：決定其他 user 看不看得到
- **Share to user**：明確 share 給某個 user（他會看到「借用員工」）
- **Export / Import**：整個 agent 設定（含 avatar / skills）匯出 JSON 再匯入
- **Status**：`active` / `paused` / `off_duty` / `budget_exceeded` / `quota_exceeded`

### 4.4 團隊 (`/groups`)

Group 是「把多個 agent 包成一個可重用單位」。Workflow 裡可以放一個 group node，執行時依照 group 的 mode 展開：

- `parallel`：所有成員同時執行
- `sequential`：按 member position 順序執行

用途：例如把「編劇組（小明 + 小華 + 小芳）」包成一個 group，workflow 上只放一個 node。

### 4.5 自動化

有兩個分頁：

**Workflows (`/workflows`)**
- Workflow 清單 + 新增 / 複製 / 匯入匯出
- **Workflow Editor (`/workflows/:id`)**：視覺化節點編輯器
  - 拖拉節點定位
  - 支援 zoom（滑鼠滾輪 / 按鈕）和 pan
  - 節點類型：`start` / `agent` / `group` / `end`
  - 節點之間自動連線
  - 點節點可編輯 label / 指派 agent / prompt template
  - 右上角「執行」按鈕 → 立即啟動一次 Run

**Schedules (`/schedules`)**
- 排程清單 + 啟用 / 停用 toggle
- 建立排程：選 workflow + cron 表達式 + 預設輸入
- 下次執行時間預覽
- 排程觸發由 `backend/services/scheduler.py` 的 tick thread 管理

### 4.6 紀錄

兩個分頁：

**Runs (`/runs`)**
- 所有 run 的清單（可過濾 status / agent / workflow / 日期）
- 點進 Run Detail 看：
  - 整個 workflow 圖（當前步驟高亮）
  - 每個 step 的 input / output / 使用的模型 / 耗時 / 成本
  - step 的 tool 呼叫（builtin tools + MCP）
  - 最終 output（Markdown 渲染）
  - 若進行中：pause / stop / cancel 按鈕

**Audit Log (`/audit`, admin only)**
- 系統層級的 audit：user 建立 / 刪除、feature flag 變更、quota 調整
- Asset 層級的 audit 走另一條路：在 Library 細節彈窗裡看

### 4.7 Skill / MCP / 知識庫 (`/library`)

統一的資產管理介面，四個分頁：

| 分頁 | 存的東西 | 典型用途 |
|------|---------|---------|
| **Skill** | Markdown 格式的 reusable prompt / playbook | 共用的寫作風格指南、review checklist |
| **工具** | 指向 builtin Python function 的 handle | 讀取時間、HTTP GET、搜尋技能庫 |
| **MCP** | Model Context Protocol server 連線 | Google Drive / GitHub / Fetch 等外部工具 |
| **知識庫** | RAG backend 連線 (pgvector / Bedrock KB / Pinecone) | 公司文件問答、歷史劇本搜尋 |

每個分頁都有：
- **搜尋**：按名稱或描述即時過濾
- **排序**：建立時間新→舊 / 舊→新、名稱 A→Z / Z→A（每個分頁獨立記憶偏好）
- **卡片網格**：每張卡顯示啟用狀態 / 授權人數 / 使用中 agent 數 / 總呼叫次數
- **細節彈窗**：
  - 使用量圖（過去 24 小時）
  - 授權使用者清單（admin 可新增 / 移除）
  - 變更紀錄
  - **編輯區**：可改名稱 / 描述 / config JSON / credential（對 MCP 和 RAG）— 即使是預設種子條目也能自由編輯

**預設種子條目**：第一次部署會自動種入一些範本，包括：
- 6 個 **skill** 範本（Brand Voice 寫作風格、品質檢核 Checklist、會議紀要格式、Bug Report 格式、Code Review 準則、使用者訪談腳本）
- 3 個 **builtin 工具**（Current Time、HTTP GET、Search Skills）
- 6 個 **MCP server 範本**（Google Drive、Google Docs、Google Slides、Google Calendar、GitHub、Fetch）— 預設 URL 空白且停用中，管理員需填入實際端點與 credential 後啟用

種子是**模板性質**的，不是現成可用的 production 連線。

### 4.8 設定 (`/settings`)

個人分頁 + admin 分頁：

**個人 (所有人)**
- 顯示名稱
- 密碼變更
- Lead proxy 設定（是否允許 Lead 代答、代答門檻）
- 偏好設定

**使用者管理 (admin)**
- 列出所有 user
- 建立 / 刪除 user
- Reset password
- 指派 role
- 個別 user 的 monthly / daily quota

**系統開關 (admin)**
- Feature flags：`grant_mcp_rag`（能否把 MCP/RAG 授權給其他 user）、`create_mcp`、`create_rag`（誰能建立）
- 切換後立即生效

---

## 5. 通知系統

- 右下角 🔔 鈴鐺顯示未讀數
- 通知類型：
  - Run 完成（成功 / 失敗）
  - Quota 即將 / 已達上限
  - Agent 自動暫停（off_duty / budget exceeded）
  - Asset 被授權 / 被移除授權
  - Proxy answer 被 retract
- 支援 mark read / resolve / dismiss

---

## 6. RAG 知識庫

三種 backend（透過 Library 的「知識庫」分頁建立）：

- **pgvector**（內建）：上傳文件 → 自動 chunk → 產生 embedding 存 `rag_documents` → 查詢時 `embedding <=> query_vec` top-K
- **Bedrock KB**（AWS）：不存在本地 DB，透過 Bedrock Knowledge Base API 查詢
- **Pinecone**（雲端向量 DB）：API key 連到外部 Pinecone 專案

全部走同一個 interface：`POST /api/assets/:id/rag/ingest` 和 `POST /api/assets/:id/rag/search`。

---

## 7. Skills 自動提取

執行完一個 Run 後，系統可以用 LLM 從 output 提取出「這次這個員工展現了什麼技能」，然後：
1. 寫入 `agent_skills` 表，狀態為 `pending`
2. Admin 在員工詳細頁的 Skills tab 看到
3. Approve → 變成這個 agent 正式的 skill
4. Reject → 刪除

這讓 skill library 會隨使用自動成長，而且每個 skill 都有人工審核把關。

---

## 8. Agent 分享與借用

- **私有 agent**（`visibility='private'`）：只有 owner 看得到
- **公開 agent**（`visibility='public'`）：所有 user 在 `/agents` 頁看得到，但標記「借用」
- **明確分享**：`agent_shares` 表記錄「owner 把這個 agent 分享給 user X」—借用者可以直接對這個 agent 發訊息，但不能改設定
- **External links**：未來可以把某個 agent 透過 signed URL 讓非登入者使用（目前框架就位，UI 未完整）

---

## 9. 多語系 / 國際化

目前 UI 全中文（繁體），assets seeds 也是中文。LLM 本身多語無障礙，但介面字串未抽出。若未來要出英文版，需要建一套 i18n key table — 目前不做。

---

## 10. 可能讓你驚訝的設計選擇

1. **沒有真人照片頭像** — 一律是 Peeps 風格 cartoon，避免個人照上傳風險和風格不一致。
2. **沒有即時訊息 push** — polling 每 8-10 秒更新。對話延遲可接受。
3. **Lead 會「代答」** — 員工 busy 時，Lead 可能直接回你一個看似員工說的答案。會明確標記，且員工可以 retract。
4. **Asset 有使用量和 audit trail** — 任何 skill / tool / mcp / rag 的每次呼叫都有記錄，便於審計和 billing。
5. **Workflow 可以 clone / export / import** — 在不同環境搬 workflow 不需要重建。

---

## 11. 尚未支援

- 真人照片上傳
- 多 tenant / 組織分層
- 即時 SSE/WebSocket push（polling 夠用）
- 多模型 provider（目前只有 AWS Bedrock → Claude）
- i18n（目前純中文）
- 行動裝置響應式設計（目前僅桌面優化）
- 匿名訪客模式（一定要登入）

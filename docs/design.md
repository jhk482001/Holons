# 設計文件 — agent_company

> 最後更新：2026-04-16
> 讀者：未來接手的工程師 / 想理解架構決策的人

本文件記錄「為什麼這樣做」。如果你想知道「程式放哪」或「怎麼跑」，請看 `implementation.md`；如果你想知道「這平台能做什麼」，請看 `features.md`。

---

## 1. 系統總覽

agent_company 是一個讓使用者以「虛擬員工」的心智模型管理一群 LLM agent 的平台。它不是聊天機器人產品，而是一個**多 agent 協作工作台**：每個 agent 是一個有名字、有職位、有技能、有工時、有預算的擬人化個體，使用者像派遣員工一樣派任務給他們，並把多個 agent 串成 workflow 完成複雜目標。

```
┌───────────────┐     HTTPS     ┌──────────────┐     TCP      ┌────────────┐
│  React SPA    │ ────────────▶ │  Flask app   │ ───────────▶ │ Postgres   │
│  (Vite + TS)  │               │   + worker   │              │ + pgvector │
└───────────────┘               └──────┬───────┘              └────────────┘
                                       │
                            ┌──────────┴──────────┐
                            ▼                     ▼
                    ┌──────────────┐      ┌──────────────┐
                    │ AWS Bedrock  │      │  MCP servers │
                    │ (Claude API) │      │ (HTTP/SSE)   │
                    └──────────────┘      └──────────────┘
```

### 核心元件

| 元件 | 技術 | 角色 |
|------|------|------|
| Frontend SPA | React 18 + Vite + TypeScript + TanStack Query | 單頁應用，所有狀態透過 polling REST 同步 |
| Backend API | Flask (同步) + psycopg2 | REST 服務、session auth、orchestration |
| Worker | 共用 Flask 進程內的 background thread | 消化 agent_tasks queue、呼叫 Claude、執行 workflow 下一步 |
| Database | Postgres 15 + `pgvector` + `pg_trgm` | 所有持久狀態（> 30 張表） |
| LLM Gateway | AWS Bedrock (Claude Sonnet / Haiku) | 目前唯一的模型後端，透過 `backend/bedrock_client.py` 統一封裝 |
| External tools | Python 模組（`backend/tools/`）+ MCP servers（HTTP streamable） | agent 可以呼叫的工具 |
| 知識庫 (RAG) | pgvector（內建）/ Bedrock KB / Pinecone | 三種 backend 透過 `backend/services/rag.py` 統一介面 |

### 為什麼這樣架構

- **單體 Flask 而非微服務**：專案規模不大（~15k 行 Python），微服務的通訊成本遠大於拆分帶來的好處。所有 domain logic 在同一個進程內，不用擔心資料一致性。
- **同步 Flask 而非 FastAPI/async**：沒有高併發需求（團隊內部工具），且 psycopg2 生態成熟。若未來要加 SSE/WebSocket 再考慮換 gunicorn + gevent。
- **Worker 與 API 共用進程**：dev 時簡單（一個指令啟動）；production 要 scale 就拆成獨立 process（目前未做）。
- **Postgres 當唯一儲存**：`pgvector` 讓我們在同一個 DB 裡同時處理關聯資料和向量搜尋，免了 Qdrant / Milvus 的運維成本。訊息佇列、job state、audit log 也全在 Postgres — trade 掉一些效能換取運維單純。
- **React Query 當 client state manager**：專案幾乎所有狀態都是「伺服器資料的鏡像」，Redux/Zustand 反而礙事。polling 也直接用 `refetchInterval`。

---

## 2. 核心 domain model

```
User (as_users)
  ├── owns ─────▶ Agent (agents)           多個
  │                 ├── has ──▶ AgentSkill[]
  │                 ├── has ──▶ AgentQuota[]
  │                 ├── uses ─▶ AssetItem[] (via agent_assets)
  │                 └── served by ──▶ Workflow[]  (as a node)
  │
  ├── owns ─────▶ Workflow (workflows)
  │                 └── contains ──▶ WorkflowNode[]
  │                       └── references ──▶ Agent | Group
  │
  ├── owns ─────▶ Group (groups_tbl)       "把多個 agent 包成一個並行/串列單元"
  │                 └── contains ──▶ GroupMember[]
  │
  ├── triggers ──▶ Run (runs)              一次 workflow 執行
  │                 └── has ──▶ RunStep[]  每個 node 的執行結果
  │
  ├── chats ────▶ LeadConversation → LeadMessage[]
  │
  ├── owns ─────▶ AssetItem (asset_items)  skill / tool / mcp / rag
  │                 └── granted to ──▶ AssetGrant[]
  │
  └── subject to ─▶ UserQuota (monthly / daily cost caps)
```

### 關鍵實體的角色

**Agent** — 擬人化的 LLM worker。除了 `model_id`（例如 `anthropic.claude-sonnet-4-20250514-v1:0`）和 `system_prompt` 之外，還有：
- `avatar_config` JSONB：Peeps SVG 組合配置（body/hair/face/facial_hair/accessory）
- `status`：`active` / `paused` / `off_duty` / `budget_exceeded` / `quota_exceeded`
- `working_hours`：JSONB 格式的每週排班，worker 判斷是否在工時內
- `max_queue_depth`：同時可接幾個 task
- `tool_config`：允許呼叫哪些 builtin tools
- `role_title`：給使用者看的「職位」，純顯示用

**Workflow** — 由多個 `WorkflowNode` 組成的 DAG。節點類型：
- `start` / `end`：流程的起終點
- `agent`：指派給某個 agent
- `group`：展開成一個 Group 內所有成員的並行/串列執行

**Run** — 一次 workflow 執行實例。狀態機：`queued → running → (done | error | cancelled | paused)`。engine 會把 workflow 展開成多個 `agent_tasks`（真正的佇列單位），每個 task 完成後驅動下一個 node。

**AssetItem** — 統一資產表。四種 kind：
- `skill`：可重用的 prompt / playbook（markdown）
- `tool`：builtin Python function 的 handle
- `mcp`：外部 MCP server 連線
- `rag`：知識庫連線（pgvector / Bedrock KB / Pinecone）

全部共用 `asset_grants`、`asset_audit_log`、`asset_usage_log`，是設計上的「統一資源授權框架」。

**Lead Conversation** — 使用者不需要知道誰在忙，只要跟「總機 Lead」說話，Lead agent 會代為分派到實際員工。這是 UX 入口的關鍵抽象。

---

## 3. 主要設計決策

### 3.1 為什麼 agent_tasks 是一張 DB table 而不是 Redis/RabbitMQ？

**Decision**: 用 Postgres 的 `FOR UPDATE SKIP LOCKED` 做 task queue。

**Why**:
- 專案已經需要 Postgres，加 Redis 是運維成本
- Postgres 事務保證「task 狀態變更 + 相關 domain 寫入」原子化 — 拿 Redis 做 queue 會需要處理兩階段提交
- `SKIP LOCKED` 的吞吐量對團隊內部工具綽綽有餘（遠低於每秒百次）
- audit / replay 直接 SQL 查

**Trade-off**: 不適合高吞吐（>1000 msg/s），且 worker 和 DB 耦合緊密。現階段可接受。

### 3.2 為什麼 polling 而不是 WebSocket/SSE？

**Decision**: 前端 polling，週期 3–10 秒。

**Why**:
- Flask 同步 worker 無法 hold long-lived 連線（每個 SSE/WS 連線 block 一個 worker）
- 延遲可接受：UI 上能看到的狀態變化（agent 狀態、run 進度）對人類使用者而言 5–10 秒內更新是 OK 的
- polling 失敗自動重試（下個 tick），不用自己寫 reconnect 邏輯
- React Query 的 `refetchInterval` 讓實作零成本

**Trade-off**: 使用者閒置時仍在敲 API。已接受。未來若真的需要，會做成 SSE，只在 `/api/events/dialog` 和 `/api/events/run` 兩個 hot path 套用。詳見下面的「未做的決策」。

### 3.3 為什麼 asset_items 是單張表而非 per-kind tables？

**Decision**: 四種資產（skill/tool/mcp/rag）共用 `asset_items`，`kind` 欄位區分。

**Why**:
- 授權 / 審計 / 用量統計對所有 kind 都是同樣的需求 — 做成單表就一套 code path 搞定 `asset_grants` / `asset_audit_log` / `asset_usage_log`
- `config` 是 JSONB，per-kind 的差異在 payload 裡處理，DB 不 care
- 跨 kind 的查詢（例如「Alice 這個月用了多少 asset？」）用 SQL 一次搞定

**Trade-off**:
- 單表沒辦法對 per-kind 欄位建索引（例如找所有 `backend='pgvector'` 的 RAG）
- JSONB schema 沒編譯期保證 — frontend 要靠 TypeScript interface + runtime 防衛

這個 trade 有意做的：預期 schema 變動頻率高，JSONB 降低 migration 成本。

### 3.4 為什麼 RBAC 只有 admin / user 兩層而不是多層角色？

**Decision**: `as_users.role` 只分 `admin` / `user`，搭配 `system_feature_flags` 做局部開關。

**Why**:
- 使用情境：團隊內部工具，admin ≈ platform ops，user ≈ 一般內容創作者
- 敏感操作（建 MCP、grant 資產、改 user quota）= admin
- 日常操作（建 agent、跑 workflow、編 skill）= user
- Feature flag 補足細粒度：例如 `grant_mcp_rag = false` 可以在保留 admin 角色的同時關掉某個功能

**Trade-off**: 若未來要多 tenant 或分專案權限，這套會不夠用。目前不做。

### 3.5 為什麼 credential 用 Fernet 對稱加密存 DB？

**Decision**: MCP 和 RAG 的 credential（API key / token）透過 `backend/services/asset_crypto.py` 以 Fernet 加密寫進 `asset_items.credential_encrypted`。key 存在 `env.config`。

**Why**:
- 團隊內部工具不需要 HSM / KMS 等級
- Fernet 有內建 IV + MAC，不會手寫出 bug
- 前端 **從不** 讀 credential — API 只回 `has_credential: boolean`
- 讀取只發生在 worker 呼叫 MCP 那一刻

**Trade-off**: key 本身放在 disk 上 — 若 disk 被拿走等於 credential 全洩。對應處理：`env.config` 不進 git、production 建議放 `/etc` 或環境變數。

### 3.6 為什麼 Lead agent 走 proxy 模式？

**Decision**: 使用者不對員工直接對話，而是對 Lead 總機；Lead 判斷要不要「代 XX 回覆」，或者把訊息轉派到真正的員工。

**Why**:
- UX：使用者不需要記住誰是誰
- 延遲抹平：員工 busy 時，Lead 可以先用上下文代答
- 安全：Lead 可以過濾掉明顯不合理的請求，免得 burn token

**Trade-off**:
- 多一層 LLM call（成本）
- Lead 代答可能說錯 — 使用者看得到 `proxy_answer_by` 標記，且員工有 `retract` 可以收回

### 3.7 為什麼 Peeps SVG 而不是上傳頭像？

**Decision**: avatar 是由幾個 SVG part（body / hair / face / facial_hair / accessory）組合出來的 cartoon character，存在 DB 的只有名字組合。

**Why**:
- 一致的視覺風格 — 不會有人上傳奇怪圖
- 零儲存成本（composition 是 deterministic 的）
- 語意化 `avatar_config`（相同員工在不同畫面都一樣）
- 快速 prototype 新員工不用煩惱頭像素材

**Trade-off**: 使用者不能用真人照片 — 這是刻意的 product decision，不是 bug。

### 3.8 為什麼 Skill 是 extracted 而不是 pre-defined？

**Decision**: Agent 的 skill 是 LLM 從歷次 Run 的 output 提取（`backend/services/skill_extractor.py`），admin 再 approve / reject。

**Why**:
- 員工真正會的技能 = 他實際做過的事 — 靜態清單會過時
- LLM 提取 + 人工審核 = 既有自動化又有 guardrail
- skills 本身也是 asset，可以 share / grant / reuse

**Trade-off**:
- 需要多一次 LLM call
- 提取品質依賴 prompt engineering
- 冷啟動時 skill 庫是空的（用 admin approval 門檻把關不讓垃圾進去）

---

## 4. 擴充邊界 / 明確不做

記錄「為什麼現在不做」比記錄「正在做什麼」更重要，因為未來有人會想做，而我們不希望他以為沒想過。

### 不做：多 tenant / organization 分層
**為什麼不做**：團隊內部工具，所有人看得到彼此的 asset（除非透過 `agent_shares` 明確分享）。若未來變成 SaaS 再加。

### 不做：WebSocket / SSE real-time push
**為什麼不做**：當前 polling 延遲 5-10 秒對使用者是可接受的，而 SSE 的實作成本（gunicorn worker 模型改動、connection lifecycle、reconnect）不值得現在付。門檻：**如果對話延遲變成使用者抱怨的前三名，就做。**

### 不做：In-memory cache 層
**為什麼不做**：Postgres 對 agent/list 類查詢 <1ms，加 cache 的 invalidation 風險大於 reads saved。如果真的變瓶頸：(a) 先上 HTTP ETag，(b) 再加 5 秒 TTL read-through，(c) write-through 是最後手段。

### 不做：Multi-model routing（同時支援 OpenAI/Anthropic/Gemini）
**為什麼不做**：Bedrock 已經能拿到 Claude 3.5/4 系列，需求沒大到要抽象多 provider。`backend/bedrock_client.py` 本來就是 thin wrapper，未來要換很快。

### 不做：Horizontal worker scaling
**為什麼不做**：目前 worker thread 在 app process 裡，因為單機吞吐夠。若要拆，`agent_tasks` 的 `SKIP LOCKED` 天然支援多 worker，只需要把 `worker.py` 改成獨立進程起起來即可 — 那時候再拆。

### 不做：Audit log 的完整 retention 策略
**為什麼不做**：目前 `asset_audit_log` 和 `audit_log` 無限累積，尚無合規壓力。建議到 6 個月時做 archive 或加 TTL。

---

## 5. 資料流與非阻塞路徑

### 5.1 一次 Workflow Run 的旅程

```
使用者按「執行」
  │
  ▼
POST /api/workflows/:id/run
  │
  │  ① runs insert (status='queued')
  │  ② dispatch_workflow() 展開 workflow_nodes → 建第一批 agent_tasks
  │
  └─▶ 立即回應 {run_id}（不等執行完成）

背景 worker thread:
  │
  ▼ 每 N 秒 poll agent_tasks (FOR UPDATE SKIP LOCKED)
  │
  ├─▶ execute_task(task):
  │     - 組 prompt（前一步 output + 當前 node template）
  │     - 呼叫 Bedrock（可能多 turn，帶 tool use）
  │     - 插 run_steps
  │     - 扣 user_quotas / agent_quotas
  │
  ├─▶ on_task_complete(task, result):
  │     - 更新 agent_tasks.status='done'
  │     - 呼叫 _advance_to_next_node() 建下一個 task
  │     - 或呼叫 _notify_run_complete() 觸發 notification
  │
  └─▶ on_task_failed: 類似，但走 error path
```

關鍵設計：**executor 不 recursively 呼叫下一個 task**，而是把下一個 task 塞回佇列讓 worker 拿。這樣 worker scale 或 crash 都是安全的。

### 5.2 Lead Conversation 的旅程

```
使用者在 Dialog Center 對 "Lead" 發訊息
  │
  ▼
POST /api/lead/chat
  │
  ├─▶ lead_messages insert (role='user')
  │
  ├─▶ lead_agent.respond():
  │     - 決定走哪條路：
  │       (a) 直接代答（低風險問題 + 有足夠 context）
  │       (b) 轉派到員工（建 run 或 agent_tasks）
  │
  └─▶ 回應 {thread_id, reply: {text, proxy_answer_by?}}

使用者下一輪可以看到 Lead 回了話，或看到員工正在處理。
若員工覺得 Lead 代答錯，可以 retract。
```

### 5.3 MCP Tool Call 的旅程

```
agent 在執行中呼叫 tool
  │
  ▼
engine._execute_with_tools():
  │
  ├─▶ gather tools:
  │     - builtin tools (backend/tools/*)
  │     - assigned MCP servers (agent_assets + asset_items.kind='mcp')
  │
  ├─▶ 組 Claude tool schema
  ├─▶ 呼叫 Bedrock (可能 tool_use block)
  │
  └─▶ 若有 tool_use:
       - builtin: 直接 Python 呼叫
       - MCP:    backend/services/mcp_client.py via streamable HTTP
       - 把 result 塞回下一輪 turn，直到沒有 tool_use 為止
```

最多 turn 數由 engine 的 safety limit 控制（避免無窮 loop）。

---

## 6. 風險與已知限制

| 風險 | 現況 | 緩解 |
|------|------|------|
| Flask dev server 不適合 production | 明白 | 未來用 gunicorn，docker-compose 已有 template |
| Secret 暴露於 env.config | 明白 | 檔案不進 git，`.env` 走 docker secret |
| LLM cost 爆炸 | 有 quota + budget_exceeded 狀態 | 每次呼叫 pre-check + post-charge |
| MCP server 失聯 / slow | 有 timeout | 寫入 `asset_audit_log` 便於排查 |
| agent_tasks 長時間卡住 | worker 有 stuck detection | reset_stuck_tasks() 排程 |
| 無資料備份 | 待辦 | pg_dump to S3 排程 |

---

## 7. 近期 UI / UX 修正（2026-04）

這些不是重大架構變動，但記下來避免重走老路：

1. **WorkflowEditor zoom 失效** — 原因是 `fitToContent` 被放進 `useEffect` 依賴陣列，每次 re-render 都會觸發 auto-fit 覆蓋使用者 zoom。用一次性 ref flag (`didFitRef`) 解決。
2. **圓形頭像偏移** — Peeps 角色的 hair/face 在 850×1200 canvas 上不是視覺中心。用 Playwright 的 `getBBox()` 量測後，改用 `viewBox="120,-60,700,700"` 配 `w=700&h=700` 讓頭像 ~70% 占比置中。關鍵 insight：SVG 的 `width/height` attribute 必須跟 `viewBox` 同比例，否則 `preserveAspectRatio` 會 letterbox。
3. **Library 介面優化** — 加入 name search + 4 種排序 + 可編輯的 config JSON 編輯器 + MCP seed 說明。Library 原本無法編輯已建立項目，現在細節彈窗內可直接改 config / credential，seeded 項目也一樣能改。

---

## 8. 延伸閱讀

- 檔案級的實作細節 → `implementation.md`
- 面向使用者的功能說明 → `features.md`
- Peeps SVG 組合系統 → 專案根目錄的 `peeps-generator/`（另一個獨立 repo）
- DB schema 完整定義 → `backend/schema.py`
- API routes 完整列表 → `backend/app.py` 頂部註解 + `grep "^@app.route"`

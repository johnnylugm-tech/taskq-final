# Software Requirements Specification (SRS) — taskq-api

> 規格單一真實來源:`/Users/johnny/projects/taskq-final/SPEC.md`(v1.0.0,2026-07-30)。
> 本文件為 `taskq-api` 規格的第 2 輪驗證測床的 SRS 轉錄 — 100% transcribe,不發明、不省略。

## 1. Introduction

### 1.1 專案名稱
`taskq-api`(對應 `PROJECT_BRIEF-2.md`,執行時由 `SPEC-2.md` 更名為 `SPEC.md`)

### 1.2 目的
任務佇列的 HTTP 服務化 — 以 REST API 提交、查詢、執行任務;資料持久化於關聯式資料庫;schema 隨版本演進;支援認證、授權與流量控制。

### 1.3 語言 / 形態
- 語言:Python 3.11
- 形態:ASGI 服務,以 `uvicorn taskq_api.app:app` 啟動;另提供 `python -m taskq_api` 管理入口(`migrate` / `seed` / `healthcheck`)

### 1.4 範圍(Scope)
本 SRS 涵蓋 10 個 Functional Requirements 與 12 個 Non-Functional Requirements,完全對應 `SPEC.md` §3、§4。每條要求皆來自 canonical spec verbatim 或以 `DERIVED:` 標籤註明詮釋理由。

### 1.5 縮寫
| 縮寫 | 全名 |
|------|------|
| FR | Functional Requirement |
| NFR | Non-Functional Requirement |
| AC | Acceptance Criterion |
| ORM | Object-Relational Mapping(SQLAlchemy) |
| RFC 7807 | Problem Details for HTTP APIs |
| p95 | 第 95 百分位延遲 |
| SBOM | Software Bill of Materials |
| CRG | Code Review Graph(framework 工具) |

## 2. Constraints

| ID | 約束 | 來源 |
|----|------|------|
| C-01 | 全部 `/v1/*` 端點必須經 `X-API-Key` 認證,缺乏或無效 → `HTTP 401` + `application/problem+json` | SPEC.md §3 FR-03 |
| C-02 | 全部資料存取經 `repository/` 層;**業務層不得直接持有 `Session`** | SPEC.md §3 FR-06 |
| C-03 | 業務層禁止 import `sqlalchemy`(僅 `repository/` 為唯一允許層) | SPEC.md §4 NFR-06 |
| C-04 | 分層契約 `api > service > repository > models`(`config` / `errors` 為 independence 模組) | SPEC.md §4 NFR-06 |
| C-05 | 全部非 2xx 回應的 `Content-Type` 必須為 `application/problem+json`(RFC 7807) | SPEC.md §3 FR-10 |
| C-06 | `stdout_tail` / `stderr_tail` / 日誌 / 錯誤 body 落盤或送出前需通過敏感資料遮蔽(正則覆蓋 sk-* / token= / Bearer / postgres URL) | SPEC.md §4 NFR-04 |
| C-07 | 全 codebase 禁用 `shell=True`、`eval(`、`exec(`(以 grep 驗證) | SPEC.md §4 NFR-02 |
| C-08 | runtime 依賴必須以 `==` 釘版於 `requirements.txt`;transitive 以 `requirements.lock` 鎖定 | SPEC.md §4 NFR-07 |
| C-09 | `/healthz`、`/readyz` 不需認證、不受限流 | SPEC.md §3 FR-03, FR-05 |
| C-10 | `asyncio.CancelledError` 必須向上傳播,不得被 `except Exception` 吞掉 | SPEC.md §4 NFR-03 |

## 3. Functional Requirements

### FR-01: 任務資源 CRUD API

> DERIVED: SPEC.md lines 79-92 — AC list is a testable decomposition of the canonical section; each AC carries a verbatim canonical phrase.

對應 `SPEC.md` lines 79-92。

**Acceptance criteria**

#### AC-1.1
`POST /v1/tasks`(scope `write`)以 `TaskCreate` pydantic 模型驗證 body,有效負載回應 `201` 並回傳 task id。

#### AC-1.2
驗證規則(非空 / ≤1000 字元 / 注入字元黑名單 / 名稱唯一)違反時,回應 `HTTP 422` + `application/problem+json`(`type=/errors/validation`)— 對應 SPEC.md line 88。

#### AC-1.3
`GET /v1/tasks/{id}`(scope `read`)回傳單一任務全欄位;未知 id 回應 `HTTP 404` + problem+json(`type=/errors/not-found`)— 對應 SPEC.md lines 84, 89。

#### AC-1.4
`GET /v1/tasks`(scope `read`)為 **cursor-based 分頁**(不得用 offset),支援 `?status=`、`?limit=`、`?cursor=`;預設 `limit=50`,上限 200;超過上限 → 422。

#### AC-1.5
`DELETE /v1/tasks/{id}`(scope `admin`)刪除任務(連同結果列,同一交易)— 對應 SPEC.md line 86。

### FR-02: 任務執行端點

> DERIVED: SPEC.md lines 93-100 — AC list is a testable decomposition of the canonical section; each AC carries a verbatim canonical phrase.

對應 `SPEC.md` lines 93-100。

**Acceptance criteria**

#### AC-2.1
`POST /v1/tasks/{id}/run`(scope `write`)回應 `HTTP 202 Accepted`,body 含 `run_id`。

#### AC-2.2
實際執行以 `asyncio.create_subprocess_exec(*shlex.split(command))` 進行,**禁 `shell=True`**,timeout 為 `TASKQ_TASK_TIMEOUT`(預設 10.0 秒)— 對應 SPEC.md line 96。

#### AC-2.3
狀態機:`pending → running → done | failed | timeout`。

#### AC-2.4
執行結果寫入 `task_results` 表(FR-07 的 v3 schema),欄位:`exit_code` / `stdout_tail` / `stderr_tail` / `duration_ms` / `finished_at`。

#### AC-2.5
`GET /v1/tasks/{id}/runs`(scope `read`)回傳該任務的歷史執行紀錄,新到舊排序。

### FR-03: API Key 認證

> DERIVED: SPEC.md lines 101-107 — AC list is a testable decomposition of the canonical section; each AC carries a verbatim canonical phrase.

對應 `SPEC.md` lines 101-107。

**Acceptance criteria**

#### AC-3.1
全部 `/v1/*` 端點要求 `X-API-Key` header;缺少或無效 → `HTTP 401` + problem+json(`type=/errors/unauthenticated`)— 對應 SPEC.md line 103。

#### AC-3.2
金鑰**以 SHA-256 雜湊儲存**於 `api_keys` 表,**不得存明文**;比對用 `hmac.compare_digest`(常數時間)— 對應 SPEC.md line 104。

#### AC-3.3
金鑰由 `python -m taskq_api key create --scope <scope>` 產生,明文**只在建立當下印出一次**。

#### AC-3.4
`revoked_at` 非空的金鑰一律視為無效。

#### AC-3.5
`/healthz`、`/readyz` 不要求認證。

### FR-04: Scope 授權

> DERIVED: SPEC.md lines 109-113 — AC list is a testable decomposition of the canonical section; each AC carries a verbatim canonical phrase.

對應 `SPEC.md` lines 109-113。

**Acceptance criteria**

#### AC-4.1
每把金鑰帶一個 scope:`read` < `write` < `admin`(階層包含)— 對應 SPEC.md line 111。

#### AC-4.2
端點所需 scope 見 FR-01/02 表;不足 → `HTTP 403` + problem+json(`type=/errors/forbidden`),且 body **不得洩漏該資源是否存在**。

#### AC-4.3
授權判定必須在**單一中介層(dependency)**完成,不得散落於各 handler;以測試斷言「每個 `/v1` 路由都經過同一個 dependency」。

### FR-05: 流量控制

> DERIVED: SPEC.md lines 115-120 — AC list is a testable decomposition of the canonical section; each AC carries a verbatim canonical phrase.

對應 `SPEC.md` lines 115-120。

**Acceptance criteria**

#### AC-5.1
per-token 令牌桶:容量 `TASKQ_RATE_BURST`,補充速率 `TASKQ_RATE_PER_SEC`。

#### AC-5.2
超限 → `HTTP 429` + problem+json(`type=/errors/rate-limited`) + `Retry-After` header(秒)— 對應 SPEC.md line 118。

#### AC-5.3
令牌桶狀態存於資料庫(`rate_buckets` 表),跨 worker 一致;更新必須在單一交易內以 row-level lock 進行。

#### AC-5.4
`/healthz`、`/readyz` 不受限流。

### FR-06: 持久化層與交易邊界

> DERIVED: SPEC.md lines 122-128 — AC list is a testable decomposition of the canonical section; each AC carries a verbatim canonical phrase.

對應 `SPEC.md` lines 122-128。

**Acceptance criteria**

#### AC-6.1
全部資料存取經由 `repository/` 層,**業務層不得直接持有 `Session`**。

#### AC-6.2
每個 API 請求一個 `Session`,交易邊界明確:成功 commit、例外 rollback(以 context manager 保證)— 對應 SPEC.md line 125。

#### AC-6.3
**禁止字串拼接 SQL**;一律使用 ORM 或參數化查詢(NFR-02)— 對應 SPEC.md line 126。

#### AC-6.4
關聯查詢必須用 `selectinload` / `joinedload` 顯式預載 — **N+1 為驗收失敗條件**(NFR-01)— 對應 SPEC.md line 127。

#### AC-6.5
連線池:`pool_size=TASKQ_DB_POOL_SIZE`(預設 5),`pool_pre_ping=True`。

### FR-07: Schema Migration(Alembic 三步演進)

> DERIVED: SPEC.md lines 130-143 — AC list is a testable decomposition of the canonical section; each AC carries a verbatim canonical phrase.

對應 `SPEC.md` lines 130-143。

**Acceptance criteria**

#### AC-7.1
revision v1:建立 `tasks`、`api_keys` 兩表;downgrade:drop 兩表。

#### AC-7.2
revision v2:新增 `tags`、`task_tags`(多對多)+ `tasks.name` 唯一索引;downgrade:drop 新表與索引,不影響 v1 資料。

#### AC-7.3
revision v3:**含資料搬遷** — 把 `tasks.result_json` 拆為獨立的 `task_results` 表,搬遷既有資料後移除原欄位;反向 downgrade:反向搬遷回 `tasks.result_json` 後 drop `task_results`,**資料不得遺失**。

#### AC-7.4
`alembic upgrade head` 與 `alembic downgrade base` 都必須成功。

#### AC-7.5
**往返可逆性驗收**:`upgrade head` → 寫入樣本資料 → `downgrade -1` → `upgrade head`,樣本資料的欄位值必須逐欄相同(v3 的資料搬遷是本條的重點)— 對應 SPEC.md line 141。

#### AC-7.6
禁止以 `op.execute("DROP TABLE ...")` 之類的破壞性捷徑取代真正的 downgrade。

#### AC-7.7
migration 檔本身納入測試覆蓋(以 `alembic` 的 offline SQL 產生 + 斷言)— 對應 SPEC.md line 143。

### FR-08: 非同步執行器

> DERIVED: SPEC.md lines 145-150 — AC list is a testable decomposition of the canonical section; each AC carries a verbatim canonical phrase.

對應 `SPEC.md` lines 145-150。

**Acceptance criteria**

#### AC-8.1
背景執行以 `asyncio.TaskGroup` 管理;服務關閉時必須 **graceful drain**(等待進行中的任務至 `TASKQ_DRAIN_TIMEOUT`,逾時則標記 `interrupted`)— 對應 SPEC.md line 147。

#### AC-8.2
併發上限 `TASKQ_MAX_CONCURRENT`(預設 8);超過時新任務排隊,不得無限制生成 coroutine。

#### AC-8.3
任務 timeout 以 `asyncio.wait_for` 實作;逾時必須**確實終止子進程**(`process.kill()` 後 `await process.wait()`),不得留下孤兒進程。

#### AC-8.4
取消語意:`asyncio.CancelledError` 必須向上傳播,**不得被 `except Exception` 吞掉**(NFR-03)— 對應 SPEC.md line 150。

### FR-09: 健康檢查與可觀測性

> DERIVED: SPEC.md lines 152-160 — AC list is a testable decomposition of the canonical section; each AC carries a verbatim canonical phrase.

對應 `SPEC.md` lines 152-160。

**Acceptance criteria**

#### AC-9.1
`GET /healthz`(無認證):進程存活 → `200` `{"status":"ok"}`。

#### AC-9.2
`GET /readyz`(無認證):DB 連線可用 **且** `alembic current` == head → `200`;否則 `503` 並在 body 說明哪一項失敗。

#### AC-9.3
`GET /v1/metrics`(scope `admin`):任務計數(按狀態)、執行延遲分位數、rate-limit 拒絕數。

#### AC-9.4
`/readyz` 的「migration 未到 head」判定是關鍵:部署新程式碼但忘記跑 migration 時必須 **fail closed**。

### FR-10: 錯誤契約(RFC 7807)

> DERIVED: SPEC.md lines 162-168 — AC list is a testable decomposition of the canonical section; each AC carries a verbatim canonical phrase.

對應 `SPEC.md` lines 162-168。

**Acceptance criteria**

#### AC-10.1
全部非 2xx 回應的 `Content-Type` 為 `application/problem+json`。

#### AC-10.2
body 欄位:`type`(URI)、`title`、`status`、`detail`、`instance`、`correlation_id`。

#### AC-10.3
**`detail` 不得洩漏內部細節**:不得含 SQL 陳述、堆疊追蹤、檔案路徑、資料庫結構描述。

#### AC-10.4
`correlation_id` 同時出現在回應 header `X-Correlation-Id` 與伺服器日誌,可用於串接。

#### AC-10.5
錯誤碼對照:422 驗證 / 401 未認證 / 403 scope 不足 / 404 未知資源 / 409 名稱衝突 / 429 超限 / 503 未就緒 / 500 其他。

## 4. Non-Functional Requirements

> DERIVED: SPEC.md lines 177-184 — AC list is a testable decomposition of the canonical section; each AC carries a verbatim canonical phrase.
### NFR-01: 效能與查詢效率

- **dimension**:`performance`(對應 `harness/harness/ssi/prompts/evaluate_dimension.md` §`### performance (Tier 3)`)
- 對應 `SPEC.md` lines 177-184

**Acceptance criteria**

#### AC-N1.1
`GET /v1/tasks/{id}` 在 10,000 筆資料下 **p95 < 30ms**(不含網路,以 ASGI transport 量測)— 對應 SPEC.md line 180。

#### AC-N1.2
`GET /v1/tasks?limit=50` 在 10,000 筆資料下 **p95 < 80ms**。

#### AC-N1.3
**N+1 為失敗條件**:列表端點回應一次請求所發出的 SQL 陳述數必須是 **常數**(與回傳筆數無關),以 SQLAlchemy event listener 計數斷言。

#### AC-N1.4
量測方式:`pytest-benchmark`。

**Coverage note**:本 NFR 全部 AC 由 `evaluate_dimension.md` §`### performance (Tier 3)`(`pytest-benchmark`)覆蓋。

> DERIVED: SPEC.md lines 185-194 — AC list is a testable decomposition of the canonical section; each AC carries a verbatim canonical phrase.
### NFR-02: HTTP 與資料層安全

- **dimension**:`security`(對應 `evaluate_dimension.md` §`### security (Tier 2)`)
- 對應 `SPEC.md` lines 185-194

**Acceptance criteria**

#### AC-N2.1
全 codebase 禁用 `shell=True`、`eval(`、`exec(`(`grep -rn "shell=True\|eval(\|exec(" 03-development/src/` 為 0 命中)— 對應 SPEC.md line 188。

#### AC-N2.2
**禁止字串拼接 SQL**:不得出現 f-string / `%` / `+` 組成的 SQL;一律 ORM 或參數化(以 grep + code review 雙重驗證)— 對應 SPEC.md line 189。

#### AC-N2.3
API key **雜湊儲存**,比對用 `hmac.compare_digest`(FR-03)— 對應 SPEC.md line 190。

#### AC-N2.4
403 回應不得洩漏資源存在性(FR-04)— 對應 SPEC.md line 191。

#### AC-N2.5
錯誤 body 不得含堆疊/SQL/路徑(FR-10)— 對應 SPEC.md line 192。

#### AC-N2.6
CORS 預設**拒絕所有來源**;允許清單由 `TASKQ_CORS_ORIGINS` 明示(空 = 全拒)— 對應 SPEC.md line 193。

#### AC-N2.7
`bandit -r 03-development/src/`:**0 HIGH、0 MEDIUM**。

**Coverage note**:AC-N2.1、N2.2、N2.5 由 `evaluate_dimension.md` §`### security (Tier 2)`(`bandit`)覆蓋;AC-N2.3 / N2.4 / N2.6 / N2.7 由整合測試覆蓋。

> DERIVED: SPEC.md lines 196-204 — AC list is a testable decomposition of the canonical section; each AC carries a verbatim canonical phrase.
### NFR-03: 錯誤處理、交易與非同步正確性

- **dimension**:`error_handling`(對應 `evaluate_dimension.md` §`### error_handling (Tier 3)`)
- 對應 `SPEC.md` lines 196-204

**Acceptance criteria**

#### AC-N3.1
每個請求的交易邊界明確:成功 commit、例外 rollback,以 context manager 保證(FR-06)— 對應 SPEC.md line 199。

#### AC-N3.2
**不得**出現裸 `except:`、`except Exception: pass`。

#### AC-N3.3
**`asyncio.CancelledError` 不得被吞掉** —— 必須重新拋出(async 專屬的吞噬陷阱)— 對應 SPEC.md line 201。

#### AC-N3.4
資料庫連線失敗 → `/readyz` `503` + 明確 detail;不得靜默重試至無限。

#### AC-N3.5
任務 timeout 必須確實終止子進程,不留孤兒(FR-08)— 對應 SPEC.md line 203。

#### AC-N3.6
migration 失敗 → 交易 rollback,資料庫維持在前一個 revision(FR-07)— 對應 SPEC.md line 204。

**Coverage note**:本 NFR 全部 AC 由 `evaluate_dimension.md` §`### error_handling (Tier 3)`(`ast-error-handling`,含 anti-pattern 扣分)覆蓋。framework 已知對 async 掃描的精確度為本輪測床要驗證的對象(SPEC.md line 483)。

> DERIVED: SPEC.md lines 206-212 — AC list is a testable decomposition of the canonical section; each AC carries a verbatim canonical phrase.
### NFR-04: 敏感資料遮蔽

- **dimension**:`security`(對應 `evaluate_dimension.md` §`### security (Tier 2)`)
- 對應 `SPEC.md` lines 206-212

**Acceptance criteria**

#### AC-N4.1
`stdout_tail` / `stderr_tail` / 日誌 / 錯誤 body 落盤或送出前,匹配正則 `(sk-[A-Za-z0-9_-]{8,}|token=\S+|Bearer\s+\S+|postgres(ql)?://[^\s]+)` 的行整行以 `[REDACTED]` 取代。

#### AC-N4.2
**資料庫連線字串**(含密碼)不得出現在任何日誌、錯誤訊息或 `/v1/metrics` 回應中。

#### AC-N4.3
API key 明文只在 `key create` 當下輸出一次,不得寫入任何持久化位置。

> DERIVED: SPEC.md lines 214-218 — AC list is a testable decomposition of the canonical section; each AC carries a verbatim canonical phrase.
### NFR-05: 文件覆蓋

- **dimension**:`documentation`(對應 `evaluate_dimension.md` §`### documentation (Tier 3)`)
- 對應 `SPEC.md` lines 214-218

**Acceptance criteria**

#### AC-N5.1
全部公開函式/類別有 docstring 且含 `[FR-XX]` 或 `[NFR-XX]` 引用,覆蓋率 **100%**。

#### AC-N5.2
每個 API 端點在 OpenAPI schema 中有 `summary` 與 `description`(FastAPI 自動產生的 `/openapi.json` 以測試斷言)— 對應 SPEC.md line 218。

**Coverage note**:AC-N5.1 由 `evaluate_dimension.md` §`### documentation (Tier 3)`(`ast-docstrings` 掃公開 `def`/`class` docstring 覆蓋率)覆蓋。AC-N5.2 由整合測試覆蓋(`/openapi.json` 結構斷言)。

> DERIVED: SPEC.md lines 220-232 — AC list is a testable decomposition of the canonical section; each AC carries a verbatim canonical phrase.
### NFR-06: 架構分層契約

- **dimension**:`architecture_constraints`(對應 `evaluate_dimension.md` §`### architecture_constraints (Tier 1 — Gate 1 only, tool-scored: import-linter)`)
- 對應 `SPEC.md` lines 220-232

**Acceptance criteria**

#### AC-N6.1
專案根目錄**必須存在 `.importlinter`**,宣告 layers contract:`api > service > repository > models`(上層可 import 下層,**下層不得 import 上層**;`config` 與 `errors` 為 independence 模組)— 對應 SPEC.md lines 223-229。

#### AC-N6.2
**額外禁令(forbidden contract)**:`repository` 以外的任何層**不得 import `sqlalchemy`**。

#### AC-N6.3
`lint-imports` 必須 **exit 0**。

#### AC-N6.4
禁止以刪除 `.importlinter`、萬用字元 `ignore_imports`、或降級 contract 的方式取得通過。

**Coverage note**:全部 AC 由 `evaluate_dimension.md` §`### architecture_constraints`(`lint-imports` exit 0 → 100,其他 → 0)覆蓋;`.importlinter` 缺席 → UNSCOREABLE(None),非 100。

> DERIVED: SPEC.md lines 234-240 — AC list is a testable decomposition of the canonical section; each AC carries a verbatim canonical phrase.
### NFR-07: 依賴與授權合規

- **dimension**:`license_compliance`(對應 `evaluate_dimension.md` §`### license_compliance (Tier 1)`)
- 對應 `SPEC.md` lines 234-240

**Acceptance criteria**

#### AC-N7.1
全部 runtime 依賴在 `requirements.txt` 以 `==` 釘版;**transitive 依賴以 lock 檔(`requirements.lock`)完整鎖定**。

#### AC-N7.2
允許的 license:MIT / BSD-2-Clause / BSD-3-Clause / Apache-2.0 / PSF;出現其他 → 該依賴不得使用。

#### AC-N7.3
**掃描範圍必須包含完整依賴樹**(直接 + transitive),證據命令:`pip-licenses --format=json --with-system`。

#### AC-N7.4
產出 SBOM 於 `08-config/SBOM.json`,含每個依賴的 `name` / `version` / `license` / `direct|transitive`。

**Coverage note**:AC-N7.2、N7.3 由 `evaluate_dimension.md` §`### license_compliance`(`scancode --license`)覆蓋。AC-N7.4(SBOM 檔產出)需要獨立的實作任務:`scancode` 評分不等於 SBOM artifact 存在性檢查,Phase 3+ 須有專屬驗證。

> DERIVED: SPEC.md lines 242-247 — AC list is a testable decomposition of the canonical section; each AC carries a verbatim canonical phrase.
### NFR-08: 變異測試

- **dimension**:`mutation_testing`(對應 `evaluate_dimension.md` §`### mutation_testing (Tier 1)`)
- 對應 `SPEC.md` lines 242-247

**Acceptance criteria**

#### AC-N8.1
`.methodology/harness_config.json` 設 `features.mutation_testing: true`。

#### AC-N8.2
**mutation score ≥ 70**。

#### AC-N8.3
範圍限定於 `service/` 與 `repository/` 兩層,並在 `harness_config.json` 註記限定理由(執行時間預算)— 對應 SPEC.md line 247。

**Coverage note**:AC-N8.2 由 `evaluate_dimension.md` §`### mutation_testing`(`mutmut` score)覆蓋。AC-N8.1、N8.3 由 `.methodology/harness_config.json` 內容檢查覆蓋。

> DERIVED: SPEC.md lines 249-257 — AC list is a testable decomposition of the canonical section; each AC carries a verbatim canonical phrase.
### NFR-09: 驗證真實性(零 skip 鐵律)

- **dimension**:`test_assertion_quality`(對應 `evaluate_dimension.md` §`### test_assertion_quality (Tier 2 — framework tool: ast-assertions)`)
- 對應 `SPEC.md` lines 249-257

**Acceptance criteria**

#### AC-N9.1
**任何 FR / NFR 的驗證測試不得是 `pytest.skip` / `skipif` / `xfail` / 無斷言的 stub**。

#### AC-N9.2
`pytest 03-development/tests -q` 的 **skipped 計數必須為 0**。

#### AC-N9.3
每個測試函式至少一個 `assert`(`zero_assert == 0`)— 對應 SPEC.md line 254。

#### AC-N9.4
**反造假條款**:不得以 `--ignore` / `-k` / `--deselect` / `collect_ignore` / 從 `testpaths` 移除目錄的方式排除測試。

#### AC-N9.5
**本輪特別條款**:`FR-07` 的三步 migration 必須以**真實資料庫**測試(SQLite 檔案,非 in-memory mock),往返可逆性以實際資料比對驗證。**不得**以「migration 邏輯太難測」為由降級為 skip。

#### AC-N9.6
`TRACEABILITY_MATRIX.md` 的 `VERIFIED` 只能在測試實際執行並通過時給出。

**Coverage note**:AC-N9.1、N9.3 由 `evaluate_dimension.md` §`### test_assertion_quality`(`ast-assertions` 計算 `asserted_tests / total_tests`)覆蓋。AC-N9.2 為 `pytest -q` 退出碼與輸出計數檢查;AC-N9.4 為 pytest 命令列參數與 `conftest.py` 內容審查;AC-N9.5、N9.6 為整合測試 + 真實 SQLite 檔案驗證。

> DERIVED: SPEC.md lines 259-264 — AC list is a testable decomposition of the canonical section; each AC carries a verbatim canonical phrase.
### NFR-10: 整合覆蓋

- **dimension**:`integration_coverage`(對應 `evaluate_dimension.md` §`### integration_coverage (Tier 2 — tool-scored: pytest-cov-integration / vitest-jest)`)
- 對應 `SPEC.md` lines 259-264

**Acceptance criteria**

#### AC-N10.1
`03-development/tests/integration/` 行覆蓋 **≥ 80%**(對原始 source tree)— 對應 SPEC.md line 262。

#### AC-N10.2
整合測試以 `httpx.AsyncClient(transport=ASGITransport(app))` 驅動,**不得直接呼叫 handler 函式**。

#### AC-N10.3
至少涵蓋:CRUD 全鏈、401/403/404/409/422/429/503 每個錯誤碼各一例、migration 往返、rate limit 觸發與恢復、graceful drain。

**Coverage note**:AC-N10.1 由 `evaluate_dimension.md` §`### integration_coverage`(`pytest --cov=03-development/src`)覆蓋,scope 為整個 source tree。AC-N10.2、N10.3 為整合測試目錄結構審查。

> DERIVED: SPEC.md lines 266-271 — AC list is a testable decomposition of the canonical section; each AC carries a verbatim canonical phrase.
### NFR-11: 可讀性

- **dimension**:`readability`(對應 `evaluate_dimension.md` §`### readability (Tier 3 — proxy metric: radon-mi / js-mi)`)
- 對應 `SPEC.md` lines 266-271

**Acceptance criteria**

#### AC-N11.1
專案 MI(LLOC 加權)**≥ 80**。

#### AC-N11.2
單一函式 CC **≤ 10**。

#### AC-N11.3
單一檔案 ≤ 400 行。

#### AC-N11.4
單一目錄 ≤ 15 檔。

#### AC-N11.5
每個 API handler ≤ 40 行(業務邏輯必須下沉到 `service/`)— 對應 SPEC.md line 271。

**Coverage note**:AC-N11.1 由 `evaluate_dimension.md` §`### readability`(`radon mi src/`,平均 MI)覆蓋。AC-N11.2 ~ N11.5 為獨立 source-file / function 結構檢查,需於 `make verify-system` 或獨立 lint script 中實作。

> DERIVED: SPEC.md lines 273-281 — AC list is a testable decomposition of the canonical section; each AC carries a verbatim canonical phrase.
### NFR-12: 系統驗證目標

- **dimension**:`execute_verification_target`(對應 `evaluate_dimension.md` §`### execute_verification_target (Tier 1 — Gates 2/3/4, tool-scored: system-verification)`)
- 對應 `SPEC.md` lines 273-281

**Acceptance criteria**

#### AC-N12.1
`Makefile` 的 `verify-system` target 必須串接:
1. `alembic upgrade head`
2. 全套測試
3. 服務啟動 + `/healthz`、`/readyz` 冒煙
4. `alembic downgrade base` 後再 `upgrade head`(往返驗證)
— 對應 SPEC.md lines 276-280。

#### AC-N12.2
`make verify-system` 必須 **exit 0** 並在 stdout 印出 `verify-system: PASS`。

**Coverage note**:AC-N12.2 由 `evaluate_dimension.md` §`### execute_verification_target`(`make verify-system` exit 0 → 100)覆蓋。AC-N12.1 為 `Makefile` 內容審查。

## 5. Acceptance Criteria Summary

| 類別 | 編號 | 簡述 | 對應 SPEC 行 |
|------|------|------|---------------|
| FR-01 | AC-1.1 ~ AC-1.5 | 任務資源 CRUD + cursor 分頁 + 422/404 | 79-92 |
| FR-02 | AC-2.1 ~ AC-2.5 | 任務執行 + async subprocess + 結果寫入 | 93-100 |
| FR-03 | AC-3.1 ~ AC-3.5 | API Key 認證 + SHA-256 + hmac.compare_digest | 101-107 |
| FR-04 | AC-4.1 ~ AC-4.3 | Scope 階層 + 403 + 單一 dependency | 109-113 |
| FR-05 | AC-5.1 ~ AC-5.4 | 令牌桶 + 429 + Retry-After + row-level lock | 115-120 |
| FR-06 | AC-6.1 ~ AC-6.5 | repository 層 + 交易邊界 + 預載 + pool | 122-128 |
| FR-07 | AC-7.1 ~ AC-7.7 | Alembic 三步 + 往返可逆 + 無破壞捷徑 | 130-143 |
| FR-08 | AC-8.1 ~ AC-8.4 | TaskGroup + graceful drain + timeout 終止子進程 | 145-150 |
| FR-09 | AC-9.1 ~ AC-9.4 | /healthz + /readyz fail-closed + /v1/metrics | 152-160 |
| FR-10 | AC-10.1 ~ AC-10.5 | RFC 7807 + correlation_id + 錯誤碼對照 | 162-168 |
| NFR-01 | AC-N1.1 ~ AC-N1.4 | p95 + N+1 防護 + pytest-benchmark | 177-184 |
| NFR-02 | AC-N2.1 ~ AC-N2.7 | shell/eval/exec 0 命中 + SQL 拼接 0 + bandit 0 | 185-194 |
| NFR-03 | AC-N3.1 ~ AC-N3.6 | 交易邊界 + 取消語意 + timeout kill | 196-204 |
| NFR-04 | AC-N4.1 ~ AC-N4.3 | 敏感字串遮蔽 + DB URL 不入日誌 + 明文單次 | 206-212 |
| NFR-05 | AC-N5.1 ~ AC-N5.2 | docstring 100% + OpenAPI summary/description | 214-218 |
| NFR-06 | AC-N6.1 ~ AC-N6.4 | .importlinter + sqlalchemy 禁令 + lint-imports exit 0 | 220-232 |
| NFR-07 | AC-N7.1 ~ AC-N7.4 | 釘版 + allowlist license + 全樹掃描 + SBOM | 234-240 |
| NFR-08 | AC-N8.1 ~ AC-N8.3 | mutation_testing flag + score ≥ 70 + 範圍限定 | 242-247 |
| NFR-09 | AC-N9.1 ~ AC-N9.6 | 0 skip + 0 zero-assert + 反造假 + migration 不 skip | 249-257 |
| NFR-10 | AC-N10.1 ~ AC-N10.3 | integration 行覆蓋 ≥ 80% + httpx ASGITransport | 259-264 |
| NFR-11 | AC-N11.1 ~ AC-N11.5 | MI ≥ 80 + CC ≤ 10 + 檔案 ≤ 400 + handler ≤ 40 | 266-271 |
| NFR-12 | AC-N12.1 ~ AC-N12.2 | verify-system target 串接 + exit 0 + PASS | 273-281 |

## 6. Out-of-Scope

- 本 SRS 不規範部署拓樸(docker-compose / k8s manifest)除 SPEC.md §5.3 必要設定檔以外
- 本 SRS 不規範前端 / SDK 客戶端
- 本 SRS 不規範 multi-region / replication(SPEC.md §2 採用單一 Postgres 設定)
- 本 SRS 不規範 webhook / event subscription(SPEC.md §9 風險矩陣未提及)
- 第 3 輪測床(SPEC.md §0)本輪(第 2 輪)不予納入

## 7. Open Issues

- 截至 SPEC.md v1.0.0,無 `FR-XX-deferred` 或 `NFR-99` 條款。所有 FR(01-10)與 NFR(01-12)皆在 canonical spec 中有明確定義。
- NFR-99 預留作為「規格新增/變更時的歧義裁決項」,本轉錄無觸發。

## 8. Risks

對應 SPEC.md §9(風險矩陣,R1-R12):

| ID | 風險 | 影響 | 可能性 | 緩解(對應 NFR/FR) |
|----|------|------|--------|---------|
| R1 | v3 資料搬遷遺失資料 | 高 | 中 | FR-07 往返可逆性測試(真實 SQLite 逐欄) |
| R2 | SQL injection | 高 | 低 | NFR-02 禁字串拼接 + ORM/參數化 + grep gate |
| R3 | API key 洩漏 | 高 | 中 | FR-03 雜湊儲存 + hmac.compare_digest + 明文單次 |
| R4 | 403 洩漏資源存在性 | 中 | 中 | FR-04 授權判定在資源查詢之前 |
| R5 | N+1 查詢大表崩潰 | 高 | 高 | NFR-01 顯式預載 + SQL 計數斷言 |
| R6 | 錯誤 body 洩漏內部結構 | 中 | 高 | FR-10 RFC 7807 固定欄位 + detail 白名單 |
| R7 | `CancelledError` 被吞 → 關閉卡死 | 中 | 中 | NFR-03 明文禁令 + 測試斷言 |
| R8 | 任務 timeout 留下孤兒進程 | 中 | 中 | FR-08 kill() + await wait() |
| R9 | 部署後忘記跑 migration | 高 | 中 | FR-09 /readyz fail closed |
| R10 | 連線池耗盡 | 中 | 中 | FR-06/08 pool_pre_ping + 併發上限 |
| R11 | transitive 依賴引入不相容 license | 中 | 中 | NFR-07 lock 檔 + 全樹掃描 |
| R12 | rate bucket 競態導致超放行 | 低 | 中 | FR-05 單一交易 + row-level lock |

## 9. Glossary

| 術語 | 定義 |
|------|------|
| taskq-api | 本規格定義的 ASGI HTTP 服務,Python 3.11 |
| SPEC.md | 規格單一真實來源(本 SRS 之 super-source) |
| FR | Functional Requirement — §3 中 `### FR-XX` 各節 |
| NFR | Non-Functional Requirement — §4 中 `### NFR-XX` 各節 |
| AC | Acceptance Criterion — 對應 `AC-<n>.<m>` / `AC-N<n>.<m>` 編號 |
| scope | API key 的授權層級:`read` < `write` < `admin`(階層包含) |
| 令牌桶 | token bucket;rate-limit 演算法,以容量 + 補充速率控制流量 |
| cursor-based 分頁 | 以游標為基準的分頁;相對於 offset 分頁可避免大表掃描 |
| problem+json | RFC 7807 定義的 `application/problem+json` 錯誤回應格式 |
| Alembic | Python SQLAlchemy 生態的 schema migration 工具 |
| N+1 查詢 | 對 N 筆主記錄的關聯查詢若未預載,將額外觸發 N 次查詢 — 為本輪禁止反模式 |
| graceful drain | 服務關閉時等待進行中任務完成的程序 |
| mutation score | 變異測試中被測試殺死的變異體比例 |
| SBOM | Software Bill of Materials;本輪產出於 `08-config/SBOM.json` |
| p95 | 第 95 百分位延遲(以 pytest-benchmark 量測) |
| CRG | Code Review Graph;framework 結構分析工具 |
| DERIVED | 標籤:表示該 AC 為 canonical spec 之外的詮釋選擇 |

---

## FR Block (machine-readable)

```json
{
  "version": "1.0",
  "created_at": "2026-09-02",
  "phase": 1,
  "project": "taskq-api",
  "functional_requirements": [
    {
      "id": "FR-01",
      "description": "Task resource CRUD API — POST/GET/DELETE /v1/tasks endpoints with cursor-based pagination, validation, and 422/404 problem+json responses.",
      "implementation_functions": ["taskq_api.api.tasks", "taskq_api.service.tasks", "taskq_api.repository.task_repo"],
      "verification_method": "integration test: test_task_crud_returns_201_422_404; test_tasks_list_cursor_pagination; test_delete_removes_results"
    },
    {
      "id": "FR-02",
      "description": "Task execution endpoint — POST /v1/tasks/{id}/run (202 + run_id); asyncio subprocess with no shell=True; status machine; result rows; GET /v1/tasks/{id}/runs.",
      "implementation_functions": ["taskq_api.api.tasks", "taskq_api.service.runner", "taskq_api.repository.task_repo"],
      "verification_method": "integration test: test_task_run_returns_202_with_run_id; test_subprocess_no_shell_true; test_run_history_newest_first"
    },
    {
      "id": "FR-03",
      "description": "API Key authentication — X-API-Key required on /v1/*; SHA-256 hashed storage; hmac.compare_digest; plaintext emitted once at key create.",
      "implementation_functions": ["taskq_api.api.deps", "taskq_api.service.auth", "taskq_api.repository.key_repo", "taskq_api.__main__"],
      "verification_method": "integration test: test_missing_api_key_returns_401; test_invalid_api_key_returns_401; test_api_keys_table_has_no_plaintext"
    },
    {
      "id": "FR-04",
      "description": "Scope authorization — read < write < admin hierarchy; single dependency for authz check; 403 must not leak resource existence.",
      "implementation_functions": ["taskq_api.api.deps", "taskq_api.service.auth"],
      "verification_method": "integration test: test_write_key_admin_endpoint_returns_403_no_disclosure; test_all_v1_routes_use_single_dependency"
    },
    {
      "id": "FR-05",
      "description": "Rate control — per-token token bucket persisted in DB with row-level lock; 429 + Retry-After; /healthz, /readyz exempt.",
      "implementation_functions": ["taskq_api.api.deps", "taskq_api.service.ratelimit", "taskq_api.repository.rate_repo"],
      "verification_method": "integration test: test_rate_limit_burst_returns_429_with_retry_after; test_rate_bucket_concurrent_no_overdraft"
    },
    {
      "id": "FR-06",
      "description": "Persistence layer and transaction boundaries — repository layer only; per-request Session with context manager; no string SQL; eager loading.",
      "implementation_functions": ["taskq_api.repository.session", "taskq_api.repository.task_repo", "taskq_api.repository.key_repo", "taskq_api.repository.rate_repo"],
      "verification_method": "integration test: test_session_rollback_on_exception; test_no_string_sql_concat; test_eager_loading_no_n_plus_one"
    },
    {
      "id": "FR-07",
      "description": "Alembic schema migration — three revisions (v1 tasks/api_keys, v2 tags + unique name index, v3 split result_json to task_results); each step reversible; data preservation across round-trip.",
      "implementation_functions": ["migrations.versions.v1_initial", "migrations.versions.v2_tags", "migrations.versions.v3_split_results"],
      "verification_method": "integration test: test_alembic_upgrade_downgrade_base; test_v3_data_migration_round_trip_preserves_columns"
    },
    {
      "id": "FR-08",
      "description": "Async executor — asyncio.TaskGroup; graceful drain on shutdown; TASKQ_MAX_CONCURRENT cap; asyncio.wait_for timeout that kills subprocess.",
      "implementation_functions": ["taskq_api.service.runner"],
      "verification_method": "integration test: test_graceful_drain_waits_running; test_task_timeout_kills_orphan_subprocess; test_cancelled_error_propagates"
    },
    {
      "id": "FR-09",
      "description": "Health checks and observability — /healthz, /readyz (DB + alembic current == head), /v1/metrics (admin scope).",
      "implementation_functions": ["taskq_api.api.health"],
      "verification_method": "integration test: test_healthz_returns_200; test_readyz_returns_503_when_migration_not_at_head; test_metrics_requires_admin_scope"
    },
    {
      "id": "FR-10",
      "description": "RFC 7807 error contract — application/problem+json for non-2xx; fixed fields (type/title/status/detail/instance/correlation_id); no internals in detail; correlation_id in X-Correlation-Id header.",
      "implementation_functions": ["taskq_api.errors", "taskq_api.api.deps"],
      "verification_method": "integration test: test_422_404_429_all_problem_json; test_500_detail_has_no_stack_trace; test_correlation_id_in_header_and_log"
    }
  ],
  "non_functional_requirements": [
    {
      "id": "NFR-01",
      "type": "performance",
      "description": "GET /v1/tasks/{id} p95 < 30ms on 10k rows; GET /v1/tasks?limit=50 p95 < 80ms; N+1 failure condition measured by SQLAlchemy event listener.",
      "test_method": "pytest-benchmark; SQLAlchemy event listener statement count assertion (test_n_plus_one_guard)"
    },
    {
      "id": "NFR-02",
      "type": "security",
      "description": "No shell=True / eval( / exec( in source; no string-concatenated SQL; SHA-256 API key hashing + hmac.compare_digest; 403 no resource-exists leak; bandit 0 HIGH/MEDIUM; CORS default-deny.",
      "test_method": "bandit -r src/ (0 HIGH/MEDIUM); grep CI gate; test_no_string_sql_concat"
    },
    {
      "id": "NFR-03",
      "type": "reliability",
      "description": "Transaction boundary per request (context manager); no bare except / except Exception pass; asyncio.CancelledError not swallowed; DB failure -> /readyz 503; timeout kills subprocess; migration rollback on failure.",
      "test_method": "ast-error-handling framework scan; integration test: test_cancelled_error_propagates; test_db_failure_readyz_503"
    },
    {
      "id": "NFR-04",
      "type": "security",
      "description": "Redact stdout_tail / stderr_tail / logs / error body for sk-*, token=, Bearer, postgres URL regex; DB URL must not appear in logs or /v1/metrics; API key plaintext emitted once.",
      "test_method": "test_secret_redaction_regex; test_db_url_not_in_logs; test_metrics_no_password"
    },
    {
      "id": "NFR-05",
      "type": "documentation",
      "description": "Public functions/classes have docstrings with [FR-XX] or [NFR-XX] reference; OpenAPI schema has summary and description for every endpoint.",
      "test_method": "ast-docstrings framework scan (100%); integration test: test_openapi_summary_description_present"
    },
    {
      "id": "NFR-06",
      "type": "layering",
      "description": ".importlinter layers contract api > service > repository > models; config/errors as independence; repository is the only layer allowed to import sqlalchemy; lint-imports exits 0; no degraded config (no ignore_imports wildcard, no removed contract).",
      "test_method": "lint-imports exit code 0; forbidden contract test asserts no sqlalchemy import outside repository"
    },
    {
      "id": "NFR-07",
      "type": "licensing",
      "description": "Runtime deps pinned via == in requirements.txt; transitive deps locked in requirements.lock; license allowlist MIT/BSD-2/BSD-3/Apache-2.0/PSF; full tree scan; SBOM artifact at 08-config/SBOM.json.",
      "test_method": "pip-licenses --format=json --with-system allowlist check; SBOM file existence and field shape validation"
    },
    {
      "id": "NFR-08",
      "type": "mutation",
      "description": "features.mutation_testing: true in .methodology/harness_config.json; mutation score >= 70; scope limited to service/ and repository/ layers with rationale.",
      "test_method": "framework command mutation-test-score; .methodology/mutation_score.json presence"
    },
    {
      "id": "NFR-09",
      "type": "testability",
      "description": "Zero skip / skipif / xfail / zero-assert; pytest -q skipped == 0; anti-fake clause (no --ignore/-k/--deselect/collect_ignore); FR-07 migration tested against actual DB (SQLite file, not mock); VERIFIED status only when test actually passed.",
      "test_method": "ast-assertions framework scan; pytest -q skipped count == 0; pytest --collect-only enumeration check"
    },
    {
      "id": "NFR-10",
      "type": "integration",
      "description": "Integration suite line coverage >= 80%; httpx.AsyncClient(transport=ASGITransport(app)); covers CRUD + each error code + migration round-trip + rate limit + graceful drain.",
      "test_method": "pytest tests/integration --cov=03-development/src (TOTAL >= 80%); structural test for httpx ASGI usage"
    },
    {
      "id": "NFR-11",
      "type": "maintainability",
      "description": "Project MI (LLOC-weighted) >= 80; function CC <= 10; file <= 400 lines; directory <= 15 files; API handler <= 40 lines.",
      "test_method": "radon mi src/ average; radon cc per function; file/directory size lint"
    },
    {
      "id": "NFR-12",
      "type": "deployability",
      "description": "make verify-system target chains alembic upgrade head, full test suite, /healthz + /readyz smoke, downgrade base + upgrade head; exit 0; stdout contains verify-system: PASS.",
      "test_method": "make verify-system exit 0 + stdout grep 'verify-system: PASS'"
    }
  ]
}
```
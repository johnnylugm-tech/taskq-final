# 漏洞掃描報告 — 2026-09-03

老闆,Gate 3 adversarial_review 維度的掃描結果如下。原始 finding 7 條、確認 4 條、反駁 3 條。

## 1. 掃描摘要

| 模組 | severity | 狀態 |
|---|---|---|
| `taskq_api.app` | high (T-13 repudiation) | confirmed,待修 |
| `migrations.versions.v3_split_results` | high (T-15 tampering) | confirmed,待修 |
| `taskq_api.service.runner` | high (T-07 DoS) | confirmed,待修 |
| `taskq_api.errors` (via `app.py`) | high (T-10 info disclosure) | confirmed,待修 |
| `taskq_api.app` (CORS) | low | refuted |
| `taskq_api.repository.task_repo` (string SQL) | low | refuted |
| `taskq_api.app` (X-Correlation-Id cap) | low | refuted (framework bound) |

## 2. 確認的 Bugs(severity 降序)

### BUG #1 — `taskq_api.app` T-13 repudiation
- 位置:`03-development/src/taskq_api/app.py:98-101`
- 問題:`_CorrelationIdMiddleware` 的 audit log 只記 correlation_id/method/path,沒有 principal.key_id。Admin DELETE、GET /v1/metrics 這類需要問責的動作,從日誌無法還原「誰」觸發。
- 證據:`_audit_logger.info("request correlation_id=%s method=%s path=%s", correlation_id, request.method, request.url.path)` — 沒有 key_id 欄位;`require_scope`(api/deps.py:77)解析的 Principal 沒有寫回 `request.state`。
- 修復:在 `require_scope` 把 Principal 存到 `request.state.principal`,並在 `_CorrelationIdMiddleware.dispatch` 於 `call_next` 之後再發一條 audit log,帶 principal 資訊。

### BUG #2 — `migrations.versions.v3_split_results` T-15 tampering
- 位置:`03-development/src/migrations/versions/v3_split_results.py:82-92, 167, 178`
- 問題:`_now_or_default` 在 `finished_at` 缺失或解析失敗時,默默寫入 `datetime.now()`。v1 的 `result_json` 是自由格式,許多實際 row 沒有 `finished_at`;升級後這些 row 的時間戳被替換為升級當下的時間,原始時間序無法恢復。
- 證據:行 167、178 都呼叫 `_now_or_default(entry.get("finished_at"))`,而 `_now_or_default` 行 84-91 的 None / ValueError 分支都回傳 `now()`。
- 修復:用 parent task 的 `created_at` 作為 fallback(同一 SELECT 讀出),或 skip `finished_at` 讓 `server_default` 補上,或直接 raise 由人工處理。不要默默替換。

### BUG #3 — `taskq_api.service.runner` T-07 DoS
- 位置:`03-development/src/taskq_api/service/runner.py:373-379`
- 問題:`_communicate_with_timeout` 只 catch `asyncio.TimeoutError`。當外層 `asyncio.wait_for`(line 334 `run_with_timeout`)或 `drain()`(line 295 `t.cancel()`)丟出 `CancelledError`(Python 3.8+ 是 BaseException 子類),會繞過這個 try/except,`proc.kill()` 不被呼叫,subprocess 變孤兒。
- 證據:行 374 `await asyncio.wait_for(proc.communicate(), timeout=limit)` 被外層 wait_for 取消時,CancelledError 沿著 await chain 傳播;行 376-379 只處理 TimeoutError。
- 修復:把 try/except 擴展到 `asyncio.CancelledError` (或 BaseException),並在 finally 區塊 `proc.kill()` + `await proc.wait()`。

### BUG #4 — `taskq_api.errors` (via `app.py`) T-10 info disclosure
- 位置:`03-development/src/taskq_api/app.py:155-163`
- 問題:`_handle_validation` 直接把 `str(exc.errors())` 塞進 `ValidationProblem.detail`。Pydantic v2 的 errors() 預設帶 `input` 鍵,把被拒的原始值原樣回傳。攻擊者在 `command` 欄位塞 bearer token / API key 會被 422 body 回顯出來;若下游 observability 收 4xx body 則 secret 洩漏。
- 證據:行 159 `problem = ValidationProblem(detail=str(exc.errors()))`;`ValidationProblem.__init__`(errors.py:56-62)直接收 detail;`redact_secrets` 沒有套用到這條路徑。
- 修復:從每個 error dict 移除 `input` 與 `ctx`,只保留 `type/loc/msg`,再 stringify。

## 3. 被反駁的 Findings

- T-04 CORS:無 CORSMiddleware,FastAPI 預設不發 Access-Control-Allow-Origin → 瀏覽器直接擋掉跨來源讀取,不可利用。
- T-08 string SQL:所有 query 都走 SQLAlchemy ORM `.where(Task.col == value)` 綁定參數;cursor decode 包在 try/except 中,沒有任何字串拼接進 SQL。
- T-03 X-Correlation-Id DoS:Starlette/uvicorn 預設 header parser 限制約 64KB,框架層就擋掉放大攻擊。

## 4. 修復優先順序

1. BUG #1(T-13)— audit log 缺 principal:寫 repro test 驗證 DELETE /v1/tasks/{id} 的 audit log 不含 key_id;在 `require_scope` 把 Principal 寫到 `request.state`;middleware 在 `call_next` 之後補發一條 audit log。
2. BUG #2(T-15)— migration 替換 finished_at:寫 repro test 餵一個沒有 finished_at 的 result_json,驗證升級後的 task_results.finished_at 不等於「現在」也不等於「原 created_at」(應該報錯或 fallback 到 created_at);改 `_now_or_default` 改用 parent.created_at,或在 None 時 raise。
3. BUG #3(T-07)— CancelledError 漏殺 subprocess:寫 repro test 用 outer wait_for/cancel 包住一個 spawn sleep 的 coroutine,驗證 proc 還活著;`_communicate_with_timeout` 加上 CancelledError 分支 + finally kill。
4. BUG #4(T-10)— Pydantic input 回顯:寫 repro test POST 一個超長 command 帶 secret,驗證 422 body 沒有原始值;改 `_handle_validation` 把 input/ctx 欄位過濾掉。

## 5. 掃描方法

- CRG 圖:已建(`.methodology/crg_baseline_p3.json` 為 P3 baseline)。
- Targets 來源:`.methodology/bug_hunt_targets.json`(15 high-risk + 21 standard,15 條 SAD §6 宣告的威脅模型)。
- Lens 配對:high-risk × 3(correctness/concurrency/resilience),standard × 1(general)。
- 驗證規則:每個 finding 經 refuter + confirmer 雙重查證;2/2 is_real 或 1/2 帶具體行號才確認,否則反駁並附行號引用。
- 不可造假:所有 file:line 引用都已 Read 確認存在;verbatim snippet 不超過 8 行;無靜態分析已擋的項目(static preflight 涵蓋:subprocess timeout、TOCTOU、except BaseException、config 死鍵)。

---

老闆,4 條 confirmed critical/high 還沒 resolved。Gate 3 的 adversarial_review 不會放行直到每條都有 `fix_commit`(commit SHA)或 `repro_test`(專案內真實存在的測試檔路徑)。Resolution 階段會依優先序逐條寫 repro test + 修補 + commit(`fix(<module>): <title>`)。

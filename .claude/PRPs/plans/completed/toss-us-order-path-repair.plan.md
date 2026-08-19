# Plan: Toss US 주문 경로 복구 + 리뷰 2차 발견 정리 (Phase 2.5)

## Summary
PR #9 2차 리뷰가 확인한 10건을 수정한다. 핵심은 **Toss US 주문 경로가 어떤 경로로도 완결되지 않는다**는 것: 인자 이름 불일치로 브로커에 닿기 전 TypeError가 나고, 설령 닿아도 체결 응답의 Decimal이 sqlite 바인딩에서 죽어 성공을 UNKNOWN으로 기록한다. 두 결함 모두 **DB 행을 먼저 지운 뒤** 발생하므로 포지션이 추적 불가 상태로 남는다.

## User Story
As a Toss 실계좌 운영자, I want US 매도 주문이 실제로 브로커에 도달하고 결과가 정확히 기록되기를, so that DB와 브로커 잔고가 어긋나 포지션을 잃지 않는다.

## Problem → Solution
"행 삭제 → 주문 시도 → 실패해도 로그만" → "주문 가능성 선검증 → 행 삭제 → 주문 → 실패 시 누적 롤백".

## Metadata
- **Complexity**: Large
- **Source PRD**: `.claude/PRPs/prds/full-migration-audit.prd.md`
- **PRD Phase**: Phase 2.5 (리뷰 2차 대응 — Phase 3 앞에 삽입)
- **Estimated Files**: ~10

---

## UX Design
N/A — internal. 단 **동작 변화 있음**: Toss US 매도가 처음으로 실제 실행된다.

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `prism-us/us_stock_tracking_agent.py` | 2840-3030 | 프로브·plan 결정·행 삭제·주문·누적의 전 구간 |
| P0 | `prism_core/order_intents.py` | 20-40, 280-320, 425-495 | quantity 필드·reserve INSERT·record_result INSERT |
| P0 | `trading/brokers/toss/adapter.py` | 118-160, 255-300, 550-590, 760-830 | async_* 시그니처·_sell 가드·세션 게이트·_settled_quantity |
| P1 | `prism_core/execution_service.py` | 293-410 | kwargs 전달·실패 시 UNKNOWN 변환 |
| P1 | `prism-us/trading/us_stock_trading.py` | 1498-1501 | KIS US는 `ticker`, Toss는 `stock_code` |
| P1 | `prism-us/tracking/db_schema.py` | 20-45 | 정리 누락된 로더 2개 |
| P2 | `utils/backfill_trigger_type.py` | 30-45 | CWD 우선 해석 잔존 |

## External Documentation
없음 — 내부 패턴만.

---

## Patterns to Mirror

### PORT_CONTRACT_POSITIONAL (핵심 근거)
```python
# SOURCE: trading/brokers/base.py:111-116
# ── Orders ───────────────────────────────────────────────────────────────
# First argument is the instrument and is passed positionally: KIS names it
# `stock_code` domestically and `ticker` for US, and US additionally takes
# `exchange`. Remaining arguments stay open so an adapter can absorb its
# own broker's spelling instead of forcing one vocabulary on both.
```
포트가 **명시적으로 위치 인자**를 요구한다. KR 호출부는 우연히 `stock_code=`로 철자가 맞아 통과했고, US 호출부의 `ticker=`가 Toss에서 깨진다. → 호출부를 위치 인자로 고치는 것이 계약 준수이며 어댑터에 별칭을 추가하는 것(계약 위반을 은폐)보다 옳다.

### TEXT_NORMALIZER (이미 있는, quantity만 빠뜨린 헬퍼)
```python
# SOURCE: prism_core/order_intents.py — record_result INSERT
_text(broker_order_id),
int(accepted),
status,
quantity,          # ← 이 줄만 raw
_text(price),      # ← 바로 옆은 정규화됨
```

### DECIMAL_NORMALIZATION (직전 라운드에서 요청 측에 적용한 규칙)
```python
# SOURCE: prism_core/order_intents.py OrderIntent.create
if isinstance(quantity, Decimal):
    quantity = (
        int(quantity)
        if quantity == quantity.to_integral_value()
        else str(quantity)
    )
```

### BROKER_GATE_OUTSIDE_TRY
```python
# SOURCE: prism-us/us_stock_tracking_agent.py:908 (같은 모듈이 이미 이렇게 한다)
settings = _load_root_broker_settings()
if settings.selected_broker() == settings.TOSS:
```

### LOADER_CLEANUP (직전 라운드에서 2곳에만 적용)
```python
# SOURCE: prism-us/us_stock_tracking_agent.py _kis_auth
sys.modules[module_name] = module
try:
    spec.loader.exec_module(module)
except BaseException:
    sys.modules.pop(module_name, None)
    raise
```

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `prism-us/us_stock_tracking_agent.py` | UPDATE | 위치 인자·프로브 게이트 이동·선검증·누적 롤백 |
| `prism_core/order_intents.py` | UPDATE | record_result 정규화 + quantity 컬럼 affinity |
| `prism-us/tracking/db_schema.py` | UPDATE | 로더 2개 exec 실패 정리 |
| `utils/backfill_trigger_type.py` | UPDATE | DB 경로 해석 통일 |
| `utils/migrate_watchlist_to_performance_tracker.py` | UPDATE | 동일 |
| `utils/migrate_lessons_to_principles.py` | UPDATE | 동일 |
| `prism-us/tests/conftest.py` | UPDATE | 체인 리셋 no-op 수정 |
| `cores/llm/features/trade_history.py` | UPDATE | 낡은 docstring(CWD 폴백 언급) 정정 |
| `tests/test_toss_us_order_path.py` | CREATE | 계약·Decimal 왕복·롤백 회귀 |
| `tests/test_migration_audit_scans.py` | UPDATE | 인자 철자 트립와이어 추가 |

## NOT Building
- 주문-먼저-삭제-나중으로의 전면 재설계 — 대규모 리팩터라 별도 과제. 여기서는 **선검증 + 롤백**으로 손실 창을 닫는다
- KIS US에 `get_holding_quantity_checked` 추가 (Phase 5)
- DB 경로 해석 공용 헬퍼 추출 (Phase 6 드리프트 청소) — 여기서는 누락된 3개 파일만 기존 체인에 합류
- `_kis_auth`/`_load_root_broker_settings` 중복 본문 통합 (Phase 5)

---

## Step-by-Step Tasks

### Task 1: US 주문 호출부를 위치 인자로 (Finding 2 — 최우선)
- **ACTION**: `prism-us/us_stock_tracking_agent.py:2993`, `:3537`의 `ticker=ticker` → 위치 인자
- **IMPLEMENT**:
  ```python
  trade_result = await trading.execute_sell(
      ticker,                      # positional: KIS spells it `ticker`, Toss `stock_code`
      limit_price=current_price,
      quantity=sell_quantity,
      intent=order_intent,
  )
  ```
  매수(`:3537`)도 동일. 나머지 kwargs는 양 어댑터 모두 동일 철자이므로 유지.
- **MIRROR**: PORT_CONTRACT_POSITIONAL
- **GOTCHA**: KIS US는 2번째 위치 인자가 `exchange`다. **반드시 첫 인자만 위치로** 두고 나머지는 keyword 유지 — `execute_sell(ticker, current_price)`처럼 쓰면 KIS에서 limit_price가 exchange로 들어간다. KR 호출부(`stock_code=`)는 양쪽 철자가 같아 지금도 동작하므로 **건드리지 않는다**(불필요한 위험).
- **VALIDATE**: `TossBroker.async_sell_stock('AAPL', limit_price=..., quantity=...)`가 TypeError 없이 `_sell`에 도달

### Task 2: 인자 철자 트립와이어 (Task 1 재발 방지)
- **ACTION**: `tests/test_migration_audit_scans.py`에 스캔 추가
- **IMPLEMENT**: `execute_buy(`/`execute_sell(` 호출 다음 줄이 `ticker=`/`stock_code=`/`symbol=`로 시작하면 위반. 단 KR 호출부 3곳은 현재 `stock_code=`로 동작 중이므로 `KNOWN_KEYWORD_INSTRUMENT` 동결 목록에 넣고 "Phase 6에서 위치 인자로 통일" 주석. 신규 추가만 차단.
  추가로 계약 테스트: `TossBroker`와 `KisBroker`의 `async_buy_stock`/`async_sell_stock` 첫 파라미터가 positional-or-keyword이며 **위치로 호출 가능**한지 `inspect.signature`로 확인.
- **MIRROR**: 기존 RATCHET(stale 검사 포함)
- **VALIDATE**: 현재 코드에서 green, `ticker=`를 새로 넣으면 red

### Task 3: record_result의 quantity 정규화 (Finding 1)
- **ACTION**: `prism_core/order_intents.py` record_result INSERT
- **IMPLEMENT**: `quantity,` → `_normalize_quantity(quantity),`. 헬퍼를 모듈 레벨에 신설하고 `OrderIntent.create`도 이를 재사용(직전 라운드에 인라인으로 넣은 로직을 옮김 — 한 곳에서만 정의):
  ```python
  def _normalize_quantity(value: Any) -> int | str | None:
      """Whole shares stay int; fractional (Toss US) keeps its exact decimal
      string. sqlite3 cannot bind Decimal and json.dumps cannot serialize it,
      and float would corrupt 0.788569."""
      if value is None or isinstance(value, (int, str)):
          return value
      if isinstance(value, Decimal):
          return int(value) if value == value.to_integral_value() else str(value)
      return _text(value)
  ```
- **GOTCHA**: `raw_response_json`(`_json(payload)`)도 Decimal을 담을 수 있다 — `_json`이 Decimal에 대해 `TypeError`를 내는지 확인하고, 낸다면 `default=str`을 추가한다. 이 경로도 **행 삭제 이후**라 같은 손실을 만든다.
- **VALIDATE**: `record_result`에 Decimal 수량 outcome을 넣어 예외 없이 저장되는지

### Task 4: quantity 컬럼 affinity (Finding 6)
- **ACTION**: `order_intents`/`broker_orders`의 `quantity`·`submitted_quantity` 선언을 `TEXT`로
- **IMPLEMENT**: 스키마 DDL 변경 + 기존 DB용 마이그레이션. SQLite는 컬럼 타입 변경이 불가하므로 `ensure_schema`에 테이블 재생성 마이그레이션(신규 테이블 생성 → INSERT SELECT → DROP → RENAME)을 추가하되, **`PRAGMA table_info`로 현재 선언 타입이 INTEGER일 때만** 수행.
- **GOTCHA**: 재생성은 인덱스·UNIQUE 제약을 함께 복원해야 한다 — 기존 DDL을 그대로 재사용할 것. 소비자는 `int | str` 양쪽을 이미 받으므로 읽기 측 변경 불필요.
- **DECISION 근거**: 대안(“소수 수량은 컬럼에 NULL, 정확값은 raw_request_json에만”)은 마이그레이션이 필요 없지만 대사(reconciliation)가 수량을 못 읽는다. 정확값 보존이 낫다.
- **VALIDATE**: 마이그레이션 전/후 DB 모두에서 `'0.84'` 저장 후 `typeof()=='text'`, 값 문자열 그대로

### Task 5: 프로브 브로커 판정을 try 밖으로 (Finding 4)
- **ACTION**: `prism-us/us_stock_tracking_agent.py:2844` 인근
- **IMPLEMENT**: `ExecutionService.us(...)` 진입 **이전**에 브로커를 판정:
  ```python
  will_queue = False
  if remaining_rows > 1 and current_price > 0:
      _settings = _load_root_broker_settings()
      if _settings.selected_broker() == _settings.TOSS:
          # Toss has no order queue at all, so a partial sell must never be
          # escalated to a full exit — not even when probing fails.
          will_queue = False
      else:
          try:
              async with ExecutionService.us(...) as _probe:
                  will_queue = await asyncio.to_thread(...)
          except Exception as probe_err:
              logger.warning(...)
              will_queue = True
  ```
- **MIRROR**: BROKER_GATE_OUTSIDE_TRY
- **GOTCHA**: Toss 분기에서 `ExecutionService.us`를 아예 열지 않으므로 리뷰가 지적한 "상수 하나 읽으려 Toss 클라이언트를 통째로 만드는" 비용도 함께 사라진다.
- **VALIDATE**: `selected_broker`가 raise하도록 몽키패치해도 Toss 경로에서 will_queue가 True로 튀지 않음

### Task 6: 매도 가능성 선검증 (Findings 3, 9 — 손실 창 폐쇄)
- **ACTION**: 행 삭제(`sell_stock`) **이전**에 게이트 추가
- **IMPLEMENT**: `sell_quantity` 확정 직후·`sell_stock` 호출 직전에:
  ```python
  # Toss refuses off-session and sub-share orders outright (no queue), and the
  # holdings row is deleted before the order goes out. Verify the order can be
  # placed at all before destroying the only record of the position.
  if _is_toss:
      if sell_quantity is not None and Decimal(str(sell_quantity)) <= 0:
          logger.error(f"{ticker} computed sell quantity is 0; skipping this row (position kept)")
          continue
      if not trading.is_market_open():
          logger.error(f"{ticker} no Toss US session open; skipping sell (position kept)")
          continue
  ```
  `continue`는 해당 행을 이번 패스에서 건너뛴다 — 행도 P&L도 건드리지 않으므로 다음 패스에서 정상 재시도된다.
- **GOTCHA**: ① `trading`은 이미 열린 컨텍스트의 트레이더여야 한다(새로 열지 말 것 — Task 5에서 프로브를 없앤 이유와 동일). ② `is_market_open()`은 동기 HTTP이므로 `asyncio.to_thread`로 감쌀 것. ③ 소수 창(fractional window)은 세션보다 좁다 — `sell_quantity`가 소수면 `fractional_window_open()`도 함께 확인.
- **VALIDATE**: 세션 닫힘 mock에서 행이 남아 있고 P&L 미기록

### Task 7: pass_sold_qty 롤백 (Finding 5)
- **ACTION**: `prism-us/us_stock_tracking_agent.py` 주문 결과 처리부
- **IMPLEMENT**: 누적을 **주문 성공 이후로 이동**하는 것이 정석이나, 중간에 `intent` 생성 등이 끼어 있으므로 최소 변경으로는 실패 시 되돌린다:
  ```python
  if ticker in pass_sold_qty and not trade_result.get("success"):
      # A refused order (closed session, shut fractional window) never left the
      # broker, so it must not count against the snapshot the final row sweeps.
      pass_sold_qty[ticker] -= sell_quantity
  ```
  `OrderOutcomeUnknown` 경로(체결 여부 불명)에서는 **되돌리지 않는다** — 팔렸을 수 있으므로 과소 매도가 과다 매도보다 안전하다. 이 비대칭을 주석으로 명시.
- **GOTCHA**: `sell_quantity`가 None(full_exit)인 경우 누적 자체를 하지 않으므로 가드 필요.
- **VALIDATE**: 1행 거부 → 최종 행의 available이 스냅샷 전량과 같음

### Task 8: db_schema 로더 정리 (Finding 8)
- **ACTION**: `prism-us/tracking/db_schema.py`의 `_load_root_kis_auth_module`·`_load_root_broker_settings`
- **IMPLEMENT**: LOADER_CLEANUP 패턴을 그대로 적용(try/except BaseException → `sys.modules.pop` → raise)
- **VALIDATE**: kis_devlp.yaml 없는 상태에서 두 번 호출 시 두 번 다 FileNotFoundError(AttributeError 아님)

### Task 9: utils 마이그레이션 3종 경로 통일 (Finding 7)
- **ACTION**: `utils/backfill_trigger_type.py:37`, `utils/migrate_watchlist_to_performance_tracker.py:110`, `utils/migrate_lessons_to_principles.py:275`
- **IMPLEMENT**: 기존 체인과 동일하게 `os.getenv("STOCK_TRACKING_DB") or <repo 루트 앵커>`. CWD 우선 탐색(`if db_path.exists()`)을 제거한다 — 고아 DB를 찾아 마이그레이션하고 성공을 보고하는 것이 문제였다.
- **GOTCHA**: 각 파일의 `Path(__file__).parents[n]` 깊이를 따로 계산할 것(utils/는 `parents[1]`).
- **VALIDATE**: 각 스크립트를 임의 디렉토리에서 `--help`로 실행해 기본 경로가 repo 루트로 출력

### Task 10: conftest 체인 리셋 복구 + docstring 정정 (Finding 10, cleanup)
- **ACTION**: `prism-us/tests/conftest.py`의 `_reset_market_data_chain`, `cores/llm/features/trade_history.py`의 `_db_path` docstring
- **IMPLEMENT**: conftest는 루트 `cores/market_data`를 **경로로** 로드하거나(모듈이 prism-us/cores에 가려짐), 최소한 `except Exception` 대신 `except ModuleNotFoundError`로 좁혀 **왜 no-op인지 주석에 남긴다**. 후자를 택하되 주석에 "prism-us/cores가 루트 cores를 가려 현재는 도달 불가 — Phase 5의 섀도잉 정리 후 활성화" 명시.
  `trade_history._db_path` docstring의 "Falls back to the CWD-relative name" 문장을 실제 동작(env → repo 루트)으로 교체.
- **VALIDATE**: US 스위트 신규 실패 0

---

## Testing Strategy

### Unit Tests (`tests/test_toss_us_order_path.py` 신설)
| Test | Input | Expected |
|---|---|---|
| 위치 인자 계약 | TossBroker/KisBroker 시그니처 | 첫 인자 위치 호출 가능 |
| Toss US 매도 도달 | `async_sell_stock('AAPL', limit_price=1, quantity=1)` | TypeError 없음 |
| Decimal 왕복 | `record_result`에 Decimal 수량 | 예외 없음, 저장값 `'0.84'` |
| 컬럼 affinity | 마이그레이션 후 저장 | `typeof=='text'` |
| 롤백 | 1행 거부 후 최종 행 | available == 스냅샷 전량 |
| 선검증 | 세션 닫힘 | 행 유지, P&L 미기록 |

### Edge Cases Checklist
- [ ] `OrderOutcomeUnknown`에서는 롤백하지 않음(과소 매도 우선)
- [ ] full_exit(quantity=None) 경로에서 롤백 가드
- [ ] 기존 DB 마이그레이션 후 인덱스·UNIQUE 보존
- [ ] KR 경로 무변화(호출부 미수정)

## Validation Commands
```bash
.venv/bin/python -m py_compile prism-us/us_stock_tracking_agent.py prism_core/order_intents.py prism-us/tracking/db_schema.py
.venv/bin/python -m pytest tests/test_toss_us_order_path.py tests/test_p0_money_path.py tests/test_migration_audit_scans.py -q
.venv/bin/python -m pytest tests/test_broker_selection.py tests/test_broker_contract.py tests/test_toss_adapter.py tests/test_toss_us_adapter.py tests/test_toss_dryrun.py tests/test_execution_service.py tests/test_kis_adapter.py -q
cd prism-us && ../.venv/bin/python -m pytest tests/ -q --ignore=tests/test_us_screening_bullish_candle.py --continue-on-collection-errors  # 신규 실패 0 (목록 diff)
```

### Manual Validation (배포 전 필수)
- [ ] **운영 DB의 us_stock_holdings와 Toss 실제 잔고 대조** — 지금까지 US 매도가 실패해 왔다면 이미 어긋나 있을 수 있다
- [ ] demo(dry-run)에서 Toss US 소수 매도 1건이 끝까지 완주하는지
- [ ] order_intents 마이그레이션 후 기존 행 보존 확인

## Acceptance Criteria
- [ ] Toss US 매도가 브로커에 도달하고 결과가 정확히 기록됨
- [ ] 주문 불가 상황에서 보유 행이 보존됨
- [ ] 거부된 주문이 누적에 반영되지 않음
- [ ] 리뷰 10건 전부 해소, 회귀 0

## Risks
| Risk | L | I | Mitigation |
|---|---|---|---|
| order_intents 마이그레이션이 기존 행 손상 | L | **높음** | 재생성 전 행수 대조, 인덱스 복원 검증, 백업 안내 |
| 위치 인자 변경이 KIS US에서 exchange 오배치 | L | 높음 | 첫 인자만 위치, 나머지 keyword 유지(GOTCHA 명시) |
| 선검증이 정상 매도까지 막음 | M | 중 | 세션·소수창 판정 실패는 skip(행 유지)이지 삭제가 아니므로 다음 패스 재시도 |
| Toss US가 실제로 처음 동작 시작 | **H** | 높음 | demo 완주 확인 후 real 전환 |

## Notes
- **이 플랜이 Phase 3보다 먼저다.** Phase 3(KIS 잔재 제거)은 대시보드·구독자 등 비매매 경로이고, 여기 있는 것은 실금전 손실 경로다.
- Finding 2는 PR 이전부터의 결함이다. 즉 **Toss로 전환한 이후 US 매도는 한 번도 성공한 적이 없을 가능성이 높다** — 감사 보고서 §7-5(US 에이전트 클린 임포트 불가)와 합치면 "US 트래킹이 Toss에서 애초에 돌지 않았다"는 일관된 그림이 된다. 운영 로그 확인이 선행되어야 한다.

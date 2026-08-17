# Plan: 소수 수량 계약 확장 + KR/KIS 회귀 고정 (PRD Phase 1)

## Summary

보유 수량 계약이 소수를 표현할 수 있게 하고, 그 변경이 KIS와 국내(KR) 경로를 건드리지 않음을 테스트로 못박는다. 조사 결과 **계약의 벽이 PRD가 가정한 것보다 얇다** — 아래 "전제 정정"을 먼저 읽을 것.

## User Story

As a **PRISM 유지보수자**,
I want **소수 보유 수량이 계약 수준에서 표현 가능해지고 KR·KIS는 그대로임이 증명되기를**,
So that **Phase 2에서 어댑터를 고칠 때 상위에서 값이 뭉개지지 않고, 국내 경로가 조용히 깨지지 않는다**.

## Problem → Solution

**현재**: `HoldingState = tuple[str, int | None]`, `normalize_checked_holding`이 `type(quantity) is int`를 엄격히 요구. 소수 수량은 표현할 방법이 없다.

**목표**: 계약이 `Decimal`을 허용하고, KIS 99/99와 토스 KR 경로가 변하지 않음이 테스트로 고정된다.

## Metadata

- **Complexity**: Small (기존 파일 2개 수정 + 테스트)
- **Source PRD**: `.claude/PRPs/prds/toss-fractional-shares.prd.md`
- **PRD Phase**: Phase 1 — 계약 확장 + 회귀 고정
- **Estimated Files**: 2 UPDATE, 1 UPDATE(테스트)
- **Branch**: `hotfix/toss-fractional-shares` (생성됨)

---

## ⚠️ 전제 정정 — 구현 전 반드시 읽을 것

PRD는 `normalize_checked_holding`이 소수 수량을 막는 벽이라고 가정했다. **조사해 보니 US 경로는 그 함수를 아예 쓰지 않는다.**

```
normalize_checked_holding 호출자 (전부 KR 전용, 로그 태그 [POSITION-PENDING][KR]):
  stock_tracking_agent.py:2817
  tools/hardstop_seller.py:427
  tools/trend_exit_seller.py:654

prism-us/ 는 normalize_checked_holding 도, get_holding_quantity_checked 도 쓰지 않는다.
  prism-us/us_stock_tracking_agent.py:2846 → get_holding_quantity (정수로 뭉개는 쪽)

TossBroker._sell() 은 자기 메서드를 직접 호출한다 (adapter.py:183),
normalize_checked_holding 을 거치지 않는다.
```

**따라서 이 Phase의 실제 가치는 "US를 위한 계약 해방"이 아니라 두 가지다:**

1. 계약(`HoldingState`, `BrokerPort` docstring)이 소수를 **표현할 수 있게** 해서 Phase 2가 타입을 어기지 않게 한다
2. **KR 3개 호출자가 소수를 받아도 안전하게 거부**함을 테스트로 고정한다 — 이들은 각자 `isinstance(int)` / `int()` 재검사를 하므로 이미 방어되어 있으나, 그 방어가 의도된 것임을 못박는다

버그의 실제 해소는 **Phase 2**다. 이 Phase만으로는 사용자에게 보이는 변화가 없다.

---

## UX Design

**N/A — 내부 계약 변경.** 이 Phase 후에도 소수 보유는 여전히 사라진다(Phase 2에서 해소).

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| **P0** | `prism_core/execution_service.py` | 26-40 | 수정 대상. `normalize_checked_holding` 전문 |
| **P0** | `trading/brokers/base.py` | 68-72, 130-146 | `HoldingState` 타입 별칭 + 포트 docstring |
| **P0** | `stock_tracking_agent.py` | 2815-2836 | KR 호출자 ①. `isinstance(checked_quantity, int)` 재검사 |
| **P0** | `tools/hardstop_seller.py` | 425-445 | KR 호출자 ②. `int(checked_qty or 0)` → 0.44는 0이 되어 차단 |
| **P1** | `tools/trend_exit_seller.py` | 650-665 | KR 호출자 ③. ②와 동일 구조 |
| **P1** | `tests/test_broker_contract.py` | 80-90, 150-175 | 3상태 검증 헬퍼 + parametrize 패턴 |
| **P2** | `trading/brokers/toss/adapter.py` | 655-690 | Phase 2에서 고칠 곳. 지금은 건드리지 않음 |

## External Documentation

**불필요.** 순수 내부 타입/계약 변경. 토스 API를 호출하지 않는다.

---

## Patterns to Mirror

### CONTRACT_NORMALIZER (수정 대상 원본)
```python
# SOURCE: prism_core/execution_service.py:26-40
def normalize_checked_holding(result: Any) -> tuple[str, int | None]:
    """Return only authoritative HELD/FLAT results; collapse malformed data."""

    if not isinstance(result, tuple) or len(result) != 2:
        return "UNKNOWN", None
    state = str(result[0] or "UNKNOWN").upper()
    quantity = result[1]
    if state == "HELD" and type(quantity) is int and quantity > 0:
        return "HELD", quantity
    if state == "FLAT" and type(quantity) is int and quantity == 0:
        return "FLAT", 0
    if state == "UNKNOWN" and quantity is None:
        return "UNKNOWN", None
    return "UNKNOWN", None
```
→ `type(x) is int`를 쓴 이유는 `bool`이 `int`의 하위형이라 `isinstance`면 `True`가 통과하기 때문이다. **`Decimal`을 추가할 때도 `bool`이 새지 않게 유지할 것.**

### TYPE_ALIAS
```python
# SOURCE: trading/brokers/base.py:68-72
# Holding state is a three-valued answer, never a bare integer. "FLAT" means the
# broker said zero; "UNKNOWN" means the broker did not say. Selling on a zero
# that was really a failed balance query is the mistake this prevents.
HoldingState = tuple[str, int | None]
```

### KR_CALLER_GUARD (건드리지 않되 동작을 이해할 것)
```python
# SOURCE: stock_tracking_agent.py:2828-2836
if holding_state == "HELD" and (
    not isinstance(checked_quantity, int) or checked_quantity <= 0
):
    blocked_tickers.add(ticker)
    logger.critical(
        "[POSITION-PENDING][KR] invalid HELD quantity symbol=%s; "
        "no intent/order/effects",
        ticker,
    )
    return False
```
```python
# SOURCE: tools/hardstop_seller.py:438-447
sold_qty = int(checked_qty or 0)
if sold_qty <= 0:
    logger.critical(
        "[POSITION-PENDING][KR] hardstop invalid HELD quantity "
        "symbol=%s quantity=%s action=retry", ticker, checked_qty,
    )
    release_lock(conn, ticker, "KR", run_id, new_state="HOLDING")
    return
```
→ 두 호출자 모두 **소수를 받으면 거래를 차단**한다. 이는 안전한 실패이며 **의도적으로 그대로 둔다.** 국내는 소수가 발생할 수 없고, 만에 하나 발생하면 멈추는 쪽이 옳다.

### TEST_STRUCTURE
```python
# SOURCE: tests/test_broker_contract.py:83-88
def assert_holding_state_survives_normalisation(state, expected):
    """Assert a three-state holding answer is not collapsed to UNKNOWN."""
    from prism_core.execution_service import normalize_checked_holding
    assert normalize_checked_holding(state) == expected
```
```python
# SOURCE: tests/test_broker_contract.py:150-165
@pytest.mark.parametrize(
    "state, expected",
    [
        (("HELD", 5), ("HELD", 5)),
        (("FLAT", 0), ("FLAT", 0)),
        (("UNKNOWN", None), ("UNKNOWN", None)),
        (("HELD", None), ("UNKNOWN", None)),
    ],
)
def test_holding_states_pass_through_the_production_normaliser(state, expected):
```
→ pytest parametrize, 함수 내부 import, duck-typed Fake. `tests/conftest.py`가 브로커 환경변수를 매 테스트 초기화한다.

### LOGGING_PATTERN
```python
# SOURCE: prism_core/execution_service.py:289-293
logger.warning(
    "[ORDER_INTENT] duplicate blocked id=%s status=%s market=%s side=%s symbol=%s",
    existing["id"], existing["status"], intent.market, intent.side, intent.symbol,
)
```
→ 대괄호 태그 + `%s` lazy 포매팅.

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `prism_core/execution_service.py` | UPDATE | `normalize_checked_holding`이 `Decimal` 허용 |
| `trading/brokers/base.py` | UPDATE | `HoldingState` 타입에 `Decimal` 추가 + docstring |
| `tests/test_broker_selection.py` | UPDATE | 계약 확장 + KR 호출자 안전 거부 테스트 |

## NOT Building

- **어댑터 수정** — `_portfolio_checked`의 절삭 제거는 **Phase 2**
- **KR 3개 호출자 수정** — 소수를 거부하는 현재 동작이 옳다. 국내는 소수가 없고, 있으면 멈춰야 한다
- **소수 매도/매수** — Phase 3, 4
- **`us_stock_holdings` 스키마** — Phase 6, 미결 Open Question
- **KIS 어댑터** — 항상 정수를 반환하므로 손댈 것이 없다

---

## Step-by-Step Tasks

### Task 1: `HoldingState` 타입 확장

- **ACTION**: `trading/brokers/base.py`의 타입 별칭 수정
- **IMPLEMENT**:
  ```python
  from decimal import Decimal
  HoldingState = tuple[str, int | Decimal | None]
  ```
  기존 주석은 유지하고, **왜 `Decimal`이며 `float`이 아닌지** 한 줄 추가:
  토스 US는 소수 주식을 보유할 수 있고, `float`은 0.1+0.2 오차로 보유량이 브로커와 어긋나 전량 매도가 잔량을 남긴다.
- **MIRROR**: TYPE_ALIAS
- **IMPORTS**: `from decimal import Decimal`
- **GOTCHA**: `BrokerPort.get_holding_quantity_checked`의 docstring도 함께 갱신할 것. 계약 문서가 타입과 어긋나면 다음 사람이 타입을 믿지 않는다
- **VALIDATE**: `python -c "from trading.brokers.base import HoldingState; print(HoldingState)"`

### Task 2: `normalize_checked_holding`이 `Decimal` 허용

- **ACTION**: `prism_core/execution_service.py:26-40` 수정
- **IMPLEMENT**:
  - `HELD` 판정에 `Decimal`을 추가하되 **`bool`은 계속 배제**한다
  - 권장 형태:
    ```python
    _QUANTITY_TYPES = (int, Decimal)

    def _is_quantity(value: Any) -> bool:
        """정수 또는 Decimal만 수량으로 인정. bool 은 int 의 하위형이라 제외한다."""
        return type(value) in _QUANTITY_TYPES
    ```
  - `HELD`: `_is_quantity(quantity) and quantity > 0`
  - `FLAT`: `_is_quantity(quantity) and quantity == 0` — `Decimal("0")`도 FLAT으로 인정
  - 반환 타입 힌트를 `tuple[str, int | Decimal | None]`로
  - docstring에 **왜 `Decimal`을 받는지와, KR 호출자는 여전히 정수를 요구한다는 사실**을 적을 것
- **MIRROR**: CONTRACT_NORMALIZER
- **IMPORTS**: `from decimal import Decimal`
- **GOTCHA**:
  - **`type(x) is int`를 `isinstance`로 바꾸지 말 것.** `isinstance(True, int)`는 `True`라 `("HELD", True)`가 수량 1로 통과한다. 원래 코드가 `type(...) is`를 쓴 이유가 이것이다
  - `Decimal("0")` 과 `0`은 `==`로 같다. `FLAT` 판정에서 둘 다 허용되는 것이 맞다
  - `float`은 **허용하지 않는다.** 허용하면 어딘가에서 float이 새어들어와 정밀도가 조용히 깨진다
- **VALIDATE**: Task 3의 테스트

### Task 3: 계약 + KR 안전성 테스트

- **ACTION**: `tests/test_broker_selection.py`에 섹션 추가
- **IMPLEMENT**:
  1. **계약이 `Decimal`을 통과시킨다**
     - `("HELD", Decimal("0.44519"))` → 그대로 유지
     - `("FLAT", Decimal("0"))` → `("FLAT", 0)` 또는 `("FLAT", Decimal("0"))` (구현에 맞춰 단언)
  2. **`bool`이 수량으로 새지 않는다** — `("HELD", True)` → `("UNKNOWN", None)`
  3. **`float`은 거부된다** — `("HELD", 0.44)` → `("UNKNOWN", None)`
  4. **기존 정수 동작 무변경** — 기존 parametrize 케이스 전부 그대로 통과
  5. **KR 호출자가 소수를 안전하게 거부한다** — `stock_tracking_agent`의 가드 조건을 그대로 재현:
     ```python
     def test_kr_callers_still_reject_a_fractional_quantity():
         """국내는 소수가 없고, 있으면 멈추는 쪽이 옳다."""
         checked = normalize_checked_holding(("HELD", Decimal("0.44")))
         state, qty = checked
         assert state == "HELD"
         # stock_tracking_agent.py:2828 의 가드
         assert not isinstance(qty, int), "KR 가드가 소수를 통과시키면 안 된다"
         # tools/hardstop_seller.py:438 의 가드
         assert int(qty or 0) <= 0
     ```
     이 테스트는 **KR 호출자를 고치지 않았다는 사실 자체를 고정**한다. 나중에 누가 무심코 고치면 실패한다
- **MIRROR**: TEST_STRUCTURE
- **IMPORTS**: `from decimal import Decimal`, `import pytest`
- **GOTCHA**: `Decimal` 비교는 `==`가 값 기반이라 `Decimal("0.44") == 0.44`가 **False**다(float 변환 오차). 테스트에서 `Decimal`끼리 비교할 것
- **VALIDATE**: `.venv/bin/python -m pytest tests/test_broker_selection.py -q`

### Task 4: 회귀 고정 + 커밋

- **ACTION**: KIS·토스 KR 무변경 확인 후 커밋
- **IMPLEMENT**:
  ```bash
  # KIS 7종 — baseline 99
  .venv/bin/python -m pytest -q -p no:cacheprovider \
    tests/test_execution_service.py tests/test_async_trading.py \
    tests/test_multi_account_domestic.py tests/test_sell_quantity_guard.py \
    tests/test_sell_denominator_sync.py tests/test_kr_pending_entry.py \
    tests/test_multi_account_kis_auth.py

  # 브로커/토스 — baseline 246
  .venv/bin/python -m pytest tests/test_broker_contract.py tests/test_kis_adapter.py \
    tests/test_broker_selection.py tests/test_toss_*.py -q -p no:cacheprovider
  ```
  커밋 프리픽스는 `fix:` (버그 수정 계열의 준비 단계). CLAUDE.md 규칙상 코드 변경이므로 feature 브랜치에서 작업 → 이미 `hotfix/toss-fractional-shares`에 있다
- **VALIDATE**: KIS **99 passed**, 브로커 **246 + 신규**

---

## Testing Strategy

### Unit Tests

| Test | Input | Expected | Edge Case? |
|---|---|---|---|
| 소수 HELD 통과 | `("HELD", Decimal("0.44519"))` | 수량 보존 | ✅ 이 Phase의 목적 |
| 소수 FLAT | `("FLAT", Decimal("0"))` | FLAT 인정 | ✅ |
| bool 배제 | `("HELD", True)` | `("UNKNOWN", None)` | ✅ `isinstance` 함정 |
| float 거부 | `("HELD", 0.44)` | `("UNKNOWN", None)` | ✅ 정밀도 오염 방지 |
| 정수 HELD (기존) | `("HELD", 5)` | `("HELD", 5)` | 무변경 확인 |
| 정수 FLAT (기존) | `("FLAT", 0)` | `("FLAT", 0)` | 무변경 확인 |
| 오염된 값 (기존) | `("HELD", None)` | `("UNKNOWN", None)` | 무변경 확인 |
| 음수 소수 | `("HELD", Decimal("-1"))` | `("UNKNOWN", None)` | ✅ |
| KR 가드 안전 거부 | `Decimal("0.44")` | KR 가드가 차단 | ✅ 의도 고정 |

### Edge Cases Checklist

- [x] `bool`이 정수로 오인되지 않음
- [x] `float` 거부
- [x] `Decimal("0")`을 FLAT으로 인정
- [x] 음수 수량
- [x] KR 호출자의 방어 유지
- [ ] 동시 접근 — 해당 없음 (순수 함수)
- [ ] 네트워크 — 해당 없음

---

## Validation Commands

### Static
```bash
.venv/bin/python -m compileall -q prism_core/execution_service.py trading/brokers/base.py
.venv/bin/python -c "from trading.brokers.base import HoldingState; from prism_core.execution_service import normalize_checked_holding; print('ok')"
```
EXPECT: 오류 없음. (저장소에 린터·타입체커 설정이 없다 — 게이트가 존재하지 않음)

### Unit
```bash
.venv/bin/python -m pytest tests/test_broker_selection.py tests/test_broker_contract.py -q -p no:cacheprovider
```
EXPECT: 전부 통과

### 회귀 (이 Phase의 핵심 게이트)
```bash
.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_execution_service.py tests/test_async_trading.py \
  tests/test_multi_account_domestic.py tests/test_sell_quantity_guard.py \
  tests/test_sell_denominator_sync.py tests/test_kr_pending_entry.py \
  tests/test_multi_account_kis_auth.py
```
EXPECT: **99 passed** — KIS 무변동

### 전체
```bash
.venv/bin/python -m pytest tests/ -q -p no:cacheprovider \
  --ignore=tests/test_agent_fit_score_constant_tripwire.py \
  --ignore=tests/test_issue_289_screening.py --ignore=tests/test_price_query_retry.py \
  --ignore=tests/test_sideways_downtrend_gate.py --ignore=tests/test_youtube_crawler.py \
  --ignore=tests/test_parallel_trading_batch.py --ignore=tests/test_screening_change_rate.py \
  --ignore=tests/test_stock_tracking_agent_process_reports.py \
  --ignore=tests/test_trigger_bearish_candle_exclusion.py
```
EXPECT: **2117 passed / 22 failed / 10 errors** (실패·에러는 기존 baseline과 동일)

> `--ignore` 9개가 필요한 이유: 4개 파일이 모듈 레벨 `sys.exit()`로 수집을 중단시키고, 1개는 없는 모듈을 import하며, 4개는 `.env` 채운 뒤 무한 대기한다. 전부 이 작업과 무관한 기존 문제다.

### Manual
- [ ] `normalize_checked_holding` docstring이 "KR 호출자는 여전히 정수를 요구한다"를 명시하는가
- [ ] `type(x) is` 검사가 유지되어 `bool`이 새지 않는가
- [ ] `float`이 허용되지 않는가

---

## Acceptance Criteria

- [ ] Task 1–4 완료
- [ ] 계약이 `Decimal`을 표현·통과시킨다
- [ ] `bool`·`float`은 여전히 거부된다
- [ ] KIS 99/99 유지
- [ ] KR 호출자 3곳 **미수정** (테스트로 고정)
- [ ] 전체 스위트 실패·에러 수 baseline 동일

## Completion Checklist

- [ ] `type(x) is` 관례 유지
- [ ] 로깅 `%s` lazy 포매팅 (추가한 경우)
- [ ] 테스트가 parametrize + 함수 내부 import 패턴을 따름
- [ ] 어댑터를 건드리지 않음 (Phase 2 범위)
- [ ] 구현 중 코드베이스 재검색 불필요

## Risks

| Risk | L | I | Mitigation |
|---|---|---|---|
| `isinstance`로 바꿔 `bool`이 수량 1로 통과 | M | H | 전용 테스트 + GOTCHA 명시 |
| `float` 허용으로 정밀도 오염 | M | H | 명시적 거부 + 테스트 |
| KR 호출자를 "친절하게" 같이 고침 | M | H | 미수정을 테스트로 고정. 국내는 소수가 없고 멈추는 게 옳다 |
| 이 Phase만 하고 Phase 2를 잊음 | M | H | 버그는 그대로다. 계획서 상단 "전제 정정"에 명시 |

## Notes

- **이 Phase는 사용자에게 보이는 변화가 없다.** 실제 버그 해소는 Phase 2다. 계약을 먼저 여는 이유는 Phase 2가 타입을 어기지 않게 하기 위해서다.
- **US 경로는 `normalize_checked_holding`을 쓰지 않는다** (`prism-us`는 `get_holding_quantity`를 쓴다). 따라서 이 Phase는 US 버그와 직접 연결되지 않는다. PRD의 Phase 1 설명은 이 점에서 과대평가되어 있었고, 계획서 상단에서 정정했다.
- 저장소에 린터·타입체커 설정이 없으므로 "타입 에러 0" 게이트는 존재하지 않는다. 검증은 테스트와 `compileall`에 의존한다.

---

*Generated: 2026-08-18*
*Source: `.claude/PRPs/prds/toss-fractional-shares.prd.md` — Phase 1*

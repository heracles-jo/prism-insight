# Plan: `BrokerPort` 추상화 + KIS 어댑터 이전 (PRD Phase 1)

## Summary

브로커 계약(`BrokerPort` Protocol)을 정의하고, 기존 KIS 트레이더를 그 계약을 만족하는 첫 번째 어댑터로 감싼다. **이 단계는 순수 추가(purely additive)다** — 기존 파일은 단 한 줄도 수정하지 않는다. 그래야 "KIS 회귀 무손실"이 검증이 아니라 구조적 보장이 된다.

## User Story

As a **PRISM 유지보수자**,
I want **브로커가 지켜야 할 계약이 코드로 명시되고 KIS가 그 계약의 첫 구현이 되기를**,
So that **토스 어댑터를 추가할 때 계약 테스트만 통과시키면 되고, 계약이 KIS 구현 형태로 굳어버리는 일이 없다**.

## Problem → Solution

**현재**: 매매 계층에 "브로커"라는 개념이 없다. `ExecutionService.domestic()`(`prism_core/execution_service.py:154`)이 `AsyncTradingContext`를 직접 import·생성하고, `_classify_result`(236행)는 문자열 `"KIS"`를 하드코딩한다. 두 번째 브로커를 끼울 자리가 없다.

**목표**: `trading/brokers/base.py`에 브로커 계약이 존재하고, KIS가 그 계약을 통과하는 어댑터로 존재한다. 프로덕션 동작은 **완전히 동일**하다 (아직 아무것도 배선하지 않았으므로).

## Metadata

- **Complexity**: Medium (신규 5파일, 기존 파일 수정 0)
- **Source PRD**: `.claude/PRPs/prds/toss-securities-broker.prd.md`
- **PRD Phase**: Phase 1 — `BrokerPort` 추상화 + KIS 이전
- **Estimated Files**: 5 CREATE, 0 UPDATE
- **Branch**: `feat/toss-securities-broker` (생성됨)

---

## ⚠️ 이 계획의 핵심 원칙

> **Phase 1은 기존 파일을 수정하지 않는다.**

PRD의 성공 지표 중 하나가 "KIS 회귀 무손실 — 기존 테스트 100% 통과"다. 기존 파일을 건드리지 않으면 회귀가 **불가능**해진다. 배선(`ExecutionService` 분기, `_classify_result`의 `"KIS"` 하드코딩 제거)은 전부 **Phase 5**의 일이다.

구현 중 "이왕이면 여기도 같이 고치자"는 유혹이 생기면 거부하라. 그건 Phase 5다.

---

## UX Design

### Before / After

**N/A — 순수 내부 변경.** 이 Phase는 사용자에게 보이는 동작을 만들지 않는다. 새 모듈이 추가되지만 아직 어떤 실행 경로도 그것을 호출하지 않는다.

### Interaction Changes

| Touchpoint | Before | After | Notes |
|---|---|---|---|
| 전체 | — | — | 변화 없음. 배선은 Phase 5 |

---

## 사전 조건 (구현 시작 전 반드시 처리)

현재 활성 파이썬은 **다른 프로젝트의 venv**(`/Users/heracles/workspace/trading-ai/.venv`, Python 3.14.6)이며 prism-insight 의존성이 없다. 실측 결과:

```
python -m pytest tests/test_execution_service.py tests/test_sell_denominator_sync.py -q
→ 1 failed, 23 passed
  실패 원인: ModuleNotFoundError: No module named 'tenacity' (trading/kis_auth.py:27)

python -m pytest tests/test_async_trading.py tests/test_multi_account_domestic.py \
                tests/test_sell_quantity_guard.py tests/test_kr_pending_entry.py -q
→ 4 errors during collection (동일 원인)
```

**즉 현재 KIS 테스트 baseline을 확립할 수 없다.** 구현 착수 전에:

```bash
cd /Users/heracles/workspace/prism-insight
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest tests/ -q          # ← 이 결과가 진짜 baseline
```

baseline을 기록해두지 않으면 "회귀 없음"을 주장할 근거가 없다. 다른 프로젝트 venv에 패키지를 설치하지 말 것.

> 참고: `pytest.ini` / `setup.cfg` / `pyproject.toml`에 pytest 설정이 **없다**. `tests/conftest.py`도 **없다**. 테스트는 리포지토리 루트에서 실행하는 것을 전제로 한다 (`trading/domestic_stock_trading.py:25`의 `import kis_auth as ka`가 `trading/`을 sys.path에 얹은 상태를 가정).

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| **P0** | `cores/market_data/source.py` | 1-77 | **이 Phase가 그대로 따라야 할 원본 패턴.** Protocol + `Unsupported`/`Unavailable` 예외 규약. 모듈 docstring이 "왜 이렇게 했는지"를 설명하는 방식까지 모방 대상 |
| **P0** | `prism_core/execution_service.py` | 108-250 | 브로커가 실제로 호출당하는 메서드 집합(`_DIRECT_ORDER_METHODS` 111-118), `__getattr__` 위임(227-233), `_classify_result`의 `"KIS"` 하드코딩(236-250) |
| **P0** | `trading/domestic_stock_trading.py` | 1252-1292 | `async_buy_stock` 시그니처 + **반환 dict 형태의 정본**(1264-1273) + timeout 시 `outcome_unknown` 처리(1281-1291) |
| **P1** | `trading/domestic_stock_trading.py` | 100-146, 2234-2273 | `DomesticStockTrading.__init__` 파라미터, `AsyncTradingContext` 컨텍스트 매니저 형태 |
| **P1** | `trading/domestic_stock_trading.py` | 1599-1616, 1712-1724 | `get_portfolio` / `get_holding_quantity_checked` 반환 형태. `("HELD"\|"FLAT"\|"UNKNOWN", int\|None)` 3상태 튜플 |
| **P1** | `prism-us/trading/us_stock_trading.py` | 236-305, 1374-1410, 2192-2230 | US 쪽 대응 시그니처. `ticker`/`exchange` 파라미터가 KR과 다름 |
| **P2** | `tests/test_execution_service.py` | 1-70 | **테스트 패턴 정본.** duck-typed `FakeTrader`/`FakeContext`, 함수 내부 import |
| **P2** | `prism_core/execution_service.py` | 27-40 | `normalize_checked_holding` — 3상태 튜플 검증 규칙 |

## External Documentation

| Topic | Source | Key Takeaway |
|---|---|---|
| — | — | **외부 조사 불필요.** 이 Phase는 순수 내부 추상화이며 토스 API를 호출하지 않는다. 토스 스펙은 Phase 2에서 필요 |

---

## Patterns to Mirror

### PROTOCOL_DEFINITION
```python
# SOURCE: cores/market_data/source.py:42-51
@runtime_checkable
class MarketDataSource(Protocol):
    """One provider of per-instrument market data.

    Implementations raise `Unsupported` for capabilities they lack and
    `Unavailable` when a call fails. Returning an empty frame is not an
    acceptable way to signal either.
    """

    name: str
```
→ `@runtime_checkable` + `Protocol`, 클래스 속성으로 `name` 선언, docstring이 구현자에게 예외 규약을 지시한다.

### EXCEPTION_CONTRACT
```python
# SOURCE: cores/market_data/source.py:25-39
class Unsupported(Exception):
    """This provider does not offer this capability at all.

    Distinct from failure: there is nothing to retry and nothing is wrong. The
    chain skips to the next source without logging an error.
    """


class Unavailable(Exception):
    """This provider could not answer right now.

    Restriction, timeout, auth, an empty result — all the same to the chain,
    which tries the next source. The message is kept for the log because the
    2026-08-04 outage was prolonged by a failure that reported only "not found".
    """
```
→ "못 한다"(Unsupported)와 "지금 안 된다"(Unavailable)를 **구조적으로 분리**한다. docstring이 실제 장애 사례를 근거로 든다. 새 예외도 이 톤을 따를 것.

### MODULE_DOCSTRING
```python
# SOURCE: cores/market_data/source.py:1-13
"""What a market data provider has to offer, and how several are combined.

The protocol is written from what the reports actually ask for, not from any
one provider's API. That is deliberate: define the port by the need and each
provider becomes an adapter, so adding a broker means writing one class rather
than editing every call site.
...
"""
```
→ **결정과 그 이유**를 쓴다. 파일이 뭘 하는지가 아니라 왜 이 형태인지를. 특히 "define the port by the need, not by one provider's API"는 이 Phase의 지도 원칙 그 자체다.

### ORDER_RESULT_SHAPE
```python
# SOURCE: trading/domestic_stock_trading.py:1264-1273 (docstring), 1281-1291 (실제)
{
    'success': bool,          # 성공 여부
    'stock_code': str,        # 종목코드
    'current_price': int,     # 주문 시점 현재가
    'quantity': int,          # 수량
    'total_amount': int,      # 총 금액
    'order_no': str | None,   # 주문번호
    'message': str,           # 결과 메시지
    'timestamp': str,         # _now_kst().isoformat()
    # 선택적:
    'outcome_unknown': True,  # 타임아웃/예외 — 브로커가 받았을 수도 있음
}
```
→ **예외를 던지지 않고 dict를 반환**한다. 비즈니스 실패는 `success: False`, 모호한 실패는 `outcome_unknown: True`. 이 구분이 `ExecutionService._classify_result`(236-250)의 입력이므로 어겨서는 안 된다.

### THREE_STATE_HOLDING
```python
# SOURCE: trading/domestic_stock_trading.py:1712-1723
def get_holding_quantity_checked(
    self, stock_code: str
) -> tuple[str, int | None]:
    """Distinguish an authoritative flat holding from a balance-query failure."""

    authoritative, portfolio = self._get_portfolio_checked()
    if not authoritative:
        return "UNKNOWN", None
    for stock in portfolio:
        if stock.get("stock_code") == stock_code:
            return "HELD", int(stock["quantity"])
    return "FLAT", 0
```
→ "잔고 0"과 "잔고 조회 실패"를 절대 섞지 않는다. 매도 로직의 안전장치다. 계약에 반드시 포함.

### LOGGING_PATTERN
```python
# SOURCE: trading/domestic_stock_trading.py:226-228
logger.info("✅ DomesticStockTrading initialized (Async Enabled)")
logger.info(f"   Mode: {mode}, Buy Amount: {self.buy_amount:,} KRW")

# SOURCE: trading/domestic_stock_trading.py:1315
logger.info(f"[Async Buy API] {stock_code} buy process started (amount: {amount:,} KRW)")

# SOURCE: prism_core/execution_service.py:289-293  (구조화 로그는 %s 스타일)
logger.warning(
    "[ORDER_INTENT] duplicate blocked id=%s status=%s market=%s side=%s symbol=%s",
    existing["id"], existing["status"], intent.market, intent.side, intent.symbol,
)
```
→ 모듈 레벨 `logger = logging.getLogger(__name__)`. 대괄호 태그 프리픽스(`[ORDER_INTENT]`, `[FALLBACK]`, `[Async Buy API]`). 상태 로그엔 이모지(✅/❌). **구조화 로그는 f-string이 아니라 `%s` lazy 포매팅**을 쓴다 — 새 코드는 `%s` 쪽을 따를 것.

### TEST_STRUCTURE
```python
# SOURCE: tests/test_execution_service.py:11-40, 61-70
class FakeTrader:
    def __init__(self):
        self.calls = []

    async def async_buy_stock(self, *args, **kwargs):
        self.calls.append(("buy", args, kwargs))
        return {"success": True, "kind": "buy"}

    def get_holding_quantity(self, ticker):
        self.calls.append(("holding", (ticker,), {}))
        return 17


def test_async_context_and_order_arguments_are_preserved():
    from prism_core.execution_service import ExecutionService   # ← 함수 내부 import

    trader = FakeTrader()
    context = FakeContext(trader)
    ...
```
→ pytest. **상속이나 mock 라이브러리가 아니라 duck-typed 수제 Fake**. 호출을 `self.calls` 리스트에 기록해 검증. **import는 테스트 함수 내부**에서 (모듈 레벨 부작용 회피 — `trading` 임포트가 설정 파일을 읽기 때문). `conftest.py` 없음.

### CONTEXT_MANAGER
```python
# SOURCE: trading/domestic_stock_trading.py:2260-2273
async def __aenter__(self):
    self.trader = DomesticStockTrading(
        mode=self.mode,
        buy_amount=self.buy_amount,
        ...
    )
    return self.trader

async def __aexit__(self, exc_type, exc_val, exc_tb):
    if exc_type:
        logger.error(f"AsyncTradingContext error: {exc_type.__name__}: {exc_val}")
```
→ `__aenter__`가 트레이더를 **생성해서 반환**한다(자기 자신이 아니라). `__aexit__`는 예외를 삼키지 않고 로깅만 한다(`return` 없음 = falsy = 전파).

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `trading/brokers/__init__.py` | CREATE | 패키지 초기화 + 공개 심볼 재노출 |
| `trading/brokers/base.py` | CREATE | `BrokerPort` Protocol, 예외 계층, 결과 형태 규약 |
| `trading/brokers/kis_adapter.py` | CREATE | 기존 KIS 트레이더를 계약 뒤로 감싸는 위임 래퍼 (KR/US) |
| `tests/test_broker_contract.py` | CREATE | **어떤 어댑터에도 적용 가능한** 재사용 계약 테스트 |
| `tests/test_kis_adapter.py` | CREATE | KIS 어댑터가 위임을 정확히 수행하는지 검증 |

**수정하는 기존 파일: 없음.**

## NOT Building

- **`ExecutionService` 배선** — `domestic()`/`us()`의 브로커 분기는 Phase 5
- **`_classify_result`의 `"KIS"` 하드코딩 제거** — 기록되는 데이터가 바뀌므로 Phase 5에서 마이그레이션과 함께
- **`PRISM_BROKER` 환경변수 / 설정 파싱** — Phase 5
- **토스 관련 코드 일체** — Phase 2 이후
- **`DomesticStockTrading` / `USStockTrading` 리팩터링** — 위임 래퍼로 감쌀 뿐, 내부는 손대지 않는다
- **dry-run 시뮬레이터** — Phase 3
- **KIS TR ID 하드코딩 정리** — 동작 변경 위험. 이 Phase의 목표가 아니다

---

## Step-by-Step Tasks

### Task 1: `trading/brokers/` 패키지 생성

- **ACTION**: `trading/brokers/__init__.py` 생성
- **IMPLEMENT**: `base`에서 공개 심볼을 재노출. `kis_adapter`는 **재노출하지 않는다** (import 시 `kis_auth` 로딩 → 설정 파일 읽기 부작용 발생). 어댑터는 호출측이 명시적으로 import 한다.
  ```python
  from trading.brokers.base import (
      BrokerPort, BrokerUnsupported, BrokerUnavailable, OrderOutcome,
  )
  __all__ = ["BrokerPort", "BrokerUnsupported", "BrokerUnavailable", "OrderOutcome"]
  ```
- **MIRROR**: `cores/market_data/__init__.py`의 재노출 방식
- **GOTCHA**: `trading/__init__.py`가 이미 존재한다. `trading.brokers`가 서브패키지로 정상 인식되는지 확인할 것. 또한 `prism-us`가 `sys.path` 조작으로 `trading`을 로드하는 경로가 있으므로(`prism_core/execution_service.py:182-198`) `trading.brokers`의 절대 import가 그 경로에서도 되는지 Task 5에서 검증
- **VALIDATE**: `python -c "from trading.brokers import BrokerPort; print(BrokerPort)"`

### Task 2: `BrokerPort` 계약 정의

- **ACTION**: `trading/brokers/base.py` 생성
- **IMPLEMENT**:
  1. 모듈 docstring — MODULE_DOCSTRING 패턴을 따라 **왜 이 포트가 KIS 형태가 아닌지**를 명시. 핵심 문장: 포트는 호출측이 실제로 필요로 하는 것으로 정의하며, 어느 한 브로커의 API 모양을 따르지 않는다.
  2. 예외 계층 (EXCEPTION_CONTRACT 패턴):
     ```python
     class BrokerError(Exception): ...
     class BrokerUnsupported(BrokerError): ...   # 이 브로커엔 이 기능이 없다 (예: 토스 예약주문)
     class BrokerUnavailable(BrokerError): ...   # 지금 못 한다 (인증/네트워크/레이트리밋)
     ```
     `cores/market_data.Unsupported`를 재사용하지 말 것 — 시세 체인의 폴백 의미와 매매의 의미가 다르다. 별도 계층으로 두되 docstring에서 관계를 언급.
  3. `OrderOutcome` — 주문 결과 dict의 **필수 키를 문서화한 TypedDict**. 런타임 강제가 아니라 계약 문서로서. ORDER_RESULT_SHAPE 패턴의 키를 정확히 그대로.
  4. `@runtime_checkable class BrokerPort(Protocol)` — 아래 표면을 선언:

     | 메서드 | 근거 |
     |---|---|
     | `name: str` | `MarketDataSource.name` 미러 (`source.py:51`) |
     | `market: str` | `"KR"` \| `"US"`. 토스는 단일 클래스가 두 시장을 커버하므로 인스턴스 속성이어야 함 |
     | `async async_buy_stock(...)` | `_DIRECT_ORDER_METHODS` (`execution_service.py:112`) |
     | `async async_sell_stock(...)` | 〃 (113) |
     | `amend_order(...)` | 〃 (114) |
     | `cancel_order(...)` | 〃 (115) |
     | `buy_reserved_order(...)` | 〃 (116) — 토스는 `BrokerUnsupported` |
     | `sell_reserved_order(...)` | 〃 (117) — 토스는 `BrokerUnsupported` |
     | `get_current_price(...)` | `execution_service` 위임 경유 사용 |
     | `get_portfolio()` | 〃 |
     | `get_account_summary()` | 〃 |
     | `get_holding_quantity(...)` | `tests/test_execution_service.py:37` |
     | `get_holding_quantity_checked(...)` | THREE_STATE_HOLDING — 매도 안전장치 |
     | `calculate_buy_quantity(...)` | 〃 |

- **MIRROR**: PROTOCOL_DEFINITION, EXCEPTION_CONTRACT, MODULE_DOCSTRING
- **IMPORTS**: `from __future__ import annotations`, `from typing import Any, Protocol, TypedDict, runtime_checkable`
- **GOTCHA**:
  - **KR과 US의 시그니처가 다르다.** KR은 `stock_code`, US는 `ticker` + `exchange`(`us_stock_trading.py:1374`). 억지로 통일하면 Phase 5에서 호출측이 깨진다. 포트는 **첫 인자를 `symbol`로 이름 붙이되 위치 인자로 받고**, 나머지는 `**kwargs`로 흘려보내 어댑터가 흡수하게 한다. Phase 1의 목표는 계약 선언이지 시그니처 통일이 아니다.
  - `Protocol`에 `async def`를 선언할 때 본문은 `...`로 두고 `async`를 붙여야 `runtime_checkable` 검사가 의미를 갖는다. 단 `runtime_checkable`은 **메서드 존재만** 검사하고 시그니처는 검사하지 않는다 — 그래서 Task 4의 계약 테스트가 필요하다.
- **VALIDATE**: `python -c "from trading.brokers.base import BrokerPort; print(sorted(BrokerPort.__protocol_attrs__))"` — 위 표의 항목이 모두 나올 것

### Task 3: KIS 어댑터 작성

- **ACTION**: `trading/brokers/kis_adapter.py` 생성
- **IMPLEMENT**:
  - `class KisBroker:` — 기존 트레이더 인스턴스를 **감싸는** 얇은 위임 래퍼.
    ```python
    class KisBroker:
        name = "kis"

        def __init__(self, trader: Any, *, market: str):
            self._trader = trader
            self.market = market.upper()
    ```
  - 포트의 각 메서드를 `self._trader`로 위임. **변환 금지** — 인자도 반환값도 그대로 통과시킨다. 이것이 "동작 무변경"의 구현적 정의다.
    ```python
    async def async_buy_stock(self, *args, **kwargs):
        return await self._trader.async_buy_stock(*args, **kwargs)
    ```
  - `buy_reserved_order` / `sell_reserved_order`: KIS는 지원하므로 그대로 위임. (토스 어댑터가 여기서 `BrokerUnsupported`를 던지게 된다.)
  - `KisDomesticBroker` / `KisUSBroker` — `market`을 고정한 얇은 팩토리 함수 또는 서브클래스. 어느 쪽이든 **트레이더를 스스로 생성하지 않는다.** 생성 책임은 Phase 5의 배선이 갖는다. 생성자는 이미 만들어진 트레이더를 받는다(의존성 주입).
- **MIRROR**: `ExecutionService.__getattr__`(`execution_service.py:227-233`)의 위임 정신. 단 여기서는 `__getattr__` 대신 **명시적 메서드**를 쓴다 — 계약을 코드로 드러내는 것이 이 Phase의 목적이므로.
- **IMPORTS**: `from __future__ import annotations`, `import logging`, `from typing import Any`, `from trading.brokers.base import BrokerPort` (타입 힌트용)
- **GOTCHA**:
  - **`kis_auth`를 이 모듈에서 import 하지 말 것.** 트레이더를 주입받으므로 필요 없다. import 하는 순간 이 모듈은 설정 파일 없이는 로드 불가능해지고, 테스트가 무거워진다.
  - `trader`가 `MultiAccountDomesticStockTrading`(`domestic_stock_trading.py:2068`)일 수도 있다. 그쪽도 동일 메서드 집합을 노출하므로 래퍼는 둘 다 감쌀 수 있어야 한다 — 타입을 `Any`로 두는 이유.
  - `get_account_summary`는 실패 시 `{}`를 반환한다(`domestic_stock_trading.py:1799`). `None`이 아니다. 변환하지 말 것.
- **VALIDATE**: Task 5의 테스트로 검증

### Task 4: 재사용 가능한 계약 테스트 작성

- **ACTION**: `tests/test_broker_contract.py` 생성
- **IMPLEMENT**:
  - **어떤 `BrokerPort` 구현에도 실행할 수 있는** 테스트 본문을 함수로 분리한다. Phase 4의 토스 어댑터가 이 파일을 재사용해야 한다 — 그게 "브로커 확장성" 지표의 실체다.
    ```python
    def assert_satisfies_broker_port(broker):
        """Phase 4의 Toss 어댑터도 이 함수를 통과해야 한다."""
    ```
  - 검증 항목:
    1. `isinstance(broker, BrokerPort)` — `runtime_checkable` 구조 검사
    2. 포트의 모든 메서드가 실제로 존재하고 호출 가능
    3. `name`이 비어있지 않은 문자열, `market`이 `{"KR", "US"}` 중 하나
    4. 주문 메서드가 ORDER_RESULT_SHAPE의 필수 키를 가진 dict를 반환
    5. `get_holding_quantity_checked`가 3상태 튜플 규약을 지킴 — `prism_core.execution_service.normalize_checked_holding`(27-40행)에 통과시켜 `UNKNOWN`으로 뭉개지지 않는지 확인. **이게 핵심 검증이다**: 이 함수가 이미 규약의 심판이므로 재구현하지 말고 그대로 쓴다.
    6. 미지원 기능이 조용히 실패하지 않고 `BrokerUnsupported`를 던지는지 (KIS는 해당 없음, 토스용 훅)
  - 이 파일 안에 `FakeKisTrader`를 두어 KIS 어댑터를 실제 KIS 없이 검증
- **MIRROR**: TEST_STRUCTURE — duck-typed Fake, 함수 내부 import
- **IMPORTS**: `import pytest`
- **GOTCHA**:
  - `assert_satisfies_broker_port`는 **테스트 함수가 아니라 헬퍼**다. 이름이 `test_`로 시작하면 pytest가 인자 없이 수집하려다 실패한다. `assert_`로 시작할 것.
  - async 메서드 검증은 `asyncio.run(...)`으로. 이 저장소엔 `pytest-asyncio` 설정이 없다 — `tests/test_execution_service.py:61-70`도 내부 `async def exercise()` + `asyncio.run` 방식을 쓴다. 그 방식을 따를 것.
- **VALIDATE**: `python -m pytest tests/test_broker_contract.py -q`

### Task 5: KIS 어댑터 위임 테스트

- **ACTION**: `tests/test_kis_adapter.py` 생성
- **IMPLEMENT**:
  1. `KisBroker`가 `assert_satisfies_broker_port`를 통과
  2. **위임 충실성**: 각 메서드 호출 시 인자가 **변형 없이** 하위 트레이더에 전달되는지. `FakeTrader.calls` 기록을 비교 (TEST_STRUCTURE 패턴)
  3. **반환값 무변형**: 트레이더가 돌려준 dict 객체가 그대로 반환되는지 (`assert result is sentinel`)
  4. `market="KR"` / `market="US"` 두 경우 모두
  5. `MultiAccountDomesticStockTrading` 형태의 Fake도 감쌀 수 있는지
- **MIRROR**: TEST_STRUCTURE
- **GOTCHA**: 반환값 검증은 `==`가 아니라 **`is`**로 할 것. 값이 같은 새 dict를 만들어 반환하는 실수를 잡아내야 한다 — 그게 곧 "동작 변경"이다.
- **VALIDATE**: `python -m pytest tests/test_kis_adapter.py -q`

### Task 6: 회귀 없음 확인 + 커밋

- **ACTION**: 전체 테스트 실행 후 baseline과 비교, 커밋·푸시
- **IMPLEMENT**:
  ```bash
  python -m pytest tests/ -q                 # baseline과 동일해야 함
  git status --short                          # 신규 5파일만 나와야 함
  git diff --stat HEAD                        # 기존 파일 수정 0줄이어야 함
  ```
  커밋 메시지:
  ```
  feat: add BrokerPort contract and KIS adapter

  Introduces trading/brokers/ with a broker contract mirrored from the
  MarketDataSource protocol pattern (cores/market_data/source.py:42).
  KIS becomes the first adapter as a pure delegating wrapper.

  Purely additive — no existing file is modified, so KIS behaviour is
  structurally unchanged rather than merely tested to be unchanged.
  Wiring into ExecutionService is deferred to PRD Phase 5.

  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  ```
- **VALIDATE**: `git diff --stat HEAD~1 -- . ':!trading/brokers' ':!tests/test_broker_contract.py' ':!tests/test_kis_adapter.py'` 가 **빈 출력**일 것. 하나라도 나오면 Phase 1의 원칙을 어긴 것

---

## Testing Strategy

### Unit Tests

| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| Protocol 구조 만족 | `KisBroker(FakeTrader(), market="KR")` | `isinstance(..., BrokerPort)` True | |
| 매수 인자 위임 | `async_buy_stock("005930", 100000, limit_price=81000)` | Fake가 동일 args/kwargs 기록 | |
| 반환 객체 동일성 | 트레이더가 sentinel dict 반환 | `result is sentinel` | ✅ 무변형 보장 |
| 3상태 — 보유 | Fake가 `("HELD", 5)` | `normalize_checked_holding` → `("HELD", 5)` | |
| 3상태 — 무보유 | Fake가 `("FLAT", 0)` | → `("FLAT", 0)` | ✅ 0과 실패 구분 |
| 3상태 — 조회실패 | Fake가 `("UNKNOWN", None)` | → `("UNKNOWN", None)` | ✅ |
| 3상태 — 오염된 값 | Fake가 `("HELD", None)` | → `("UNKNOWN", None)` | ✅ 방어 |
| `outcome_unknown` 통과 | Fake가 `{"success": False, "outcome_unknown": True}` | 키가 보존됨 | ✅ 모호 실패 |
| 빈 account summary | Fake가 `{}` | `{}` 그대로 (None 아님) | ✅ |
| US 시장 | `market="us"` | `broker.market == "US"` | 대소문자 정규화 |
| 다중계좌 트레이더 | `FakeMultiAccountTrader` | 동일하게 위임 | ✅ |

### Edge Cases Checklist

- [x] 반환 dict가 새 객체로 복제되지 않는지 (`is` 검사)
- [x] `outcome_unknown` 플래그 보존
- [x] `get_account_summary()`의 `{}` 반환 (None 아님)
- [x] 3상태 홀딩 튜플의 4가지 경우
- [x] `market` 대소문자 정규화
- [ ] 네트워크 실패 — **해당 없음.** 이 Phase는 네트워크를 타지 않는다 (Phase 2)
- [ ] 동시 접근 — **해당 없음.** 래퍼는 상태를 갖지 않으며 락은 하위 트레이더가 관리(`domestic_stock_trading.py:222-224`)
- [ ] 권한 거부 — 해당 없음 (Phase 2)

---

## Validation Commands

### 0. 환경 준비 (최초 1회, 필수)
```bash
cd /Users/heracles/workspace/prism-insight
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest tests/ -q | tail -3        # ← baseline 기록
```
EXPECT: baseline 수치를 적어둘 것. 현재 다른 프로젝트 venv에서는 `tenacity` 부재로 측정 불가

### 1. Static Analysis
```bash
python -m compileall -q trading/brokers/
python -c "from trading.brokers import BrokerPort, BrokerUnsupported, BrokerUnavailable; print('ok')"
```
EXPECT: 오류 없음. (이 저장소엔 mypy/ruff 설정이 없다 — 타입체커 게이트는 존재하지 않음)

### 2. Unit Tests (신규)
```bash
python -m pytest tests/test_broker_contract.py tests/test_kis_adapter.py -q
```
EXPECT: 전부 통과

### 3. Full Test Suite (회귀)
```bash
python -m pytest tests/ -q | tail -3
```
EXPECT: **Task 0에서 기록한 baseline과 정확히 동일한 pass/fail 수** (신규 테스트 증가분 제외)

### 4. 순수 추가 검증 (이 Phase의 핵심 게이트)
```bash
git diff --stat HEAD -- . ':!trading/brokers' ':!tests/test_broker_contract.py' ':!tests/test_kis_adapter.py'
```
EXPECT: **빈 출력.** 출력이 있으면 Phase 1 원칙 위반

### 5. Database Validation
해당 없음 — 이 Phase는 스키마를 건드리지 않는다

### 6. Browser Validation
해당 없음 — 내부 변경

### Manual Validation
- [ ] `trading/brokers/base.py`의 모듈 docstring이 "왜 KIS 모양이 아닌가"를 설명하는가
- [ ] 포트 메서드 중 `ExecutionService._DIRECT_ORDER_METHODS`의 6개가 전부 포함되었는가
- [ ] `KisBroker`의 어떤 메서드도 인자/반환값을 변형하지 않는가 (육안 확인)
- [ ] `kis_adapter.py`가 `kis_auth`를 import 하지 않는가
- [ ] `assert_satisfies_broker_port`가 Phase 4에서 재사용 가능한 형태인가 (인자로 broker를 받는 헬퍼)

---

## Acceptance Criteria

- [ ] Task 1–6 완료
- [ ] `tests/test_broker_contract.py`, `tests/test_kis_adapter.py` 통과
- [ ] 전체 테스트가 baseline 대비 회귀 0
- [ ] `git diff`상 기존 파일 수정 **0줄**
- [ ] `assert_satisfies_broker_port`가 Phase 4에서 그대로 재사용 가능
- [ ] 커밋 + 푸시 완료

## Completion Checklist

- [ ] `cores/market_data/source.py`의 Protocol/예외 패턴을 따랐다
- [ ] 예외 규약이 "못 한다" vs "지금 안 된다"로 분리되어 있다
- [ ] 로깅이 `%s` lazy 포매팅 + 대괄호 태그 관례를 따른다
- [ ] 테스트가 duck-typed Fake + 함수 내부 import 패턴을 따른다
- [ ] 하드코딩된 값 없음
- [ ] 불필요한 범위 추가 없음 (배선·토스·리팩터링 전부 제외)
- [ ] 구현 중 코드베이스 재검색이 필요 없었다

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **baseline 미확립 상태로 착수** | **H** | **H** | Task 0을 건너뛰지 말 것. venv 없이는 "회귀 없음"을 주장할 수 없다 |
| 포트를 KIS 모양으로 굳혀버림 | M | H | KR/US 시그니처 차이를 `**kwargs`로 흡수. Phase 4에서 토스가 이 계약을 만족 못 하면 **계약을 고치고 Phase 1을 재방문**한다 — 토스를 KIS 모양에 욱여넣지 않는다 |
| "이왕 하는 김에" 배선까지 진행 | M | H | Validation 4번 게이트가 기계적으로 차단 |
| `runtime_checkable`이 시그니처를 검사 안 함 | H | L | 알려진 한계. Task 4의 계약 테스트가 실제 호출로 보완 |
| `trading.brokers` import가 prism-us 경로에서 실패 | L | M | `execution_service.py:182-198`의 sys.path 조작 경로에서도 import 되는지 Task 5에서 확인 |

## Notes

- **PRD의 US 장외 매수 미해결 이슈는 이 Phase에 영향 없다.** Phase 1은 계약만 정의하며, 예약주문은 포트에 선언되기만 하고 KIS는 그대로 지원한다. 그 결정은 Phase 6 착수 전까지만 확정되면 된다.
- **`_classify_result`의 `"KIS"` 하드코딩**(`execution_service.py:238, 246, 250`)은 이 Phase에서 **의도적으로 남겨둔다.** 이 값이 `IntentStore`에 기록되므로 변경 시 기존 데이터와의 정합성 검토가 필요하고, 그건 Phase 5의 마이그레이션 범위다. 발견해두었으니 Phase 5 계획에서 다시 꺼낼 것.
- 이 저장소에는 **린터·타입체커 설정이 없다**(`pyproject.toml`/`setup.cfg`/`pytest.ini` 부재). 따라서 "타입 에러 0" 같은 게이트는 존재하지 않으며, 검증은 테스트와 `compileall`에 의존한다. 새 설정 파일을 추가하는 것은 이 Phase의 범위가 아니다.
- 어댑터가 트레이더를 **주입받는** 설계(스스로 생성하지 않음)는 Phase 3의 dry-run 시뮬레이터가 같은 자리에 다른 객체를 끼울 수 있게 해준다. 의도된 것이다.

---

*Generated: 2026-08-17*
*Source: `.claude/PRPs/prds/toss-securities-broker.prd.md` — Phase 1*

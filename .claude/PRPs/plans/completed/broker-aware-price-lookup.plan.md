# Plan: 현재가 조회를 브로커 인식으로

## Summary

`tracking/helpers.py` 의 현재가 조회가 KRX → **KIS 하드코딩** → DB 최종가 순인데, 토스 전용 설치에서는 셋 다 막힌다. 그 결과 매수 후보가 전부 탈락하고 배치가 `Purchased: 0 items` 로 끝난다. KIS 하드코딩을 브로커 팩토리로 바꾸고, 그마저 실패할 때를 위해 소스 체인을 한 단 넣는다.

## User Story

As a **KIS·KRX 자격증명이 없는 운영자**,
I want **매수 후보의 현재가가 조회되기를**,
So that **"자격증명 없이 동작한다" 가 리포트 생성에서 끝나지 않고 매매까지 이어진다.**

## Problem → Solution

KRX 로그인 필요 → KIS 403(placeholder) → DB 0(신규 후보는 행 없음) → **후보 전량 탈락**
**→** KRX → **브로커 팩토리(`PRISM_BROKER` 인식)** → **소스 체인** → DB

## Metadata
- **Complexity**: **Small** — 1 파일 + 테스트, 실질 변경 40 줄 안팎
- **Source PRD**: `.claude/PRPs/prds/toss-install-buy-path.prd.md`
- **PRD Phase**: Phase 1 — 현재가 조회를 브로커 인식으로
- **Estimated Files**: 2

---

## UX Design

**Internal change — no user-facing UX transformation.**

간접 효과: `Purchased: 0 items` 가 실제 매수 판단으로 바뀐다.

### Interaction Changes

| Touchpoint | Before | After | Notes |
|---|---|---|---|
| 배치 로그 | `current price query failed` × 후보 수 | 가격과 **어느 소스**인지 | 기존 KIS 폴백도 소스를 남긴다 |
| 매수 후보 처리 | 전량 탈락 | 판단 진행 | 이 Phase 의 목적 |
| KIS 설치 | KRX → KIS → DB | **동일** (팩토리가 KIS 를 고름) | 동작 불변이어야 한다 |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `tracking/helpers.py` | 51-105 | `get_current_stock_price` — 재시도 루프와 폴백 진입점 |
| P0 | `tracking/helpers.py` | 107-126 | `_get_price_from_kis` — **고칠 대상**. 도크스트링이 깨진 전제를 명시한다 |
| P0 | `tracking/helpers.py` | 128-150 | `_get_last_price_from_db` — 최종 폴백. 신규 후보에 0 을 주는 곳 |
| P0 | `trading/brokers/factory.py` | 162-184 | `domestic_trader()` — `PRISM_BROKER` 를 보고 KIS·토스를 고른다 |
| P1 | `cores/market_data/__init__.py` | `get_market_ohlcv_by_date` | 체인 경유 최근 종가 |
| P2 | `tests/test_price_query_retry.py` | 1-35 | 이 함수의 기존 테스트. **스크립트형이라 pytest 가 아니다** |

## External Documentation

없음. **No external research needed — feature uses established internal patterns.**

---

## Patterns to Mirror

### BROKER_AGNOSTIC_TRADER
```python
# SOURCE: trading/brokers/factory.py:162-184
def domestic_trader(**kwargs: Any) -> Any:
    """The KR trader itself, for callers that do not use the async context.

    Several call sites construct `DomesticStockTrading` directly — the Telegram
    portfolio reporter, the dashboard generators, the Stance quote provider.
    They need a trader, not a context, and before this existed they silently
    stayed on KIS no matter what `PRISM_BROKER` said.
    """
    if config.selected_broker() == config.TOSS:
        return build_toss_broker(
            "KR", mode=kwargs.get("mode"), buy_amount=kwargs.get("buy_amount")
        )

    from trading.domestic_stock_trading import DomesticStockTrading

    return DomesticStockTrading(**kwargs)
```
`helpers.py:116` 의 `from trading.domestic_stock_trading import AsyncTradingContext` 가 정확히 이 함수의 도크스트링이 말하는 "silently stayed on KIS" 사례다.

### CURRENT_PRICE_CONTRACT
```python
# SOURCE: trading/brokers/toss/adapter.py — get_current_price
            return {
                "stock_code": stock_code,
                "stock_name": self.stock_name(stock_code),
                # KIS reports KR prices as int and callers index arithmetic off
                # that; keeping the type identical avoids surprises downstream.
                "current_price": int(price) if self.market == "KR" else price,
            }
```
KIS·토스 양쪽이 `{"current_price": ...}` 를 돌려준다. 실측: 005930→271250, 000660→1680000, 042660→91800.

### LAZY_IMPORT_INSIDE_FUNCTION
```python
# SOURCE: tracking/helpers.py:113-117
    import asyncio
    try:
        from trading.domestic_stock_trading import AsyncTradingContext
        async with AsyncTradingContext() as trading:
            info = await asyncio.to_thread(trading.get_current_price, ticker)
```
이 파일은 무거운 임포트를 **함수 안에서** 한다. 유지한다 — 저장소 tripwire(`tests/test_no_module_scope_kis_import.py`)가 모듈 스코프 KIS 임포트를 금지한다.

### DEGRADE_TO_ZERO
```python
# SOURCE: tracking/helpers.py:120-126
        price = float((info or {}).get("current_price") or 0)
        if price > 0:
            logger.warning(f"{ticker} current price via KIS fallback: {price:,.0f} KRW (KRX unavailable)")
        return price
    except Exception as e:
        logger.error(f"{ticker} KIS price fallback failed: {e}")
        return 0.0
```
폴백 함수는 **예외를 올리지 않고 0.0 을 반환**한다. 호출측이 다음 단으로 넘어간다. 이 계약을 유지한다.

### FALLBACK_LOGGING
```python
        if price > 0:
            logger.warning(f"{ticker} current price via KIS fallback: {price:,.0f} KRW (KRX unavailable)")
```
폴백이 답하면 **어느 소스인지 로그에 남긴다.** 다만 이 저장소는 직전 작업에서 "복구된 실패는 WARNING 이 아니다" 로 정리했다(`cores/market_data/source.py` 의 `_announce`). 새 코드는 **INFO** 를 쓴다.

### TEST_STRUCTURE (pytest 쪽)
```python
# SOURCE: tests/test_lazy_kis_call_sites.py (이번 세션에 추가됨)
@pytest.fixture
def toss_selected(monkeypatch, tmp_path):
    monkeypatch.setenv("PRISM_BROKER", "toss")
    monkeypatch.setenv("PRISM_TRADING_MODE", "real")
    monkeypatch.setattr(settings, "load_toss_config", lambda *a, **k: {...})
    monkeypatch.setattr(settings, "KIS_CONFIG_FILE", tmp_path / "absent-kis.yaml")
```
브로커 선택은 `monkeypatch.setenv` + `settings` 모듈 패치로 세운다. `tests/conftest.py` 가 브로커 환경변수를 테스트마다 초기화하므로 명시적으로 세워야 한다.

> ⚠️ `tests/test_price_query_retry.py` 는 **pytest 파일이 아니다.** 모듈 스코프에서 `sys.exit()` 를 부르는 스크립트형이라 전체 스위트의 `--ignore` 9 개 중 하나다. 확장하지 말고 새 pytest 파일을 만든다.

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `tracking/helpers.py` | UPDATE | KIS 하드코딩 → 팩토리, 체인 폴백 추가 |
| `tests/test_broker_aware_price.py` | CREATE | 회귀 고정. 기존 스크립트형 파일은 확장 불가 |

## NOT Building

- **1 순위(KRX 전 종목 스냅샷) 교체** — PRD 의 Open Question 이다. `get_market_ohlcv_by_ticker` 는 특정일 전 종목 스냅샷이라 체인에 없는 형태이고, 바꾸면 KIS 사용자의 동작이 바뀐다. **폴백만 고친다**
- **DB 최종가 폴백 개선** — 신규 후보에 과거 가격이 없는 것은 정상이다
- **`mcp_doctor` / 설정 안내** — PRD Phase 2·3·4
- **US 경로(`prism-us/us_stock_tracking_agent.py:264`)** — 같은 이름의 함수가 있으나 yfinance 기반이고 KIS 하드코딩 문제가 없다. 확인만 하고 손대지 않는다
- **`AsyncTradingContext` 제거** — KIS 경로는 팩토리를 통해 그대로 살아 있어야 한다

---

## Step-by-Step Tasks

### Task 1: KIS 하드코딩을 브로커 팩토리로

- **ACTION**: `tracking/helpers.py:107-126` 의 `_get_price_from_kis` 를 브로커 인식으로 바꾸고 이름을 고친다
- **IMPLEMENT**:
```python
async def _get_price_from_broker(ticker: str) -> float:
    """Live quote from whichever broker this install trades through.

    This used to import `trading.domestic_stock_trading` directly and its
    docstring said KIS credentials "are already configured wherever the
    tracking agents run". That stopped being true when the broker became
    selectable: a Toss install has no KIS credentials, the call 403s, and since
    a fresh buy candidate has no `stock_holdings` row the DB fallback returns 0
    and the candidate is dropped. A batch reported `Purchased: 0 items` with all
    three candidates lost this way.

    Returns 0.0 on any failure — the caller falls through to the next source.
    """
    import asyncio

    try:
        from trading.brokers.factory import domestic_trader

        trader = domestic_trader()
        info = await asyncio.to_thread(trader.get_current_price, ticker)
        price = float((info or {}).get("current_price") or 0)
        if price > 0:
            logger.info(
                f"{ticker} current price via broker: {price:,.0f} KRW (KRX unavailable)"
            )
        return price
    except Exception as e:
        logger.error(f"{ticker} broker price lookup failed: {e}")
        return 0.0
```
- **MIRROR**: `BROKER_AGNOSTIC_TRADER` (팩토리 경유), `LAZY_IMPORT_INSIDE_FUNCTION` (함수 내 임포트 유지), `DEGRADE_TO_ZERO` (예외 대신 0.0), `FALLBACK_LOGGING` (단 INFO)
- **IMPORTS**: 함수 안의 `from trading.brokers.factory import domestic_trader` 뿐. **모듈 스코프 임포트 추가 금지** — `tests/test_no_module_scope_kis_import.py` 가 막는다
- **GOTCHA**: ① 기존 코드는 `AsyncTradingContext` 를 `async with` 로 썼다. `domestic_trader()` 는 **컨텍스트가 아니라 트레이더**를 돌려주므로 `async with` 를 쓰면 안 된다(팩토리 도크스트링이 그 구분을 설명한다). ② `mode` 를 넘기지 않는다 — 시세 조회일 뿐이고, 팩토리가 설정에서 고른다. ③ 로그를 WARNING 이 아니라 **INFO** 로. 이 저장소는 직전 작업에서 "복구된 실패는 경고가 아니다" 로 정리했다
- **VALIDATE**:
```bash
git grep -n "_get_price_from_kis" -- '*.py' || echo "  옛 이름 잔재 없음 ✓"
```

### Task 2: 소스 체인을 한 단 넣는다

- **ACTION**: 브로커도 실패했을 때 `cores.market_data` 의 최근 종가를 쓴다
- **IMPLEMENT**:
```python
def _get_price_from_chain(ticker: str) -> float:
    """Most recent close from the market-data source chain.

    A last line before the DB fallback, which returns 0 for anything not
    already held — precisely the new buy candidates this matters for. The chain
    reaches sources the broker path does not: on a Toss install it answers even
    when both KRX and the broker are unreachable.

    A close is not a live quote, and the log says so. It is still a real number
    for a real instrument, which is what the alternative — zero, meaning "drop
    this candidate" — is not.
    """
    try:
        from datetime import datetime, timedelta

        from cores.market_data import get_market_ohlcv_by_date

        end = datetime.now()
        start = end - timedelta(days=10)
        frame = get_market_ohlcv_by_date(
            start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), ticker
        )
        if frame is None or len(frame) == 0 or "Close" not in frame.columns:
            return 0.0
        price = float(frame["Close"].iloc[-1])
        if price > 0:
            logger.info(
                f"{ticker} current price via source chain: {price:,.0f} KRW "
                "(last close, not a live quote)"
            )
        return price
    except Exception as e:
        logger.error(f"{ticker} source chain price lookup failed: {e}")
        return 0.0
```
그리고 호출부(`helpers.py:100-105`)를:
```python
                # KRX exhausted — try the broker, then the source chain, before
                # the DB fallback. A new buy candidate has no stock_holdings row,
                # so the DB fallback returns 0 and the whole report analysis is
                # silently skipped (2026-07-13 KRX outage dropped all 3 afternoon
                # candidates; a Toss install dropped all 3 again on 2026-08-18,
                # for a different reason at the same spot).
                broker_price = await _get_price_from_broker(ticker)
                if broker_price > 0:
                    return broker_price
                chain_price = _get_price_from_chain(ticker)
                if chain_price > 0:
                    return chain_price
                return _get_last_price_from_db(cursor, ticker, account_key=account_key)
```
- **MIRROR**: `DEGRADE_TO_ZERO`, `FALLBACK_LOGGING` (INFO), `LAZY_IMPORT_INSIDE_FUNCTION`
- **IMPORTS**: 함수 안의 `from cores.market_data import get_market_ohlcv_by_date`
- **GOTCHA**: ① `get_market_ohlcv_by_date(start, end, ticker)` — **인자 순서가 `(시작, 종료, 티커)`** 다. 이 세션에서 이미 한 번 틀렸던 부분이다. ② 체인이 아무 소스도 못 찾으면 **빈 DataFrame** 을 돌려준다(예외가 아니다) — `len(frame) == 0` 을 봐야 한다. ③ 이 함수는 **동기**다. `asyncio.to_thread` 로 감싸지 않는다 — 체인 내부가 블로킹이지만 기존 KRX 경로도 같은 방식이고, 여기만 다르게 하면 일관성이 깨진다
- **VALIDATE**: 아래 실환경 검증 참조

### Task 3: 회귀 테스트

- **ACTION**: `tests/test_broker_aware_price.py` 를 만든다
- **IMPLEMENT**:
```python
"""The price lookup has to work on an install without KIS or KRX.

`_get_price_from_kis` imported the KIS trading module directly, and its
docstring assumed "KIS credentials are already configured wherever the tracking
agents run". A Toss install has none: the call 403s, and because a fresh buy
candidate has no `stock_holdings` row the DB fallback returns 0, so the
candidate is dropped before anything looks at it. One batch reported
`Purchased: 0 items` with all three candidates lost exactly there.

The order is KRX, then the broker, then the source chain, then the DB. Each of
the middle two exists because the one before it can be unavailable on a real
install, so both are pinned here.
"""

import asyncio

import pytest

import tracking.helpers as helpers


class FakeTrader:
    def __init__(self, price):
        self._price = price
        self.calls = []

    def get_current_price(self, ticker):
        self.calls.append(ticker)
        return {"current_price": self._price} if self._price else None


def test_the_broker_is_asked_rather_than_kis_directly(monkeypatch):
    """The factory reads PRISM_BROKER; importing the KIS module does not."""
    trader = FakeTrader(91800)
    monkeypatch.setattr(
        "trading.brokers.factory.domestic_trader", lambda **kw: trader
    )

    price = asyncio.run(helpers._get_price_from_broker("042660"))

    assert price == 91800.0
    assert trader.calls == ["042660"]


def test_a_broker_failure_degrades_to_zero_rather_than_raising(monkeypatch):
    """The caller reads 0 as "try the next source"; an exception would end the run."""
    def boom(**kwargs):
        raise RuntimeError("403")

    monkeypatch.setattr("trading.brokers.factory.domestic_trader", boom)

    assert asyncio.run(helpers._get_price_from_broker("042660")) == 0.0


def test_a_broker_with_no_quote_returns_zero(monkeypatch):
    monkeypatch.setattr(
        "trading.brokers.factory.domestic_trader", lambda **kw: FakeTrader(None)
    )

    assert asyncio.run(helpers._get_price_from_broker("042660")) == 0.0


def test_the_chain_supplies_a_last_close(monkeypatch):
    import pandas as pd

    frame = pd.DataFrame({"Close": [91700.0, 91800.0]})
    monkeypatch.setattr(
        "cores.market_data.get_market_ohlcv_by_date", lambda *a, **k: frame
    )

    assert helpers._get_price_from_chain("042660") == 91800.0


def test_an_empty_chain_result_is_zero_not_an_exception(monkeypatch):
    """The chain returns an empty frame when nothing answers, not an error."""
    import pandas as pd

    monkeypatch.setattr(
        "cores.market_data.get_market_ohlcv_by_date", lambda *a, **k: pd.DataFrame()
    )

    assert helpers._get_price_from_chain("042660") == 0.0


def test_the_chain_is_asked_with_start_end_ticker(monkeypatch):
    """Argument order, which is (start, end, ticker) and easy to get backwards."""
    seen = {}

    def capture(start, end, ticker):
        seen.update(start=start, end=end, ticker=ticker)
        import pandas as pd
        return pd.DataFrame({"Close": [1.0]})

    monkeypatch.setattr("cores.market_data.get_market_ohlcv_by_date", capture)

    helpers._get_price_from_chain("042660")

    assert seen["ticker"] == "042660"
    assert len(seen["start"]) == 8 and len(seen["end"]) == 8
    assert seen["start"] < seen["end"]


def test_no_module_scope_import_of_the_kis_trader():
    """The repo tripwire forbids it, and this file is where it used to live."""
    import ast
    from pathlib import Path

    source = Path(helpers.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    module_scope = [
        name.name
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for name in ([node] if isinstance(node, ast.ImportFrom) else node.names)
    ]
    joined = " ".join(str(getattr(n, "module", None) or getattr(n, "name", "")) for n in module_scope)

    assert "domestic_stock_trading" not in joined
```
- **MIRROR**: `TEST_STRUCTURE` (monkeypatch 로 경계를 세움), 이 저장소의 테스트 명명 관례(성질을 문장으로, 도크스트링은 막는 실패를 설명)
- **IMPORTS**: `asyncio`, `pytest`, `tracking.helpers`. `pandas` 는 테스트 안에서
- **GOTCHA**: ① `monkeypatch.setattr("trading.brokers.factory.domestic_trader", ...)` 로 **문자열 경로**를 쓴다. `helpers` 가 함수 안에서 임포트하므로 `helpers.domestic_trader` 는 존재하지 않는다. ② `asyncio.run` 을 쓴다 — 이 저장소에 `pytest-asyncio` 설정이 있는지 확인되지 않았고, 동기 래핑이 확실하다. ③ 마지막 테스트는 `tests/test_no_module_scope_kis_import.py` 와 중복처럼 보이지만, 그 tripwire 의 allowlist 가 바뀌어도 **이 파일만은 지키도록** 국소적으로 고정한다
- **VALIDATE**: `pytest tests/test_broker_aware_price.py -q` 전부 통과, 그리고 팩토리 호출을 되돌리면 첫 테스트가 실패한다

---

## Testing Strategy

### Unit Tests

| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| `test_the_broker_is_asked_rather_than_kis_directly` | 가짜 트레이더 | 91800.0, 티커 전달 | **본 결함** |
| `test_a_broker_failure_degrades_to_zero_rather_than_raising` | 예외 던지는 팩토리 | 0.0 | 계약 |
| `test_a_broker_with_no_quote_returns_zero` | `None` 반환 | 0.0 | 빈 응답 |
| `test_the_chain_supplies_a_last_close` | 종가 2행 | 마지막 값 | 신규 폴백 |
| `test_an_empty_chain_result_is_zero_not_an_exception` | 빈 DataFrame | 0.0 | **체인의 소진 표현** |
| `test_the_chain_is_asked_with_start_end_ticker` | 인자 캡처 | 순서 확인 | **이 세션에서 틀렸던 부분** |
| `test_no_module_scope_import_of_the_kis_trader` | 소스 AST | KIS 임포트 없음 | tripwire 국소 고정 |

### Edge Cases Checklist
- [x] **빈 입력** — 체인이 빈 프레임을 돌려주는 경우
- [x] **네트워크 실패** — 팩토리·체인 각각 예외 → 0.0
- [x] **잘못된 타입** — `None` 응답
- [ ] 최대 크기 / 동시 접근 — 해당 없음
- [ ] 권한 거부 — 403 은 예외로 잡혀 0.0 이 된다(테스트로 덮음)

---

## Validation Commands

### Static Analysis
```bash
.venv/bin/python -m py_compile tracking/helpers.py tests/test_broker_aware_price.py
```
EXPECT: 오류 없음

### Unit Tests
```bash
.venv/bin/python -m pytest tests/test_broker_aware_price.py -p no:cacheprovider -q
```
EXPECT: 7 passed

### 변이 검증
```bash
# _get_price_from_broker 를 옛 KIS 하드코딩으로 되돌린 뒤
.venv/bin/python -m pytest tests/test_broker_aware_price.py -q -k broker_is_asked
```
EXPECT: **실패해야 한다**

### 실환경 검증 — 토스, KIS·KRX 자격증명 없이 (읽기 전용)
```bash
cd /Users/heracles/workspace/prism-insight
PYTHONPATH=$PWD .venv/bin/python - <<'PY'
from dotenv import load_dotenv
load_dotenv("/Users/heracles/workspace/prism-insight/.env")
import asyncio, logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
import tracking.helpers as H
for tk in ("005930", "000660", "042660"):
    broker = asyncio.run(H._get_price_from_broker(tk))
    chain = H._get_price_from_chain(tk)
    print(f"  {tk}: broker={broker:,.0f}  chain={chain:,.0f}")
PY
```
EXPECT: 세 종목 모두 **broker 가 0 이 아니다.** 실측 기준값 — 005930 ≈ 271,250 / 000660 ≈ 1,680,000 / 042660 ≈ 91,800 (장중 변동하므로 **0 이 아닌지**를 본다)

### KIS 경로가 그대로인지
```bash
PYTHONPATH=$PWD PRISM_BROKER=kis .venv/bin/python -c "
import trading.brokers.settings as s
from trading.brokers.factory import domestic_trader
print('  broker =', s.selected_broker())
print('  trader =', type(domestic_trader(mode='demo')).__name__)"
```
EXPECT: `broker = kis`, `trader = DomesticStockTrading` — 팩토리가 KIS 를 고른다

### Full Test Suite
```bash
.venv/bin/python -m pytest tests/ -p no:cacheprovider --tb=no -q -rfE \
  --ignore=tests/test_agent_fit_score_constant_tripwire.py \
  --ignore=tests/test_issue_289_screening.py --ignore=tests/test_price_query_retry.py \
  --ignore=tests/test_sideways_downtrend_gate.py --ignore=tests/test_youtube_crawler.py \
  --ignore=tests/test_parallel_trading_batch.py --ignore=tests/test_screening_change_rate.py \
  --ignore=tests/test_stock_tracking_agent_process_reports.py \
  --ignore=tests/test_trigger_bearish_candle_exclusion.py
```
EXPECT: **22 failed / 10 errors** — 전부 사전 존재
> `test_price_query_retry.py` 가 `--ignore` 목록에 있다. 이 함수의 기존 테스트인데 스크립트형(모듈 스코프 `sys.exit()`)이라 수집되지 않는다 — **그래서 새 pytest 파일을 만든다**

### 그 스크립트형 테스트도 여전히 도는지
```bash
PYTHONPATH=$PWD .venv/bin/python tests/test_price_query_retry.py; echo "exit=$?"
```
EXPECT: `exit=0` — 재시도 로직을 건드리지 않았으므로 통과해야 한다

### KIS 회귀
```bash
.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_execution_service.py tests/test_async_trading.py tests/test_multi_account_domestic.py \
  tests/test_sell_quantity_guard.py tests/test_sell_denominator_sync.py tests/test_kr_pending_entry.py \
  tests/test_multi_account_kis_auth.py
```
EXPECT: **99 passed**

### Manual Validation
- [ ] 토스 설치에서 후보 3종목 현재가가 **3/3 조회**된다
- [ ] 로그가 어느 소스에서 왔는지 말한다 (broker / source chain)
- [ ] 체인 경유 값이 "last close, not a live quote" 라고 밝힌다
- [ ] `_get_price_from_kis` 라는 이름이 저장소에 남아 있지 않다
- [ ] 모듈 스코프에 KIS 임포트가 추가되지 않았다

---

## Acceptance Criteria
- [ ] Task 1-3 완료
- [ ] 토스 + 자격증명 없음에서 3/3 조회
- [ ] 신규 테스트 7 개 통과 + 변이 검증
- [ ] `test_price_query_retry.py` 스크립트가 여전히 exit 0
- [ ] KIS 회귀 99/99
- [ ] 전체 스위트 baseline(22 failed / 10 errors) 동일

## Completion Checklist
- [ ] 팩토리 경유이고 `async with` 를 쓰지 않았다
- [ ] 폴백이 예외 대신 0.0 을 돌려준다
- [ ] 로그가 INFO 이고 소스를 밝힌다
- [ ] `get_market_ohlcv_by_date` 인자 순서가 `(start, end, ticker)` 다
- [ ] 모듈 스코프 임포트 추가 없음
- [ ] 1 순위 KRX 스냅샷은 손대지 않았다
- [ ] 범위 밖 변경 없음 (PRD Phase 2·3·4)

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `domestic_trader()` 를 `async with` 로 써서 깨짐 | **M** | H | Task 1 GOTCHA. 팩토리는 컨텍스트가 아니라 트레이더를 준다 |
| `get_market_ohlcv_by_date` 인자 순서를 틀림 | **M** | M | 이 세션에서 이미 한 번 틀렸다. 전용 테스트로 고정 |
| 체인 종가를 실시간가처럼 쓰게 됨 | **M** | M | 로그에 명시. 순서상 브로커 뒤라 브로커가 살아 있으면 안 쓰인다 |
| KIS 설치의 동작이 바뀜 | **M** | **H** | 팩토리가 KIS 를 고르는지 별도 검증 + KIS 회귀 99/99 |
| 1 순위를 안 고쳐 KRX 설치는 검증 안 됨 | **H** | M | 의도된 범위. PRD Open Question |
| 이 저장소에서 매수 경로 전체를 재현 못 함 | **H** | M | 함수 단위로 검증. 배치 전체는 보고 호스트에서 |

## Notes

**이 Phase 는 매수 0건의 직접 원인만 고친다.** MCP 서버 즉사(오류 130건)는 PRD Phase 3·4 이고, 그것을 고치지 않으면 배치는 여전히 산출물이 없다. 두 가지가 독립된 원인이다.

**폴백 순서를 브로커 → 체인으로 둔 이유**: 브로커는 실시간가를 주고 체인은 종가를 준다. 매수 판단에는 실시간가가 맞다. 체인은 브로커마저 없거나 막힌 설치의 마지막 방어선이고, 0(= 후보 탈락)보다는 낫다는 판단이다.

**1 순위를 안 건드리는 것이 이 계획의 가장 큰 제약이다.** KRX 가 살아 있는 설치에서는 이 변경이 실행되지 않으므로, 그 경로의 동작 보존은 "안 건드렸다" 로만 담보된다. KIS 회귀 99/99 가 최소한의 확인이다.

**측정 환경**: `PRISM_BROKER=toss`, `PRISM_TRADING_MODE=real`, `PRISM_MARKET_DATA_SOURCES=toss,krx,fdr`. 이 호스트의 `kis_devlp.yaml` 은 example 기반이고 KRX 로그인도 실패하므로, 보고된 호스트와 같은 조건이다.

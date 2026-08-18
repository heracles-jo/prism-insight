# Plan: 토스 수급 조회 페이지 크기 분리

## Summary

`cores/market_data/toss_source.py` 의 `_PAGE = 200` 이 캔들과 투자자별 수급 두 엔드포인트에 공유되는데, 수급 엔드포인트(`/api/v1/stocks/{symbol}/investor-trading`)의 `count` 상한은 **100** 이다. 그래서 토스가 체인 첫 소스인 설치에서 수급 조회가 항상 HTTP 400 으로 실패한다. 상수를 엔드포인트별로 분리한다.

## User Story

As a **매수/매도 판단을 AI 에게 맡기는 운영자**,
I want **토스에서 투자자별 순매수 데이터가 실제로 조회되기를**,
So that **"외국인/기관 순매수 중인가"를 묻는 프롬프트가 빈 데이터 위에서 답하지 않는다.**

## Problem → Solution

`count=200` → HTTP 400 `invalid-request` → 어떤 소스도 수급을 못 줌 → 매매 판단이 근거 없이 내려짐
**→** 수급 전용 `count=100` → 페이지네이션 정상 → 2 년치 639 행 확인

## Metadata
- **Complexity**: **Small** (1 개 소스 파일 + 1 개 테스트 파일, 실질 변경 3 줄)
- **Source PRD**: `.claude/PRPs/prds/kospi-kosdaq-mcp-deauth.prd.md`
- **PRD Phase**: Phase 1 — 수급 페이지 크기 수정
- **Estimated Files**: 2

---

## UX Design

**Internal change — no user-facing UX transformation.**

간접적으로는 리포트 본문이 바뀐다. 현재 "투자자별 순매수 수량 확인 불가" 로 비어 있는 항목이 채워진다.

### Interaction Changes

| Touchpoint | Before | After | Notes |
|---|---|---|---|
| `get_stock_trading_volume` MCP 도구 | `{"error": "...어떤 소스에서도 받지 못했습니다"}` | 일자별 순매수 dict | Phase 2 전환 후 에이전트가 실제로 받게 됨 |
| 체인 로그 | `investor_flows: no source could answer (toss: ... HTTP 400 ...)` | 로그 없음 (첫 소스가 답함) | 토스가 체인 1 순위일 때 |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `cores/market_data/toss_source.py` | 33-42 | 상수 정의부. 여기를 고친다 |
| P0 | `cores/market_data/toss_source.py` | 142-184 | `_walk_candles` — `_PAGE` 사용처 ①. **200 을 유지해야 한다** |
| P0 | `cores/market_data/toss_source.py` | 233-272 | `_walk_flows` — `_PAGE` 사용처 ②. 여기만 100 |
| P1 | `tests/test_toss_source.py` | 14-66 | `candles()`, `flow_record()`, `StubClient`, `make_source()` 헬퍼 |
| P1 | `tests/test_toss_source.py` | 164-186 | 페이지네이션 테스트 2 종 — 미러할 구조 |
| P2 | `tests/test_toss_source.py` | 241-267 | 수급 테스트 기존 2 종 |

## External Documentation

| Topic | Source | Key Takeaway |
|---|---|---|
| 토스 `investor-trading` `count` 상한 | **실측 (2026-08-18)** | 100 OK / 101 FAIL. 이분 탐색으로 확정 |
| 토스 캔들 `count` | **실측** | 200 정상 (현행 `price_history` 가 동작 중) |
| 페이지 커버리지 | **실측** | `count=100` 1 페이지 = 100 거래일 ≈ 5 개월. `_MAX_PAGES=20` → 약 8 년 |

---

## Patterns to Mirror

### NAMING_CONVENTION
```python
# SOURCE: cores/market_data/toss_source.py:33-42
_CANDLES = "/api/v1/candles"
_INDICATOR_CANDLES = "/api/v1/market-indicators/{symbol}/candles"
_INVESTOR_TRADING = "/api/v1/stocks/{symbol}/investor-trading"
_STOCKS = "/api/v1/stocks"

# One request's worth. Toss paginates with `before`/`nextBefore`, so this only
# sets how many round trips a long range costs.
_PAGE = 200
_MAX_PAGES = 20
```
모듈 상수는 `_UPPER_SNAKE`, 앞에 **왜 그 값인지**를 설명하는 주석이 붙는다. 값만 두지 않는다.

### ERROR_HANDLING
```python
# SOURCE: cores/market_data/toss_source.py:186-192
@staticmethod
def _clip(frame: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    window = frame[
        (frame.index >= pd.Timestamp(start)) & (frame.index <= pd.Timestamp(end))
    ]
    if window.empty:
        raise Unavailable("Toss returned nothing inside the requested range")
    return window
```
빈 프레임을 반환하지 않고 `Unavailable` 을 올린다 — 체인이 다음 소스로 넘어가게 하기 위해서. 이 규칙은 건드리지 않는다.

### PAGINATION_LOOP
```python
# SOURCE: cores/market_data/toss_source.py:153-156
for _ in range(_MAX_PAGES):
    page_params = dict(params, count=_PAGE)
    if before:
        page_params["before"] = before
```
```python
# SOURCE: cores/market_data/toss_source.py:238-241
for _ in range(_MAX_PAGES):
    params: dict[str, Any] = {"count": _PAGE}
    if until:
        params["until"] = until
```
두 워커가 **커서 이름이 다르다** (`before`/`nextBefore` vs `until`/`nextUntil`). 상수만 공유했을 뿐 별개 루프다.

### TEST_STRUCTURE
```python
# SOURCE: tests/test_toss_source.py:44-66
class StubClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def request(self, method, path, *, params=None, **kwargs):
        self.calls.append((method, path, params))
        for key, value in self.responses.items():
            if path.startswith(key):
                if isinstance(value, Exception):
                    raise value
                return value(params) if callable(value) else value
        raise AssertionError(f"unexpected path {path}")


def make_source(responses):
    from cores.market_data.toss_source import TossSource

    client = StubClient(responses)
    return TossSource(client), client
```
`client.calls` 에 `(method, path, params)` 가 쌓이므로 **보낸 파라미터를 직접 검증할 수 있다.** 이번 테스트의 핵심 도구다.

### TEST_NAMING
```python
# SOURCE: tests/test_toss_source.py:178-186
def test_pagination_stops_when_the_cursor_stops_moving():
    """A provider repeating its cursor must not spin forever."""
```
테스트 이름은 **주장하는 성질을 문장으로** 쓴다. 도크스트링은 그 성질이 왜 중요한지(어떤 실패를 막는지) 한 줄.

### FLOW_FIXTURE
```python
# SOURCE: tests/test_toss_source.py:33-41
def flow_record(date, individual="291850", foreigner="-319700", institution="37900",
                other="1000"):
    def block(v):
        return None if v is None else {"buyVolume": "0", "sellVolume": "0", "netBuyVolume": v}
    return {
        "date": date, "updatedAt": f"{date}T15:40:00+09:00",
        "individual": block(individual), "foreigner": block(foreigner),
        "institution": block(institution), "otherCorporation": block(other),
    }
```

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `cores/market_data/toss_source.py` | UPDATE | `_PAGE` 를 `_CANDLE_PAGE` / `_FLOW_PAGE` 로 분리, 두 사용처 갱신 |
| `tests/test_toss_source.py` | UPDATE | 상한 초과를 막는 회귀 테스트 추가 |

## NOT Building

- **`_MAX_PAGES` 조정** — 20 페이지 × 100 = 약 8 년으로 충분함이 실측됐다. 건드릴 이유가 없다
- **다른 엔드포인트의 `count` 감사** — `_INDICATOR_CANDLES` 는 `_walk_candles` 를 공유하므로 캔들 상수를 따른다. 별도 조사는 이번 범위 밖
- **MCP 서버 전환** — PRD Phase 2 의 일이다. 이 Phase 는 체인 내부만 고친다
- **`get_stock_trading_volume` 의 오류 메시지 개선** — PRD Phase 4
- **KIS/네이버 소스의 수급 경로** — 토스만 고친다

---

## Step-by-Step Tasks

### Task 1: 상수를 엔드포인트별로 분리

- **ACTION**: `cores/market_data/toss_source.py:40-42` 의 `_PAGE` 를 두 상수로 나눈다
- **IMPLEMENT**:
```python
# One request's worth. Toss paginates with `before`/`nextBefore`, so this only
# sets how many round trips a long range costs.
_CANDLE_PAGE = 200

# Investor trading caps `count` at 100 and 400s above it — measured, 100 OK and
# 101 rejected. Sharing the candle page size sent 200 and made every flow
# lookup fail with `invalid-request`, which read as "Toss has no flow data"
# rather than "the request was malformed". One page is ~5 months of trading
# days, so `_MAX_PAGES` still reaches back about eight years.
_FLOW_PAGE = 100

_MAX_PAGES = 20
```
- **MIRROR**: `NAMING_CONVENTION` — 상수 위에 **왜 그 값인지** 주석. 값만 두지 않는다
- **IMPORTS**: 없음
- **GOTCHA**: `_PAGE` 라는 이름을 남겨두지 말 것. 남기면 다음 사람이 어느 쪽인지 모른 채 쓴다. 완전히 제거한다
- **VALIDATE**: `git grep -n "_PAGE" cores/market_data/toss_source.py` 가 `_CANDLE_PAGE`/`_FLOW_PAGE`/`_MAX_PAGES` 만 보여야 한다

### Task 2: `_walk_candles` 사용처 갱신

- **ACTION**: `cores/market_data/toss_source.py:154` 를 `_CANDLE_PAGE` 로 바꾼다
- **IMPLEMENT**: `page_params = dict(params, count=_CANDLE_PAGE)`
- **MIRROR**: `PAGINATION_LOOP` (캔들 쪽)
- **IMPORTS**: 없음
- **GOTCHA**: 여기는 **값을 바꾸지 않는다.** 200 이 정상 동작 중이다. 이름만 바뀐다
- **VALIDATE**: `pytest tests/test_toss_source.py -k pagination` 통과 (기존 캔들 페이지네이션 테스트 2 종)

### Task 3: `_walk_flows` 사용처 갱신

- **ACTION**: `cores/market_data/toss_source.py:239` 를 `_FLOW_PAGE` 로 바꾼다
- **IMPLEMENT**: `params: dict[str, Any] = {"count": _FLOW_PAGE}`
- **MIRROR**: `PAGINATION_LOOP` (수급 쪽 — 커서가 `until`/`nextUntil` 이다)
- **IMPORTS**: 없음
- **GOTCHA**: 이 루프의 커서는 `before` 가 아니라 `until` 이다. 캔들 루프와 헷갈리지 말 것
- **VALIDATE**: 아래 실계좌 검증 참조

### Task 4: 상한을 고정하는 회귀 테스트

- **ACTION**: `tests/test_toss_source.py` 의 수급 테스트 구역(241 행 이후)에 추가한다
- **IMPLEMENT**:
```python
def test_flow_requests_stay_within_the_api_count_limit():
    """Toss 400s on count > 100 for investor trading, and 400 reads as no data.

    The candle endpoint takes 200, so sharing one page-size constant sent 200
    here too and every flow lookup failed. The chain then reported that no
    source could answer, which is indistinguishable from a stock nobody traded.
    """
    source, client = make_source({
        "/api/v1/stocks/005930/investor-trading": {
            "records": [flow_record("2026-08-18")], "nextUntil": None,
        }
    })

    source.investor_flows("005930", "20260818", "20260818")

    counts = [params["count"] for _method, _path, params in client.calls]
    assert counts, "no request was made"
    assert max(counts) <= 100


def test_candle_requests_still_use_the_larger_page():
    """Splitting the constant must not quietly halve candle throughput."""
    source, client = make_source({
        "/api/v1/candles": {"candles": candles(["2026-08-18"]), "nextBefore": None}
    })

    source.price_history("005930", "20260818", "20260818")

    assert [params["count"] for _m, _p, params in client.calls] == [200]
```
- **MIRROR**: `TEST_STRUCTURE` (`client.calls` 로 보낸 파라미터 검증), `TEST_NAMING` (성질을 문장으로, 도크스트링은 막는 실패를 설명)
- **IMPORTS**: 파일 상단에 이미 있는 `candles`, `flow_record`, `make_source` 를 그대로 쓴다. 새 임포트 없음
- **GOTCHA**: 상한을 `_FLOW_PAGE` 상수와 비교하지 말 것 — 상수를 200 으로 되돌려도 테스트가 같이 따라가서 통과한다. **리터럴 100 과 비교해야** 회귀를 잡는다
- **VALIDATE**: 두 테스트가 통과하고, `_FLOW_PAGE` 를 200 으로 되돌리면 첫 번째가 실패해야 한다 (변이 검증)

---

## Testing Strategy

### Unit Tests

| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| `test_flow_requests_stay_within_the_api_count_limit` | 수급 1 일 조회 | 보낸 `count` 최대값 ≤ 100 | 회귀 방지 |
| `test_candle_requests_still_use_the_larger_page` | 캔들 1 일 조회 | 보낸 `count` == 200 | 분리 부작용 방지 |
| 기존 `test_investor_flows_use_the_exchange_column_names` | 스텁 수급 | 한글 컬럼 유지 | 회귀 |
| 기존 `test_a_provisional_same_day_record_is_excluded_from_history` | 잠정 레코드 | 제외됨 | 회귀 |
| 기존 `test_pagination_walks_back_until_the_window_is_covered` | 캔들 2 페이지 | 4 행 | 회귀 |

### Edge Cases Checklist
- [x] **최대 크기 입력** — `count` 상한이 이 변경의 주제 그 자체
- [x] **페이지네이션 경계** — 기존 커서 정지 테스트가 이미 덮는다
- [ ] 빈 입력 — 해당 없음 (상수 변경)
- [ ] 잘못된 타입 — 해당 없음
- [ ] 동시 접근 — 해당 없음 (상수는 읽기 전용)
- [x] **네트워크 실패** — 기존 `test_an_api_failure_becomes_unavailable` 이 덮는다
- [ ] 권한 거부 — 해당 없음

---

## Validation Commands

### Static Analysis
```bash
.venv/bin/python -m py_compile cores/market_data/toss_source.py tests/test_toss_source.py
```
EXPECT: 오류 없음 (저장소에 타입체커·린터 설정 없음)

### Unit Tests
```bash
.venv/bin/python -m pytest tests/test_toss_source.py -p no:cacheprovider -q
```
EXPECT: 전부 통과 (기존 24 개 + 신규 2 개)

### 변이 검증 (테스트가 실제로 결함을 잡는지)
```bash
# _FLOW_PAGE 를 일시적으로 200 으로 되돌린 뒤
.venv/bin/python -m pytest tests/test_toss_source.py -k count_limit -q
```
EXPECT: **실패해야 한다.** 통과하면 테스트가 무의미하다

### 실계좌 검증 (읽기 전용, 주문 없음)
```bash
PYTHONPATH=$PWD .venv/bin/python - <<'PY'
from dotenv import load_dotenv
load_dotenv("/Users/heracles/workspace/prism-insight/.env")
import logging; logging.basicConfig(level=logging.ERROR)
from cores.market_data.toss_source import TossSource
src = TossSource()
for start, end, label in (("20260811","20260818","1주"),
                          ("20260101","20260818","연초부터"),
                          ("20240101","20260818","2년+")):
    df = src.investor_flows("005930", start, end)
    print(f"{label:8} rows={len(df):4} {df.index.min().date()}~{df.index.max().date()} {list(df.columns)}")
PY
```
EXPECT (수정 전 실측값과 일치해야 함):
```
1주       rows=   5 2026-08-11~2026-08-18 ['기관합계', '외국인합계', '개인', '기타법인']
연초부터     rows= 153 2026-01-02~2026-08-18 [...]
2년+      rows= 639 2024-01-02~2026-08-18 [...]
```

### 체인 경유 검증
```bash
PYTHONPATH=$PWD .venv/bin/python - <<'PY'
from dotenv import load_dotenv
load_dotenv("/Users/heracles/workspace/prism-insight/.env")
import logging; logging.basicConfig(level=logging.ERROR)
import cores.market_data.mcp_server as m
r = m.get_stock_trading_volume("20260811", "20260818", "005930")
r = r.fn() if hasattr(r, "fn") else r
print("error" in r if isinstance(r, dict) else "?", str(r)[:120])
PY
```
EXPECT: `False` — 즉 `{"error": ...}` 가 아니라 실데이터. **인자 순서는 `(start, end, ticker)` 다**

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
EXPECT: **22 failed / 10 errors** — 전부 사전 존재. 이 숫자를 넘으면 회귀다
> 9 개 `--ignore` 는 사전 존재 문제다: 4 개는 모듈 스코프 `sys.exit()`, 1 개는 없는 모듈 임포트, 4 개는 `.env` 가 채워진 뒤 멈춘다

### KIS 회귀
```bash
.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_execution_service.py tests/test_async_trading.py tests/test_multi_account_domestic.py \
  tests/test_sell_quantity_guard.py tests/test_sell_denominator_sync.py tests/test_kr_pending_entry.py \
  tests/test_multi_account_kis_auth.py
```
EXPECT: **99 passed**

### Manual Validation
- [ ] `git grep -n "_PAGE\b" cores/` 결과에 `_PAGE` 단독 이름이 남아 있지 않다
- [ ] 실계좌 검증의 3 개 범위가 모두 위 기대값과 일치한다
- [ ] 변이 검증에서 테스트가 실제로 red 로 뒤집힌다

---

## Acceptance Criteria
- [ ] Task 1-4 완료
- [ ] `tests/test_toss_source.py` 전부 통과 (기존 + 신규 2)
- [ ] 변이 검증에서 신규 테스트가 red 로 뒤집힌다
- [ ] 실계좌 3 개 범위가 기대 행 수와 일치
- [ ] 체인 경유 `get_stock_trading_volume` 이 `error` 를 반환하지 않는다
- [ ] KIS 회귀 99/99
- [ ] 전체 스위트가 baseline(22 failed / 10 errors)과 동일

## Completion Checklist
- [ ] 상수에 **왜 그 값인지** 주석이 붙었다 (모듈 관례)
- [ ] 캔들 쪽 값이 200 그대로다
- [ ] 테스트가 상수가 아닌 리터럴과 비교한다
- [ ] `_PAGE` 라는 모호한 이름이 남지 않았다
- [ ] 새 임포트 없음
- [ ] 범위 밖 변경 없음 (MCP 전환은 Phase 2)

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| 테스트가 상수를 참조해 회귀를 못 잡는다 | **M** | H | 리터럴 100 과 비교. 변이 검증을 완료 조건에 넣음 |
| 다른 엔드포인트도 상한이 다르다 | L | M | `_INDICATOR_CANDLES` 는 `_walk_candles` 를 공유하므로 캔들 상수를 따른다. 현재 정상 동작 확인됨 |
| 실계좌 검증이 장 시간에 따라 행 수가 달라진다 | **M** | L | 당일(2026-08-18) 행 포함 여부로 ±1 이 날 수 있다. 정확한 일치가 아니라 **범위와 컬럼**을 본다 |
| 페이지 수 증가로 요청량 2 배 | L | L | 수급은 `DEFAULT` 레이트리밋 그룹이고 20 페이지 상한이 그대로다 |

## Notes

**PRD 의 미해결 질문 하나가 이 조사에서 해소됐다.** PRD 는 "토스 `investor-trading` 이 삼성전자 기준 **2 행만** 반환했다" 를 High 위험으로 적었으나, 그것은 측정 오류였다 — 응답 dict 의 키 개수(`records`, `nextUntil`)를 행 수로 읽었다. 실제로는 1 페이지에 100 레코드가 오고 2 년 범위에서 639 행이 나온다. **PRD 의 해당 Open Question 과 Technical Risk 는 구현 시 함께 정정할 것.**

**왜 이 Phase 가 Phase 2 보다 먼저이거나 최소한 동시여야 하는가**: MCP 서버만 전환하면 `get_stock_ohlcv`·`get_stock_market_cap`·`get_index_ohlcv`·`get_ticker_name` 는 살아나지만 `get_stock_trading_volume` 은 그대로 실패한다. 그리고 수급은 매수/매도 스페셜리스트가 쓰는 데이터다. 두 Phase 가 다 끝나야 "자격증명 없이 전 도구 동작" 이 성립한다.

**측정 환경**: `PRISM_BROKER=toss`, `PRISM_TRADING_MODE=real`, `PRISM_MARKET_DATA_SOURCES=toss,krx,fdr`. KRX 는 이 머신에서 로그인 실패 중이므로 체인이 토스에 의존한다 — 이 버그가 그대로 노출되는 조건이다.

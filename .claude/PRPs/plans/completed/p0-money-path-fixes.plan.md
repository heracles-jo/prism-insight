# Plan: P0 실금전 수정 (Phase 2)

## Summary
Toss 실계좌 운영에 직접 영향을 주는 P0 결함 6건을 수정한다: ① 매수 금액 설정 체인 연결, ② 강제청산 감지의 조용한 실패 제거, ③ US 소수점 매도 + 포트 외 메서드 호출, ④ DB 경로 cwd 비의존화, ⑤ US 트래킹 에이전트의 모듈 스코프 KIS 로드 지연화(+테스트 우회 제거), ⑥ dry-run DB 경로 절대화. Phase 1이 고정한 **strict xfail 4건 중 3건과 KNOWN 항목 3건이 이 수정으로 green 전환**되어야 하며, xfail 마크/KNOWN 목록 제거가 완료 판정이다.

## User Story
As a Toss 실계좌 운영자, I want 설정한 매수 금액이 실제 주문에 반영되고 매도 안전장치가 전 구간 작동하기를, so that 무인 운영 중 자금 배분과 청산이 의도대로 동작한다.

## Problem → Solution
설정 파일 사장·조용한 실패·정수 절삭·cwd 의존 → 기존 단일 지점(`settings.buy_amount`, `selected_broker`, Decimal 계약, `Path(__file__)` 기준 경로)으로 수렴.

## Metadata
- **Complexity**: Large
- **Source PRD**: `.claude/PRPs/prds/full-migration-audit.prd.md`
- **PRD Phase**: Phase 2 — P0 실금전 수정
- **Estimated Files**: ~15 (프로덕션 11, 테스트 4)

---

## UX Design
N/A — internal change. (단, 대시보드/로그의 매수 금액이 설정값을 따르게 되는 행동 변화 있음)

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `trading/brokers/factory.py` | 75-135 | 수정 지점 ①: `:107` |
| P0 | `trading/brokers/settings.py` | 82-160, 209-235 | `buy_amount()` 체인(이미 완성, 미사용)·`_TRADING_DEFAULTS`(usd 키 존재) |
| P0 | `stock_tracking_agent.py` | 104-136, 504-527, 3612-3628 | 지연 로드 미러 원본·계좌 dict·KR 플로어 블록 |
| P0 | `prism-us/us_stock_tracking_agent.py` | 195-230, 842-888, 2790-2810, 2870-2895, 3355-3372 | 수정 지점 ⑤·계좌 dict·`is_market_open` 호출·fractional 소비·US 파일럿 |
| P0 | `prism-us/tracking/db_schema.py` | 1104-1120 | 수정 지점 ③ 함수 원문 |
| P1 | `trading/brokers/toss/adapter.py` | 88-115, 295-390 | `buy_amount` 소비·US 세션 판정(`open_us_session`) — `is_market_open` 재료 |
| P1 | `cores/corporate_status.py` | 70-99 | 수정 지점 ② 함수 원문 |
| P1 | `prism_core/time_windows.py` | all (43줄) | KR 장시간 canonical — Toss KR `is_market_open` 재료 |
| P1 | `prism-us/tests/test_issue_448_distribution_days_prompt.py` | 55-101 | 제거할 monkeypatch 우회 |
| P2 | `tests/test_migration_audit_scans.py` | all | 제거할 xfail/KNOWN 항목 위치 |
| P2 | `tests/test_no_module_scope_kis_import.py` | US_ENTRY_POINTS, KNOWN_PATH_LOAD_OFFENDERS | 동일 |
| P2 | `cores/regime_policy.py` | 297-317 | `configured_entry_amount`의 키 계약 (`buy_amount_krw`/`buy_amount_usd`) |
| P2 | `trading/brokers/toss/dryrun.py` | 44-52 | 수정 지점 ⑥ |

## External Documentation
없음 — "No external research needed — feature uses established internal patterns."

---

## Patterns to Mirror

### LAZY_KIS_IMPORT + MASK_COPY (⑤의 미러 원본)
```python
# SOURCE: stock_tracking_agent.py:104-136
def _mask_account_number(account_number: str | None) -> str:
    """... A copy of `kis_auth.mask_account_number`, which is pure string work but sits
    in a module that reads kis_devlp.yaml on import. ..."""
    if not account_number:
        return ""
    account_str = str(account_number)
    if len(account_str) <= 4:
        return "*" * len(account_str)
    return f"{account_str[:2]}{'*' * (len(account_str) - 4)}{account_str[-2:]}"

def _kis_auth():
    """KIS auth helpers, loaded on demand. ..."""
    from trading import kis_auth as ka
    return ka
```
주의: US 쪽은 `prism-us/trading/`이 root `trading`을 가리므로 `from trading import kis_auth` 불가 — 기존 `spec_from_file_location` 방식을 **함수 안으로** 옮기고 `sys.modules` 캐시(`_load_root_broker_settings()`(`:202-221`)와 동일 캐시 패턴)를 쓴다.

### BROKER_GATE (②의 미러)
```python
# SOURCE: stock_tracking_agent.py:518-522
from trading.brokers.settings import primary_account_scope, selected_broker, TOSS
if selected_broker() == TOSS:
    ...toss branch...
```

### SETTINGS_BUY_AMOUNT (①이 연결할 기존 체인)
```python
# SOURCE: trading/brokers/settings.py:151-157
def buy_amount(market: str) -> int:
    """Per-order budget: environment, then the broker's file, then the default."""
    from_env = toss_buy_amount(market)
    if from_env is not None:
        return from_env
    key = "default_unit_amount" if market.upper() == "KR" else "default_unit_amount_usd"
    return int(trading_settings()[key])
```

### DB_PATH_DERIVED (④의 미러)
```python
# SOURCE: weekly_insight_report.py:25
DB_PATH = str(Path(__file__).parent / "stock_tracking_db.sqlite")
```

### TOSS_DECIMAL (③의 수량 규약)
```python
# SOURCE: trading/brokers/toss/adapter.py:73-85, 347-351
def _dec(value, default="0") -> Decimal: ...   # 수량은 float 금지, Decimal 유지
FRACTIONAL_SCALE = Decimal("0.000001")          # 6자리, ROUND_DOWN
```

### TEST_STRUCTURE
`tests/test_broker_selection.py` 스타일: monkeypatch.setenv/delenv + 서술형 테스트명. root `tests/conftest.py`·`prism-us/tests/conftest.py` 모두 브로커 env 자동 초기화됨(Phase 1).

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `trading/brokers/factory.py` | UPDATE | ① `:107` env-only → `config.buy_amount(market)` |
| `stock_tracking_agent.py` | UPDATE | ① 계좌 dict `buy_amount_krw` + ④ `db_path` 기본값 |
| `prism-us/us_stock_tracking_agent.py` | UPDATE | ① `buy_amount_usd` + ④ 기본값 + ⑤ 지연 로드 + mask 사본 |
| `cores/corporate_status.py` | UPDATE | ② 브로커 게이트 + 명시 로그 |
| `prism-us/tracking/db_schema.py` | UPDATE | ③ `compute_us_fractional_sell_quantity` Decimal화 |
| `trading/brokers/toss/adapter.py` | UPDATE | ③ `is_market_open`/`is_reserved_order_available` 구현 |
| `stock_tracking_enhanced_agent.py` | UPDATE | ④ `db_path` 기본값 |
| `compress_trading_memory.py` | UPDATE | ④ 3곳 |
| `retry_journal_entry.py` | UPDATE | ④ |
| `tools/compare_position_ledger.py` | UPDATE | ④ |
| `cores/llm/features/trade_history.py` | UPDATE | ④ |
| `trading/brokers/toss/dryrun.py` | UPDATE | ⑥ `DEFAULT_DB_PATH` 절대화 |
| `prism-us/tests/test_issue_448_distribution_days_prompt.py` | UPDATE | ⑤ monkeypatch 우회 제거 |
| `tests/test_no_module_scope_kis_import.py` | UPDATE | xfail/KNOWN 축소 (⑤ 완료 증명) |
| `tests/test_migration_audit_scans.py` | UPDATE | xfail/KNOWN 축소 (①③ 완료 증명) |
| `tests/test_p0_money_path.py` | CREATE | ①②③ 회귀 테스트 |
| `prism-us/tests/test_fractional_sell_quantity.py` | CREATE | ③ 단위 테스트 |

## NOT Building
- BrokerPort 프로토콜에 `is_market_open` 추가 — **KIS KR 트레이더(`domestic_stock_trading.py`)에 해당 메서드가 없음**(grep 확인). 포트 선언은 KIS KR을 계약 위반으로 만들므로 TossBroker 구현으로만 해결. fill_chaser의 `get_revisable_orders`/`get_unfilled_orders` 갭은 Phase 5.
- KIS US 트레이더에 `get_holding_quantity_checked` 추가 (Phase 5)
- Toss용 상장폐지/거래정지 **탐지 구현** — ②는 조용한 실패를 명시적 스킵 로그로 바꾸는 것까지. 탐지 자체는 KIS 전용 유지(후속 검토)
- DB 파일명 문자열의 단일 상수 모듈화 (Phase 6 드리프트 청소에서 — 여기서는 각 파일을 `Path(__file__)` 기준으로만 전환)

---

## Step-by-Step Tasks

### Task 1: 매수 금액 체인 연결 (①-a)
- **ACTION**: `trading/brokers/factory.py:107` 수정
- **IMPLEMENT**:
  ```python
  amount = buy_amount if buy_amount is not None else config.buy_amount(market)
  ```
  `config.buy_amount()`는 env→브로커 파일→기본값으로 **항상 int를 반환**하므로 `:111`의 `if amount is not None` 분기는 유지해도 무해(항상 전달됨).
- **MIRROR**: SETTINGS_BUY_AMOUNT
- **GOTCHA**: `toss_buy_amount()` 직접 호출을 남기지 말 것 — env 전용이라 사장의 원인이었음. factory 모듈의 `config`가 `trading.brokers.settings`의 별칭인지 import부에서 확인.
- **VALIDATE**: `tests/test_migration_audit_scans.py::test_the_configured_buy_amount_reaches_the_order_path`가 **XPASS로 실패** → Task 9에서 xfail 마크 제거

### Task 2: Toss 계좌 dict에 매수 금액 채움 (①-b)
- **ACTION**: KR `stock_tracking_agent.py:518-522`, US `prism-us/us_stock_tracking_agent.py:853-856`
- **IMPLEMENT**: KR —
  ```python
  from trading.brokers.settings import buy_amount, primary_account_scope, selected_broker, TOSS
  if selected_broker() == TOSS:
      account_key, name, product, _mode = primary_account_scope("kr")
      return [{"account_key": account_key, "name": name, "product": product,
               "buy_amount_krw": buy_amount("kr")}]
  ```
  US — `settings = _load_root_broker_settings()` 경유이므로:
  ```python
  return [{"account_key": account_key, "name": name, "product": product,
           "buy_amount_usd": settings.buy_amount("us")}]
  ```
- **MIRROR**: BROKER_GATE. 키 이름은 `cores/regime_policy.py:308`의 계약(`buy_amount_krw`/`buy_amount_usd`)과 정확히 일치해야 함 — KIS 쪽은 `kis_auth`가 같은 키를 채움.
- **GOTCHA**: KR 파일럿은 값 없으면 **매수 차단**(`_regime_floor_block=True`, `:3616-3625`), US는 파일럿 강등(`:3359-3369`) — 두 동작 모두 이 값 공급으로 정상화되며 다른 코드는 건드리지 않는다.
- **VALIDATE**: Task 10의 단위 테스트 + `configured_entry_amount({...,"buy_amount_krw":100000}, "kr", 0.5) == 50000`

### Task 3: 강제청산 감지 조용한 실패 제거 (②)
- **ACTION**: `cores/corporate_status.py:70-98` `fetch_status_codes` 수정
- **IMPLEMENT**: `uniq` 계산 직후, KIS 임포트 **이전**에:
  ```python
  from trading.brokers.settings import selected_broker, TOSS
  if selected_broker() == TOSS:
      logger.warning(
          "종목상태코드 자동탐지는 KIS 전용입니다 — broker=toss에서는 건너뜁니다 "
          "(상장폐지/거래정지 TIER0 자동탐지 비활성, 매도 본로직은 정상)"
      )
      return out
  ```
  기존 KIS 경로·예외 처리는 무수정.
- **MIRROR**: BROKER_GATE. 로그 문구는 이 파일의 기존 한국어 로그 스타일(`:95-97`)을 따름.
- **GOTCHA**: 기존 `except Exception` 광역 처리(`:96`)는 KIS 설치의 일시 장애 보호용이므로 유지 — 게이트는 그 앞단에 추가만.
- **VALIDATE**: 신규 테스트 — `PRISM_BROKER=toss`에서 `fetch_status_codes(["005930"])`가 `{}` 반환 + caplog에 "KIS 전용" 경고 + `sys.modules`에 `trading.domestic_stock_trading` 미등장

### Task 4: US 소수점 매도 수량 Decimal화 (③-a)
- **ACTION**: `prism-us/tracking/db_schema.py:1104-1119` 재작성
- **IMPLEMENT**:
  ```python
  def compute_us_fractional_sell_quantity(total_quantity, remaining_rows):
      """Shares to sell for one row when ``remaining_rows`` rows remain (US, #288).

      Quantities may be fractional under Toss (Decimal); integral totals keep the
      original integer arithmetic so KIS behaviour is unchanged. int(Decimal("0.44"))
      is 0 — the truncation this replaces reported a held position as nothing to sell.
      """
      from decimal import Decimal, InvalidOperation, ROUND_DOWN
      try:
          total = Decimal(str(total_quantity))
          n = int(remaining_rows)
      except (InvalidOperation, TypeError, ValueError):
          try:
              return int(total_quantity) if total_quantity else 0
          except (TypeError, ValueError):
              return 0
      if total <= 0:
          return 0
      if total == total.to_integral_value():          # KIS/integer path: unchanged
          total_i = int(total)
          return total_i if n <= 1 else total_i // n
      if n <= 1:
          return total                                 # sweep the exact remainder
      return (total / n).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
  ```
- **MIRROR**: TOSS_DECIMAL (6자리 ROUND_DOWN — 어댑터 `FRACTIONAL_SCALE`과 동일 스케일)
- **GOTCHA**: 반환 타입이 int|Decimal 혼합 — 소비부 `us_stock_tracking_agent.py:2885-2888`의 `pass_sold_qty[ticker] += sell_quantity`는 int 0에서 시작하므로 Decimal과의 `+=`는 동작하나, **초기값을 `Decimal("0")`으로** 바꿔 타입 일관 유지(`:2885`). float 사용 금지(어댑터 `_dec` docstring 사유).
- **VALIDATE**: 신규 `prism-us/tests/test_fractional_sell_quantity.py` — 표: `(10,3)→3`, `(10,1)→10`, `(Decimal("0.44"),1)→Decimal("0.44")`, `(Decimal("1.68"),2)→Decimal("0.84")`, `(Decimal("0.0000019"),2)→Decimal("0.000000")` 절삭, `(0,n)→0`, `("junk",2)→0`

### Task 5: TossBroker에 세션 판정 메서드 구현 (③-b)
- **ACTION**: `trading/brokers/toss/adapter.py`의 US session gating 절(`:295` 인근)에 추가
- **IMPLEMENT**:
  ```python
  def is_market_open(self, *, now: datetime.datetime | None = None) -> bool:
      """True while an order can be placed right now.

      US: any of the four Toss sessions (the KIS US trader answers the same
      question for exchange hours; here the day market makes Korean daytime
      tradeable). KR: the canonical regular window — closing-price orders are
      unsupported on Toss, so only 'regular' counts as open.
      """
      if self.market == "US":
          return self.open_us_session(now=now) is not None
      from prism_core.time_windows import domestic_order_window
      return domestic_order_window(now) == "regular"

  def is_reserved_order_available(self, *, now: datetime.datetime | None = None) -> bool:
      """Always False: Toss has no time-based reserved orders (BrokerUnsupported
      on the order methods); callers that would queue must take their fallback."""
      return False
  ```
- **MIRROR**: `open_us_session`(`:299-343`)의 시그니처 관례(`*, now=None`)와 docstring 톤
- **GOTCHA**: ① `us_stock_tracking_agent.py:2802`의 소비 로직상 Toss US 데이마켓 중에는 `will_queue=False` → fractional 판이 정상 진행. 세션 밖(07-09 KST)엔 `will_queue=True` → full_exit 선택 후 주문이 명시 실패(`success=False`) — 큐가 없는 브로커의 올바른 폴백. ② KR은 `domestic_order_window`의 `closing`/`reserved`를 open으로 치지 않는다(Toss 미지원). ③ `datetime`은 파일 상단에 이미 임포트됨.
- **VALIDATE**: `tests/test_migration_audit_scans.py::test_broker_port_method_calls_match_the_implementations`가 stale 검사로 실패 → Task 9에서 KNOWN_CONTRACT_GAPS 2건 제거. 신규 단위 테스트(Task 10): `now` 주입으로 KR regular/휴장, US 세션 mock.

### Task 6: US 트래킹 에이전트 지연 KIS 로드 (⑤)
- **ACTION**: `prism-us/us_stock_tracking_agent.py:195-199` 모듈 스코프 로드 제거, `:858-860`·`:885-887`의 `ka` 사용처 치환
- **IMPLEMENT**: `:195-199`를 다음으로 교체:
  ```python
  # trading/kis_auth.py is loaded on demand: it reads kis_devlp.yaml at module
  # scope, so loading it up here made this agent unstartable on a Toss-only
  # install (audit P0 #5). Loaded by file path because prism-us/trading/
  # shadows the root trading package — same idiom as _load_root_broker_settings.
  import importlib.util as _importlib_util

  def _kis_auth():
      module_name = "kis_auth"
      cached = sys.modules.get(module_name)
      if cached is not None:
          return cached
      spec = _importlib_util.spec_from_file_location(
          module_name, PROJECT_ROOT / "trading/kis_auth.py"
      )
      module = _importlib_util.module_from_spec(spec)
      sys.modules[module_name] = module
      spec.loader.exec_module(module)
      return module
  ```
  사용처: `:858` `ka.getEnv()` → `_kis_auth().getEnv()`, `:860` 동일. `:885,887`의 `ka.mask_account_number` → 파일 상단 헬퍼 절에 `_mask_account_number` **사본 추가**(LAZY_KIS_IMPORT + MASK_COPY의 KR 원문 그대로, docstring에 "copy of kis_auth.mask_account_number / stock_tracking_agent._mask_account_number" 명시) 후 치환. 모듈 스코프의 `ka = ...` 이름은 완전 제거(다른 사용처 없는지 `grep -n '\bka\.' prism-us/us_stock_tracking_agent.py`로 확인 — 조사 기준 4곳이 전부).
- **MIRROR**: LAZY_KIS_IMPORT + MASK_COPY, `_load_root_broker_settings`(`:202-221`)의 sys.modules 캐시
- **GOTCHA**: ① `sys.modules["kis_auth"]` 캐시 이름을 기존과 동일하게 유지 — census 별칭 검사가 이 이름을 보므로, 지연화 후 Toss 경로에서 로드되지 않아야 CLEAN. ② `_get_trading_accounts`의 KIS 분기는 Toss에서 도달 불가(가드 `:854`)이므로 지연 로드로 안전. ③ mask는 순수 문자열 작업이라 사본이 정답(KIS 임포트 유발 금지 — KR과 같은 사유).
- **VALIDATE**: `tests/test_no_module_scope_kis_import.py`의 US census 2건이 **XPASS로 실패** + path-load 스캔 stale 실패 → Task 9에서 마크/목록 제거 후 전부 pass

### Task 7: 테스트 우회 제거 (⑤-b)
- **ACTION**: `prism-us/tests/test_issue_448_distribution_days_prompt.py:80-100`의 `spec_from_file_location` monkeypatch 블록 삭제
- **IMPLEMENT**: `_real_spec_from_file_location`/`_kis_auth_safe_spec` 정의와 `importlib.util.spec_from_file_location = _kis_auth_safe_spec` 대입 제거. `:71`의 `sys.modules.setdefault("trading.kis_auth", MagicMock())`은 유지(다른 임포트 경로 보호, 무해).
- **GOTCHA**: 지연화(Task 6) **후**에만 제거 가능 — 순서 준수. 삭제 후 이 테스트 파일 단독 실행으로 즉시 확인.
- **VALIDATE**: `cd prism-us && ../.venv/bin/python -m pytest tests/test_issue_448_distribution_days_prompt.py -q`

### Task 8: DB 경로 cwd 비의존화 (④·⑥)
- **ACTION**: 상대경로 기본값 전부를 `Path(__file__)` 기준으로 전환
- **IMPLEMENT**: DB_PATH_DERIVED 미러.
  - `stock_tracking_agent.py:204` → `db_path: str = str(Path(__file__).parent / "stock_tracking_db.sqlite")` (repo 루트 = 현행 cron cwd와 동일 파일)
  - `stock_tracking_enhanced_agent.py:57` 동일 (repo 루트)
  - `prism-us/us_stock_tracking_agent.py:530` → `str(Path(__file__).parent / "stock_tracking_db.sqlite")` (**prism-us/ 디렉토리** = 현행 US 실행 cwd와 동일 파일)
  - `compress_trading_memory.py:132,383,514` / `retry_journal_entry.py:189` / `tools/compare_position_ledger.py:22`(루트 기준: `parents[1]`) / `cores/llm/features/trade_history.py:72`(`parents[2]`) — 각 파일 위치에서 repo 루트로 해석되도록 parents 인덱스 산정
  - `trading/brokers/toss/dryrun.py:48` → `DEFAULT_DB_PATH = str(Path(__file__).resolve().parents[3] / "toss_dryrun.sqlite")` (repo 루트; `Path` 임포트 필요 여부 확인)
- **GOTCHA**: **라이브 DB 위치 보존이 절대 조건.** 전환 원칙: "그 파일이 프로덕션에서 실행되던 cwd에서 가리키던 파일"과 동일 경로가 되도록만 바꾼다(KR=repo 루트, US=prism-us/). tools/·utils/ 파일은 각자 `Path(__file__).parents[n]`으로 **repo 루트**를 가리킬 것(이 도구들은 루트 DB를 봄 — `tools/hardstop_seller.py:117`과 같은 대상). 각 파일 수정 후 `python -c "import ...; print(default)"`로 산출 경로를 눈으로 확인.
- **VALIDATE**: Manual Validation 체크리스트(아래) — 운영 머신에서 sqlite 파일 실제 위치·mtime 대조

### Task 9: Phase 1 트립와이어 축소 (완료 증명)
- **ACTION**: xfail 마크 3건 + KNOWN 3건 제거
- **IMPLEMENT**:
  - `tests/test_no_module_scope_kis_import.py`: `US_ENTRY_POINTS`의 `us_stock_tracking_agent` `pytest.param` → 평문 문자열로; `KNOWN_PATH_LOAD_OFFENDERS`에서 `prism-us/us_stock_tracking_agent.py` 제거 (`examples/generate_us_dashboard_json.py`는 Phase 3 — 유지)
  - `tests/test_migration_audit_scans.py`: `test_the_configured_buy_amount_reaches_the_order_path`의 `@pytest.mark.xfail` 데코레이터 제거(테스트는 상시 가드로 존치); `KNOWN_CONTRACT_GAPS`에서 us_stock_tracking_agent 2행 제거(fill_chaser 2행은 Phase 5 — 유지)
- **GOTCHA**: 제거 순서는 무관하지만 **구현 완료 전에 제거하면 즉시 실패** — 마지막에 일괄 수행. strict xfail의 XPASS 실패가 먼저 보이는 것이 정상 신호.
- **VALIDATE**: `pytest tests/test_no_module_scope_kis_import.py tests/test_migration_audit_scans.py -q` → fail 0, XPASS 0, 남는 xfail은 `generate_us_dashboard_json` 1건뿐

### Task 10: 회귀 테스트 신설
- **ACTION**: `tests/test_p0_money_path.py` CREATE
- **IMPLEMENT** (서술형 이름, monkeypatch 스타일은 `tests/test_broker_selection.py` 미러):
  1. `test_the_broker_file_amount_reaches_the_toss_broker` — `monkeypatch.setattr(factory_config, "trading_settings", lambda: {...default_unit_amount: 77000...})` 또는 `settings.buy_amount` 자체를 스텁하고, `TossClient`/`TossAuth`/`load_toss_config`를 factory 모듈 속성으로 스텁 → `build_toss_broker(market="KR", mode="demo")`(실명은 factory의 빌더 함수명 확인) 반환 broker의 `.buy_amount == 77000`
  2. `test_env_amount_still_beats_the_broker_file` — `PRISM_BUY_AMOUNT_KRW=123000` 설정 시 `.buy_amount == 123000`
  3. `test_corporate_status_skips_loudly_under_toss` — Task 3 VALIDATE 내용
  4. `test_toss_kr_market_open_follows_the_canonical_window` / `test_toss_us_market_open_follows_the_session_calendar` — `TossBroker(client=stub)`에 `now` 주입(KR: 10:00 KST→True, 16:30→False; US: `open_us_session` 스텁)
  5. `test_toss_reserved_orders_are_never_available`
- **GOTCHA**: TossBroker 생성은 client 스텁으로 네트워크 무접촉. conftest가 브로커 env를 초기화하므로 각 테스트가 필요한 env를 명시 설정.
- **VALIDATE**: `pytest tests/test_p0_money_path.py -v`

---

## Testing Strategy

### Unit Tests
| Test | Input | Expected | Edge |
|---|---|---|---|
| buy 체인 | 파일값 77000, env 없음 | broker.buy_amount=77000 | env가 파일 이김 |
| fractional | Decimal 0.44/1행 | Decimal 0.44 | 6자리 절삭, junk→0, 정수 경로 무변화 |
| corporate gate | broker=toss | {} + 경고 로그 | KIS 미임포트 |
| is_market_open | KR 10:00/16:30, US 세션 유/무 | True/False | tz-aware now 주입 |

### Edge Cases Checklist
- [x] `int(Decimal("0.44"))==0` 회귀 방지 (테스트 명시)
- [x] KIS 정수 경로 산술 byte-동일 (`10//3==3`)
- [x] xfail 3건 XPASS→제거 흐름 (완료 판정)
- [x] DB 경로: 운영 파일 위치 불변 (수동 검증 필수)

## Validation Commands

### Static
```bash
.venv/bin/python -m py_compile trading/brokers/factory.py trading/brokers/toss/adapter.py cores/corporate_status.py prism-us/tracking/db_schema.py prism-us/us_stock_tracking_agent.py
```

### Unit
```bash
.venv/bin/python -m pytest tests/test_p0_money_path.py tests/test_migration_audit_scans.py tests/test_no_module_scope_kis_import.py -q
cd prism-us && ../.venv/bin/python -m pytest tests/test_fractional_sell_quantity.py tests/test_issue_448_distribution_days_prompt.py tests/test_multi_account_us.py -q
```
EXPECT: fail 0 / XPASS 0 / 잔여 xfail 1(`generate_us_dashboard_json`, Phase 3)

### 회귀 (영향권 스위트)
```bash
.venv/bin/python -m pytest tests/test_broker_selection.py tests/test_broker_contract.py tests/test_toss_adapter.py tests/test_toss_us_adapter.py tests/test_toss_dryrun.py tests/test_lazy_kis_call_sites.py tests/test_multi_account_domestic.py -q
cd prism-us && ../.venv/bin/python -m pytest tests/ -q --ignore=tests/test_us_screening_bullish_candle.py --continue-on-collection-errors 2>&1 | tail -3
```
EXPECT: root 전부 green; prism-us는 기준선 123 failed(Phase 1 시점) 대비 **신규 실패 0** (실패 목록 diff로 확인 — Phase 1과 같은 comm 방식)

### Manual Validation (운영 머신, 배포 전 필수)
- [ ] `ls -la stock_tracking_db.sqlite prism-us/stock_tracking_db.sqlite toss_dryrun.sqlite 2>/dev/null` — 수정 전 실파일 위치 기록
- [ ] 수정 후 각 진입점을 `--help` 또는 demo로 1회 기동, 동일 파일 mtime 갱신 확인 (새 빈 DB 생성되면 경로 산정 오류 — 즉시 롤백)
- [ ] demo 드라이런에서 `[BROKER]` 로그의 buy_amount가 `toss_config.yaml` 값과 일치
- [ ] `PRISM_BROKER=toss` 매도 루프 로그에 "KIS 전용 … 건너뜁니다" 경고 1회 출현

## Acceptance Criteria
- [ ] P0 6건 전부 수정, Phase 1 xfail 3건·KNOWN 3건 제거로 증명
- [ ] 신규 테스트 green, 영향권 스위트 신규 실패 0
- [ ] 라이브 DB 파일 위치 불변 (수동 검증 통과)
- [ ] 프로덕션 동작 변화는 의도된 4가지뿐: 매수 금액 출처, 강제청산 스킵 로그, 소수점 매도 가능, cwd 비의존

## Completion Checklist
- [ ] 사본 함수(docstring에 원본 명시)·게이트·지연 로드가 기존 패턴과 문체 일치
- [ ] float로 수량을 다루는 코드 0
- [ ] 보고서 `.claude/PRPs/reports/full-migration-audit-report.md` §1 표의 P0 행에 처리 결과 기입
- [ ] PRD Phase 2 → complete

## Risks
| Risk | L | I | Mitigation |
|---|---|---|---|
| DB 경로 전환이 운영 파일을 벗어남 | M | **치명** | 전환 원칙(현행 cwd 결과와 동일) + 수동 검증 게이트 + 새 빈 DB 감지 시 롤백 |
| buy_amount 연결로 주문 금액이 갑자기 변경 | M | H | 현행 toss_config=100000 == 하드코딩 100_000이라 **행동 무변화**가 기본. 배포 전 demo 로그로 금액 확인 |
| Decimal 반환이 KIS US 경로에 유입 | L | M | 정수 입력은 int 반환(경로 분리) + KIS US 보유는 항상 정수 |
| test_issue_448이 다른 이유로 패치에 의존 | L | L | Task 7을 Task 6 직후 단독 실행으로 즉시 검증 |

## Notes
- 이 플랜은 Phase 1 트립와이어의 strict xfail/stale 검사를 **완료 판정 장치**로 사용한다: 구현이 끝나면 해당 검사들이 스스로 목록 축소를 강제한다.
- `examples/messaging/*`·`generate_us_dashboard_json`의 KIS 잔재는 건드리지 않는다(Phase 3).

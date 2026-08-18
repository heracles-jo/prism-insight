# Plan: KIS 잔재 제거 (Phase 3)

## Summary
Toss 설치에서도 KIS에 닿는 비매매 경로 7곳을 정리한다. 대시보드가 `kis_devlp.yaml`로 매매 모드를 정하는 탓에 **실계좌 운영이 demo로 표기**되고, US 대시보드는 KIS 로드 성공 여부를 게이트로 써서 Toss에서 **빈 포트폴리오**를 렌더링한다. 나머지는 사문 임포트·비게이트 KIS 직행·미운영 구독자다. Phase 1 트립와이어의 xfail 1건과 KNOWN 4건이 이 작업으로 사라지는 것이 완료 판정이다.

## User Story
As a Toss 실계좌 운영자, I want 대시보드와 도구가 KIS 설정이 아니라 실제 선택된 브로커를 보기를, so that 화면의 모드·보유 종목이 실제 계좌 상태와 일치한다.

## Problem → Solution
"KIS 설정 파일/모듈 로드 성공 = 진실"이라는 대리 판정 → 팩토리·`trading_settings()`에 직접 질의.

## Metadata
- **Complexity**: Medium
- **Source PRD**: `.claude/PRPs/prds/full-migration-audit.prd.md`
- **PRD Phase**: Phase 3 — KIS 잔재 제거 (KR). Phase 4와 병렬 가능
- **Estimated Files**: ~9 (프로덕션 7, 테스트 2)

---

## UX Design

### Before
```
Toss 실계좌 운영 중
  ├─ KR 대시보드  : mode="demo"  ← kis_devlp.yaml 부재 시 기본값
  └─ US 대시보드  : portfolio=[] ← KIS 로드 실패로 게이트 차단
```

### After
```
Toss 실계좌 운영 중
  ├─ KR 대시보드  : mode="real"  ← toss_config.yaml의 default_mode
  └─ US 대시보드  : portfolio=[…] ← 팩토리의 us_trader()가 실제 응답
```

### Interaction Changes
| Touchpoint | Before | After | Notes |
|---|---|---|---|
| KR 대시보드 모드 표기 | kis_devlp.yaml | 선택된 브로커의 설정 | **표시가 바뀜** — 실계좌면 real |
| US 대시보드 포트폴리오 | 빈 배열 | 실제 보유 | Toss에서만 변화 |
| `--mode` CLI 기본값 안내 | kis 기본값 | 브로커 기본값 | |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `examples/generate_dashboard_json.py` | 73-98, 128, 1385-1390 | 수정 ①. `_live_trading_available()`(82-98)가 **이미 올바른 패턴** |
| P0 | `examples/generate_us_dashboard_json.py` | 55-71, 163-180 | 수정 ②. 게이트가 팩토리 호출을 막는 구조 |
| P0 | `trading/brokers/settings.py` | 107-175 | `trading_settings(broker=None)`·`configured_mode()` |
| P1 | `stance_mark.py` | 30-46 | 수정 ③. 사문 임포트 2줄 |
| P1 | `tools/check_kr_pending_readiness.py` | 402-435, 505-515 | 수정 ④. `domestic.ka` 경유 + KIS 필드 |
| P1 | `cores/archive/data_enricher.py` | 190-232 | 수정 ⑤. 비게이트 `DomesticStockTrading` |
| P1 | `trigger_batch.py` | 279-300 | 수정 ⑥. 무조건 KIS 스냅샷 시도 |
| P2 | `cores/corporate_status.py` | 80-100 | Phase 2가 만든 **게이트 문구·구조의 정본** |
| P2 | `tests/test_migration_audit_scans.py` | KNOWN_* 집합 | 축소 대상 |
| P2 | `tests/test_no_module_scope_kis_import.py` | `_LEFTOVER_CONFIG_XFAIL`, `KNOWN_PATH_LOAD_OFFENDERS` | 축소 대상 |

## External Documentation
없음 — "No external research needed — feature uses established internal patterns."

---

## Patterns to Mirror

### FACTORY_AVAILABILITY (①②의 정본, 같은 파일에 이미 있음)
```python
# SOURCE: examples/generate_dashboard_json.py:82-98
def _live_trading_available() -> bool:
    """Can the configured broker give us a domestic trader?

    This used to import `DomesticStockTrading` at module scope and treat the
    result as the answer, which was wrong twice over. It made loading this
    module require `kis_devlp.yaml`, and it asked about KIS specifically — so a
    Toss install answered "no" and the dashboard silently rendered an empty
    portfolio even though `domestic_trader()` would have worked. Ask the factory
    the question actually being asked, and ask it lazily.
    """
    try:
        from trading.brokers.factory import domestic_trader  # noqa: F401
        return True
    except Exception as exc:  # noqa: BLE001 - any failure means no live data
        logger.warning(f"Live trading data unavailable: {exc}")
        return False
```
**US판은 이 함수를 그대로 미러하면 된다** — `us_trader`로 바꾸기만.

### BROKER_GATE_ALLOWLIST (④⑤의 정본, Phase 2 산출물)
```python
# SOURCE: cores/corporate_status.py
# 허용목록 방향(KIS일 때만 진행): 미인식 브로커나 미래의 제3 브로커가
# KIS 직행 경로로 떨어지지 않게 하고, never-raises 계약도 지킨다.
try:
    from trading.brokers.settings import selected_broker, KIS
    broker = selected_broker()
except Exception as e:
    logger.warning(f"브로커 설정 확인 실패({e}) — 종목상태코드 prefetch 스킵")
    return out
if broker != KIS:
    logger.warning(
        f"종목상태코드 자동탐지는 KIS 전용입니다 — broker={broker}에서는 건너뜁니다 "
        "(상장폐지/거래정지 TIER0 자동탐지 비활성, 매도 본로직은 정상)"
    )
    return out
```
`!= KIS` 허용목록, try 안, 명시적 사유 로그 — 세 요소 모두 유지할 것.

### TRADING_SETTINGS (①의 모드 출처)
```python
# SOURCE: trading/brokers/settings.py:107+
def trading_settings(broker: str | None = None) -> dict[str, Any]:
    """Trading settings from a broker's own file — `broker`, or the configured one.

    Never raises and never requires a file. ...
    """
```
반환 키: `default_unit_amount`, `default_unit_amount_usd`, `auto_trading`, `default_mode`.
모드는 `configured_mode()`가 더 정확하다 — `PRISM_TRADING_MODE`가 파일보다 우선한다.

### RATCHET_SHRINK (완료 증명 방식)
```python
# SOURCE: tests/test_migration_audit_scans.py
stale = known - offenders
assert not stale, (
    f"fixed but still allowlisted — remove from the KNOWN set for {what}: "
    + ", ".join(sorted(stale))
)
```
수정 후 KNOWN에서 항목을 지우지 않으면 stale 검사가 실패한다 — 이것이 완료 신호다.

### TEST_STRUCTURE
`tests/test_lazy_kis_call_sites.py` 스타일: `monkeypatch.setenv("PRISM_BROKER", "toss")` 후 대상 함수가 팩토리를 묻는지 확인. 서술형 테스트명.

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `examples/generate_dashboard_json.py` | UPDATE | ① kis_devlp 모듈 스코프 read 제거, 모드는 `configured_mode()` |
| `examples/generate_us_dashboard_json.py` | UPDATE | ② 모듈 스코프 KIS 경로 로드·게이트 제거, 팩토리 질의로 |
| `stance_mark.py` | UPDATE | ③ 사문 임포트 2줄 삭제 |
| `tools/check_kr_pending_readiness.py` | UPDATE | ④ KIS 조회 함수에 브로커 게이트 |
| `cores/archive/data_enricher.py` | UPDATE | ⑤ 브로커 게이트 + 명시 로그 |
| `trigger_batch.py` | UPDATE | ⑥ KIS 스냅샷 시도를 조건부로 |
| `examples/messaging/gcp_pubsub_subscriber_example.py` | UPDATE | ⑦ KIS 전용임을 진입점에서 명시 |
| `examples/messaging/redis_subscriber_example.py` | UPDATE | ⑦ 동일 |
| `tests/test_migration_audit_scans.py` | UPDATE | KNOWN 축소 + ALLOWED 이관 |
| `tests/test_no_module_scope_kis_import.py` | UPDATE | xfail·KNOWN 축소 |
| `tests/test_kis_residue_removal.py` | CREATE | ①②④⑤ 회귀 |

## NOT Building
- **messaging 구독자의 브로커 인식화** — 운영자 확인 결과 미운영. KIS 전용임을 진입점에서 명시하고 문서에 기록하는 데까지만 (open question 해소)
- `data_enricher`의 Toss 대체 구현 — 아카이브 보강 기능이라 KIS 전용 유지가 타당. 게이트와 로그만
- `check_kr_pending_readiness`의 Toss 지원 — 미체결 조회는 `BrokerPort` 갭(Phase 5)에 걸려 있음
- 데이터 소스 체인 작업 일체 — Phase 4
- `prism_core/order_intents.py`·`stance_adapter.py`·`fill_chaser.py`의 형태 누출 — Phase 5·6

---

## Step-by-Step Tasks

### Task 1: KR 대시보드의 kis_devlp 의존 제거 (①)
- **ACTION**: `examples/generate_dashboard_json.py` — 모듈 스코프 `CONFIG_FILE`/`_cfg` 블록(73-80) 삭제, 사용처 2곳 교체
- **IMPLEMENT**:
  ```python
  # 73-80 블록 제거. 대신 사용처에서:
  # :128
  from trading.brokers.settings import configured_mode
  self.trading_mode = trading_mode if trading_mode is not None else configured_mode()
  # :1387 (argparse help)
  help=f"트레이딩 모드 (demo: 모의투자, real: 실전투자, 기본값: {configured_mode()})"
  ```
  `configured_mode()`를 쓰는 이유: `PRISM_TRADING_MODE`가 파일보다 우선한다는 규칙을 그 함수가 이미 담고 있다(`settings.py:165-173`). `trading_settings()["default_mode"]`를 직접 읽으면 env 우선순위를 잃는다.
- **MIRROR**: TRADING_SETTINGS
- **IMPORTS**: `from trading.brokers.settings import configured_mode` — **함수 안 또는 파일 상단 어디든 무방**(이 모듈은 kis_auth를 임포트하지 않으므로 모듈 스코프도 안전). 기존 임포트 스타일에 맞춰 상단 배치.
- **GOTCHA**: ① `yaml` 임포트가 이 블록에서만 쓰였다면 함께 정리(다른 사용처 확인 필수 — `grep -n "yaml\." examples/generate_dashboard_json.py`). ② `_cfg` 참조가 두 곳뿐인지 `grep -n "_cfg" ` 로 재확인. ③ **동작 변화**: Toss 실계좌면 표기가 demo→real로 바뀐다. 이는 버그 수정이지 회귀가 아니다.
- **VALIDATE**: `PRISM_BROKER=toss` 환경에서 `_cfg` 미참조 + `configured_mode()` 반환값이 `toss_config.yaml`의 `default_mode`와 일치. `test_migration_audit_scans::test_no_new_direct_reads_of_the_kis_config_file`의 KNOWN에서 이 파일 제거 후 통과

### Task 2: US 대시보드의 KIS 게이트 제거 (②)
- **ACTION**: `examples/generate_us_dashboard_json.py` — 모듈 스코프 로드 블록(55-71) 삭제, `get_kis_us_trading_data`(163-)의 게이트를 팩토리 질의로 교체
- **IMPLEMENT**:
  ```python
  # 55-71 블록(USStockTrading / KIS_US_AVAILABLE / importlib 경로 로드) 전부 삭제

  def _live_us_trading_available() -> bool:
      """Can the configured broker give us a US trader?

      Mirrors _live_trading_available in the KR dashboard. The module-scope
      KIS load this replaces asked whether *KIS* was importable and used the
      answer to gate the factory call below — so a Toss install rendered an
      empty portfolio even though `us_trader()` would have answered.
      """
      try:
          from trading.brokers.factory import us_trader  # noqa: F401
          return True
      except Exception as exc:  # noqa: BLE001 - any failure means no live data
          logger.warning(f"Live US trading data unavailable: {exc}")
          return False

  # get_kis_us_trading_data 안:
      if not _live_us_trading_available():
          logger.warning("US trading data unavailable for the configured broker.")
          return {"portfolio": [], "account_summary": {}}
  ```
  메서드명 `get_kis_us_trading_data`는 **그대로 둔다** — 호출부 변경은 이 Phase 범위 밖이고, 이름 변경은 Phase 6 정리 대상.
- **MIRROR**: FACTORY_AVAILABILITY (같은 프로젝트의 KR 형제 함수를 그대로)
- **GOTCHA**: ① `USStockTrading`/`KIS_US_AVAILABLE` 다른 참조가 없는지 `grep -n "USStockTrading\|KIS_US_AVAILABLE" examples/generate_us_dashboard_json.py`로 전수 확인 후 삭제. ② `_ilu`(importlib.util) 임포트가 이 블록 전용이면 함께 제거. ③ 이 수정으로 **census xfail이 XPASS**가 되므로 Task 8에서 마크를 지워야 한다.
- **VALIDATE**: `pytest tests/test_no_module_scope_kis_import.py -q` → `examples.generate_us_dashboard_json`의 leftover-config xfail이 XPASS로 실패 → Task 8에서 제거 후 pass

### Task 3: stance_mark 사문 임포트 삭제 (③)
- **ACTION**: `stance_mark.py:40-41`
- **IMPLEMENT**: 아래 두 줄만 삭제. 나머지는 손대지 않는다.
  ```python
  from prism_core.stance_quotes import KisQuoteProvider          # 삭제
  from trading.domestic_stock_trading import DomesticStockTrading  # 삭제
  ```
  바로 아래 `from prism_core.stance_quotes import quote_provider_for_broker`와 `return` 문이 실제 동작이며, 삭제되는 두 이름은 어디서도 쓰이지 않는다.
- **GOTCHA**: 파일 전체에서 두 이름이 다시 쓰이지 않는지 확인(`grep -n "KisQuoteProvider\|DomesticStockTrading" stance_mark.py`). `stance_mark`는 census `ENTRY_POINTS`에 있으므로 이 삭제로 임포트 시 KIS 도달이 사라진다.
- **VALIDATE**: `PRISM_BROKER=toss python -c "import stance_mark; stance_mark.build_fetcher('real')"`가 KIS 임포트 없이 동작(네트워크 실패는 무관)

### Task 4: readiness 도구 브로커 게이트 (④)
- **ACTION**: `tools/check_kr_pending_readiness.py`의 `inquire_kis_open_sells()` 진입부
- **IMPLEMENT**: BROKER_GATE_ALLOWLIST를 그대로 적용, KIS 임포트보다 **앞**에:
  ```python
  async def inquire_kis_open_sells() -> dict[str, dict[str, Any]]:
      """Read authoritative open SELLs for every configured active KR account.

      KIS-only: it reads the KIS revisable-order inquiry and its field names
      (sll_buy_dvsn_cd, psbl_qty). Toss has no equivalent on the port yet, so
      this reports "not applicable" rather than auditing a broker it cannot see.
      """
      try:
          from trading.brokers.settings import selected_broker, KIS
          broker = selected_broker()
      except Exception as exc:  # noqa: BLE001
          logger.warning("브로커 설정 확인 실패(%s) — 미체결 조회 스킵", exc)
          return {}
      if broker != KIS:
          logger.warning(
              "미체결 매도 조회는 KIS 전용입니다 — broker=%s에서는 건너뜁니다", broker
          )
          return {}

      from prism_core.execution_service import ExecutionService
      from trading import domestic_stock_trading as domestic
      ...  # 이하 기존 그대로
  ```
- **MIRROR**: BROKER_GATE_ALLOWLIST
- **GOTCHA**: ① 이 파일에 `logger`가 있는지 확인(없으면 `logging.getLogger(__name__)` 추가). ② 호출부(`:509` 부근)가 빈 dict를 어떻게 해석하는지 확인 — `_unknown_report` 강등이 그대로 유지되면 충분하나, "브로커 미지원"이 "조회 실패"로 보이지 않도록 호출부 로그 문구를 점검. ③ `domestic.ka` 경유가 게이트 뒤로 갔으므로 Toss에서 kis_auth 미도달.
- **VALIDATE**: `PRISM_BROKER=toss`에서 `inquire_kis_open_sells()`가 `{}` 반환 + 경고 로그 + `sys.modules`에 `trading.kis_auth` 미등장

### Task 5: archive enricher 브로커 게이트 (⑤)
- **ACTION**: `cores/archive/data_enricher.py`의 `KRDataEnricher._get_trading()`
- **IMPLEMENT**:
  ```python
  def _get_trading(self):
      """Lazy-init DomesticStockTrading in demo mode.

      KIS-only: the daily-chart call below is a KIS API (FHKST03010100) and
      reads KIS response shapes. Under any other broker this returns None and
      enrichment is simply unavailable — said once, not swallowed.
      """
      if self._trading is None and not self._checked_broker:
          self._checked_broker = True
          try:
              from trading.brokers.settings import selected_broker, KIS
              broker = selected_broker()
          except Exception as e:
              logger.warning(f"브로커 설정 확인 실패({e}) — KR 보강 비활성")
              return None
          if broker != KIS:
              logger.warning(
                  f"KR 아카이브 보강은 KIS 전용입니다 — broker={broker}에서는 건너뜁니다"
              )
              return None
          try:
              from trading.domestic_stock_trading import DomesticStockTrading
              self._trading = DomesticStockTrading(mode="demo")
          except Exception as e:
              logger.warning(f"KIS trading init failed (enrichment unavailable): {e}")
      return self._trading
  ```
  `__init__`에 `self._checked_broker = False` 추가.
- **MIRROR**: BROKER_GATE_ALLOWLIST
- **GOTCHA**: ① `_checked_broker` 플래그가 없으면 종목마다 게이트 로그가 반복된다(이 클래스는 세마포어 5로 병렬 호출됨). ② `_get_trading()`의 기존 계약(None 반환 시 호출자가 `{}` 반환)은 유지 — `_sync_fetch_daily:208`이 이미 `if not trading: return {}`. ③ 이 파일은 형태 스캔 KNOWN에 있는데, 게이트 후에는 **ALLOWED로 이관**한다(정당한 KIS 전용).
- **VALIDATE**: `PRISM_BROKER=toss`에서 `KRDataEnricher()._get_trading()`이 None + 경고 1회, 두 번째 호출은 로그 없음

### Task 6: trigger_batch의 KIS 스냅샷 조건부 시도 (⑥)
- **ACTION**: `trigger_batch.py`의 `load_market_snapshot_bundle()`
- **IMPLEMENT**: `try` 진입 전에 사용 가능 여부를 판정:
  ```python
  def _kis_snapshot_usable() -> bool:
      """Whether a KIS snapshot attempt can plausibly succeed.

      The Naver fallback below already covers failure, but on a Toss install
      with no KIS market-data source configured the attempt fails on every run
      — a guaranteed round-trip and a warning line for nothing.
      """
      try:
          from trading.brokers.settings import selected_broker, KIS
          if selected_broker() == KIS:
              return True
      except Exception:  # noqa: BLE001 - fall through to the source list
          pass
      import os
      sources = os.getenv("PRISM_MARKET_DATA_SOURCES", "")
      return "kis" in {s.strip().lower() for s in sources.split(",") if s.strip()}

  def load_market_snapshot_bundle(trade_date: str) -> MarketSnapshotBundle:
      """Load current KIS quotes plus previous OPEN API data, or Naver fallback."""
      if not _kis_snapshot_usable():
          logger.info("[MARKET-DATA] KIS not configured; using Naver snapshot directly")
      else:
          try:
              bundle = build_kis_openapi_snapshot_bundle(trade_date)
              ...
              return bundle
          except Exception as primary_exc:
              logger.warning(...)
      # 이하 기존 Naver 폴백 그대로
  ```
- **GOTCHA**: ① Naver 폴백 블록의 들여쓰기가 바뀌지 않도록 `else:` 안에 KIS 시도만 넣고 폴백은 바깥에 둔다. ② `PRISM_MARKET_DATA_SOURCES`에 "kis"를 넣은 Toss 설치는 여전히 시도해야 하므로 브로커만으로 판정하면 안 된다. ③ 이 함수는 배치당 1회 호출이라 비용은 크지 않다 — 로그 소음과 명확성이 목적임을 주석에 남길 것.
- **VALIDATE**: `PRISM_BROKER=toss`(소스 미지정)에서 KIS 시도 없이 Naver 경로 진입, `PRISM_MARKET_DATA_SOURCES=kis,krx`에서는 시도함

### Task 7: messaging 구독자를 KIS 전용으로 명시 (⑦ — open question 해소)
- **ACTION**: `examples/messaging/gcp_pubsub_subscriber_example.py`, `redis_subscriber_example.py`
- **IMPLEMENT**: 두 파일 모듈 docstring 상단에 명시하고, main 진입점에서 1회 경고:
  ```python
  """... (기존 docstring)

  BROKER SUPPORT: KIS only. Every order path here builds KIS traders directly
  and reads kis_devlp.yaml; it predates the broker abstraction and is not part
  of any Toss install's runtime. Route orders through
  `prism_core.execution_service.ExecutionService` if this ever needs to serve
  another broker.
  """
  ```
  main()/entry 함수 시작부:
  ```python
  from trading.brokers.settings import selected_broker, KIS
  if selected_broker() != KIS:
      logger.error(
          "This subscriber is KIS-only; PRISM_BROKER=%s is not supported. "
          "Refusing to start rather than placing orders on the wrong broker.",
          selected_broker(),
      )
      return 1
  ```
- **GOTCHA**: ① `return 1`이 적절한 진입점인지 각 파일 구조 확인(async main이면 `sys.exit(1)`). ② 이 게이트로 `get_trading_mode()`의 kis_devlp read가 정당해지므로, 형태/devlp 스캔에서 **KNOWN → ALLOWED 이관**. ③ 문서(`docs/EXTERNAL_SUBSCRIBER_GUIDE.md`, `examples/messaging/SUBSCRIBER_OPS_HARNESS.md`)에도 KIS 전용 한 줄 추가.
- **VALIDATE**: `PRISM_BROKER=toss`에서 조기 종료 + 명확한 오류 메시지

### Task 8: 트립와이어 축소 (완료 증명)
- **ACTION**: KNOWN/ALLOWED/xfail 갱신
- **IMPLEMENT**:
  - `tests/test_no_module_scope_kis_import.py`: `_LEFTOVER_CONFIG_XFAIL`에서 `examples.generate_us_dashboard_json` 제거(딕셔너리가 비면 파라미터화 헬퍼도 단순화), `KNOWN_PATH_LOAD_OFFENDERS`에서 `examples/generate_us_dashboard_json.py` 제거(집합이 빈 set이 됨 — stale 검사가 여전히 성립하는지 확인)
  - `tests/test_migration_audit_scans.py`:
    - `KNOWN_DEVLP_OFFENDERS`: `examples/generate_dashboard_json.py` 제거, `examples/messaging/gcp_pubsub_subscriber_example.py`는 **`DEVLP_ALLOWED`로 이관**(KIS 전용 선언)
    - `KNOWN_SHAPE_OFFENDERS`: `cores/archive/data_enricher.py`·`tools/check_kr_pending_readiness.py`를 **`SHAPE_ALLOWED`로 이관**(게이트 뒤 KIS 전용). `prism_core/order_intents.py`·`stance_adapter.py`·`tools/fill_chaser.py`는 그대로 (Phase 5·6)
- **GOTCHA**: 빈 KNOWN 집합에서 `_assert_frozen`의 stale 검사가 `set() - offenders = set()`이 되어 항상 통과한다 — 정상. 새 위반 차단은 계속 동작한다.
- **VALIDATE**: `pytest tests/test_no_module_scope_kis_import.py tests/test_migration_audit_scans.py -q` → fail 0, **XPASS 0**, 잔여 xfail 1건(`us_stock_tracking_agent` census, §7-5 사유)

### Task 9: 회귀 테스트 신설
- **ACTION**: `tests/test_kis_residue_removal.py` CREATE
- **IMPLEMENT** (서술형 이름, `tests/test_lazy_kis_call_sites.py` 스타일):
  1. `test_the_kr_dashboard_mode_comes_from_the_configured_broker` — `monkeypatch`로 `configured_mode`를 스텁, 대시보드 생성자의 `trading_mode`가 그 값인지
  2. `test_the_us_dashboard_asks_the_factory_for_availability` — `_live_us_trading_available`이 `trading.brokers.factory.us_trader` 임포트로 판정하는지(모듈 속성 스텁)
  3. `test_readiness_audit_skips_a_non_kis_broker` — `PRISM_BROKER=toss`에서 `{}` + 경고
  4. `test_archive_enrichment_says_it_is_kis_only_once` — None 반환 + 로그 1회(2회 호출)
  5. `test_the_snapshot_skips_kis_when_it_is_not_configured` — `_kis_snapshot_usable()`의 3가지 조합
- **GOTCHA**: 대시보드 모듈은 임포트가 무거울 수 있다(번역·yfinance). 임포트 실패 시 `pytest.importorskip`으로 스킵하되, **census가 이미 임포트 가능성을 보증**하므로 스킵이 상시화되지 않는지 확인.
- **VALIDATE**: `pytest tests/test_kis_residue_removal.py -v`

---

## Testing Strategy

### Unit Tests
| Test | Input | Expected | Edge |
|---|---|---|---|
| KR 대시보드 모드 | broker=toss, toss_config default_mode=real | `trading_mode == "real"` | `PRISM_TRADING_MODE`가 우선 |
| US 대시보드 가용성 | 팩토리 임포트 성공/실패 | True/False | KIS 부재와 무관 |
| readiness 게이트 | broker=toss | `{}` + 경고 | 미인식 브로커도 스킵 |
| enricher 게이트 | broker=toss, 2회 호출 | None, 로그 1회 | 병렬 호출 |
| snapshot 게이트 | (kis)/(toss)/(toss+sources=kis) | True/False/True | |

### Edge Cases Checklist
- [ ] `PRISM_TRADING_MODE`가 `configured_mode()`보다 우선하는지
- [ ] 미인식 브로커(`PRISM_BROKER=tos`)에서 전 게이트가 스킵(never-raises)
- [ ] enricher 게이트 로그가 종목마다 반복되지 않음
- [ ] KIS 설치에서 5개 경로 전부 **동작 무변화**

## Validation Commands
```bash
.venv/bin/python -m py_compile examples/generate_dashboard_json.py examples/generate_us_dashboard_json.py stance_mark.py tools/check_kr_pending_readiness.py cores/archive/data_enricher.py trigger_batch.py

.venv/bin/python -m pytest tests/test_kis_residue_removal.py tests/test_migration_audit_scans.py tests/test_no_module_scope_kis_import.py tests/test_lazy_kis_call_sites.py -q

.venv/bin/python -m pytest tests/test_broker_selection.py tests/test_broker_contract.py tests/test_toss_adapter.py tests/test_toss_us_adapter.py tests/test_p0_money_path.py tests/test_toss_us_order_path.py tests/test_execution_service.py -q

cd prism-us && ../.venv/bin/python -m pytest tests/ -q --ignore=tests/test_us_screening_bullish_candle.py --continue-on-collection-errors  # 신규 실패 0 (목록 diff)
```

### Manual Validation
- [ ] `PRISM_BROKER=toss python examples/generate_dashboard_json.py --help` — 기본 모드 안내가 `toss_config.yaml` 값
- [ ] 대시보드 1회 생성 후 JSON의 `trading_mode`와 US `portfolio` 확인 (**실계좌 표기가 demo→real로 바뀌는 것이 정상**)
- [ ] KIS 설치에서도 두 대시보드가 종전과 동일하게 렌더링

## Acceptance Criteria
- [ ] Toss에서 KR 대시보드 모드가 실제 브로커 설정을 반영
- [ ] Toss에서 US 대시보드가 실제 보유 종목을 렌더링
- [ ] xfail 1건·KNOWN 4건 제거/이관으로 완료 증명
- [ ] KIS 설치 동작 무변화, 회귀 0

## Completion Checklist
- [ ] 게이트 3곳이 모두 허용목록(`!= KIS`) 방향 + try 안 + 명시 로그
- [ ] 감사 보고서 §2의 해당 항목에 처리 결과 기입
- [ ] PRD Phase 3 → complete, open question(messaging) 해소 표기
- [ ] `docs/EXTERNAL_SUBSCRIBER_GUIDE.md`에 KIS 전용 명시

## Risks
| Risk | L | I | Mitigation |
|---|---|---|---|
| 대시보드 모드 표기가 바뀌어 운영자가 놀람 | **H** | L | 의도된 수정임을 보고서·PR에 명시. 실제로 실계좌면 real이 정답 |
| US 대시보드가 처음으로 실데이터를 부름 (Toss) | M | M | `us_trader()`는 조회 전용. demo에서 먼저 확인 |
| `_cfg`/`KIS_US_AVAILABLE` 잔여 참조를 놓침 | M | M | 삭제 전 grep 전수 확인(각 Task GOTCHA에 명시) |
| trigger_batch 들여쓰기 변경으로 폴백 경로 손상 | L | H | `else:`에 KIS 시도만, 폴백은 바깥. 배치 1회 실행으로 즉시 확인 |

## Notes
- Phase 4(데이터 소스)와 파일 집합이 거의 분리된다. 유일한 공통 파일이 `trigger_batch.py`인데, **Task 6은 KIS 스냅샷 게이트만** 건드리고 Phase 4는 로그인 클라이언트 이전을 맡는다 — 같은 파일의 다른 함수라 병렬 작업 시 충돌 가능성은 낮지만 순서를 조율할 것.
- 이 Phase는 실금전 주문 경로를 건드리지 않는다. Phase 2·2.5에서 반복된 "게이트가 정상 동작까지 막는" 실패 유형은 여기서는 조회·표시 경로에 한정되므로 위험도가 낮다.

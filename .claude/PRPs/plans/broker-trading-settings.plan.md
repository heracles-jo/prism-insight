# Plan: 브로커별 매매 설정 로더 (PRD Phase 1)

## Summary

매매 설정(`default_unit_amount`, `auto_trading`, `default_mode`)이 `kis_devlp.yaml`에만 있어서, 토스만 쓰는 설치도 KIS 파일을 요구합니다. `toss_config.yaml`이 자기 매매 설정을 갖게 하고, `PRISM_BROKER`에 따라 출처를 고르는 로더를 추가합니다. **KIS 사용자는 파일도 값도 그대로**입니다.

## User Story

As a **KIS 계좌가 없는 운영자**,
I want **매매 설정을 토스 설정 파일에서 읽기를**,
So that **쓰지도 않을 증권사의 설정 파일 없이 기동할 수 있다**.

## Problem → Solution

**현재**: `trading/domestic_stock_trading.py:104,106,108`이 `kis_devlp.yaml`의 `_cfg`에서 매매 설정을 읽습니다. `examples/generate_dashboard_json.py:73`은 그 파일을 직접 읽습니다. 토스만 쓰려 해도 이 파일이 있어야 합니다.

**목표**: `PRISM_BROKER=toss`면 `toss_config.yaml`에서 읽고, `kis`면 지금과 동일하게 동작합니다.

## Metadata

- **Complexity**: Small (신규 0 / 수정 2 + 테스트)
- **Source PRD**: `.claude/PRPs/prds/toss-only-startup.prd.md`
- **PRD Phase**: Phase 1 — 브로커별 매매 설정 로더
- **Estimated Files**: 2 UPDATE, 1 UPDATE(테스트)
- **Branch**: `fix/toss-only-startup` (생성됨)

---

## ⚠️ 이 Phase만으로는 문제가 해결되지 않습니다

PRD가 원인을 두 갈래로 기록한 이유입니다:

| 갈래 | 해소 |
|---|---|
| ① 모듈 스코프 임포트 (`kis_auth.py:118`) | **Phase 2** |
| ② 매매 설정이 KIS 파일에만 있음 | **이 Phase** |

**Phase 1만 하면 여전히 임포트에서 죽고, Phase 2만 하면 설정을 못 읽습니다.** 둘 다 끝나야 `kis_devlp.yaml` 없는 기동이 성립합니다. 두 Phase는 서로 독립적이라 순서는 상관없습니다.

이 Phase의 성공 신호는 "토스 전용 기동"이 아니라 **"매매 설정을 KIS 파일 없이 읽을 수 있다"**입니다.

---

## UX Design

**N/A — 내부 설정 로딩 변경.** 이 Phase 후에도 토스 전용 기동은 아직 안 됩니다(Phase 2 필요).

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| **P0** | `trading/brokers/settings.py` | 전체 (약 120줄) | 수정 대상. 기존 로더 패턴·예외·기본값 관례가 전부 여기 있음 |
| **P0** | `trading/domestic_stock_trading.py` | 100-110 | KIS가 읽는 키와 사용처. **이 파일은 수정하지 않음** |
| **P0** | `trading/config/kis_devlp.yaml.example` | 1-13 | 키 이름과 기본값의 정본 |
| **P1** | `trading/config/toss_config.yaml.example` | 전체 | 추가 대상 파일. 주석 톤을 맞출 것 |
| **P1** | `tests/test_broker_selection.py` | 1-30, 76-132 | 테스트 패턴 + `load_toss_config` 기존 테스트 |
| **P2** | `.claude/PRPs/prds/toss-only-startup.prd.md` | Evidence, Decisions | 왜 공통 파일로 쪼개지 않는지 |

## External Documentation

**불필요.** 순수 내부 설정 로딩. 토스 API를 호출하지 않습니다.

---

## Patterns to Mirror

### CONFIG_LOADER (이 파일의 핵심 패턴)
```python
# SOURCE: trading/brokers/settings.py:76-107
def load_toss_config(path: Path | None = None) -> dict[str, Any]:
    """Read `toss_config.yaml`, with env overrides for containerised runs."""
    target = path or TOSS_CONFIG_FILE
    config: dict[str, Any] = {}

    if target.exists():
        try:
            with open(target, encoding="utf-8") as handle:
                config = yaml.safe_load(handle) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise BrokerConfigError(f"could not read {target}: {exc}") from exc

    # Environment wins, so secrets can be injected without a file on disk.
    for key, env in (
        ("client_id", "TOSS_CLIENT_ID"),
        ...
    ):
        value = os.getenv(env)
        if value:
            config[key] = value
```
→ **파일이 없어도 예외를 던지지 않는다.** 없으면 빈 dict로 시작하고 환경변수가 채운다. 필수값이 끝내 비었을 때만 `BrokerConfigError`. 새 로더도 같은 태도를 따를 것 — 매매 설정은 기본값이 있으므로 **아예 예외를 던지지 않는다**.

### SAFE_DEFAULT (안전 기본값 관례)
```python
# SOURCE: trading/brokers/settings.py:56-70
def trading_mode() -> str:
    """`demo` or `real`, defaulting to `demo`.

    An unrecognised value resolves to `demo` and logs loudly rather than
    raising: refusing to start is not obviously safer than starting in the
    harmless mode, and a batch that dies at 09:00 has its own cost.
    """
    raw = (os.getenv("PRISM_TRADING_MODE") or DEMO).strip().lower()
    if raw not in {DEMO, REAL}:
        logger.error(
            "[BROKER] PRISM_TRADING_MODE=%r is not recognised; falling back to demo", raw
        )
        return DEMO
    return raw
```
→ 인식 불가한 값은 **안전한 쪽으로 떨어뜨리고 크게 로깅**한다. 죽지 않는다.

### TOLERANT_NUMBER
```python
# SOURCE: trading/brokers/settings.py:109-120
def toss_buy_amount(market: str) -> int | None:
    """Per-order budget, if configured. `None` lets the adapter default."""
    env = "PRISM_BUY_AMOUNT_KRW" if market.upper() == "KR" else "PRISM_BUY_AMOUNT_USD"
    raw = os.getenv(env)
    if not raw:
        return None
    try:
        return int(float(raw))
    except ValueError:
        logger.warning("[BROKER] %s=%r is not a number; ignoring", env, raw)
        return None
```
→ 숫자 파싱 실패는 **경고 후 무시**. 예외를 올리지 않는다.

### KIS_SETTING_KEYS (건드리지 않을 원본)
```python
# SOURCE: trading/domestic_stock_trading.py:104-108
DEFAULT_BUY_AMOUNT = _cfg["default_unit_amount"]
AUTO_TRADING       = _cfg["auto_trading"]
DEFAULT_MODE       = _cfg["default_mode"]
```
```yaml
# SOURCE: trading/config/kis_devlp.yaml.example:5-12
default_unit_amount: 10000       # 한국 주식 (KRW)
default_unit_amount_usd: 100     # 미국 주식 (USD)
auto_trading: true
default_mode: demo
```
→ 키 이름과 기본값의 정본. **토스 설정에도 같은 키 이름을 쓴다** — 두 파일에서 이름이 다르면 사람이 헷갈린다.

### LOGGING_PATTERN
```python
# SOURCE: trading/brokers/settings.py:64
logger.error("[BROKER] PRISM_TRADING_MODE=%r is not recognised; falling back to demo", raw)
```
→ 모듈 레벨 `logger = logging.getLogger(__name__)`, `[BROKER]` 태그, `%s`/`%r` lazy 포매팅.

### TEST_STRUCTURE
```python
# SOURCE: tests/test_broker_selection.py:18-26
@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("PRISM_BROKER", "PRISM_TRADING_MODE", "TOSS_CLIENT_ID", ...):
        monkeypatch.delenv(name, raising=False)
    yield
```
```python
# SOURCE: tests/test_broker_selection.py:79-87
def test_toss_config_reads_the_yaml_file(tmp_path):
    path = tmp_path / "toss_config.yaml"
    path.write_text("client_id: c_abc\nclient_secret: s_xyz\n", encoding="utf-8")

    loaded = config.load_toss_config(path)
    assert loaded["client_id"] == "c_abc"
```
→ `tmp_path`에 실제 yaml을 써서 검증. 함수 내부 import. `tests/conftest.py`가 브로커 환경변수를 매 테스트 초기화한다.

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `trading/brokers/settings.py` | UPDATE | 매매 설정 로더 추가 |
| `trading/config/toss_config.yaml.example` | UPDATE | 매매 설정 키 추가 + 주석 |
| `tests/test_broker_selection.py` | UPDATE | 로더 테스트 |

**수정하지 않는 파일**: `trading/domestic_stock_trading.py`, `trading/kis_auth.py`, `trading/config/kis_devlp.yaml.example` — KIS 경로 무변경이 이 작업의 제약이다.

## NOT Building

- **진입점의 지연 임포트** — Phase 2
- **`domestic_stock_trading.py`가 새 로더를 쓰게 만들기** — KIS는 지금 방식을 유지한다. 이 로더는 **토스 경로가 KIS 파일을 안 읽게** 하려는 것이지 KIS를 바꾸려는 게 아니다
- **`kis_devlp.yaml` 스키마 변경** — 기존 사용자 마이그레이션 금지
- **공통 설정 파일 신설** — PRD 결정
- **`prism-us`** — 미결 (PRD Open Question)
- **`accounts:` 다중 계좌** — KIS 고유 개념. 토스 대응은 미검증 (PRD Open Question)

---

## Step-by-Step Tasks

### Task 1: `toss_config.yaml.example`에 매매 설정 추가

- **ACTION**: 예시 파일에 키 4개 추가
- **IMPLEMENT**:
  ```yaml
  # ── 매매 설정 ────────────────────────────────────────────────
  # KIS 를 쓰지 않는 설치도 기동할 수 있도록, 매매 설정은 각 브로커의
  # 설정 파일이 자기 것을 가진다. PRISM_BROKER=toss 일 때 여기서 읽는다.

  default_unit_amount: 100000      # 종목당 매수 금액 (KRW)
  default_unit_amount_usd: 100     # 종목당 매수 금액 (USD)
  auto_trading: true               # 자동매매 작동 여부
  default_mode: demo               # demo | real
  ```
  기존 자격증명 섹션 아래에 둔다. **⚠️ 모의투자 없음** 경고는 이미 파일 상단에 있으므로 반복하지 않는다.
- **MIRROR**: `KIS_SETTING_KEYS` — **키 이름을 KIS와 동일하게** 쓸 것
- **GOTCHA**:
  - `default_mode`는 `PRISM_TRADING_MODE` 환경변수와 의미가 겹친다. **환경변수가 우선**임을 주석에 명시할 것 — 안 그러면 두 곳이 싸운다
  - KRW 기본값을 `10000`(KIS 예시)이 아니라 `100000`으로 두는 이유: `TossBroker.DEFAULT_BUY_AMOUNT_KRW`가 이미 `100_000`이다. 파일과 코드 기본값이 어긋나면 안 된다
- **VALIDATE**: `python -c "import yaml,pathlib; print(sorted(yaml.safe_load(pathlib.Path('trading/config/toss_config.yaml.example').read_text())))"`

### Task 2: `settings.py`에 매매 설정 로더 추가

- **ACTION**: `trading/brokers/settings.py`에 함수 추가
- **IMPLEMENT**:
  1. 기본값을 **코드에 한 곳**으로 둔다 (두 파일이 어긋나지 않게):
     ```python
     _TRADING_DEFAULTS = {
         "default_unit_amount": 100_000,
         "default_unit_amount_usd": 100,
         "auto_trading": True,
         "default_mode": DEMO,
     }
     ```
  2. `trading_settings() -> dict[str, Any]`:
     - `selected_broker()`가 `TOSS`면 `load_toss_config()`에서 위 키만 뽑아 기본값 위에 덮어쓴다
     - `KIS`면 `kis_devlp.yaml`을 읽는다. **단 `kis_auth`를 임포트하지 말 것** — 그러면 이 함수 자체가 문제의 일부가 된다. `CONFIG_DIR / "kis_devlp.yaml"`을 직접 `yaml.safe_load` 하고, 파일이 없으면 기본값을 쓴다
     - 어느 경우든 **예외를 던지지 않는다.** 매매 설정은 기본값이 있다
  3. `buy_amount(market)`: `toss_buy_amount()`를 대체하지 말고 **감싼다** — 환경변수 우선, 없으면 `trading_settings()`, 없으면 기본값. 기존 호출자(`factory.build_toss_broker`)가 계속 동작해야 한다
  4. `auto_trading()` / `configured_mode()`도 같은 방식
- **MIRROR**: `CONFIG_LOADER`(파일 없어도 안 죽음), `SAFE_DEFAULT`(인식 불가 → 안전값 + 로깅), `TOLERANT_NUMBER`(파싱 실패 → 경고 후 무시)
- **IMPORTS**: 이미 있는 것으로 충분 (`os`, `yaml`, `Path`, `Any`, `logger`)
- **GOTCHA**:
  - **`trading.domestic_stock_trading`이나 `kis_auth`를 임포트하지 말 것.** 이 모듈은 KIS 없이 임포트 가능해야 한다 — 지금 그렇고, 그게 이 작업의 전제다
  - `trading_mode()`(환경변수)와 `default_mode`(파일)의 우선순위를 **하나로 정할 것**: 환경변수 > 파일 > 기본값. 기존 `trading_mode()`의 동작을 바꾸지 말고, 파일값은 그 아래 단계로만 쓴다
  - `auto_trading`은 bool이다. yaml에서 `"true"` 문자열로 올 수 있으니 `_to_bool` 같은 관용 변환을 둘 것
- **VALIDATE**: Task 3의 테스트

### Task 3: 테스트

- **ACTION**: `tests/test_broker_selection.py`에 섹션 추가
- **IMPLEMENT**:
  1. **KIS 파일 없이 토스 설정을 읽는다** — 이 Phase의 핵심. `TOSS_CONFIG_FILE`을 `tmp_path`로 monkeypatch하고, KIS 경로는 존재하지 않게 한 뒤 `trading_settings()`가 값을 돌려주는지
  2. **파일이 전혀 없어도 기본값으로 동작** — 예외가 나지 않는다
  3. **`PRISM_BROKER=kis`면 `kis_devlp.yaml`에서 읽는다** — 기존 사용자 무변경
  4. **환경변수 > 파일** — `PRISM_BUY_AMOUNT_KRW`가 파일값을 덮는다
  5. **`auto_trading` 문자열 허용** — `"true"` → `True`
  6. **`settings.py`가 KIS를 임포트하지 않는다** — 서브프로세스로 `import trading.brokers.settings` 후 `'kis_auth' in sys.modules` 가 False
- **MIRROR**: `TEST_STRUCTURE` — `tmp_path`에 실제 yaml, 함수 내부 import
- **IMPORTS**: `import pytest`
- **GOTCHA**: `tests/conftest.py`가 `PRISM_BROKER` 등을 매 테스트 초기화하므로, 브로커를 정하려면 **명시적으로 `monkeypatch.setenv`** 해야 한다
- **VALIDATE**: `.venv/bin/python -m pytest tests/test_broker_selection.py -q`

### Task 4: 회귀 고정 + 커밋

- **ACTION**: KIS 무변경 확인 후 커밋
- **IMPLEMENT**:
  ```bash
  .venv/bin/python -m pytest -q -p no:cacheprovider \
    tests/test_execution_service.py tests/test_async_trading.py \
    tests/test_multi_account_domestic.py tests/test_sell_quantity_guard.py \
    tests/test_sell_denominator_sync.py tests/test_kr_pending_entry.py \
    tests/test_multi_account_kis_auth.py
  git diff --stat -- trading/config/kis_devlp.yaml.example trading/domestic_stock_trading.py
  ```
  두 번째 명령은 **빈 출력**이어야 한다 — KIS 파일 무변경이 성공 지표다
- **VALIDATE**: KIS **99 passed**, KIS 파일 diff 0

---

## Testing Strategy

### Unit Tests

| Test | Input | Expected | Edge Case? |
|---|---|---|---|
| 토스 설정에서 읽기 | `PRISM_BROKER=toss` + toss yaml | 파일값 반환 | ✅ 이 Phase의 목적 |
| KIS 파일 없이 동작 | 토스 설정만 존재 | 예외 없음 | ✅ 핵심 |
| 설정 파일 전무 | 아무 파일도 없음 | 기본값 반환 | ✅ |
| KIS 경로 무변경 | `PRISM_BROKER` 미설정 | `kis_devlp.yaml`에서 읽음 | ✅ 회귀 방지 |
| 환경변수 우선 | `PRISM_BUY_AMOUNT_KRW=250000` + 파일 `100000` | `250000` | ✅ 충돌 규칙 |
| bool 관용 변환 | `auto_trading: "true"` | `True` | ✅ |
| 숫자 아님 | `default_unit_amount: "많이"` | 기본값 + 경고 | ✅ |
| KIS 임포트 없음 | `import trading.brokers.settings` | `kis_auth` 미로드 | ✅ 이 작업의 전제 |

### Edge Cases Checklist

- [x] 설정 파일 부재
- [x] 잘못된 타입 (문자열 bool, 비숫자)
- [x] 환경변수와 파일 충돌
- [x] KIS 경로 무변경
- [ ] 동시 접근 — 해당 없음 (읽기 전용)
- [ ] 네트워크 — 해당 없음

---

## Validation Commands

### Static
```bash
.venv/bin/python -m compileall -q trading/brokers/settings.py
.venv/bin/python -c "from trading.brokers import settings; print(settings.trading_settings())"
```
EXPECT: 오류 없음. (저장소에 린터·타입체커 설정 없음 — 게이트가 존재하지 않는다)

### KIS 임포트 부재 (이 Phase의 전제)
```bash
.venv/bin/python -c "
import sys; import trading.brokers.settings
print('kis_auth loaded:', 'kis_auth' in sys.modules)"
```
EXPECT: `False`

### Unit
```bash
.venv/bin/python -m pytest tests/test_broker_selection.py -q -p no:cacheprovider
```
EXPECT: 전부 통과

### 회귀 (핵심 게이트)
```bash
.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_execution_service.py tests/test_async_trading.py \
  tests/test_multi_account_domestic.py tests/test_sell_quantity_guard.py \
  tests/test_sell_denominator_sync.py tests/test_kr_pending_entry.py \
  tests/test_multi_account_kis_auth.py
```
EXPECT: **99 passed**

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
EXPECT: **22 failed / 10 errors** (baseline 동일), passed는 신규 테스트만큼 증가

> `--ignore` 9개가 필요한 이유: 4개 파일이 모듈 레벨 `sys.exit()`로 수집을 중단시키고, 1개는 없는 모듈을 import하며, 4개는 `.env` 채운 뒤 무한 대기한다. 전부 이 작업과 무관한 기존 문제다.

### Manual
- [ ] `settings.py`가 `kis_auth`/`domestic_stock_trading`을 임포트하지 않는가
- [ ] 기본값이 코드 한 곳에만 있고 두 yaml 예시와 일치하는가
- [ ] 환경변수 > 파일 > 기본값 우선순위가 문서화됐는가
- [ ] `kis_devlp.yaml.example`과 `domestic_stock_trading.py`가 **무변경**인가

---

## Acceptance Criteria

- [ ] Task 1–4 완료
- [ ] `PRISM_BROKER=toss`에서 KIS 파일 없이 매매 설정을 읽는다
- [ ] 설정 파일이 전무해도 기본값으로 동작한다 (예외 없음)
- [ ] `PRISM_BROKER` 미설정 시 `kis_devlp.yaml`에서 읽는다
- [ ] `settings.py`가 KIS를 임포트하지 않는다
- [ ] KIS 99/99 유지, `kis_devlp.yaml.example`·`domestic_stock_trading.py` diff 0

## Completion Checklist

- [ ] 파일 부재를 예외로 만들지 않았다 (`CONFIG_LOADER` 관례)
- [ ] 인식 불가한 값은 안전값 + 로깅 (`SAFE_DEFAULT` 관례)
- [ ] `[BROKER]` 태그 + `%s` lazy 포매팅
- [ ] 키 이름이 KIS와 동일하다
- [ ] 테스트가 `tmp_path` + 함수 내부 import 패턴을 따른다
- [ ] 구현 중 코드베이스 재검색이 필요 없었다

## Risks

| Risk | L | I | Mitigation |
|---|---|---|---|
| **이 Phase만 하고 Phase 2를 잊음** | **M** | **H** | 여전히 임포트에서 죽는다. 계획서 상단에 명시 |
| `settings.py`가 KIS를 임포트하게 됨 | M | H | 전용 테스트로 고정. 이 모듈이 감염되면 전제가 무너진다 |
| 두 yaml의 기본값이 코드와 어긋남 | M | M | 기본값을 코드 한 곳에 두고 파일은 덮어쓰기만 |
| `default_mode`와 `PRISM_TRADING_MODE`가 싸움 | M | M | 우선순위를 환경변수 > 파일 > 기본값으로 못박고 주석·테스트로 고정 |
| KIS 경로를 "이왕이면" 같이 리팩터링 | M | H | KIS 파일 diff 0을 성공 지표로 |

## Notes

- **이 Phase는 사용자에게 보이는 변화가 없다.** 토스 전용 기동은 Phase 2까지 가야 성립한다.
- `settings.py`가 KIS 없이 임포트된다는 것은 **이미 실측으로 확인됐다**(`brokers.factory`·`execution_service`가 KIS 파일 없이 임포트 OK). 그 성질을 깨지 않는 것이 이 작업의 제약이다.
- `toss_buy_amount()`는 `factory.build_toss_broker`가 쓰고 있다. **대체하지 말고 감싸야** 기존 동작이 유지된다.
- 저장소에 린터·타입체커 설정이 없으므로 "타입 에러 0" 게이트는 존재하지 않는다.

---

*Generated: 2026-08-18*
*Source: `.claude/PRPs/prds/toss-only-startup.prd.md` — Phase 1*

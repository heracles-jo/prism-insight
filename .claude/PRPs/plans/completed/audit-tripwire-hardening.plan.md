# Plan: 감사 체계 강화 (트립와이어 사각지대 7종 + 감사 보고서 초판)

## Summary
기존 2계층 트립와이어(`tests/test_no_module_scope_kis_import.py`: AST 스캔 + import census)를 확장해 알려진 사각지대 7종을 커버하고, 알려진 결함은 **strict xfail / 동결 allowlist(ratchet)** 로 고정한다. 동시에 4개 조사 결과 전체를 심각도별 감사 보고서로 정리한다. 이 단계에서는 **프로덕션 코드를 일절 수정하지 않는다** — 결함을 테스트로 고정하는 것이 전부다.

## User Story
As a Toss 실계좌 운영자, I want 알려진 마이그레이션 결함 전부가 실패 가능한 테스트로 고정되기를, so that 이후 수정(Phase 2~6)이 xfail→pass 전환으로 자동 검증되고 같은 유형의 재발이 배포 전에 잡힌다.

## Problem → Solution
트립와이어가 함수 스코프 임포트, `spec_from_file_location` 경로 로드, prism-us 진입점, `kis_devlp.yaml` 직접 읽기, KIS 응답 형태 누출, BrokerPort 미정의 메서드 호출, 설정 키 사장(死藏)을 못 잡음 → 각 사각지대에 대응하는 테스트를 추가하되, 현재 알려진 위반은 xfail/ratchet으로 기록해 CI는 green 유지.

## Metadata
- **Complexity**: Medium
- **Source PRD**: `.claude/PRPs/prds/full-migration-audit.prd.md`
- **PRD Phase**: Phase 1 — 감사 체계 강화
- **Estimated Files**: 5 (UPDATE 2, CREATE 3)

---

## UX Design

N/A — internal change (테스트·문서만).

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `tests/test_no_module_scope_kis_import.py` | all (250) | 확장 대상. AST 걷기·probe·census 패턴의 원본 |
| P0 | `tests/test_broker_selection.py` | 408-453 | ratchet(allowlist) 스타일 grep 트립와이어의 원본 |
| P1 | `tests/conftest.py` | all (57) | prism-us에 미러할 브로커 env 초기화 fixture |
| P1 | `prism-us/tests/conftest.py` | 1-37 | 수정 지점. `sys.path`/`os.chdir`/publish_guard 처리 방식 |
| P1 | `trading/brokers/base.py` | 92-165 | BrokerPort 공식 메서드 목록 (계약 감사의 기준) |
| P2 | `trading/brokers/settings.py` | 88-230 | `trading_settings()`/`buy_amount()`/`toss_buy_amount()` — 설정 키 생존성 테스트 대상 |
| P2 | `prism-us/us_stock_tracking_agent.py` | 195-221 | 모듈 스코프 `spec_from_file_location` 결함의 실물 (xfail 대상) |

## External Documentation

없음 — 내부 패턴만 사용. "No external research needed — feature uses established internal patterns."

---

## Patterns to Mirror

### AST_MODULE_SCOPE_WALK (확장할 함수)
```python
# SOURCE: tests/test_no_module_scope_kis_import.py:87-111
def _module_scope_imports(tree: ast.Module):
    pending = list(tree.body)
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.Try, ast.If, ast.With)):
            for attr in ("body", "orelse", "finalbody", "handlers"):
                pending.extend(getattr(node, attr, None) or [])
        elif isinstance(node, ast.ExceptHandler):
            pending.extend(node.body)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        ...
```
`def`/`class` 안으로 내려가지 않는 것이 규칙의 본질. 새 `spec_from_file_location` 탐지도 같은 걷기에 `ast.Expr`/`ast.Assign` 값 내부의 `ast.Call` 검사를 추가하는 방식으로 확장한다.

### SUBPROCESS_PROBE (census)
```python
# SOURCE: tests/test_no_module_scope_kis_import.py:167-183
probe = (
    f"import {module}\n"
    "import sys\n"
    "print('KIS_LOADED' if 'trading.kis_auth' in sys.modules else 'CLEAN')\n"
)
return subprocess.run(
    [sys.executable, "-c", probe],
    cwd=str(REPO_ROOT),
    env={**os.environ, "PRISM_BROKER": "toss", "PRISM_TRADING_MODE": "demo"},
    capture_output=True, text=True, timeout=300,
)
```
서브프로세스인 이유: 현재 프로세스의 `sys.modules` 오염 방지. **확장 포인트**: `'trading.kis_auth'` 단일 검사 → 별칭 집합 검사.

### RATCHET_GREP_TRIPWIRE (동결 allowlist)
```python
# SOURCE: tests/test_broker_selection.py:408-453
allowed = {
    "trading/brokers/factory.py",          # the factory itself
    "cores/market_data/kis_source.py",     # *is* the KIS source
    ...
}
out = subprocess.run(
    ["git", "grep", "-nE", r"\b(DomesticStockTrading|USStockTrading|...)\("],
    capture_output=True, text=True,
).stdout
offenders = []
for line in out.splitlines():
    path = line.split(":", 1)[0]
    if path in allowed: continue
    if path.startswith(("tests/", "prism-us/tests/", "examples/messaging/")): continue
    if re.search(r"^\s*(#|\*|\"|')", line.split(":", 2)[-1]): continue  # 주석/문서열
    offenders.append(line)
assert not offenders, "...:\n  " + "\n  ".join(offenders)
```
git grep + 경로 allowlist + 주석 필터. 새 스캔(응답 형태 누출, kis_devlp 직접 읽기)은 이 구조를 그대로 미러하되, **현재 위반을 `KNOWN_OFFENDERS`(경로 집합)로 동결**하고 "새 항목 추가 금지, Phase N에서 축소" 주석을 단다.

### BROKER_ENV_RESET_FIXTURE
```python
# SOURCE: tests/conftest.py:20-48
_BROKER_ENV = (
    "PRISM_BROKER", "PRISM_TRADING_MODE",
    "PRISM_BUY_AMOUNT_KRW", "PRISM_BUY_AMOUNT_USD",
    "TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET", "TOSS_ACCOUNT_SEQ", "TOSS_BASE_URL",
    "PRISM_MARKET_DATA_SOURCES",
)

@pytest.fixture(autouse=True)
def _neutral_broker_env(monkeypatch):
    for name in _BROKER_ENV:
        monkeypatch.delenv(name, raising=False)
    _reset_market_data_chain()
    yield
    _reset_market_data_chain()
```
prism-us conftest에 이 fixture를 미러. `_reset_market_data_chain`은 `try: from cores.market_data import set_default_chain / except Exception: return` 방어 패턴 그대로.

### TEST_NAMING (프로젝트 관례)
테스트 이름은 문장형 snake_case: `test_entry_point_ignores_a_leftover_kis_config`, `test_no_production_code_constructs_a_kis_trader_directly`. 모듈 docstring은 "왜 이 규칙이 존재하는가"를 서술. 새 파일도 동일하게.

### XFAIL_KNOWN_DEFECT
```python
pytest.param(
    "us_stock_tracking_agent",
    marks=pytest.mark.xfail(
        strict=True,
        reason="prism-us/us_stock_tracking_agent.py:195-199 loads kis_auth "
               "by path at module scope — fixed in Phase 2 (P0 #5)",
    ),
)
```
`strict=True` 필수 — Phase 2에서 고쳐지면 XPASS가 **실패**로 떠서 마크 제거를 강제한다(수정 검증의 자동화).

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `tests/test_no_module_scope_kis_import.py` | UPDATE | 별칭 census, prism-us 진입점, spec_from_file_location AST 규칙 |
| `tests/test_migration_audit_scans.py` | CREATE | kis_devlp 직접 읽기·KIS 응답 형태 누출·BrokerPort 계약·설정 키 생존성 (ratchet 스캔 4종) |
| `prism-us/tests/conftest.py` | UPDATE | 브로커 env 초기화 autouse fixture 추가 |
| `.claude/PRPs/reports/full-migration-audit-report.md` | CREATE | 감사 보고서 초판 (부록 A의 내용 사용) |
| `.claude/PRPs/prds/full-migration-audit.prd.md` | UPDATE | Phase 1 상태 in-progress → (완료 시) complete, 플랜 링크 |

## NOT Building

- 프로덕션 코드 수정 일절 없음 (P0 수정은 Phase 2)
- `examples/messaging/*` 스캔 예외 유지 (기존 트립와이어와 동일하게 제외 — Phase 3에서 처리 방향 결정)
- KIS 응답 형태 누출의 **수정** (스캔만; `prism_core/order_intents.py`의 의도적 이중 형태는 allowlist)
- 런타임 계약 검사(호출 시점 검증) — 정적 스캔으로 충분, 런타임 훅은 과설계

---

## Step-by-Step Tasks

### Task 1: census의 sys.modules 검사를 별칭 집합으로 확장
- **ACTION**: `tests/test_no_module_scope_kis_import.py`의 `_probe_import` 수정
- **IMPLEMENT**: probe 코드가 단일 키 대신 별칭 집합을 검사하고 발견 목록을 출력:
  ```python
  KIS_MODULE_ALIASES = {
      "trading.kis_auth",              # 정상 임포트 경로
      "kis_auth",                      # us_stock_tracking_agent.py:195의 spec 로드 이름
      "prism_root_trading_kis_auth",   # prism-us/tracking/db_schema.py:27의 로더 이름
      "prism_us_stock_trading",        # generate_us_dashboard_json.py / gcp 구독자의 로드 이름
  }
  probe = (
      f"import {module}\n"
      "import sys\n"
      f"hits = sorted(m for m in {sorted(KIS_MODULE_ALIASES)!r} if m in sys.modules)\n"
      "print('KIS_LOADED:' + ','.join(hits) if hits else 'CLEAN')\n"
  )
  ```
  두 census 테스트의 assert 메시지에 `result.stdout` 포함(어느 별칭이 로드됐는지 표시).
- **MIRROR**: SUBPROCESS_PROBE
- **GOTCHA**: `db_schema.py`의 로더는 **콜러블로 전달만** 되고 KIS 분기에서만 실행됨(`settings.py:271-276`) — 임포트만으로는 `prism_root_trading_kis_auth`가 `sys.modules`에 없어야 정상. 별칭 검사가 이를 위반으로 오탐하지 않는지 확인(로드가 안 됐으면 안 잡힘 — 정상).
- **VALIDATE**: `pytest tests/test_no_module_scope_kis_import.py -x -q` — 기존 13개 진입점 여전히 통과

### Task 2: prism-us 진입점을 census에 추가 (알려진 결함은 strict xfail)
- **ACTION**: 같은 파일에 `US_ENTRY_POINTS` 추가 + 전용 probe
- **IMPLEMENT**:
  ```python
  # prism-us modules are run as scripts from the prism-us dir, so the probe
  # inserts that dir on sys.path instead of importing through a package.
  US_ENTRY_POINTS = [
      pytest.param("us_stock_tracking_agent", marks=pytest.mark.xfail(
          strict=True, reason="module-scope spec_from_file_location of kis_auth "
          "(us_stock_tracking_agent.py:195-199) — Phase 2")),
      "us_trigger_batch",
      "us_stock_analysis_orchestrator",
      "us_pending_order_batch",
      "us_performance_tracker_batch",
      "us_telegram_summary_agent",
  ]
  ```
  probe는 기존 `_probe_import`에 `prefix_path: str | None = None` 파라미터를 추가해 재사용:
  ```python
  pre = f"import sys; sys.path.insert(0, {str(REPO_ROOT / 'prism-us')!r})\n" if prefix_path else ""
  ```
  기존 두 census 테스트의 `@pytest.mark.parametrize`를 `ENTRY_POINTS + US_ENTRY_POINTS` 대신 **별도 테스트 2개**로 추가(`test_us_entry_point_*`) — 기존 테스트 ID를 바꾸지 않기 위함.
- **MIRROR**: SUBPROCESS_PROBE, XFAIL_KNOWN_DEFECT
- **IMPORTS**: 기존 파일의 `pytest`, `subprocess`, `sys` 재사용
- **GOTCHA** (3건):
  1. US 에이전트 임포트는 production `.env`를 로드하고 publish_guard 이슈가 있음 — probe env에 반드시 킬스위치 추가: `from messaging.publish_guard import DISABLE_ENV_VAR` 후 `env={..., DISABLE_ENV_VAR: "1"}`. (`prism-us/tests/conftest.py:28-36`이 같은 이유로 이미 설정함)
  2. `us_telegram_summary_agent` 등은 무거운 의존(mcp_agent 등)을 끌 수 있음 — 임포트 실패가 KIS와 무관한 사유(외부 API 키 부재 등)면 해당 모듈은 목록에서 빼고 보고서에 "census 불가 — 사유" 기록. **timeout=300 유지.**
  3. `us_pending_order_batch`는 `ALLOWED`(AST 스캔)에는 있지만 census는 통과해야 함 — KIS 임포트가 `selected != "kis"` 가드 뒤 함수 스코프이므로 Toss probe에서 CLEAN이어야 정상.
- **VALIDATE**: `pytest tests/test_no_module_scope_kis_import.py -q` — us_stock_tracking_agent 2건이 `xfail`, 나머지 US 진입점 pass

### Task 3: 모듈 스코프 `spec_from_file_location` AST 규칙 추가
- **ACTION**: 같은 파일에 `_module_scope_kis_path_loads()` + 테스트 추가
- **IMPLEMENT**: `_module_scope_imports`와 같은 걷기 골격에서 `ast.Expr`/`ast.Assign`/`ast.AnnAssign` 노드의 값을 `ast.walk`로 내려가 `ast.Call` 중 함수명이 `spec_from_file_location`(Attribute든 Name이든 `.attr`/`.id`로 판별)인 것을 찾고, **인자 어딘가의 문자열 상수에 `kis_auth` 또는 `us_stock_trading`이 포함**되면 위반:
  ```python
  def _module_scope_calls(tree):
      pending = list(tree.body)
      while pending:
          node = pending.pop()
          if isinstance(node, (ast.Try, ast.If, ast.With)):
              for attr in ("body", "orelse", "finalbody", "handlers"):
                  pending.extend(getattr(node, attr, None) or [])
          elif isinstance(node, ast.ExceptHandler):
              pending.extend(node.body)
          elif isinstance(node, (ast.Expr, ast.Assign, ast.AnnAssign)):
              for call in ast.walk(node):
                  if isinstance(call, ast.Call):
                      yield node.lineno, call
  ```
  위반 판정 헬퍼: `_call_targets_kis(call)` — `call.func`가 `spec_from_file_location`이고 `any("kis_auth" in c.value or "us_stock_trading" in c.value for c in ast.walk(call) if isinstance(c, ast.Constant) and isinstance(c.value, str))`. 문자열이 아닌 Path 조합(`PROJECT_ROOT / "trading/kis_auth.py"`)도 상수 조각 `"trading/kis_auth.py"`가 walk에 걸리므로 커버됨.
  테스트 이름: `test_no_module_scope_kis_load_by_file_path`. **알려진 위반 2건을 `KNOWN_OFFENDERS` 동결 집합으로**:
  ```python
  KNOWN_PATH_LOAD_OFFENDERS = {
      "prism-us/us_stock_tracking_agent.py",   # Phase 2 P0 — 수정 시 이 줄 삭제
      "examples/generate_us_dashboard_json.py", # Phase 3 — KIS_US_AVAILABLE 게이트
  }
  ```
  offender가 집합에 있으면 통과(단 집합에 있는데 위반이 **없으면** 실패 — "stale allowlist" 검사로 ratchet 강제):
  ```python
  stale = KNOWN_PATH_LOAD_OFFENDERS - {path for path, _ in found}
  assert not stale, f"fixed but still allowlisted — remove from KNOWN_PATH_LOAD_OFFENDERS: {stale}"
  new = [f"{p}:{ln}" for p, ln in found if p not in KNOWN_PATH_LOAD_OFFENDERS]
  assert not new, "..."
  ```
- **MIRROR**: AST_MODULE_SCOPE_WALK + RATCHET_GREP_TRIPWIRE의 allowlist 철학
- **GOTCHA**: `prism-us/tracking/db_schema.py:21-35`의 로드는 **함수 내부**(로더 콜러블)이므로 모듈 스코프 걷기에 안 걸림 — 의도된 설계라 offender 목록에 넣지 말 것. `examples/messaging/gcp_pubsub_subscriber_example.py:435`도 함수 스코프라 이 규칙 대상 아님(보고서에만 기록).
- **VALIDATE**: `pytest tests/test_no_module_scope_kis_import.py::test_no_module_scope_kis_load_by_file_path -q`

### Task 4: `kis_devlp.yaml` 직접 읽기 스캔 (신규 파일 시작)
- **ACTION**: `tests/test_migration_audit_scans.py` 생성, 첫 테스트 작성
- **IMPLEMENT**: 모듈 docstring에 "Phase 1 감사 스캔 — 각 KNOWN_OFFENDERS는 동결 목록이며 추가 금지, 해당 Phase에서 축소" 명시. git grep 패턴 `kis_devlp\.yaml`, 대상은 **파일을 여는 코드**이므로 주석/에러 메시지 문자열 오탐을 줄이기 위해 라인 필터: `open(`, `Path(`, `/ "config"`, `yaml.` 중 하나 포함 라인만 위반 후보로.
  ```python
  ALLOWED = {
      "trading/kis_auth.py",                    # 소유자
      "trading/domestic_stock_trading.py",      # KIS 전용 (AST ALLOWED와 동일 사유)
      "prism-us/trading/us_stock_trading.py",   # KIS 전용
      "trading/brokers/settings.py",            # broker_config_hint가 파일명을 안내문으로 씀
  }
  KNOWN_OFFENDERS = {
      "examples/generate_dashboard_json.py",             # Phase 3 — mode를 trading_settings()로
      "examples/messaging/gcp_pubsub_subscriber_example.py",  # Phase 3 — 처리 방향 결정
  }
  ```
  stale-allowlist 검사 포함(Task 3과 동일 패턴).
- **MIRROR**: RATCHET_GREP_TRIPWIRE
- **IMPORTS**: `re`, `subprocess`, `pathlib.Path`, `pytest`
- **GOTCHA**: `tests/`·`prism-us/tests/`·`docs/`·`.claude/`는 경로 프리픽스로 제외. grep은 git-tracked만 보므로 로컬 설정 파일은 자동 제외.
- **VALIDATE**: `pytest tests/test_migration_audit_scans.py -q -k devlp`

### Task 5: KIS 응답 형태 누출 스캔
- **ACTION**: 같은 파일에 두 번째 테스트
- **IMPLEMENT**: git grep 패턴 `\b(rt_cd|msg1|ORD_DVSN|getBody\(\)|output1|output2)\b` (`output` 단독은 오탐 과다 — 제외하고 `getBody()`가 대신 잡음).
  ```python
  ALLOWED_SHAPE = {
      "trading/kis_auth.py", "trading/domestic_stock_trading.py",
      "prism-us/trading/us_stock_trading.py", "trading/brokers/kis_adapter.py",
      "cores/kis_market_snapshot.py", "cores/market_data/kis_source.py",
  }
  KNOWN_SHAPE_OFFENDERS = {
      "prism_core/order_intents.py",          # 의도적 이중 형태 (rt_cd|code OR) — Phase 6 재검토
      "cores/archive/data_enricher.py",       # Phase 3 — KIS 직접 호출 제거와 함께
      "tools/check_kr_pending_readiness.py",  # Phase 3
      "tools/fill_chaser.py",                 # psbl_qty 등 — Phase 5 BrokerPort 정리와 함께
      "tools/trend_exit_seller.py",           # 존재 시 — 첫 실행 결과로 확정
  }
  ```
  **첫 로컬 실행 결과로 KNOWN 목록을 확정**할 것(스캔이 찾은 실제 파일 집합과 정확히 일치해야 stale 검사가 성립).
- **MIRROR**: RATCHET_GREP_TRIPWIRE
- **GOTCHA**: `trading/samples/`, `tests/` 프리픽스 제외. 주석 필터(`^\s*#`)는 정규식 검사 라인 것 그대로.
- **VALIDATE**: `pytest tests/test_migration_audit_scans.py -q -k shape`

### Task 6: BrokerPort 계약 — 미정의 메서드 호출 감사
- **ACTION**: 같은 파일에 세 번째 테스트
- **IMPLEMENT**: 알려진 트레이더 호출부의 메서드명이 실제 구현(양 브로커)에 존재하는지 정적 확인. grep이 아니라 **curated 목록 + hasattr** 방식(오탐 없는 최소주의):
  ```python
  # 트레이더 객체에 대해 프로덕션이 실제로 호출하는 메서드 (조사로 확정된 것)
  CALLED_ON_TRADERS = {
      "tools/fill_chaser.py": {"get_revisable_orders", "get_unfilled_orders"},
      "prism-us/us_stock_tracking_agent.py": {"is_market_open", "is_reserved_order_available"},
  }
  KNOWN_CONTRACT_GAPS = {
      # (file, method): 어느 구현에 없는가 — Phase 2/5에서 축소
      ("tools/fill_chaser.py", "get_revisable_orders"): "toss",
      ("tools/fill_chaser.py", "get_unfilled_orders"): "toss",
      ("prism-us/us_stock_tracking_agent.py", "is_market_open"): "toss+port",
      ("prism-us/us_stock_tracking_agent.py", "is_reserved_order_available"): "toss+port",
  }
  ```
  검증 로직: ① 각 메서드가 해당 파일에 여전히 존재하는지 git grep로 확인(사라졌으면 stale — 목록 갱신 강제) ② `from trading.brokers.toss.adapter import TossBroker`에 `hasattr` — gap 표기와 실상이 일치하는지 확인(구현되면 KNOWN에서 제거 강제). BrokerPort 쪽은 `trading.brokers.base.BrokerPort`의 어노테이션/멤버로 확인.
- **MIRROR**: RATCHET 철학 (stale 검사 필수)
- **IMPORTS**: `from trading.brokers.toss.adapter import TossBroker` — **GOTCHA**: TossBroker 임포트가 설정 파일을 요구하면 클래스 자체(`hasattr(TossBroker, ...)`)만 검사하고 인스턴스화하지 않는다. 임포트 자체가 실패하면 그 사실이 별도 결함이므로 테스트를 fail로 둔다.
- **VALIDATE**: `pytest tests/test_migration_audit_scans.py -q -k contract`

### Task 7: 설정 키 생존성 (`toss_config.yaml` 키가 실제로 읽히는가)
- **ACTION**: 같은 파일에 네 번째 테스트
- **IMPLEMENT**: 두 검사:
  1. **정적**: `settings.buy_amount(` 호출부가 `trading/brokers/settings.py`·`tests/` 밖에 ≥1곳 존재해야 함. 현재 0곳 → `pytest.mark.xfail(strict=True, reason="settings.buy_amount() has zero callers — factory.py:107 reads env only; Phase 2 P0 #1")`.
  2. **동적**: `monkeypatch`로 broker=toss 설정 후 `from trading.brokers.settings import trading_settings` 호출이 `default_unit_amount`/`auto_trading`/`default_mode` 키를 반환하는지(설정 파일 없이도 `_TRADING_DEFAULTS` 폴백으로) — 이건 현재도 pass여야 하는 기준선.
- **MIRROR**: XFAIL_KNOWN_DEFECT
- **GOTCHA**: 동적 검사는 실제 `toss_config.yaml`(로컬 자격증명 포함)을 읽을 수 있음 — `monkeypatch.setenv`로 격리하거나 반환 **키 존재만** 검사하고 값은 검사하지 않는다.
- **VALIDATE**: `pytest tests/test_migration_audit_scans.py -q -k liveness` — 1번은 xfail, 2번은 pass

### Task 8: prism-us conftest에 브로커 env 초기화 추가
- **ACTION**: `prism-us/tests/conftest.py` UPDATE — 파일 끝에 fixture 추가
- **IMPLEMENT**: BROKER_ENV_RESET_FIXTURE를 그대로 미러(튜플·fixture·`_reset_market_data_chain` 3요소). docstring에 root `tests/conftest.py`를 참조 표기.
- **MIRROR**: BROKER_ENV_RESET_FIXTURE
- **GOTCHA**: prism-us conftest는 `os.chdir(PRISM_US_DIR)`를 이미 수행(`:26`) — fixture 추가가 이 동작을 건드리지 않도록 **기존 코드 무수정, 추가만**. `cores.market_data`는 root 경로가 `sys.path`에 있어(`:22`) 임포트 가능하지만 방어 try/except 유지.
- **VALIDATE**: `cd prism-us && python -m pytest tests/ -q -x --co | head` (수집 오류 없음) 후 빠른 서브셋 `python -m pytest tests/test_multi_account_us.py -q`

### Task 9: 감사 보고서 초판 작성
- **ACTION**: `.claude/PRPs/reports/full-migration-audit-report.md` CREATE
- **IMPLEMENT**: 부록 A(이 플랜 하단)의 내용을 심각도 구조로 정리:
  - §1 P0 실금전 4건(+US 모듈 스코프 로드) — 각 항목: 증상 / 원인 file:line / 고정한 트립와이어 테스트명 / 수정 Phase
  - §2 KIS 우회 잔재 (BYPASS/GUARDED-LAZY/UNCLEAR 태그별)
  - §3 데이터 소스 (체인 인벤토리 표, 로그인 의존 call site 목록, 네이버 4곳, git 3파 연혁)
  - §4 하드코딩 (카테고리 6종, 매매 경로 우선순위 6건)
  - §5 KR/US 비대칭 표 (25행, 태그별)
  - §6 트립와이어 커버리지 — 사각지대 7종 ↔ Phase 1에서 추가된 테스트 대응표
  - §7 미해결 질문 5건 (PRD와 동일)
- **VALIDATE**: 보고서의 모든 file:line이 부록 A와 일치, 각 P0에 트립와이어 테스트명 연결됨

### Task 10: PRD 상태 갱신 + 전체 검증
- **ACTION**: PRD Phase 1 행을 `in-progress` + PRP Plan 링크로 갱신 (구현 완료 시 `complete`)
- **VALIDATE**: 아래 Validation Commands 전체 실행

---

## Testing Strategy

### Unit Tests (이번 단계의 산출물 자체가 테스트)

| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| 기존 census 13종 | PRISM_BROKER=toss probe | CLEAN (회귀 없음) | — |
| US census 5종 | 동일 + prism-us path | CLEAN | us_tracking_agent은 strict xfail |
| path-load AST 규칙 | git-tracked *.py | KNOWN 2건만, stale 없음 | Path 조합 상수 탐지 |
| kis_devlp 스캔 | git grep | KNOWN 2건만 | 에러 메시지 문자열 오탐 필터 |
| 형태 누출 스캔 | git grep | KNOWN 목록과 정확 일치 | `output` 단독 오탐 배제 확인 |
| 계약 감사 | hasattr(TossBroker) | KNOWN_GAPS와 실상 일치 | TossBroker 임포트 실패 시 fail |
| buy_amount 생존성 | git grep 호출부 | strict xfail (0 callers) | Phase 2 후 XPASS→fail로 제거 강제 |

### Edge Cases Checklist
- [x] stale allowlist (고쳐졌는데 목록에 남음) — 모든 ratchet에 stale 검사 필수
- [x] xfail이 조용히 XPASS로 남는 것 — 전부 `strict=True`
- [x] 로컬 `.env` 오염 — Task 8이 해소
- [x] 서브프로세스 timeout — 300s 유지, US 에이전트 무거움 감안

---

## Validation Commands

### Static Analysis
```bash
python -m py_compile tests/test_migration_audit_scans.py tests/test_no_module_scope_kis_import.py
```
EXPECT: 무출력 (구문 오류 없음)

### Unit Tests
```bash
python -m pytest tests/test_no_module_scope_kis_import.py tests/test_migration_audit_scans.py -v
```
EXPECT: 전부 pass 또는 xfail (XPASS·fail 0건). xfail 사유가 전부 Phase 번호를 명시

### Full Test Suite
```bash
python -m pytest tests/ -q
cd prism-us && python -m pytest tests/ -q
```
EXPECT: 기존 대비 신규 실패 0건 (Task 8 fixture가 기존 US 테스트를 깨지 않는지 특히 확인 — 브로커 env에 의존하던 숨은 테스트가 드러나면 해당 테스트에 `monkeypatch.setenv` 명시가 올바른 수정)

### Manual Validation
- [ ] `git stash`로 임의 프로덕션 파일에 `from trading.kis_auth import ...`를 모듈 스코프에 넣고 AST 테스트가 file:line으로 실패하는지 확인 후 원복
- [ ] 보고서의 P0 4건 각각에 대응 트립와이어 테스트명이 적혀 있는지

---

## Acceptance Criteria
- [ ] 사각지대 7종 각각에 대응 테스트 존재 (①함수 스코프/경로 로드 ②spec_from_file_location ③prism-us census ④kis_devlp 직접 읽기 ⑤형태 누출 ⑥BrokerPort 계약 ⑦설정 키 생존성)
- [ ] 알려진 결함 전부 strict xfail 또는 KNOWN allowlist로 고정, stale 검사 포함
- [ ] CI green (fail 0, XPASS 0)
- [ ] 감사 보고서 초판 존재, P0 ↔ 테스트 매핑 완비
- [ ] 프로덕션 코드 diff 0줄

## Completion Checklist
- [ ] 테스트 이름·docstring이 기존 파일의 서술형 스타일과 동일
- [ ] 모든 KNOWN 목록에 축소 담당 Phase 주석
- [ ] 하드코딩 값 없음 (경로는 `REPO_ROOT` 기준)
- [ ] PRD Phase 1 상태 갱신

## Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| US 진입점 census가 KIS 외 사유로 임포트 실패 | M | M | 실패 사유가 KIS 무관이면 목록 제외 + 보고서 기록 (Task 2 GOTCHA 2) |
| 형태 누출 grep 오탐으로 KNOWN 목록 비대 | M | L | 첫 실행 결과로 목록 확정, 패턴에서 `output` 단독 제외 |
| Task 8 fixture가 기존 US 테스트의 숨은 env 의존을 드러냄 | M | L | 드러난 테스트에 명시적 `monkeypatch.setenv` — 그것이 곧 목적 |
| census 실행 시간 증가 (진입점 13→18, 각 2회 probe) | H | L | 허용 (CI 수 분 수준). 필요 시 `-n auto` 병렬화는 후속 |

## Notes
- 이 플랜의 원칙: **Phase 1은 관측만 한다.** 코드를 고치고 싶은 유혹(한 줄이면 되는 것도 있음)을 참을 것 — 수정은 Phase 2~6에서 xfail→pass 전환으로 검증되어야 한다.
- `examples/messaging/`은 기존 두 트립와이어 모두 제외 중이며 이번에도 제외 유지(단 kis_devlp 스캔의 KNOWN에는 등재) — 전면 처리는 Phase 3의 결정 사항.

---

## 부록 A — 조사 결과 원본 (보고서 작성용, 2026-08-18 4개 병렬 조사)

### A1. P0 실금전 (Phase 2 대상)
1. **매수 금액 설정 사장**: `trading/brokers/factory.py:107` env만 확인 → `toss_config.yaml` `default_unit_amount` 미도달 → `trading/brokers/toss/adapter.py:49` `DEFAULT_BUY_AMOUNT_KRW=100_000` 폴백. `settings.buy_amount()`(`settings.py:150-157`) 호출부 0. 연관: Toss 계좌 dict에 `buy_amount_krw` 없음(`stock_tracking_agent.py:520-522`) → `cores/regime_policy.py:308-313` → `stock_tracking_agent.py:3616-3625` `_regime_floor_block=True` 매수 거부. US 동일(`us_stock_tracking_agent.py:854-856`, `:3359-3369`).
2. **강제청산 감지 무력화**: `cores/corporate_status.py:87` KIS 직접 임포트, `stock_tracking_agent.py:3103-3104` 호출, Toss에서 `except`로 `{}` — TIER0 상장폐지/거래정지 감지 불능.
3. **US 소수점 매도 불가**: `prism-us/tracking/db_schema.py:1104-1119` `int(total_quantity)` 절삭(0.44→0). 호출 `us_stock_tracking_agent.py:2887`, 수량 출처는 Toss에서 Decimal. + `us_stock_tracking_agent.py:2802` `is_market_open`/`is_reserved_order_available` 호출 — BrokerPort(`trading/brokers/base.py:92-163`)·TossBroker 모두 미보유 → AttributeError → `:2803` except → `will_queue=True` → `:2822` full_exit 분기.
4. **DB 경로 분열**: `stock_tracking_db.sqlite` 20곳. 상대경로 그룹: `stock_tracking_agent.py:204`, `stock_tracking_enhanced_agent.py:57`, `prism-us/us_stock_tracking_agent.py:530`, `compress_trading_memory.py:132,383,514`, `retry_journal_entry.py:189`, `tools/compare_position_ledger.py:22`, `cores/llm/features/trade_history.py:72` 등. PROJECT_ROOT 그룹: `performance_tracker_batch.py:36`, `weekly_insight_report.py:25`, `tools/hardstop_seller.py:117`, `tools/fill_chaser.py:152`, `tools/trend_exit_seller.py:161` 등. + `trading/brokers/toss/dryrun.py:48` 상대 `toss_dryrun.sqlite`.
5. **US 모듈 스코프 KIS 로드**: `prism-us/us_stock_tracking_agent.py:195-199` `spec_from_file_location("kis_auth", PROJECT_ROOT/"trading/kis_auth.py")` 무조건 실행. `ka` 사용처 `:858,860`(가드됨), `:885,887`(비가드). 테스트 우회: `prism-us/tests/test_issue_448_distribution_days_prompt.py:71-100` monkeypatch.

### A2. KIS 우회 잔재 (Phase 3 대상)
- [BYPASS] `stance_mark.py:40-41` 사문 임포트 2건(미사용). census에 있으나 `build_fetcher()` 미호출로 통과 중.
- [BYPASS] `cores/archive/data_enricher.py:196` `DomesticStockTrading(mode="demo")` 직접 + KIS 형태(`:212` tr_id, `:221` `_request`, `:225,263` `getBody().output2`). 경유: `cores/archive/price_tracker.py:132`, `cores/archive/ingest.py:19`.
- [BYPASS] `tools/check_kr_pending_readiness.py:406-410` `domestic.ka` 속성 경유(`getEnv`/`get_configured_accounts`), KIS 필드 `:427,430`. 실패 시 `_unknown_report`.
- [BYPASS] `examples/generate_dashboard_json.py:74-80` `kis_devlp.yaml` 모듈 스코프 read → `default_mode`가 `:128` trading_mode, `:1387` CLI 기본값, `:156` `domestic_trader(mode=...)`로 전파. `_live_trading_available()`(`:82-98`)은 이미 팩토리 사용(대비).
- [BYPASS] `examples/generate_us_dashboard_json.py:57-71` 모듈 스코프 KIS 로드 → `:165` `KIS_US_AVAILABLE` 게이트가 팩토리 경로(`:171-173`)를 차단 → Toss에서 US 대시보드 빈 값. (`:83-85` trading_settings는 이미 적용됨)
- [BYPASS] `examples/messaging/redis_subscriber_example.py:105,138` / `gcp_pubsub_subscriber_example.py:63-69(kis_devlp),478,521(KR 주문),432-442+577,619(US 주문 KIS 직행),707(LIVE 자가진단)` — 문서상 cron 대상(`SUBSCRIBER_OPS_HARNESS.md:71`, `EXTERNAL_SUBSCRIBER_GUIDE.md:113-116`)이나 **운영자 확인 결과 현재 미운영**.
- [UNCLEAR] `trigger_batch.py:17,282` KIS 스냅샷 무조건 시도(`cores/kis_market_snapshot.py:90` lazy KIS) → `:290` except로 Naver 폴백. Toss에서 매회 실패 비용.
- [UNCLEAR] `prism_core/order_intents.py:412,427-429` `rt_cd|code`, `message|msg1` 이중 형태, 기본 `broker="KIS"`.

### A3. 데이터 소스 (Phase 4 대상)
**체인**(`cores/market_data/__init__.py`): `_BUILDERS={krx,fdr,kis,naver,toss}`(`:65`), 기본 `krx,fdr`(`:79`), `PRISM_MARKET_DATA_SOURCES`(`:98`). `get_market_sector_map`(`:149`)만 체인 무시 Naver 무조건 폴백. 인트라데이 분기 `:214-259`.
**소스 커버리지**: krx_source(로그인, 전 항목, sector_map 미지원) / fdr(OHLCV·지수 2종·시총 근사) / naver(수급 최근 10세션, 섹터맵 EUC-KR 스크레이프 ~80요청) / kis(OHLCV·지수·수급·인트라데이) / toss(opt-in, OHLCV·지수·수급·인트라데이·종목명). **krx_openapi는 `_BUILDERS` 미등록** — `cores/krx_openapi_snapshot.py`(EOD 전용, 수급 없음)는 스냅샷 계층에만 존재. 스냅샷 선택: `trigger_batch.py:279-317` KIS+KRX_OPENAPI → Naver.
**로그인 클라이언트**(`krx_data_client`, vendored: KRX_ID/KRX_PW·카카오 2FA·`~/.krx_session.json`·MDC getJsonData.cmd) 직접 의존: `trigger_batch.py:19-24(모듈 스코프),63,128,163,167,272,1322,1792`, `tracking/helpers.py:63,219`, `tracking/compression.py:297`, `weekly_insight_report.py:160`, `weekly_market_facts.py:117-141`, `stock_tracking_enhanced_agent.py:166,295,1097`, `performance_tracker_batch.py:40-43`, `update_stock_data.py:16`, `events/jeoningu_price_fetcher.py:9-25`, `cores/market_data/krx_source.py:61`(체인 내).
**pykrx 직접**: `cores/stock_chart.py:1462`, `stock_analysis_orchestrator.py:167,506`, `tools/trend_exit_seller.py:385,418`, `utils/backfill_performance_tracker.py:17`, `utils/migrate_watchlist_to_performance_tracker.py:33`, `weekly_market_facts.py:137`.
**FDR 직접**: `trigger_batch.py:239`, `tools/rs_rating_backtest.py:53,108,146`.
**네이버 4곳**(공유 클라이언트 없음): `cores/market_data/naver_source.py`(trend API+섹터), `cores/naver_market_snapshot.py`(marketValue+fchart, PAGE_SIZE=100), `weekly_market_facts.py:225-300`(investorDealTrendDay, read_html 2단 헤더, 날짜 `"26.07.24"`), `kakao_bot/adapters/prism/report_adapter.py:756-780`(사설 `_fetch_daily_pair` 직접 호출).
**문서 부패**: `.env.example:8-16` KRX_ID/PW "필수", `:18` "대체할 예정"; `docs/SETUP.md:175-180` 폐기된 PyPI 서버 + KAKAO 계정 안내.
**git 연혁**: 07-22 Naver 스냅샷 폴백(fd78cde,2bad235) → 08-04 IP차단·체인 탄생(9c2eeb7)·OpenAPI(f949576) → 08-05 KIS 소스·MCP 체인화(4773fa3) → 08-06 naver_source(04ed29c) → 08-17 toss_source(27be7f7) → 08-18 무자격 섹터(c1707a3).

### A4. 하드코딩 (Phase 2·6 대상)
- 배포 경로: `tools/backfill_us_journal.py:24` `/root/prism-insight`, `tools/forward_campaign_events.py:39,42` `/home/prism/...`(다른 배포 경로 혼재).
- Toss base URL 4곳: `toss/auth.py:50`(원본 DEFAULT_BASE_URL), `factory.py:85`, `toss/smoke.py:55`, `toss_source.py:106`.
- `default_unit_amount` 4곳 값 분열: settings `_TRADING_DEFAULTS` 100_000 / toss adapter 100_000 / toss_config.yaml 100000 / **kis_devlp.yaml 10000 (10배 차)**.
- `MAX_SLOTS=10` 5곳(LLM 프롬프트 문자열 2곳 포함: `cores/agents/trading_agents.py:531`, `prism-us/.../trading_agents.py:200`).
- `oneil_fallback.py` KR/US **바이트 동일** 중복(매도 임계값 8종, `tools/hardstop_seller.py:300` 경유 실매도 사용).
- 스크리닝 상수 수동 미러: `REGIME_SCORE_WEIGHTS`(`trigger_batch.py:556-562`≡`us_trigger_batch.py:92-98`), EXTENSION_ADR, LOOKBACK 2종, TRIGGER_CRITERIA(값은 상이).
- KST 정의 6곳 + offset 변형 2곳(`cores/archive/persistent_insights.py:30`, `insight_agent.py:44`).
- `t.me/stock_ai_agent` 13곳(`telegram_ai_bot.py`) vs env `TELEGRAM_CHANNEL_USERNAME`(`telegram_bot_agent.py:153`) 무시.
- Telegram sendMessage URL 3곳(tools/oauth_healthcheck·firecrawl_quality_check·subscriber_healthcheck).
- 모델명 48리터럴/30파일 (gpt-5.6-luna 12, gpt-5.4-mini 13, gpt-5.6-sol 4, terra 2, legacy 다수) — `cores/llm/models.py` 미경유.
- `toss/dryrun.py:135` 통화를 티커 형태로 추론, `:48` 상대 DB 경로.
- Toss KR 주문 경로가 `prism_core/time_windows.py` 미경유(어댑터 `:302-311,350-351,376-380` 자체 세션 모델).

### A5. KR/US 비대칭 (Phase 5 대상, 태그: US-MISSING 9 / DIVERGED 5)
- [US-MISSING] 지연 KIS 임포트(v2.21.2): US 트래킹 에이전트 모듈 스코프 로드(A1-5).
- [US-MISSING] census/AST 트립와이어의 US 커버(본 Phase 1이 해소).
- [US-MISSING] 대시보드 가용성 팩토리 질의: `generate_us_dashboard_json.py:165` 게이트(A2). 테스트도 KR만(`tests/test_lazy_kis_call_sites.py:99-103`).
- [US-MISSING] `get_holding_quantity_checked`: `prism-us/trading/us_stock_trading.py` 부재(`tests/test_kis_adapter.py:19-20`에 명시 인정).
- [US-MISSING] `_request_with_retry`(EGW00215): KR `domestic_stock_trading.py:1570-1594`에만.
- [US-MISSING] 브로커 티어 가격 조회: KR `tracking/helpers.py:96-143` vs US `us_stock_tracking_agent.py:264-304`(yfinance 3회→DB, `:281` 주석은 "미러" 주장).
- [US-MISSING] `PRISM_MARKET_DATA_SOURCES`/소스 체인: US 없음(yfinance 단일) — **문서화만, 구현은 후속 PRD**.
- [US-MISSING] US conftest 브로커 env 초기화(본 Phase 1 Task 8이 해소).
- [US-MISSING] prism-us 브로커/Toss 테스트 0개(KR 5,546줄).
- [DIVERGED] `_safe_float` KR 부재 / `_safe_int` 구현 상이(strip 유무).
- [DIVERGED] `compute_us_fractional_sell_quantity` int 절삭(A1-3).
- [DIVERGED] trading_settings: US 대시보드 적용·KR 대시보드 미적용(`generate_dashboard_json.py:74-80`).
- [DIVERGED] `PRISM_TRADING_MODE`: US pending-order는 DB 행의 mode 사용(`us_pending_order_batch.py:168`).
- [DIVERGED] `is_market_open` 등 포트 외 메서드(A1-3). fill_chaser는 양 브로커 공통 결함(`get_revisable_orders`/`get_unfilled_orders` — Toss에서 조용히 no-op, `tools/fill_chaser.py:322,334,349`).
- 참고: `trading/brokers/kis_adapter.py` `KisBroker` 프로덕션 미사용(팩토리 `:181,214`가 raw 클래스 반환, `broker_label` `:217-225`은 "`.name` 없음→KIS" 폴백 의존).
- v2.21.x ~15커밋 중 US 반영 2커밋(1696525, 6b64ab4).

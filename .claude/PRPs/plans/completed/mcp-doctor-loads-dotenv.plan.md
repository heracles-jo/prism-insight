# Plan: 진단이 런타임과 같은 환경을 보게 한다

## Summary

`tools/mcp_doctor.py` 는 `.env` 를 읽지 않고 `os.environ` 만 본다. 런타임은 진입점마다 `load_dotenv()` 를 하므로, 셸에서 진단을 그냥 돌리면 **실제로는 정상인 서버가 `UNSET_ENV` 로 FAIL 처리된다.** 이 도구의 존재 이유가 호스트 간 결과를 비교하는 것인데, 거짓 FAIL 은 진짜 고장과 구별되지 않는다.

## User Story

As a **MCP 서버가 왜 안 뜨는지 찾는 운영자**,
I want **진단 도구가 런타임과 같은 환경을 보기를**,
So that **진단 결과를 그대로 믿을 수 있고 `set -a; source .env` 를 아는 사람만 진실을 보지 않는다.**

## Problem → Solution

`mcp_doctor` 가 `os.environ` 만 봄 → `.env` 에 있는 값이 UNSET 으로 보임 → 정상 서버가 FAIL
**→** 런타임과 같은 방식으로 `.env` 를 로드하고, **어디서 읽었는지 출력에 남긴다**

## Metadata
- **Complexity**: **Small** — 1 파일 + 테스트, 실질 변경 10 줄 안팎
- **Source PRD**: `.claude/PRPs/prds/mcp-config-migration-visibility.prd.md`
- **PRD Phase**: Phase 2 — 진단이 런타임과 같은 것을 보게
- **Estimated Files**: 2

---

## UX Design

### Before
```
$ python tools/mcp_doctor.py
  FAIL firecrawl    env FIRECRAWL_API_KEY <- ${FIRECRAWL_API_KEY} [UNSET]
                    problems: UNSET_ENV
  unhealthy servers: 3

$ set -a; source .env; set +a; python tools/mcp_doctor.py
  ok  firecrawl     env FIRECRAWL_API_KEY <- ${FIRECRAWL_API_KEY} [set]
  unhealthy servers: 0          ← 같은 호스트, 다른 결과
```

### After
```
$ python tools/mcp_doctor.py
  env: .env (loaded)            ← 무엇을 읽었는지 밝힌다
  ok  firecrawl     env FIRECRAWL_API_KEY <- ${FIRECRAWL_API_KEY} [set]
  unhealthy servers: 0          ← source 여부와 무관하게 같은 결과
```

### Interaction Changes

| Touchpoint | Before | After | Notes |
|---|---|---|---|
| 셸에서 그냥 실행 | 거짓 FAIL | 런타임과 일치 | 이 Phase 의 본체 |
| 출력 머리말 | `host` / `root` / `config` | `env` 한 줄 추가 | 호스트 간 비교가 목적이므로 출처를 밝힌다 |
| `--json` | 변경 없음 | `env` 키 추가 | 기계 비교용 |
| 이미 `source .env` 한 셸 | 정상 | **정상 유지** | 실환경이 파일을 이겨야 한다 |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `tools/mcp_doctor.py` | 25-47 | 임포트 구역. `sys.path` 조작 후 `cores.llm` 을 늦게 임포트하는 구조 |
| P0 | `tools/mcp_doctor.py` | 234-260 | `main()` 과 `payload` 구성. `env` 를 넣을 자리 |
| P0 | `tools/mcp_doctor.py` | 290-300 | 사람이 읽는 출력의 머리말. `config:` 줄 옆에 붙인다 |
| P0 | `tools/mcp_doctor.py` | 77-118 | `_check_env` — 실제로 `os.environ` 을 읽는 곳. **여기는 안 고친다** |
| P1 | `tests/test_mcp_doctor.py` | 1-15, 63-78 | 테스트 관례. `monkeypatch` 로 환경을 세우고 `_check_env` 를 직접 부른다 |
| P2 | `cores/analysis.py` | `load_dotenv` 호출부 | 런타임이 `.env` 를 로드하는 방식 |

## External Documentation

| Topic | Source | Key Takeaway |
|---|---|---|
| `python-dotenv` | `requirements.txt:29` (`python-dotenv>=1.0.0`) | 이미 의존성이다. 새로 추가할 것 없음 |
| `load_dotenv` 의 기본 동작 | 라이브러리 규약 | **기존 환경변수를 덮어쓰지 않는다**(`override=False` 가 기본). 실환경이 파일을 이긴다 — 이 도구에 필요한 동작 그대로다 |

---

## Patterns to Mirror

### LATE_IMPORT_AFTER_SYSPATH
```python
# SOURCE: tools/mcp_doctor.py:36-47
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cores.llm import config_loader  # noqa: E402
from cores.llm.config_loader import (  # noqa: E402
    load_mcp_registry,
    load_report_mcp_registry,
)
```
이 파일은 `tools/` 아래에 있어 리포 루트를 `sys.path` 에 넣은 **뒤에** 리포 모듈을 임포트한다. `# noqa: E402` 가 그 표식이다.

### PAYLOAD_THEN_PRINT
```python
# SOURCE: tools/mcp_doctor.py:249-259
    payload = {
        "host": os.uname().nodename,
        "project_root": str(project_root),
        "config": {
            "native": str(config_loader._NATIVE_CONFIG),
            "native_exists": config_loader._NATIVE_CONFIG.exists(),
            "legacy": str(config_loader._LEGACY_CONFIG),
            "legacy_exists": config_loader._LEGACY_CONFIG.exists(),
        },
        "registries": {},
    }
```
```python
# SOURCE: tools/mcp_doctor.py:290-297
    print(f"host: {payload['host']}")
    print(f"root: {payload['project_root']}")
    cfg = payload["config"]
    print(
        f"config: native={'y' if cfg['native_exists'] else 'n'} "
        f"legacy={'y' if cfg['legacy_exists'] else 'n'}"
    )
```
**`payload` 를 먼저 만들고 그것을 출력한다.** `--json` 과 사람용 출력이 같은 자료에서 나오므로, 새 정보는 `payload` 에 넣은 뒤 출력에서 꺼내 쓴다.

### NEVER_PRINT_VALUES
```python
# SOURCE: tools/mcp_doctor.py:77-82 (docstring)
    """Report env var names and whether a value resolves — never the value.
```
```python
# SOURCE: tests/test_mcp_doctor.py:63-64
class TestEnvReporting:
    """Values must never appear — two were leaked into a chat log this way."""
```
**값은 절대 출력하지 않는다.** 이름과 set/unset 만. 이 파일의 도크스트링이 그 사고를 기록하고 있다.

### FALSE_POSITIVE_PRINCIPLE
```python
# SOURCE: tools/mcp_doctor.py:51-52
# Being strict here matters: this output is meant to be diffed across hosts,
# and a false positive is indistinguishable from a real breakage.
```
이 Phase 가 고치는 것이 정확히 이 원칙의 위반이다.

### TEST_STRUCTURE
```python
# SOURCE: tests/test_mcp_doctor.py:66-71
    def test_env_reference_reports_name_and_whether_it_is_set(self, monkeypatch):
        monkeypatch.setenv("SOME_KEY", "super-secret-value")

        [entry] = _check_env({"SOME_KEY": "${SOME_KEY}"})

        assert entry == {"key": "SOME_KEY", "source": "${SOME_KEY}", "set": True}
```
`monkeypatch` 로 환경을 세우고 헬퍼를 직접 부른다. 관련 테스트는 `class Test...` 로 묶는다.

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `tools/mcp_doctor.py` | UPDATE | `.env` 로드 + 출처를 `payload` 와 출력에 |
| `tests/test_mcp_doctor.py` | UPDATE | 기존 파일에 이어 붙인다. 새 파일을 만들지 않는다 |

## NOT Building

- **인터프리터 실행 가능 여부 검증** — PRD Phase 3. 이 Phase 는 환경만 다룬다
- **체인 FALLBACK 로그 레벨** — PRD Phase 4, Open Question 선행
- **`_check_env` 의 판정 로직** — 이미 `${VAR:-default}` 를 이해한다. 건드리지 않는다
- **`config_loader` 에 `.env` 로드 추가** — 그것은 라이브러리이고 진입점이 아니다. 임포트만으로 환경을 바꾸면 테스트와 다른 소비자에게 영향이 간다. **진단 도구만 진입점처럼 행동한다**
- **`--no-dotenv` 같은 플래그** — 옵션을 늘리면 "어느 모드로 돌렸나" 가 새 변수가 된다. 항상 로드하고 무엇을 읽었는지 밝히는 편이 비교에 낫다

---

## Step-by-Step Tasks

### Task 1: `.env` 를 로드하고 무엇을 읽었는지 기록

- **ACTION**: `tools/mcp_doctor.py` 임포트 구역 뒤에 로더를 추가한다
- **IMPLEMENT**:
```python
def _load_repo_env(project_root: Path) -> dict:
    """Read the repo `.env`, the way every runtime entry point does.

    Without this the diagnostic reads a different environment than the thing it
    is diagnosing: `cores/analysis.py` and the MCP servers call `load_dotenv()`
    at start-up, so a key that lives only in `.env` is present for them and
    absent here. The tool then reports a healthy server as UNSET_ENV, and its
    whole premise — that two hosts' outputs can be compared — depends on that
    not happening.

    `load_dotenv` does not override an existing variable, so a shell that
    already exported one still wins. Returns what was read so the caller can
    say so; two hosts disagreeing is only informative if you know which file
    each of them used.
    """
    env_path = project_root / ".env"
    if not env_path.exists():
        return {"path": str(env_path), "loaded": False}

    from dotenv import load_dotenv

    load_dotenv(env_path)
    return {"path": str(env_path), "loaded": True}
```
- **MIRROR**: `LATE_IMPORT_AFTER_SYSPATH` — 파일 상단이 아니라 함수 안에서 `dotenv` 를 임포트한다. 이 파일은 이미 `sys.path` 조작 뒤에 리포 모듈을 늦게 임포트하는 구조다
- **IMPORTS**: 함수 내 `from dotenv import load_dotenv`. `python-dotenv>=1.0.0` 은 이미 `requirements.txt:29` 에 있다
- **GOTCHA**: ① **`override=True` 를 쓰지 말 것.** 기본값(`False`)이 옳다 — 셸에서 명시적으로 export 한 값이 파일을 이겨야 한다. 런타임도 그렇게 동작한다. ② 반환값에 **값을 담지 말 것.** 경로와 여부만. 이 파일의 도크스트링이 자격증명 유출 사고를 기록하고 있다. ③ `.env` 가 없는 체크아웃에서도 죽지 않아야 한다
- **VALIDATE**: `grep -n "override" tools/mcp_doctor.py` 가 비어야 한다(기본값 사용)

### Task 2: `main()` 이 로드하고 `payload` 에 넣는다

- **ACTION**: `main()` 에서 `project_root` 를 정한 직후에 부르고, `payload` 에 `env` 키를 넣는다
- **IMPLEMENT**:
```python
    project_root = Path(__file__).resolve().parent.parent
    # Before the registries are loaded: the loader interpolates ${VAR} from the
    # environment, so reading .env afterwards would be too late.
    env_source = _load_repo_env(project_root)

    sources = [("report", load_report_mcp_registry)]
```
그리고 `payload` 에:
```python
        "env": env_source,
```
- **MIRROR**: `PAYLOAD_THEN_PRINT` — `payload` 에 먼저 넣는다. `--json` 과 사람용 출력이 같은 자료를 쓴다
- **IMPORTS**: 없음
- **GOTCHA**: **레지스트리 로드보다 반드시 먼저 불러야 한다.** `config_loader` 가 `${VAR}` 를 치환하는 시점에 환경이 이미 채워져 있어야 한다. 나중에 로드하면 `_check_env` 의 `set` 판정만 바뀌고 레지스트리가 해석한 값은 그대로 비어 있어 **둘이 어긋난다**
- **VALIDATE**: `--json` 출력에 `env.loaded` 가 있고, `registries.report.servers[].env[].set` 이 `.env` 값을 반영한다

### Task 3: 사람이 읽는 출력에 한 줄

- **ACTION**: `config:` 줄 다음에 `env:` 줄을 넣는다
- **IMPLEMENT**:
```python
    env_info = payload["env"]
    print(
        f"env: {env_info['path']} "
        f"({'loaded' if env_info['loaded'] else 'not found'})"
    )
```
- **MIRROR**: `PAYLOAD_THEN_PRINT` (머리말 서식), `NEVER_PRINT_VALUES` (경로와 여부만)
- **IMPORTS**: 없음
- **GOTCHA**: 몇 개를 읽었는지 같은 수치를 넣지 말 것. `.env` 의 항목 수는 자격증명 개수를 시사한다. **경로와 로드 여부면 충분하다**
- **VALIDATE**: `python tools/mcp_doctor.py | head -5` 에 `env:` 줄이 보인다

### Task 4: 테스트

- **ACTION**: `tests/test_mcp_doctor.py` 에 클래스를 하나 이어 붙인다
- **IMPLEMENT**:
```python
class TestDotenvLoading:
    """The diagnostic has to read the same environment as the runtime.

    Every entry point calls `load_dotenv()`; this tool did not, so a key that
    lives only in `.env` looked unset here and set everywhere else. The result
    was a healthy server reported as UNSET_ENV — the exact false positive the
    module docstring says makes the output worthless.
    """

    def test_a_variable_from_the_env_file_is_visible(self, monkeypatch, tmp_path):
        monkeypatch.delenv("PRISM_DOCTOR_PROBE", raising=False)
        (tmp_path / ".env").write_text(
            "PRISM_DOCTOR_PROBE=from-file\n", encoding="utf-8"
        )

        result = _load_repo_env(tmp_path)

        assert result["loaded"] is True
        assert os.environ.get("PRISM_DOCTOR_PROBE") == "from-file"

    def test_an_exported_variable_beats_the_file(self, monkeypatch, tmp_path):
        """A shell that set it explicitly meant it; the file must not overrule."""
        monkeypatch.setenv("PRISM_DOCTOR_PROBE", "from-shell")
        (tmp_path / ".env").write_text(
            "PRISM_DOCTOR_PROBE=from-file\n", encoding="utf-8"
        )

        _load_repo_env(tmp_path)

        assert os.environ["PRISM_DOCTOR_PROBE"] == "from-shell"

    def test_a_checkout_without_an_env_file_still_runs(self, tmp_path):
        result = _load_repo_env(tmp_path)

        assert result["loaded"] is False
        assert result["path"].endswith(".env")

    def test_the_report_names_the_file_but_not_its_contents(self, tmp_path):
        """Saying how many keys were read would leak how many secrets exist."""
        (tmp_path / ".env").write_text(
            "SOME_KEY=super-secret-value\n", encoding="utf-8"
        )

        result = _load_repo_env(tmp_path)

        assert set(result) == {"path", "loaded"}
        assert "super-secret-value" not in repr(result)
```
- **MIRROR**: `TEST_STRUCTURE` (`monkeypatch`, `class Test...` 묶음), `NEVER_PRINT_VALUES` (마지막 테스트가 그 규칙을 지킨다)
- **IMPORTS**: 파일 상단의 임포트에 `os` 를 추가하고, `from tools.mcp_doctor import ...` 목록에 `_load_repo_env` 를 넣는다
- **GOTCHA**: ① `load_dotenv` 는 **진짜 `os.environ` 을 바꾼다.** `monkeypatch.setenv/delenv` 를 써야 테스트가 끝날 때 되돌아간다. 첫 테스트는 `delenv` 로 시작해 다른 테스트의 잔재를 지운다. ② 리포 실제 `.env` 를 쓰지 말 것 — `tmp_path` 다. 실파일에는 진짜 자격증명이 있다. ③ `PRISM_DOCTOR_PROBE` 처럼 실재하지 않는 이름을 쓴다
- **VALIDATE**: `pytest tests/test_mcp_doctor.py -q` 전부 통과, 그리고 `_load_repo_env` 를 무력화하면 첫 테스트가 실패한다

---

## Testing Strategy

### Unit Tests

| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| `test_a_variable_from_the_env_file_is_visible` | `.env` 에만 있는 변수 | `os.environ` 에 나타남 | 본 기능 |
| `test_an_exported_variable_beats_the_file` | 셸 값 + 파일 값 | 셸 값 유지 | **우선순위** |
| `test_a_checkout_without_an_env_file_still_runs` | `.env` 없음 | `loaded=False`, 예외 없음 | 신규 클론 |
| `test_the_report_names_the_file_but_not_its_contents` | 값이 든 `.env` | 경로·여부만 | **유출 방지** |

### Edge Cases Checklist
- [x] **`.env` 부재** — 신규 클론이나 컨테이너
- [x] **셸 export 와 파일 충돌** — 셸이 이긴다
- [x] **값 유출** — 반환값에 값이 없다
- [ ] 빈 입력 / 최대 크기 / 동시 접근 — 해당 없음
- [ ] 권한 거부 — `.env` 를 못 읽는 경우는 `load_dotenv` 에 맡긴다(예외가 나면 그게 진단 결과다)

---

## Validation Commands

### Static Analysis
```bash
.venv/bin/python -m py_compile tools/mcp_doctor.py tests/test_mcp_doctor.py
```
EXPECT: 오류 없음

### Unit Tests
```bash
.venv/bin/python -m pytest tests/test_mcp_doctor.py -p no:cacheprovider -q
```
EXPECT: 기존 + 신규 4 개 전부 통과

### 핵심 검증 — 셸 로드 여부와 무관하게 같은 결과
```bash
cd /Users/heracles/workspace/prism-insight
echo "--- 그냥 실행 ---"
PYTHONPATH=$PWD .venv/bin/python tools/mcp_doctor.py | tail -3
echo "--- source .env 후 ---"
( set -a; . ./.env; set +a; PYTHONPATH=$PWD .venv/bin/python tools/mcp_doctor.py | tail -3 )
```
EXPECT: **`unhealthy servers` 숫자가 같다.** 이 Phase 의 성공 신호 그 자체

### 출처가 출력에 있는지
```bash
PYTHONPATH=$PWD .venv/bin/python tools/mcp_doctor.py | head -6 | grep "^env:"
PYTHONPATH=$PWD .venv/bin/python tools/mcp_doctor.py --json | .venv/bin/python -c "import json,sys; print(json.load(sys.stdin)['env'])"
```
EXPECT: `env: /path/.env (loaded)` 와 `{'path': ..., 'loaded': True}`

### 값이 새지 않는지
```bash
PYTHONPATH=$PWD .venv/bin/python tools/mcp_doctor.py --json > /tmp/doctor.json
grep -icE "sk-|fc-|pplx-|jsd9399" /tmp/doctor.json || echo "  자격증명 없음 ✓"
rm -f /tmp/doctor.json
```
EXPECT: 0

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
- [ ] `source .env` 유무로 `unhealthy servers` 가 달라지지 않는다
- [ ] `env:` 줄이 경로와 로드 여부만 말한다 (항목 수 없음)
- [ ] `--json` 에 자격증명이 없다
- [ ] `.env` 를 임시로 치워도 도구가 죽지 않는다

---

## Acceptance Criteria
- [ ] Task 1-4 완료
- [ ] `source .env` 전후 `unhealthy servers` 동일
- [ ] `env:` 줄이 사람용·JSON 양쪽에 있다
- [ ] 신규 테스트 4 개 통과 + 변이 검증
- [ ] 출력에 자격증명 0
- [ ] KIS 회귀 99/99
- [ ] 전체 스위트 baseline(22 failed / 10 errors) 동일

## Completion Checklist
- [ ] `override=True` 를 쓰지 않았다 (셸이 파일을 이긴다)
- [ ] `.env` 로드가 레지스트리 로드보다 먼저다
- [ ] 반환값·출력에 값이 없다
- [ ] `.env` 부재에서 죽지 않는다
- [ ] 기존 테스트 파일에 이어 붙였다 (새 파일 아님)
- [ ] 범위 밖 변경 없음 (Phase 3·4 는 건드리지 않음)

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `.env` 로드가 레지스트리 로드보다 늦어 둘이 어긋남 | **M** | **H** | Task 2 GOTCHA. `project_root` 직후에 호출 |
| `override=True` 로 셸 값을 덮어씀 | M | M | 기본값 사용. `grep -n override` 를 VALIDATE 에 넣음 |
| 테스트가 진짜 `os.environ` 을 오염시킴 | **M** | M | `monkeypatch.setenv/delenv` 필수. `tmp_path` 의 `.env` 만 씀 |
| 출력에 항목 수를 넣어 자격증명 개수를 시사 | L | M | Task 3 GOTCHA. 경로·여부만 |
| 진단이 정상으로 나오지만 서버는 여전히 죽음 | **H** | M | 사실이다. 인터프리터 검증은 Phase 3. 이 Phase 는 **거짓 FAIL** 만 없앤다 |

## Notes

**이 Phase 는 거짓 FAIL 을 없애지 거짓 OK 를 없애지 않는다.** `PRISM_MCP_PYTHON` 이 `mcp` 없는 인터프리터를 가리켜도 `command_found` 는 참이므로 여전히 `ok` 로 나온다. 그것이 Phase 3 이고, **Phase 2 없이 Phase 3 을 하면** 환경을 잘못 읽은 채로 인터프리터를 검증하게 되므로 순서가 이렇다.

**`config_loader` 에 `.env` 로드를 넣지 않는 이유**: 그것은 라이브러리다. 임포트만으로 프로세스 환경을 바꾸면 테스트와 다른 소비자에게 영향이 간다. 리포에서 `.env` 를 로드하는 것은 언제나 **진입점**의 일이었고(`cores/analysis.py`, `cores/market_data/mcp_server.py` 등), 진단 도구도 진입점이다.

**측정 근거 (2026-08-18, 이 호스트)**: `tools/mcp_doctor.py` 에 `load_dotenv` 없음. 그냥 실행하면 `firecrawl`·`perplexity` 가 `UNSET_ENV` 로 FAIL, `unhealthy servers: 2`. 두 키는 `.env` 에 있다. `python-dotenv>=1.0.0` 은 이미 `requirements.txt:29` 에 있어 새 의존성이 아니다.

# Plan: 진단이 삭제하면 안 되는 파일을 삭제하라고 하지 않게

## Summary

`tools/mcp_doctor.py` 가 `mcp_agent.config.yaml` 을 두고 *"still present but no longer used; delete it to stop it drifting"* 라고 안내한다. **틀렸다.** 리포트 경로만 native 레지스트리로 이전됐고, `MCPApp` 을 쓰는 분석 에이전트는 여전히 그 파일을 읽는다 — 보고된 배치 로그에 25회 참조됐다. 안내를 따르면 기업정보·매크로·뉴스·매매판단 에이전트가 전부 깨진다.

## User Story

As a **진단 출력을 보고 정리를 하려는 운영자**,
I want **각 설정 파일이 무엇을 담당하는지 듣기를**,
So that **아직 쓰이는 파일을 지우지 않는다.**

## Problem → Solution

`legacy … no longer used; delete it` → 따르면 분석 에이전트 전멸
**→** 두 파일이 각각 **어느 경로**를 결정하는지 말한다. 삭제 권고 없음

## Metadata
- **Complexity**: **Small** — 1 파일 + 테스트, 실질 변경 15 줄 안팎
- **Source PRD**: `.claude/PRPs/prds/toss-install-buy-path.prd.md`
- **PRD Phase**: Phase 2 — 거짓 안내 제거
- **Estimated Files**: 2

---

## UX Design

### Before
```
config: native=y legacy=y
  note: a legacy mcp_agent.config.yaml is still present but no longer used;
        delete it to stop it drifting
```
`native` / `legacy` 라는 이름이 "하나는 현역, 하나는 잔재" 를 함의하고, 그 다음 줄이 그것을 명시적 지시로 만든다.

### After
```
config:
  report path (cores/llm/mcp_servers.yaml)  present
  agent path  (mcp_agent.config.yaml)       present
              — read by mcp-agent for the analysis agents; ${VAR} is not
                expanded there, so values go in the file
```

### Interaction Changes

| Touchpoint | Before | After | Notes |
|---|---|---|---|
| 머리말 | `native=y legacy=y` + 삭제 권고 | 경로별 역할 | 이 Phase 의 본체 |
| `--json` | `config.native` / `config.legacy` | 같은 키 유지 + `role` 추가 | **기존 키를 지우지 않는다** — 호스트 간 diff 가 이 도구의 목적이다 |
| 레거시 부재 시 | (아무 말 없음) | "analysis agents will fail" | 없는 것도 문제다 |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `tools/mcp_doctor.py` | 400-415 | 고칠 대상. `config:` 머리말과 문제의 note |
| P0 | `tools/mcp_doctor.py` | 249-262 | `payload["config"]` 구성 — JSON 계약 |
| P1 | `cores/llm/config_loader.py` | 1-17 | 탐색 순서. **native 우선, legacy 폴백** 이라는 사실의 출처 |
| P1 | `tests/test_mcp_doctor.py` | 1-15, 63-64 | 테스트 관례와 "값을 출력하지 않는다" 규칙 |
| P2 | `docs/SETUP_ko.md` | "MCP 설정 파일이 둘인 이유" 절 | PR #7 에서 이미 정리한 서술. 문구를 맞춘다 |

## External Documentation

없음. **No external research needed — feature uses established internal patterns.**

---

## Patterns to Mirror

### PAYLOAD_THEN_PRINT
```python
# SOURCE: tools/mcp_doctor.py:249-262
    payload = {
        "host": os.uname().nodename,
        "project_root": str(project_root),
        "env": env_source,
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
# SOURCE: tools/mcp_doctor.py:400-413
    print(f"host: {payload['host']}")
    print(f"root: {payload['project_root']}")
    cfg = payload["config"]
    print(
        f"config: native={'y' if cfg['native_exists'] else 'n'} "
        f"legacy={'y' if cfg['legacy_exists'] else 'n'}"
    )
    env_info = payload["env"]
    print(...)
    if cfg["legacy_exists"]:
        print(
            "  note: a legacy mcp_agent.config.yaml is still present but no "
            "longer used; delete it to stop it drifting"
        )
```
`payload` 를 먼저 만들고 그것을 출력한다. `--json` 과 사람용이 같은 자료를 쓴다.

### FALSE_POSITIVE_PRINCIPLE
```python
# SOURCE: tools/mcp_doctor.py:51-52
# Being strict here matters: this output is meant to be diffed across hosts,
# and a false positive is indistinguishable from a real breakage.
```
이 도구의 원칙이다. **틀린 안내는 거짓 양성보다 나쁘다** — 따르면 시스템이 깨진다.

### NEVER_PRINT_VALUES
```python
# SOURCE: tools/mcp_doctor.py:77-78
    """Report env var names and whether a value resolves — never the value.
```
경로와 존재 여부는 출력해도 되지만 값은 안 된다. 이 Phase 는 경로만 다룬다.

### DOC_WORDING (이미 정리된 서술)
```markdown
# SOURCE: docs/SETUP_ko.md — "MCP 설정 파일이 둘인 이유"
| 파일 | 읽는 쪽 | 환경변수 치환 |
| `cores/llm/mcp_servers.yaml` | 리포트 생성 | **지원** |
| `mcp_agent.config.yaml` | 분석 에이전트 (mcp-agent 프레임워크) | **미지원** |
```
PR #7 에서 문서는 이미 바로잡았다. **도구만 뒤처져 있다.** 같은 어휘를 쓴다.

### TEST_STRUCTURE
```python
# SOURCE: tests/test_mcp_doctor.py:66-71
    def test_env_reference_reports_name_and_whether_it_is_set(self, monkeypatch):
        monkeypatch.setenv("SOME_KEY", "super-secret-value")

        [entry] = _check_env({"SOME_KEY": "${SOME_KEY}"})

        assert entry == {"key": "SOME_KEY", "source": "${SOME_KEY}", "set": True}
```
관련 테스트는 `class Test...` 로 묶고, `monkeypatch` 로 상태를 세운다.

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `tools/mcp_doctor.py` | UPDATE | 머리말과 note. `payload` 에 역할 추가 |
| `tests/test_mcp_doctor.py` | UPDATE | 기존 파일에 이어 붙인다 |

## NOT Building

- **레거시 설정의 인터프리터 검사** — PRD Phase 3. 이 Phase 는 **문구만** 고친다
- **`.example`·문서 갱신** — PRD Phase 4. `docs/SETUP_ko.md` 는 PR #7 에서 이미 맞다
- **`native` / `legacy` JSON 키 제거** — 호스트 간 diff 가 이 도구의 목적이라 기존 키를 깨면 안 된다. **추가만** 한다
- **두 설정 파일 통합** — PRD 가 명시적으로 범위 밖으로 뒀다
- **`config_loader` 의 DEPRECATION 경고 문구** — 그것은 "키를 `.env` 로 옮겨라" 이고 사실이다. 다른 이야기다

---

## Step-by-Step Tasks

### Task 1: `payload` 에 각 파일의 역할을 담는다

- **ACTION**: `tools/mcp_doctor.py:249-262` 의 `payload["config"]` 에 역할을 추가한다
- **IMPLEMENT**:
```python
        "config": {
            "native": str(config_loader._NATIVE_CONFIG),
            "native_exists": config_loader._NATIVE_CONFIG.exists(),
            "native_role": "report generation",
            "legacy": str(config_loader._LEGACY_CONFIG),
            "legacy_exists": config_loader._LEGACY_CONFIG.exists(),
            # Not a leftover. mcp-agent reads this file for the analysis agents
            # — company info, macro, news, the buy and sell specialists — and
            # they are the majority of MCP traffic in a batch.
            "legacy_role": "analysis agents (mcp-agent)",
        },
```
- **MIRROR**: `PAYLOAD_THEN_PRINT` — 출력이 아니라 `payload` 에 먼저 넣는다
- **IMPORTS**: 없음
- **GOTCHA**: **기존 네 키(`native`, `native_exists`, `legacy`, `legacy_exists`)를 지우거나 이름을 바꾸지 말 것.** 이 도구의 `--json` 은 호스트 간 비교용이고, 다른 버전의 출력과 diff 될 수 있다. 추가만 한다
- **VALIDATE**:
```bash
PYTHONPATH=$PWD .venv/bin/python tools/mcp_doctor.py --json 2>/dev/null | \
  .venv/bin/python -c "import json,sys; c=json.load(sys.stdin)['config']; print(sorted(c))"
```
EXPECT: 기존 4 키 + `native_role`, `legacy_role`

### Task 2: 머리말을 역할 중심으로 다시 쓴다

- **ACTION**: `tools/mcp_doctor.py:400-413` 의 `config:` 출력과 note 를 바꾼다
- **IMPLEMENT**:
```python
    cfg = payload["config"]
    print("config:")
    for key, label in (("native", "report path"), ("legacy", "agent path ")):
        state = "present" if cfg[f"{key}_exists"] else "MISSING"
        print(f"  {label} ({Path(cfg[key]).name})  {state}  — {cfg[f'{key}_role']}")
    if not cfg["legacy_exists"]:
        # Its absence is the problem, not its presence. This used to advise
        # deleting it, which would have taken out every analysis agent: the
        # report path migrated to the native registry, the agents did not.
        print(
            "  the agent path config is missing; the analysis agents have no "
            "MCP servers without it"
        )
    if cfg["legacy_exists"]:
        print(
            "  note: ${VAR} is not expanded in the agent path config — "
            "mcp-agent does not interpolate, so values go in the file itself"
        )
```
- **MIRROR**: `PAYLOAD_THEN_PRINT` (payload 에서 꺼내 출력), `DOC_WORDING` (`docs/SETUP_ko.md` 와 같은 어휘 — "리포트 경로" / "분석 에이전트")
- **IMPORTS**: `Path` 는 이미 임포트돼 있다(`from pathlib import Path`)
- **GOTCHA**: ① **"delete" 라는 단어를 남기지 말 것.** 이 Phase 의 전부다. ② 치환 미지원 안내를 여기 넣는 이유는, 그 파일을 열어 고칠 사람이 이 출력을 보고 있기 때문이다 — Phase 4 가 `.example` 에 적을 내용의 요약이다. ③ 파일명만 출력한다(`Path(...).name`). 전체 경로는 이미 `payload` 에 있고 머리말이 길어지면 읽히지 않는다
- **VALIDATE**:
```bash
PYTHONPATH=$PWD .venv/bin/python tools/mcp_doctor.py 2>/dev/null | head -8
PYTHONPATH=$PWD .venv/bin/python tools/mcp_doctor.py 2>/dev/null | grep -ci "delete"
```
EXPECT: 역할이 보이고, `delete` 매칭 **0**

### Task 3: 테스트

- **ACTION**: `tests/test_mcp_doctor.py` 에 클래스를 이어 붙인다
- **IMPLEMENT**:
```python
class TestConfigRoles:
    """The agent path config is in use, and the tool used to say otherwise.

    It advised deleting `mcp_agent.config.yaml` as a leftover. Only the report
    path migrated to the native registry; mcp-agent still reads that file for
    the analysis agents — company info, macro, news, the buy and sell
    specialists — and a batch log showed 25 references to it. Following the
    advice would have taken all of them out.
    """

    def _run(self, capsys, argv=None):
        from tools.mcp_doctor import main

        main(argv or [])
        return capsys.readouterr().out

    def test_the_output_never_advises_deleting_a_config(self, capsys):
        assert "delete" not in self._run(capsys).lower()

    def test_each_config_is_named_with_what_reads_it(self, capsys):
        out = self._run(capsys)

        assert "report path" in out
        assert "agent path" in out

    def test_the_json_keeps_the_keys_other_hosts_are_diffed_on(self, capsys):
        """Comparing hosts is the point; renaming a key breaks the comparison."""
        import json

        from tools.mcp_doctor import main

        main(["--json"])
        config = json.loads(capsys.readouterr().out)["config"]

        assert {"native", "native_exists", "legacy", "legacy_exists"} <= set(config)
        assert config["legacy_role"]

    def test_the_json_says_what_reads_each_file(self, capsys):
        import json

        from tools.mcp_doctor import main

        main(["--json"])
        config = json.loads(capsys.readouterr().out)["config"]

        assert "report" in config["native_role"]
        assert "agent" in config["legacy_role"]
```
- **MIRROR**: `TEST_STRUCTURE` (`class Test...`), 이 저장소의 명명 관례(성질을 문장으로, 도크스트링은 막는 실패를 설명)
- **IMPORTS**: `json` 은 테스트 안에서. `capsys` 는 pytest 기본 픽스처
- **GOTCHA**: ① `main()` 은 **종료 코드를 반환**하지 `sys.exit` 하지 않는다(파일 끝의 `raise SystemExit(main())` 이 그 일을 한다). 그대로 부를 수 있다. ② `main()` 이 실제 레지스트리를 읽으므로 이 테스트는 **환경에 의존**한다 — 그래서 값이 아니라 **문구와 키의 존재**만 검사한다. 이 세션에서 실제 변수명에 의존한 테스트가 한 번 깨진 적이 있다. ③ `main()` 이 `.env` 를 로드한다(PR #7). 부작용이지만 이 테스트가 값을 보지 않으므로 무해하다
- **VALIDATE**: `pytest tests/test_mcp_doctor.py -q` 전부 통과, 그리고 note 를 되돌리면 첫 테스트가 실패한다

---

## Testing Strategy

### Unit Tests

| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| `test_the_output_never_advises_deleting_a_config` | 기본 실행 | `delete` 없음 | **본 결함** |
| `test_each_config_is_named_with_what_reads_it` | 기본 실행 | 두 역할 표기 | 대체 안내 |
| `test_the_json_keeps_the_keys_other_hosts_are_diffed_on` | `--json` | 기존 4 키 유지 | **하위 호환** |
| `test_the_json_says_what_reads_each_file` | `--json` | 역할 키 | 기계 판독 |

### Edge Cases Checklist
- [x] **레거시 부재** — 이제 그것이 문제로 보고된다(구현에 포함)
- [x] **하위 호환** — JSON 키 유지
- [ ] 빈 입력 / 최대 크기 / 동시 접근 / 권한 — 해당 없음 (출력 문구)

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
EXPECT: 기존 37 + 신규 4 = 41 passed

### 핵심 검증 — 삭제 권고가 사라졌는지
```bash
cd /Users/heracles/workspace/prism-insight
PYTHONPATH=$PWD .venv/bin/python tools/mcp_doctor.py 2>/dev/null | head -8
echo "--- delete 매칭 ---"
PYTHONPATH=$PWD .venv/bin/python tools/mcp_doctor.py 2>/dev/null | grep -ci "delete"
```
EXPECT: 역할이 보이고 `delete` 매칭 **0**

### 레거시가 없을 때
```bash
mv mcp_agent.config.yaml /tmp/_legacy_hidden.yaml
PYTHONPATH=$PWD .venv/bin/python tools/mcp_doctor.py 2>/dev/null | head -8
mv /tmp/_legacy_hidden.yaml mcp_agent.config.yaml
```
EXPECT: `MISSING` 과 "analysis agents have no MCP servers without it". **원복 확인 필수** — 이 파일에는 자격증명이 들어 있다

### 변이 검증
```bash
# note 를 옛 문구로 되돌린 뒤
.venv/bin/python -m pytest tests/test_mcp_doctor.py -q -k never_advises
```
EXPECT: **실패해야 한다**

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

### KIS 회귀
```bash
.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_execution_service.py tests/test_async_trading.py tests/test_multi_account_domestic.py \
  tests/test_sell_quantity_guard.py tests/test_sell_denominator_sync.py tests/test_kr_pending_entry.py \
  tests/test_multi_account_kis_auth.py
```
EXPECT: **99 passed**

### Manual Validation
- [ ] 출력 어디에도 설정 파일 삭제 권고가 없다
- [ ] 두 파일이 각각 무엇을 담당하는지 읽힌다
- [ ] `--json` 의 기존 키가 그대로다
- [ ] `mcp_agent.config.yaml` 이 원복됐다 (자격증명 포함 파일)

---

## Acceptance Criteria
- [ ] Task 1-3 완료
- [ ] 출력에 `delete` 0 건
- [ ] 두 경로의 역할이 사람용·JSON 양쪽에
- [ ] JSON 기존 4 키 유지
- [ ] 신규 테스트 4 개 통과 + 변이 검증
- [ ] KIS 회귀 99/99
- [ ] 전체 스위트 baseline(22 failed / 10 errors) 동일

## Completion Checklist
- [ ] "delete" 라는 단어가 출력에 없다
- [ ] `docs/SETUP_ko.md` 와 같은 어휘를 쓴다
- [ ] JSON 키를 지우거나 이름 바꾸지 않았다
- [ ] 레거시 부재를 문제로 보고한다
- [ ] 값(자격증명)을 출력하지 않는다
- [ ] 범위 밖 변경 없음 (Phase 3·4)

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| JSON 키를 바꿔 호스트 간 diff 가 깨짐 | **M** | M | Task 1 GOTCHA + 전용 테스트 |
| 테스트가 실제 레지스트리에 의존해 불안정 | **M** | M | 값이 아니라 문구·키만 검사. 이 세션에서 같은 실수를 한 적이 있다 |
| 검증 중 `mcp_agent.config.yaml` 을 원복 안 함 | **M** | **H** | 자격증명이 든 파일이다. 원복을 수동 검증 항목에 넣음 |
| 문구만 고쳐 실제 문제는 남음 | **H** | M | 사실이다. 인터프리터 검사는 Phase 3, 안내는 Phase 4 |

## Notes

**이 Phase 는 아무 동작도 고치지 않는다.** 잘못된 지시를 없앨 뿐이다. 그런데 그 지시는 따르면 분석 에이전트가 전멸하므로, 동작을 고치는 것보다 급하다.

**Phase 3 이 이 Phase 에 의존하는 이유**: 레거시가 "쓰이지 않는다" 고 말하면서 그 파일의 인터프리터를 검사하는 것은 앞뒤가 맞지 않는다. 역할을 바로 말한 뒤에 검사를 얹는다.

**측정 근거 (2026-08-18)**: `tools/mcp_doctor.py:411` 이 삭제를 권한다. `MCPApp(...)` 이 `stock_analysis_orchestrator.py`·`prism-us/cores/us_analysis.py`·`events/jeoningu_trading.py` 등에 있고, 그 프레임워크가 `mcp_agent.config.yaml` 을 읽는다. 에이전트의 `server_names` 에 `firecrawl`·`perplexity`·`kospi_kosdaq`·`sqlite`·`time` 이 등장한다.

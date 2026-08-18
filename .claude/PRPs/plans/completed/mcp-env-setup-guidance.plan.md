# Plan: 설정 이전이 요구하는 환경변수를 안내한다

## Summary

리포트 경로의 MCP 설정이 값을 하드코딩하던 `mcp_agent.config.yaml` 에서 환경변수를 치환하는 `cores/llm/mcp_servers.yaml` 로 옮겨가면서, `.env` 에 넣어야 할 변수가 다섯 개 생겼다. **그중 어느 것도 `.env.example` 에 없다.** 하나(`PRISM_MCP_PYTHON`)는 미설정 시 MCP 서버가 조용히 죽는다.

## User Story

As a **PRISM 을 새 호스트에 배포하는 운영자**,
I want **`.env.example` 만 보고 필요한 설정을 다 알기를**,
So that **리포트가 조용히 비는 것을 배포 몇 시간 뒤가 아니라 설정 시점에 피한다.**

## Problem → Solution

`.env.example` 에 새 변수 0/5 → 운영자가 yaml 주석을 읽어야만 알 수 있음 → `PRISM_MCP_PYTHON` 을 놓치면 서버가 조용히 죽음
**→** 다섯 개 전부 **왜 필요한지·안 넣으면 무슨 일이 나는지**와 함께 `.env.example` 에, 그리고 `docs/SETUP_ko.md` 에 반영

## Metadata
- **Complexity**: **Small** — 문서 2 파일, 코드 변경 없음
- **Source PRD**: `.claude/PRPs/prds/mcp-config-migration-visibility.prd.md`
- **PRD Phase**: Phase 1 — 설정 안내
- **Estimated Files**: 2 (+1 테스트)

---

## UX Design

### Before
```
운영자가 .env.example 복사 → 채움 → 기동
  → 리포트 섹션이 "Analysis failed: ..." 또는 지표가 빔
  → 원인이 PRISM_MCP_PYTHON 이라는 것은
     cores/llm/mcp_servers.yaml 주석에만 적혀 있음
```

### After
```
운영자가 .env.example 복사 → 새 변수 5종이 이유와 함께 적혀 있음
  → PRISM_MCP_PYTHON 항목이 "미설정 시 서버가 조용히 죽는다" 고 명시
  → 기동
```

### Interaction Changes

| Touchpoint | Before | After | Notes |
|---|---|---|---|
| `.env.example` | 새 변수 0/5 | 5/5, 각각 이유 포함 | 이 Phase 의 본체 |
| `docs/SETUP_ko.md` | `mcp_agent.config.yaml` 편집을 안내 | 어느 파일이 무엇을 결정하는지 구분 | 200 행에 `PRISM_MCP_PYTHON` 언급은 이미 있으나 맥락이 없다 |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `.env.example` | 18-28 | **미러할 서식의 최고 예시.** KRX OPEN API 블록이 배경·주의·전환설계까지 적는 방식 |
| P0 | `.env.example` | 233-257 | 브로커 구역. `⚠️` 사용법과 주석처리 관례 |
| P0 | `cores/llm/mcp_servers.yaml` | 20-45 | 변수 5종이 **왜** 필요한지가 여기 주석에 있다. 옮겨 적을 원문 |
| P1 | `docs/SETUP_ko.md` | 170-200 | MCP 설정 안내 구역. 이미 `PRISM_MCP_PYTHON` 을 한 줄 언급한다 |
| P2 | `cores/llm/config_loader.py` | 1-17 | 탐색 순서(native → legacy)와 치환 규칙 |

## External Documentation

없음. **No external research needed — feature uses established internal patterns.**

---

## Patterns to Mirror

### ENV_EXAMPLE_BLOCK (배경까지 적는다)
```
# SOURCE: .env.example:18-28
# KRX Data Marketplace OPEN API (정식 경로 - 위 스크래핑 로그인을 대체할 예정)
# https://openapi.krx.co.kr 에서 신청해 발급받는 40자리 인증키.
# HTTP 요청 헤더 AUTH_KEY 에 그대로 실어 보낸다.
#
# 배경: 2026-08-04 KRX가 자동화 수단을 통한 대량 조회를 탐지해 서버 IP를 차단했다.
# 이용약관 제10조 제2호가 자동 수집을 금지하고 있어 스크래핑은 고쳐서 되는 문제가
# 아니며, KRX 안내문이 직접 이 OPEN API를 공식 경로로 지목했다.
# 전환 설계: tasks/plan-a-krx-exit-design.md
#
# 주의: 일별매매정보는 EOD(장 마감 후) 데이터라 장중 스크리닝에는 쓸 수 없다.
KRX_OPENAPI_AUTH_KEY=
```
**이 파일은 변수 이름만 적지 않는다.** 무엇인지, 어디서 얻는지, 왜 그런지, 그리고 함정을 적는다. 새 항목도 이 밀도를 따른다.

### ENV_EXAMPLE_WARNING (⚠️ 는 돈이나 조용한 실패에 쓴다)
```
# SOURCE: .env.example:243-246
# ⚠️  토스증권에는 모의투자 서버가 없다. PRISM_BROKER=toss + PRISM_TRADING_MODE=real
#     조합은 첫 주문부터 실제 돈이 나간다. demo 에서는 주문 API 를 호출하지 않는
#     로컬 dry-run 시뮬레이터가 대신 응답하며, 시세는 실제 데이터를 쓴다.
# PRISM_TRADING_MODE=demo
```
`⚠️` 는 남발하지 않는다. 되돌릴 수 없거나 **조용히 잘못되는** 것에만 붙는다. `PRISM_MCP_PYTHON` 이 정확히 후자다.

### ENV_EXAMPLE_OPTIONALITY
```
# SOURCE: .env.example:47-51
# Redis Streams (Optional - for trading signal pub/sub)
# Upstash Redis configuration (obtain from https://console.upstash.com)
# When configured, buy/sell signals will be published via Redis Streams.
# UPSTASH_REDIS_REST_URL=https://your-redis.upstash.io
```
선택 항목은 `# ` 로 주석처리하고 헤더에 `(Optional)`. 필수는 주석 없이 플레이스홀더 값을 둔다(`KRX_ID=your_krx_id`).

### SOURCE_OF_TRUTH (옮겨 적을 원문)
```yaml
# SOURCE: cores/llm/mcp_servers.yaml:20-45
  # command: 이 서버는 리포 코드를 import 하므로 **리포 의존성이 설치된 인터프리터**
  # 여야 한다. 맨 `python3` 는 호스트마다 다른 것을 가리킨다 — 맥미니에서는
  # Homebrew 3.14 라서 `mcp` 조차 없다. 오케스트레이터를 띄우는 것과 같은 파이썬을
  # PRISM_MCP_PYTHON 으로 지정한다.
  kospi_kosdaq:
    command: ${PRISM_MCP_PYTHON:-python3}
    ...
    env:
      # 레지스트리가 env 를 명시적으로 넘기므로 리포 루트를 직접 준다.
      PYTHONPATH: ${PRISM_REPO_ROOT:-.}
```

### TEST_STRUCTURE (설정 파일을 검사하는 기존 테스트)
```python
# SOURCE: tests/test_kospi_kosdaq_server_switch.py (이번 세션에 추가됨)
def _server_config(rel: str):
    path = REPO_ROOT / rel
    if not path.exists():
        pytest.skip(f"{rel} is not present in this checkout")
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    servers = config.get("mcp", {}).get("servers") or config.get("servers") or {}
    return servers["kospi_kosdaq"]
```
설정 파일의 성질은 파일을 직접 파싱해 검사한다. 존재하지 않으면 `skip`.

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `.env.example` | UPDATE | 새 변수 5종. 이 Phase 의 본체 |
| `docs/SETUP_ko.md` | UPDATE | 어느 설정 파일이 무엇을 결정하는지 구분 |
| `tests/test_env_example_covers_mcp_registry.py` | CREATE | 레지스트리가 참조하는 변수가 안내되지 않는 상태를 막는다 |

## NOT Building

- **`mcp_doctor` 의 `.env` 로드** — PRD Phase 2. 병렬이지만 별개 변경이다
- **인터프리터 검증** — PRD Phase 3
- **체인 FALLBACK 로그 레벨** — PRD Phase 4, Open Question 선행
- **`PRISM_MCP_PYTHON` 자동 추정** — PRD 가 명시적으로 기각했다. 오케스트레이터와 서버가 다른 인터프리터여야 하는 구성을 막는다
- **`.env` 실파일 수정** — gitignore 대상이고 사용자 것이다. 예시만 고친다
- **`mcp_agent.config.yaml.example` 수정** — 그 경로는 치환이 안 되므로 환경변수를 적을 수 없다. 제약을 `SETUP_ko.md` 에 적는 것으로 대신한다

---

## Step-by-Step Tasks

### Task 1: `.env.example` 에 MCP 레지스트리 구역 신설

- **ACTION**: `.env.example` 끝(브로커 구역 뒤)에 새 구역을 추가한다
- **IMPLEMENT**:
```
# MCP 서버 레지스트리 (리포트 경로)
#
# 리포트 생성이 쓰는 MCP 서버 설정은 cores/llm/mcp_servers.yaml 이 결정한다.
# 그 파일은 값을 하드코딩하지 않고 여기 있는 값을 ${VAR} 로 치환하므로,
# 아래 항목이 비면 해당 서버가 뜨지 못한다.
#
# (분석 에이전트가 쓰는 mcp_agent.config.yaml 은 별개이며 치환을 지원하지 않는다.
#  그쪽은 값을 파일에 직접 적어야 한다 — docs/SETUP_ko.md 참고)

# ⚠️  이 서버들은 리포 코드를 import 하므로 **리포 의존성이 설치된 인터프리터**여야
#     한다. 맨 python3 는 호스트마다 다른 것을 가리킨다 — 시스템 python3 가 3.9 라
#     mcp 패키지조차 없는 호스트가 흔하다. 미설정 시 kospi_kosdaq·time 서버가
#     오류 없이 뜨지 않고, 리포트에서는 해당 섹션이 조용히 비는 것으로만 보인다.
#     오케스트레이터를 띄우는 것과 같은 파이썬을 절대경로로 적는다.
# PRISM_MCP_PYTHON=/path/to/prism-insight/.venv/bin/python

# MCP 서버 자식 프로세스가 리포를 import 할 수 있도록 PYTHONPATH 로 넘어간다.
# 미설정 시 "." 이며, 오케스트레이터를 리포 루트에서 실행하면 그대로 동작한다.
# 다른 디렉터리에서 실행하거나 cron 을 쓴다면 절대경로로 적는다.
# PRISM_REPO_ROOT=/path/to/prism-insight

# 리포트에 실리는 수치만 다른 시세 소스 순서를 쓰고 싶을 때. 미설정이면
# PRISM_MARKET_DATA_SOURCES 를 그대로 따른다.
# PRISM_REPORT_DATA_SOURCES=

# 웹 리서치·검색 MCP 서버 키. 예전에는 mcp_agent.config.yaml 에 직접 적었으나
# 레지스트리 이전 후로는 여기서 읽는다. 미설정 시 해당 서버만 뜨지 않는다.
# FIRECRAWL_API_KEY=
# PERPLEXITY_API_KEY=
```
- **MIRROR**: `ENV_EXAMPLE_BLOCK` (이유·출처·함정까지), `ENV_EXAMPLE_WARNING` (`⚠️` 는 조용한 실패에만), `ENV_EXAMPLE_OPTIONALITY` (선택 항목은 `# ` 주석)
- **IMPORTS**: 없음
- **GOTCHA**: ① **실제 키를 적지 말 것.** 이 세션에서 자격증명이 대화 기록에 노출된 사고가 이미 있었다. 플레이스홀더만. ② `PRISM_MCP_PYTHON` 은 주석처리하되 **미설정의 대가**를 반드시 적는다 — 이 항목의 존재 이유가 그것이다. ③ 맥미니/Homebrew 같은 특정 호스트 이름을 그대로 옮기지 말 것. 원문 주석은 그 호스트를 예로 들지만, `.env.example` 은 모든 호스트가 읽는다
- **VALIDATE**:
```bash
for v in PRISM_MCP_PYTHON PRISM_REPO_ROOT PRISM_REPORT_DATA_SOURCES FIRECRAWL_API_KEY PERPLEXITY_API_KEY; do
  grep -qE "^#?\s*$v" .env.example && echo "  ✓ $v" || echo "  ✗ $v 없음"
done
```

### Task 2: `docs/SETUP_ko.md` 에서 두 설정 파일을 구분

- **ACTION**: 170-200 행 구역에 어느 파일이 무엇을 결정하는지 명시한다
- **IMPLEMENT**: `mcp_agent.config.yaml` 편집 안내 앞에 다음 취지를 넣는다 —
  - `cores/llm/mcp_servers.yaml` = 리포트 경로. `.env` 를 치환한다. **보통 편집할 필요가 없다**
  - `mcp_agent.config.yaml` = 분석 에이전트 경로(mcp-agent 프레임워크). **`${VAR}` 치환을 지원하지 않으므로** 값을 파일에 직접 적어야 한다
  - 따라서 `PRISM_MCP_PYTHON` 은 리포트 경로에만 듣는다. 분석 에이전트 경로의 인터프리터는 `command` 를 직접 고쳐야 한다
- **MIRROR**: 기존 문서의 서술 톤(합쇼체). 200 행의 `PRISM_MCP_PYTHON` 언급과 충돌하지 않게 이어 쓴다
- **IMPORTS**: 없음
- **GOTCHA**: 문서가 `mcp_agent.config.yaml` 예시에 `args: ["-m", "cores.market_data.mcp_server"]` 를 이미 적고 있다(178 행). 이번 세션 변경과 일치하므로 **그 부분은 건드리지 않는다**
- **VALIDATE**: `grep -c "mcp_servers.yaml" docs/SETUP_ko.md` 가 1 이상, 그리고 치환 미지원 언급이 있다

### Task 3: 안내 누락을 막는 테스트

- **ACTION**: `tests/test_env_example_covers_mcp_registry.py` 를 만든다
- **IMPLEMENT**:
```python
"""The registry substitutes env vars; `.env.example` has to name them.

`cores/llm/mcp_servers.yaml` stopped hardcoding values and started reading
`${VAR}` from the environment. Nothing told the operator which variables that
meant, so a fresh host came up with servers that could not start —
`PRISM_MCP_PYTHON` unset resolves to a bare `python3`, which on a machine whose
system interpreter is 3.9 has no `mcp` at all. The server dies without an error
anybody sees; the report just has an empty section.

This test fails when the registry grows a variable the example does not
mention, so the two cannot drift apart again.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY = REPO_ROOT / "cores" / "llm" / "mcp_servers.yaml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

# `${VAR}` and `${VAR:-default}`, the two forms the loader interpolates.
_ENV_REF = re.compile(r"\$\{([A-Z][A-Z0-9_]*)(?::-[^}]*)?\}")


def _referenced_variables() -> set[str]:
    if not REGISTRY.exists():
        pytest.skip("registry not present in this checkout")
    return set(_ENV_REF.findall(REGISTRY.read_text(encoding="utf-8")))


def _documented_variables() -> set[str]:
    if not ENV_EXAMPLE.exists():
        pytest.skip(".env.example not present in this checkout")
    # Commented-out entries count: an optional variable is still documented.
    return set(
        re.findall(
            r"^#?\s*([A-Z][A-Z0-9_]*)=", ENV_EXAMPLE.read_text(encoding="utf-8"), re.M
        )
    )


def test_every_variable_the_registry_reads_is_documented():
    undocumented = sorted(_referenced_variables() - _documented_variables())

    assert not undocumented, (
        "cores/llm/mcp_servers.yaml reads these, but .env.example never mentions "
        f"them: {undocumented}. An operator has no way to learn they exist."
    )


def test_the_interpreter_variable_says_what_happens_without_it():
    """Naming it is not enough; its absence is silent and needs saying.

    Every other variable here degrades visibly — a missing API key takes out one
    server and says so. An unset PRISM_MCP_PYTHON falls back to a `python3` that
    may not be able to run the server at all, and the only symptom is an empty
    report section.
    """
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    block = text[text.index("PRISM_MCP_PYTHON") - 800 : text.index("PRISM_MCP_PYTHON")]

    assert "조용히" in block or "silent" in block.lower(), (
        "PRISM_MCP_PYTHON is listed but its silent failure mode is not explained"
    )


def test_the_example_carries_no_real_credentials():
    """A placeholder file is the easiest place to leak a key by accident."""
    text = ENV_EXAMPLE.read_text(encoding="utf-8")

    for line in text.splitlines():
        match = re.match(r"^#?\s*([A-Z][A-Z0-9_]*)=(.+)$", line)
        if not match:
            continue
        name, value = match.group(1), match.group(2).split("#")[0].strip()
        if not value or not any(k in name for k in ("KEY", "TOKEN", "SECRET", "PW")):
            continue
        assert not re.fullmatch(r"[A-Za-z0-9_\-]{20,}", value), (
            f"{name} in .env.example looks like a real credential, not a placeholder"
        )
```
- **MIRROR**: `TEST_STRUCTURE` (파일을 직접 파싱, 없으면 `skip`), 그리고 이 저장소의 테스트 명명 관례(성질을 문장으로, 도크스트링은 막는 실패를 설명)
- **IMPORTS**: `re`, `pathlib.Path`, `pytest` — 전부 표준/기존
- **GOTCHA**: ① 정규식이 `${VAR:-default}` 를 잡아야 한다. `${VAR}` 만 잡으면 `PRISM_MCP_PYTHON` 을 놓친다 — `mcp_doctor` 가 정확히 그 버그를 갖고 있었다. ② 주석처리된 항목도 "안내됨" 으로 친다. 선택 변수는 주석처리가 이 파일의 관례다. ③ 세 번째 테스트는 이 세션의 자격증명 노출 사고에서 나온 것이다. 길이 20 이상의 영숫자만 걸러 오탐을 줄인다
- **VALIDATE**: `pytest tests/test_env_example_covers_mcp_registry.py -q` 전부 통과. 그리고 `.env.example` 에서 `PRISM_MCP_PYTHON` 줄을 지우면 첫 테스트가 실패한다

---

## Testing Strategy

### Unit Tests

| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| `test_every_variable_the_registry_reads_is_documented` | 레지스트리 yaml | 미안내 변수 0 | **드리프트 방지** |
| `test_the_interpreter_variable_says_what_happens_without_it` | `.env.example` | 조용한 실패가 설명됨 | 이름만 적는 것 방지 |
| `test_the_example_carries_no_real_credentials` | `.env.example` | 실제 키 없음 | 유출 방지 |

### Edge Cases Checklist
- [x] **`${VAR:-default}` 형태** — 정규식이 두 형태를 다 잡아야 한다
- [x] **주석처리된 항목** — 선택 변수의 정상 표기이므로 안내로 친다
- [x] **파일 부재** — 체크아웃에 없으면 `skip`
- [ ] 빈 입력 / 동시 접근 / 권한 — 해당 없음 (문서 검사)

---

## Validation Commands

### Static Analysis
```bash
.venv/bin/python -m py_compile tests/test_env_example_covers_mcp_registry.py
.venv/bin/python -c "
import re, pathlib
t = pathlib.Path('.env.example').read_text(encoding='utf-8')
print('변수 수:', len(re.findall(r'^#?\s*[A-Z][A-Z0-9_]*=', t, re.M)))"
```
EXPECT: 오류 없음

### Unit Tests
```bash
.venv/bin/python -m pytest tests/test_env_example_covers_mcp_registry.py -p no:cacheprovider -q
```
EXPECT: 3 passed

### 변이 검증
```bash
# .env.example 에서 PRISM_MCP_PYTHON 줄을 임시로 지운 뒤
.venv/bin/python -m pytest tests/test_env_example_covers_mcp_registry.py -q -k documented
```
EXPECT: **실패해야 한다**

### 안내 완전성 (사람이 보는 확인)
```bash
for v in PRISM_MCP_PYTHON PRISM_REPO_ROOT PRISM_REPORT_DATA_SOURCES FIRECRAWL_API_KEY PERPLEXITY_API_KEY; do
  grep -qE "^#?\s*$v" .env.example && echo "  ✓ $v" || echo "  ✗ $v"
done
```
EXPECT: 5/5 ✓

### 실제 키가 안 들어갔는지
```bash
git diff --cached -- .env.example | grep -iE "^\+.*(sk-|fc-|pplx-|[A-Za-z0-9]{32,})" || echo "  자격증명 패턴 없음 ✓"
```
EXPECT: 없음

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
- [ ] `.env.example` 을 처음 보는 사람 관점에서 `PRISM_MCP_PYTHON` 항목만 읽고 "안 넣으면 어떻게 되는지" 를 알 수 있다
- [ ] 특정 호스트 이름(맥미니 등)이 `.env.example` 에 들어가지 않았다
- [ ] `docs/SETUP_ko.md` 가 두 설정 파일의 역할을 구분한다
- [ ] diff 에 실제 자격증명이 없다

---

## Acceptance Criteria
- [ ] Task 1-3 완료
- [ ] 새 변수 5/5 가 `.env.example` 에 이유와 함께
- [ ] 신규 테스트 3 개 통과 + 변이 검증
- [ ] KIS 회귀 99/99
- [ ] 전체 스위트 baseline(22 failed / 10 errors) 동일
- [ ] `.env.example` 에 실제 자격증명 0

## Completion Checklist
- [ ] `.env.example` 의 기존 밀도(이유·출처·함정)를 따랐다
- [ ] `⚠️` 를 조용한 실패에만 썼다
- [ ] 선택 변수는 주석처리했다
- [ ] 특정 호스트에 종속된 서술을 일반화했다
- [ ] `mcp_agent.config.yaml` 의 치환 미지원을 문서에 남겼다
- [ ] 범위 밖 변경 없음 (Phase 2-4 는 건드리지 않음)

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `.env.example` 에 실제 키를 적는 실수 | **M** | **H** | 플레이스홀더만. 전용 테스트 + 커밋 전 diff 검사 |
| 정규식이 `${VAR:-default}` 를 못 잡아 테스트가 무의미 | **M** | M | `mcp_doctor` 가 정확히 그 버그를 가졌었다. 두 형태 모두 잡도록 작성하고 변이 검증 |
| 문서만 고치고 실제 문제는 남는다 | **H** | M | 사실이다. 이 Phase 는 안내만 다룬다. 진단(2)·검증(3)·로그(4)가 남아 있음을 리포트에 명시 |
| `docs/SETUP_ko.md` 의 기존 서술과 충돌 | L | L | 200 행이 이미 `PRISM_MCP_PYTHON` 을 언급한다. 지우지 말고 맥락을 보강 |

## Notes

**이 Phase 는 문제를 고치지 않는다. 알 수 있게 할 뿐이다.** `PRISM_MCP_PYTHON` 미설정은 여전히 서버를 조용히 죽이고, `mcp_doctor` 는 여전히 `.env` 를 안 읽어 거짓 FAIL 을 낸다. 그 둘은 PRD Phase 2·3 이다. 이 Phase 만 하고 멈추면 **운영자가 안내를 읽었을 때만** 문제를 피한다.

**Phase 2 가 Phase 3 을 막고 있다.** PRD 는 1 과 2 를 병렬로 뒀지만, 진단 체인(2 → 3)이 더 긴 경로다. 1 은 아무것도 막지 않는다. 병렬로 돌릴 수 있다면 2 를 먼저 시작하는 편이 낫다.

**보고된 세 번째 증상(종목명 강등)은 이 Phase 와 무관하다.** PRD 조사에서 재현되지 않았고 원인은 체인 FALLBACK 로그의 레벨이다(Phase 4). 이 Phase 로 해결되지 않는다.

**측정 환경**: 이 호스트도 보고된 맥미니와 같은 조건이다 — `/usr/bin/python3` = 3.9.6, `import mcp` 실패. 즉 `PRISM_MCP_PYTHON` 을 안 넣으면 여기서도 같은 일이 난다.

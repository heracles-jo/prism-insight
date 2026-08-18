# Plan: `kospi_kosdaq` 를 자격증명 없는 자체 서버로 전환

## Summary

KRX 로그인이 깨져 `kospi_kosdaq` 도구가 전부 실패하는 상태를, 저장소 안에 이미 있는 대체 서버(`cores/market_data/mcp_server.py`)로 돌려 해소한다. 조사해 보니 **리포트 경로는 이미 전환돼 있고**, 남은 소비자는 두 곳이다 — 분석 에이전트가 쓰는 `mcp_agent.config.yaml`, 그리고 MCP 를 아예 우회해 PyPI 모듈을 직접 임포트하는 `cores/data_prefetch.py`.

## User Story

As a **KRX 계정 없이 PRISM 을 돌리는 운영자**,
I want **시세·수급 도구가 자격증명 없이 동작하기를**,
So that **로그인이 끊겼다는 이유로 리포트 지표와 매매 판단 근거가 조용히 비지 않는다.**

## Problem → Solution

`KRX_ID`/`KRX_PW` → 로그인 실패 → 폴백 없음 → 도구 전부 `error` → **배치는 정상 종료, 데이터만 빔**
**→** 소스 체인(`toss,krx,fdr`) 위의 자체 서버 → 자격증명 불필요

## Metadata
- **Complexity**: **Medium** — 파일 수는 적으나 소비 경로가 셋으로 갈려 있고 그중 둘이 서로 다른 메커니즘이다
- **Source PRD**: `.claude/PRPs/prds/kospi-kosdaq-mcp-deauth.prd.md`
- **PRD Phase**: Phase 2 — MCP 서버 전환
- **Estimated Files**: 3 (+1 테스트)

---

## 조사로 드러난 것 — PRD 의 가정을 정정한다

PRD 는 "`mcp_agent.config.yaml` 만 옛 경로에 머물러 있다" 고 적었다. **측정해 보니 소비 경로가 셋이고, 그중 하나는 이미 전환돼 있다.**

| # | 경로 | 무엇이 결정하나 | 현재 상태 |
|---|---|---|---|
| 1 | 리포트 생성 (`report_generator.py`, `cores/report_generation.py`) | `cores/llm/config_loader.py` → **native `cores/llm/mcp_servers.yaml`** | ✅ **이미 자체 서버** |
| 2 | 분석 에이전트 (`stock_analysis_orchestrator.py` → `MCPApp` → `Agent(server_names=["kospi_kosdaq"])`) | mcp-agent 프레임워크 → **`mcp_agent.config.yaml`** | ❌ PyPI + `KRX_ID`/`KRX_PW` |
| 3 | 프리페치 (`cores/data_prefetch.py`) | **MCP 를 안 쓴다.** `import kospi_kosdaq_stock_server as server` 직접 임포트 | ❌ PyPI |

`tools/mcp_doctor.py` 실측:
```
config: native=y legacy=y
  note: a legacy mcp_agent.config.yaml is still present but no longer used; delete it to stop it drifting
[report] source=.../cores/llm/mcp_servers.yaml
  FAIL kospi_kosdaq   command=python3
        env PRISM_REPORT_DATA_SOURCES <- inline [UNSET]
        problems: UNSET_ENV
```
경로 1 은 자체 서버를 가리키지만 **선언된 선택적 env 가 비어 있어 unhealthy 로 잡힌다.** 이것도 이 Phase 에서 정리한다.

**경로 3 이 가장 중요하다.** 에이전트는 `server_names=[] if prefetched_data else ["kospi_kosdaq"]` 로 만들어진다 (`cores/agents/stock_price_agents.py:162,315`) — 즉 **프리페치가 성공하면 MCP 를 아예 쓰지 않는다.** 프리페치가 주 경로이고 MCP 는 폴백이다.

---

## UX Design

**Internal change — no user-facing UX transformation.**

간접 효과: 현재 비어 있는 리포트 항목(이동평균·RSI·MACD·볼린저·지지저항·투자자별 순매수)이 채워진다.

### Interaction Changes

| Touchpoint | Before | After | Notes |
|---|---|---|---|
| `data_prefetch` 로그 | `OHLCV 데이터 조회 실패. ...(KRX_ID, KRX_PW) 환경변수가 설정...` | 정상 마크다운 | 주 경로 |
| 분석 에이전트 MCP 폴백 | 도구 호출 시 `error` dict | 실데이터 | 프리페치 실패 시에만 탐 |
| `mcp_doctor` | `unhealthy servers: 3` | `kospi_kosdaq` 이 목록에서 빠짐 | firecrawl·perplexity 는 별건(키 미설정) |
| 운영자 설정 | `mcp_agent.config.yaml` 에 KRX 계정 | 없음 | |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `cores/llm/mcp_servers.yaml` | 20-45 | **이미 완성된 정답 설정.** `mcp_agent.config.yaml` 에 그대로 옮긴다. 주석에 함정 2 개가 기록돼 있다 |
| P0 | `cores/data_prefetch.py` | 55-67 | `_get_mcp_server_module()` — 경로 3 의 **유일한 교체 지점** |
| P0 | `cores/data_prefetch.py` | 84-92, 108-115, 134-140 | 직접 호출 3 곳. 인자 순서 `(start, end, ticker)` |
| P0 | `cores/data_prefetch.py` | 218-236 | 섹터 블록 — `get_sector_info` 가 자체 서버에 **없다**. `except Exception` 이 덮는지 확인 |
| P1 | `cores/market_data/mcp_server.py` | 1-40 | 왜 만들어졌는지(2026-08-05 사건). 도구 계약 |
| P1 | `cores/market_data/mcp_server.py` | 329-335 | `main()` / `__main__` — `python -m` 진입점 |
| P1 | `mcp_agent.config.yaml` | `kospi_kosdaq` 블록 | 바꿀 대상 |
| P2 | `tools/mcp_doctor.py` | 1-30 | 검증 도구. 시크릿을 절대 출력하지 않는다 |
| P2 | `cores/agents/stock_price_agents.py` | 159-163, 312-316 | `server_names=[] if prefetched_data else [...]` — 프리페치가 주 경로인 근거 |

## External Documentation

없음. **No external research needed — feature uses established internal patterns.**

---

## Patterns to Mirror

### MCP_SERVER_CONFIG (정답이 이미 저장소에 있다)
```yaml
# SOURCE: cores/llm/mcp_servers.yaml:20-45
  # KRX 자격증명(KAKAO_ID/KAKAO_PW)이 더 이상 필요 없다.
  #
  # command: 이 서버는 리포 코드를 import 하므로 **리포 의존성이 설치된 인터프리터**
  # 여야 한다. 맨 `python3` 는 호스트마다 다른 것을 가리킨다 — 맥미니에서는
  # Homebrew 3.14 라서 `mcp` 조차 없다. 오케스트레이터를 띄우는 것과 같은 파이썬을
  # PRISM_MCP_PYTHON 으로 지정한다.
  kospi_kosdaq:
    command: ${PRISM_MCP_PYTHON:-python3}
    args:
    - -m
    - cores.market_data.mcp_server
    env:
      # 레지스트리가 env 를 명시적으로 넘기므로 리포 루트를 직접 준다.
      # 기본값 '.' 는 자식의 cwd(오케스트레이터가 리포 루트에서 실행)를 가리킨다.
      PYTHONPATH: ${PRISM_REPO_ROOT:-.}
```
**두 함정이 주석으로 남아 있다.** 맨 `python3` 는 호스트에 따라 `mcp` 조차 없는 인터프리터를 가리킨다. 그리고 레지스트리가 env 를 명시적으로 넘기므로 `PYTHONPATH` 를 직접 줘야 한다.

### LAZY_MODULE_ACCESSOR
```python
# SOURCE: cores/data_prefetch.py:55-67
def _get_mcp_server_module():
    """Import kospi_kosdaq_stock_server module for direct library calls.

    Returns:
        The kospi_kosdaq_stock_server module, or None if import fails
    """
    try:
        import kospi_kosdaq_stock_server as server
        return server
    except ImportError:
        logger.warning("kospi_kosdaq_stock_server module not available, prefetch disabled")
        return None
```
모듈을 함수 안에서 임포트하고 실패하면 `None` 을 반환한다. 호출측은 `if not server: return ""` 로 degrade 한다. **이 계약을 유지한다.**

### GUARDED_CALL
```python
# SOURCE: cores/data_prefetch.py:80-93
    try:
        server = _get_mcp_server_module()
        if not server:
            return ""

        data = server.get_stock_ohlcv(start_date, end_date, company_code)

        return _dict_to_markdown(data, f"Stock OHLCV: {company_code} ({start_date}~{end_date})")
    except Exception as e:
```
전 호출이 `try/except Exception` 안에 있고 실패 시 빈 문자열이다. 섹터 블록도 마찬가지라 **없는 함수를 불러도 `AttributeError` 가 잡혀 오늘과 같은 경고로 끝난다.**

### LOGGING_PATTERN
```python
# SOURCE: cores/data_prefetch.py:232-236
        if sector_data:
            result["sector_map"] = sector_data
            logger.info(f"Prefetched sector_map: {len(sector_data)} tickers")
        else:
            logger.warning("Sector map not available from get_sector_info")
```
성공은 `logger.info` + 건수, 부분 실패는 `logger.warning`, 예외는 `logger.error`. f-string.

### TEST_STRUCTURE
```python
# SOURCE: tests/test_toss_source.py:56-66
def make_source(responses):
    from cores.market_data.toss_source import TossSource

    client = StubClient(responses)
    return TossSource(client), client
```
```python
# SOURCE: tests/test_no_module_scope_kis_import.py (이번 세션에 추가된 tripwire)
result = subprocess.run(
    [sys.executable, "-c", probe],
    cwd=str(REPO_ROOT), env={**os.environ, ...},
    capture_output=True, text=True, timeout=300,
)
```
설정·임포트 성질을 검사할 때는 **서브프로세스**를 쓴다. 현재 프로세스의 `sys.modules` 가 검사 대상을 가리기 때문이다.

### TEST_NAMING
```python
# SOURCE: tests/test_toss_source.py:178
def test_pagination_stops_when_the_cursor_stops_moving():
    """A provider repeating its cursor must not spin forever."""
```
주장하는 성질을 문장으로. 도크스트링은 **어떤 실패를 막는지** 한 줄.

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `mcp_agent.config.yaml` | UPDATE | 경로 2. `kospi_kosdaq` 를 자체 서버로, `KRX_ID`/`KRX_PW`/`KRX_LOGIN_METHOD` 제거 |
| `cores/data_prefetch.py` | UPDATE | 경로 3. `_get_mcp_server_module()` 이 자체 모듈을 반환 |
| `cores/llm/mcp_servers.yaml` | UPDATE | `PRISM_REPORT_DATA_SOURCES` 미설정이 unhealthy 로 잡히는 문제 |
| `tests/test_kospi_kosdaq_server_switch.py` | CREATE | 회귀 고정 |

> ⚠️ `mcp_agent.config.yaml` 은 **사용자가 직접 자격증명을 넣어 관리하는 파일**이다. `KRX_ID`/`KRX_PW` 값을 커밋에 노출하지 말 것 — 키를 지우는 변경이므로 diff 에 값이 **삭제된 형태로 남는다.** 커밋 전 `git diff --cached` 로 확인하고, 값이 보이면 `mcp_agent.config.yaml.example` 쪽만 고치고 실파일은 사용자가 직접 수정하도록 안내한다.

## NOT Building

- **`get_sector_info` 구현** — PRD Phase 3. 이 Phase 에서는 **없는 채로 degrade** 하면 된다 (오늘과 동일)
- **`load_all_tickers`** — 13 개 프롬프트 전부가 "절대 사용 금지" 다 (측정 완료). 구현하지 않는다
- **`get_stock_fundamental`** — 소비처가 `cores/archive/` 뿐
- **`mcp_agent.config.yaml` 삭제** — `mcp_doctor` 가 권하지만, 다른 서버(firecrawl·perplexity 등) 설정이 아직 이 파일에 있고 mcp-agent 프레임워크가 읽는다. 파일 제거는 별건
- **firecrawl / perplexity 의 UNSET_ENV** — 키 미설정 문제이고 브로커·시세와 무관
- **`prism-us`** — `prism-us/cores/data_prefetch.py` 는 `kospi_kosdaq` 를 쓰지 않는다 (grep 0 건). KR 전용 문제다
- **조용한 실패 개선** — PRD Phase 4

---

## Step-by-Step Tasks

### Task 1: `mcp_agent.config.yaml` 의 `kospi_kosdaq` 전환

- **ACTION**: `kospi_kosdaq` 블록을 자체 서버로 교체하고 `env` 의 KRX 키 3 개를 제거한다
- **IMPLEMENT**:
```yaml
    kospi_kosdaq:
      # 소스 체인(toss → krx → fdr) 위에서 도는 저장소 내 서버. 도구명·인자·반환이
      # PyPI kospi-kosdaq-stock-server 와 동일하므로 에이전트 프롬프트는 그대로다.
      # KRX 가 로그인을 필수화한 뒤 그 패키지는 자격증명 없이 아무것도 못 준다.
      #
      # command: 이 서버는 리포 코드를 import 하므로 리포 의존성이 설치된
      # 인터프리터여야 한다. 맨 python3 는 호스트마다 다른 것을 가리킨다.
      command: "${PRISM_MCP_PYTHON:-python3}"
      args:
        [
          "-m",
          "cores.market_data.mcp_server"
        ]
      env:
        # 자식 프로세스가 리포를 임포트할 수 있어야 한다.
        PYTHONPATH: "${PRISM_REPO_ROOT:-.}"
```
- **MIRROR**: `MCP_SERVER_CONFIG` — `cores/llm/mcp_servers.yaml:20-45` 를 그대로 따른다. 이미 검증된 설정이다
- **IMPORTS**: 없음
- **GOTCHA**: ① `command` 를 맨 `python3` 로 두지 말 것 — 호스트에 따라 `mcp` 조차 없는 인터프리터다. ② `PYTHONPATH` 를 빼지 말 것 — 자식이 `cores.market_data` 를 못 찾는다. ③ mcp-agent 프레임워크가 `${VAR:-default}` 치환을 지원하는지 확인해야 한다. **지원하지 않으면 리터럴 문자열이 command 가 되어 기동이 실패한다** — Task 1 VALIDATE 가 이것을 잡는다
- **VALIDATE**:
```bash
PYTHONPATH=$PWD .venv/bin/python -c "
import yaml
c = yaml.safe_load(open('mcp_agent.config.yaml', encoding='utf-8'))
s = c['mcp']['servers']['kospi_kosdaq']
assert s['args'] == ['-m', 'cores.market_data.mcp_server'], s['args']
assert not any(k.startswith('KRX') for k in (s.get('env') or {})), s.get('env')
print('  config OK:', s['command'], s['args'], list((s.get('env') or {})))
"
```

### Task 2: 치환 지원 여부 확인 후 `command` 확정

- **ACTION**: mcp-agent 가 `${VAR:-default}` 를 치환하는지 실측하고, **치환하지 않으면 리터럴 인터프리터 경로로 바꾼다**
- **IMPLEMENT**: 아래 VALIDATE 를 먼저 돌린다. 치환이 안 되면 `command` 를 `"python3"` 로 두되 **`.env` 에 아무것도 요구하지 않는 형태**로 하고, 인터프리터 불일치 위험을 `docs/SETUP_ko.md` 에 한 줄로 남긴다
- **MIRROR**: `MCP_SERVER_CONFIG` 의 주석이 이 위험을 이미 설명한다
- **IMPORTS**: 없음
- **GOTCHA**: `.env` 에 `PRISM_MCP_PYTHON`·`PRISM_REPO_ROOT` 가 **현재 둘 다 미설정**이다 (측정 완료). 따라서 기본값이 실제로 쓰인다 — 기본값이 동작해야 한다
- **VALIDATE**:
```bash
# mcp-agent 가 config 를 읽어 실제로 어떤 command 를 쓰는지
PYTHONPATH=$PWD .venv/bin/python -c "
from mcp_agent.config import get_settings
s = get_settings()
srv = s.mcp.servers['kospi_kosdaq']
print('  command =', repr(srv.command))
print('  args    =', srv.args)
print('  env     =', srv.env)
print('  치환됨?', '\${' not in str(srv.command))
"
```
EXPECT: `치환됨? True`. **False 면 리터럴로 바꾼다**

### Task 3: `data_prefetch` 를 자체 모듈로 (가장 중요)

- **ACTION**: `cores/data_prefetch.py:55-67` 의 `_get_mcp_server_module()` 이 자체 모듈을 반환하게 한다
- **IMPLEMENT**:
```python
def _get_mcp_server_module():
    """Market-data module used for direct library calls.

    Returns the repo's own server rather than the PyPI
    `kospi_kosdaq_stock_server`. That package scrapes KRX Data Marketplace with
    an id/password session, and once KRX made login mandatory it stopped
    answering at all — its pykrx fallback is disabled for the same reason. The
    failure was quiet: every tool returned an error dict, the batch finished,
    and the report simply had no moving averages, RSI, MACD or investor flows.

    The repo module exposes the same function names, argument order and dict
    shape on top of the source chain, so nothing downstream changes except
    where the numbers come from.

    Returns:
        The market data module, or None if import fails
    """
    try:
        import cores.market_data.mcp_server as server
        return server
    except ImportError:
        logger.warning("market data module not available, prefetch disabled")
        return None
```
- **MIRROR**: `LAZY_MODULE_ACCESSOR` — 함수 내 임포트, 실패 시 `None`, `logger.warning`. 계약을 그대로 유지한다
- **IMPORTS**: 함수 안의 `import cores.market_data.mcp_server as server` 뿐. 모듈 스코프 임포트 추가 금지 (이 세션의 tripwire 가 KIS 에 대해 같은 규칙을 강제한다)
- **GOTCHA**: ① `get_sector_info` 는 자체 모듈에 **없다.** `data_prefetch:223` 이 호출하지만 `except Exception` 안이라 `AttributeError` 가 잡혀 오늘과 동일한 경고로 끝난다 — **VALIDATE 에서 실제로 확인할 것.** ② 인자 순서는 `(start, end, ticker)` 로 양쪽이 같다. ③ `@mcp.tool()` 데코레이터가 원본 함수를 그대로 돌려주므로 직접 호출이 동작한다 (측정 완료: `type=function`, `has .fn=False`)
- **VALIDATE**:
```bash
PYTHONPATH=$PWD .venv/bin/python - <<'PY'
from dotenv import load_dotenv
load_dotenv("/Users/heracles/workspace/prism-insight/.env")
import logging; logging.basicConfig(level=logging.WARNING)
import cores.data_prefetch as dp
print("모듈:", dp._get_mcp_server_module().__name__)
print("OHLCV:", dp.prefetch_stock_ohlcv("005930", "20260811", "20260818")[:90] or "(빈 문자열=실패)")
print("수급  :", dp.prefetch_stock_trading_volume("005930", "20260811", "20260818")[:90] or "(빈 문자열=실패)")
print("지수  :", dp.prefetch_index_ohlcv("1001", "20260811", "20260818")[:90] or "(빈 문자열=실패)")
PY
```
EXPECT: 모듈이 `cores.market_data.mcp_server`, 세 항목 모두 비어 있지 않은 마크다운

### Task 4: 선택적 env 가 unhealthy 로 잡히는 문제 정리

- **ACTION**: `cores/llm/mcp_servers.yaml` 의 `PRISM_REPORT_DATA_SOURCES` 를 `mcp_doctor` 가 UNSET_ENV 로 잡지 않게 한다
- **IMPLEMENT**: 기본값을 주어 항상 값이 있게 한다 — `PRISM_REPORT_DATA_SOURCES: ${PRISM_REPORT_DATA_SOURCES:-}` 는 빈 문자열이라 UNSET 으로 잡힌다. **선언 자체를 지우는 쪽**이 맞다: 서버가 이미 미설정 시 일반 체인 순서로 폴백하므로(yaml 주석), 빈 값을 명시적으로 넘길 이유가 없다
- **MIRROR**: `MCP_SERVER_CONFIG` 의 기존 주석 — 결정을 주석으로 남기는 관례
- **IMPORTS**: 없음
- **GOTCHA**: 지우기 전에 **서버가 이 env 를 정말 선택적으로 다루는지 확인**할 것. `cores/market_data/mcp_server.py` 에서 `PRISM_REPORT_DATA_SOURCES` 를 어떻게 읽는지 보고, 필수라면 지우지 말고 `mcp_doctor` 쪽을 고친다
- **VALIDATE**: `PYTHONPATH=$PWD .venv/bin/python tools/mcp_doctor.py` 에서 `kospi_kosdaq` 이 FAIL 목록에서 빠진다

### Task 5: 회귀 테스트

- **ACTION**: `tests/test_kospi_kosdaq_server_switch.py` 를 만든다
- **IMPLEMENT**:
```python
"""The kospi_kosdaq tools must not need KRX credentials.

KRX made login mandatory, the PyPI `kospi-kosdaq-stock-server` has no
credential-free path, and its pykrx fallback is switched off. Every tool then
returns an error dict — quietly, because the batch still finishes and the
report is still produced, just without moving averages, RSI, MACD or investor
flows. These tests pin the two consumers that were still pointed at it.
"""

import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def _server_config(path):
    config = yaml.safe_load((REPO_ROOT / path).read_text(encoding="utf-8"))
    servers = config.get("mcp", {}).get("servers") or config.get("servers") or config
    return servers["kospi_kosdaq"]


def test_the_agent_config_points_at_the_repo_server():
    """`mcp_agent.config.yaml` drives the analysis agents' MCP tools."""
    server = _server_config("mcp_agent.config.yaml")

    assert server["args"] == ["-m", "cores.market_data.mcp_server"]


def test_no_krx_credentials_are_required_anywhere():
    """A credential named here is a credential the operator must obtain."""
    for path in ("mcp_agent.config.yaml", "cores/llm/mcp_servers.yaml"):
        env = _server_config(path).get("env") or {}
        leftovers = [k for k in env if k.startswith(("KRX_", "KAKAO_"))]
        assert not leftovers, f"{path} still demands {leftovers}"


def test_prefetch_does_not_import_the_credentialed_package():
    """data_prefetch bypasses MCP entirely, so the config swap misses it.

    Agents are built with `server_names=[] if prefetched_data else [...]`, so
    prefetch is the primary path and MCP is only the fallback. Leaving this on
    the PyPI package would keep the main path broken while the config looked
    fixed.
    """
    probe = (
        "import cores.data_prefetch as dp\n"
        "print(dp._get_mcp_server_module().__name__)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=300,
    )

    assert result.returncode == 0, result.stderr[-2000:]
    assert "cores.market_data.mcp_server" in result.stdout


def test_the_repo_server_starts_as_a_module():
    """`python -m` is how the config launches it; an import error is silent there."""
    probe = "import cores.market_data.mcp_server as m; print(bool(m.main))"
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=300,
    )

    assert result.returncode == 0, result.stderr[-2000:]
    assert "True" in result.stdout
```
- **MIRROR**: `TEST_STRUCTURE` (서브프로세스), `TEST_NAMING` (성질을 문장으로, 도크스트링은 막는 실패)
- **IMPORTS**: `subprocess`, `sys`, `pathlib.Path`, `yaml` — 전부 표준/기존 의존성
- **GOTCHA**: ① `mcp_agent.config.yaml` 과 `cores/llm/mcp_servers.yaml` 은 **구조가 다르다** (`mcp.servers` vs 최상위). `_server_config` 가 둘 다 다룬다. ② 프리페치 테스트는 **서브프로세스**여야 한다 — 다른 테스트가 이미 임포트한 모듈이 결과를 가릴 수 있다
- **VALIDATE**: `pytest tests/test_kospi_kosdaq_server_switch.py -q` 전부 통과, 그리고 변경을 되돌리면 red 로 뒤집힌다

---

## Testing Strategy

### Unit Tests

| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| `test_the_agent_config_points_at_the_repo_server` | `mcp_agent.config.yaml` | args == `["-m","cores.market_data.mcp_server"]` | 회귀 |
| `test_no_krx_credentials_are_required_anywhere` | 두 레지스트리 | `KRX_*`/`KAKAO_*` 키 0 | 회귀 |
| `test_prefetch_does_not_import_the_credentialed_package` | 서브프로세스 임포트 | `cores.market_data.mcp_server` | **가장 놓치기 쉬운 경로** |
| `test_the_repo_server_starts_as_a_module` | 서브프로세스 임포트 | `main` 존재 | 기동 불가 조기 발견 |

### Edge Cases Checklist
- [x] **없는 도구 호출** — `get_sector_info` 부재. 기존 `except Exception` 이 덮는지 VALIDATE 로 확인
- [x] **환경변수 미설정** — `PRISM_MCP_PYTHON`·`PRISM_REPO_ROOT` 둘 다 현재 미설정. 기본값이 동작해야 함
- [x] **네트워크 실패** — 소스 체인이 순차 폴백. 기존 동작
- [ ] 빈 입력 — 해당 없음
- [ ] 동시 접근 — 해당 없음
- [ ] 권한 거부 — 해당 없음 (자격증명을 없애는 것이 이 작업)

---

## Validation Commands

### Static Analysis
```bash
.venv/bin/python -m py_compile cores/data_prefetch.py tests/test_kospi_kosdaq_server_switch.py
.venv/bin/python -c "import yaml; yaml.safe_load(open('mcp_agent.config.yaml',encoding='utf-8')); yaml.safe_load(open('cores/llm/mcp_servers.yaml',encoding='utf-8')); print('yaml ok')"
```
EXPECT: 오류 없음

### Unit Tests
```bash
.venv/bin/python -m pytest tests/test_kospi_kosdaq_server_switch.py -p no:cacheprovider -q
```
EXPECT: 4 passed

### MCP 상태 진단
```bash
PYTHONPATH=$PWD .venv/bin/python tools/mcp_doctor.py
```
EXPECT: `kospi_kosdaq` 이 FAIL 목록에 없다. `unhealthy servers` 가 3 → **2** (firecrawl·perplexity 는 API 키 미설정으로 별건)

### 프리페치 실동작 (읽기 전용)
```bash
PYTHONPATH=$PWD .venv/bin/python - <<'PY'
from dotenv import load_dotenv
load_dotenv("/Users/heracles/workspace/prism-insight/.env")
import logging; logging.basicConfig(level=logging.WARNING)
import cores.data_prefetch as dp
print("모듈:", dp._get_mcp_server_module().__name__)
for label, fn, args in (
    ("OHLCV", dp.prefetch_stock_ohlcv, ("005930", "20260811", "20260818")),
    ("수급",  dp.prefetch_stock_trading_volume, ("005930", "20260811", "20260818")),
    ("지수",  dp.prefetch_index_ohlcv, ("1001", "20260811", "20260818")),
):
    out = fn(*args)
    print(f"  {label}: {'OK len=' + str(len(out)) if out else '실패(빈 문자열)'}")
PY
```
EXPECT: 모듈 `cores.market_data.mcp_server`, 세 항목 전부 OK

### 자격증명 제거 확인
```bash
git grep -n "KRX_ID\|KRX_PW\|KRX_LOGIN_METHOD" -- 'mcp_agent.config.yaml' 'cores/llm/mcp_servers.yaml' || echo "  없음 ✓"
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
EXPECT: **22 failed / 10 errors** — 전부 사전 존재. 넘으면 회귀
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
- [ ] `mcp_agent.config.yaml` diff 에 **KRX 자격증명 값이 노출되지 않는다** (삭제 라인에 값이 찍힌다 — 커밋 전 반드시 확인)
- [ ] 섹터 블록이 `AttributeError` 를 삼키고 오늘과 같은 경고만 남긴다
- [ ] `mcp_doctor` 의 `unhealthy servers` 에서 `kospi_kosdaq` 이 사라졌다

---

## Acceptance Criteria
- [ ] Task 1-5 완료
- [ ] `mcp_doctor` 에서 `kospi_kosdaq` FAIL 해소
- [ ] `data_prefetch` 가 자체 모듈을 쓰고 3 개 프리페치가 실데이터 반환
- [ ] 두 레지스트리에 `KRX_*`/`KAKAO_*` 키 0
- [ ] 신규 테스트 4 개 통과 + 변이 검증
- [ ] KIS 회귀 99/99
- [ ] 전체 스위트 baseline(22 failed / 10 errors) 동일
- [ ] **자격증명 값이 커밋에 들어가지 않았다**

## Completion Checklist
- [ ] `cores/llm/mcp_servers.yaml` 의 검증된 설정을 그대로 따랐다 (독자 설계 금지)
- [ ] `_get_mcp_server_module()` 의 반환 계약(모듈 또는 `None`)을 유지했다
- [ ] 모듈 스코프 임포트를 추가하지 않았다
- [ ] 없는 `get_sector_info` 가 degrade 로 끝난다
- [ ] 테스트가 두 yaml 의 구조 차이를 다룬다
- [ ] 범위 밖 변경 없음 (섹터 구현은 Phase 3)

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **자격증명이 diff 에 노출** | **M** | **H** | 삭제 라인에 값이 찍힌다. 커밋 전 `git diff --cached` 확인을 완료 조건에 넣음. 노출되면 `.example` 만 고치고 실파일은 사용자에게 맡긴다 |
| mcp-agent 가 `${VAR:-기본값}` 치환 미지원 | **M** | H | **Task 2 가 이것만 위해 존재한다.** 미지원이면 리터럴로 되돌린다 |
| 호스트의 `python3` 가 리포 의존성이 없는 인터프리터 | **M** | H | 이미 겪은 문제라 `cores/llm/mcp_servers.yaml` 주석에 기록돼 있다. `PRISM_MCP_PYTHON` 으로 지정 |
| `get_sector_info` 부재가 예상과 달리 크래시 | L | M | `except Exception` 안이지만 **실제로 확인한다** (Manual Validation) |
| 자체 서버가 못 덮는 도구를 에이전트가 호출 | L | M | `load_all_tickers` 13 곳 전부 사용 금지(측정), `get_stock_fundamental` 은 아카이브 전용 |
| 프리페치 실패가 조용히 빈 문자열로 끝난다 | **H** | M | 기존 동작이며 Phase 4 의 주제. 이 Phase 에서는 VALIDATE 로 사람이 확인 |

## Notes

**PRD 의 범위 서술을 정정해야 한다.** PRD 는 "config 만 옛 경로에 머물러 있다" 고 적었으나 소비 경로는 셋이고 그중 리포트 경로는 **이미 전환돼 있다**. 남은 둘 중 `data_prefetch` 는 MCP 를 아예 우회하므로 **config 를 아무리 고쳐도 안 바뀐다.** 구현 시 PRD 의 해당 서술을 함께 고칠 것.

**왜 `data_prefetch` 가 더 중요한가**: 에이전트는 `server_names=[] if prefetched_data else ["kospi_kosdaq"]` 로 만들어진다. 프리페치가 성공하면 MCP 를 아예 쓰지 않는다. 즉 프리페치가 주 경로이고 MCP 는 폴백이다. config 만 고치면 **주 경로는 그대로 깨진 채 설정만 고쳐진 것처럼 보인다.**

**이 Phase 만으로는 PRD 의 성공 지표가 안 채워진다.** `get_sector_info` 는 여전히 없어 `sector_map` 이 비고(Phase 3), 실패의 조용함도 그대로다(Phase 4). 이 Phase 의 목표는 "6/6 도구" 가 아니라 **"자격증명 없이 5 개 도구가 실데이터를 준다"** 이다.

**측정 환경**: `PRISM_BROKER=toss`, `PRISM_TRADING_MODE=real`, `PRISM_MARKET_DATA_SOURCES=toss,krx,fdr`. KRX 는 이 머신에서 로그인 실패 중이라 체인이 토스에 의존한다. Phase 1(`_FLOW_PAGE=100`)이 이미 머지돼 있어 수급도 동작한다.

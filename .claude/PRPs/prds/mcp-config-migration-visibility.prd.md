# MCP 설정 이전이 남긴 것 — 안내되지 않은 변수와 못 보는 진단

> **출처**: 다른 호스트에서 전체 기동 후 올라온 보고 (2026-08-18). 네 항목 전부 이 저장소에서 재현·측정했다.

---

## Problem Statement

리포트 경로의 MCP 설정이 `mcp_agent.config.yaml`(레거시, 값 하드코딩)에서 `cores/llm/mcp_servers.yaml`(환경변수 치환)로 이전됐다. 이전 자체는 옳지만 **운영자에게 무엇을 `.env`에 넣어야 하는지 알려주는 것이 하나도 없다.** 그중 `PRISM_MCP_PYTHON`은 미설정 시 MCP 서버가 **조용히 죽고**, 그 상태를 진단하라고 만든 `tools/mcp_doctor.py`는 정상인 서버를 FAIL로, 죽은 서버를 OK로 보고한다.

## Evidence

**보고 (다른 호스트, 전체 기동)**

> `.env`에 다음을 추가해야 했습니다: `FIRECRAWL_API_KEY`, `PERPLEXITY_API_KEY`, `PRISM_MCP_PYTHON`, `PRISM_REPO_ROOT`
> `PRISM_MCP_PYTHON`이 특히 중요했습니다 … 미설정 시 `kospi_kosdaq`·`time` 서버가 조용히 죽습니다.
> `tools/mcp_doctor.py`는 `.env`를 로드하지 않아 … 실제로는 정상인 서버를 `UNSET_ENV`로 FAIL 처리합니다.

**이 저장소에서 재현 (2026-08-18)**

| 주장 | 확인 |
|---|---|
| 새 변수 4종이 안내되지 않음 | `.env.example` 에 **6/6 전부 없음**. 보고된 4종 + `PRISM_REPORT_DATA_SOURCES` + `PRISM_MCP_WORKDIR`(구현 중 테스트가 발견) |
| 이 호스트의 `python3` 가 `mcp` 를 못 가짐 | `/usr/bin/python3` = **3.9.6**, `import mcp` → `ModuleNotFoundError` |
| `mcp_doctor` 가 `.env` 를 안 읽음 | 파일에 `load_dotenv` **없음**. `os.environ` 만 본다 |
| 종목명이 코드로 강등됨 | **재현 안 됨** — 아래 참조 |

**세 번째 보고는 오독이었고, 원인은 로그다**

```
WARNING [FALLBACK] fdr answered ticker_name after krx: KRX 직접 로그인 정보가 필요합니다.
```

기본 체인(`krx,fdr`)에서 `get_market_ticker_name("005930")` 은 `'삼성전자'` 를 정상 반환한다. FDR 이 답한다. 그런데 **체인은 성공했는데도 WARNING 을 찍고**, KRX 가 첫 소스이고 자격증명이 없으면 **모든 조회마다** 찍는다. 보고자가 "강등"으로 읽은 것이 이것이다.

## 이것이 직전 작업의 이면이라는 점

방금 끝낸 PRD(`kospi-kosdaq-mcp-deauth`)의 Phase 4 는 **침묵이 실패를 감추는 문제**를 다뤘다 — `"Top-down pool: empty (pure bottom-up mode)"`. 이번 보고는 **소음이 성공을 감추는 정반대 문제**다. 둘 다 결과는 같다: 로그가 신호로서 쓸모없어진다.

`mcp_doctor` 는 자기 도크스트링에 이 원칙을 적어두고 있다:

> "Being strict here matters: this output is meant to be diffed across hosts, and **a false positive is indistinguishable from a real breakage**."

`.env` 를 안 읽어 정상 서버를 FAIL 로 보고하는 것이 정확히 그 위반이다.

## Proposed Solution

네 가지를 각각 끊는다. 공통 주제는 **"운영자가 알아야 할 것을 시스템이 말하게 한다"** 이다.

1. **안내**: `.env.example` 과 설정 문서가 새 변수를 이유와 함께 적는다
2. **진단이 진실을 말하게**: `mcp_doctor` 가 런타임과 같은 방식으로 환경을 해석한다(`.env` 로드)
3. **진단이 못 보는 것을 보게**: "명령이 PATH 에 있다" 와 "그 명령이 이 서버를 띄울 수 있다" 를 구분한다
4. **복구된 실패를 실패처럼 말하지 않는다**: 체인이 답을 얻었으면 WARNING 이 아니다

## Key Hypothesis

We believe **설정 요구사항을 명시하고 진단 도구가 런타임과 같은 환경을 보게 하는 것**이 **MCP 서버가 조용히 죽는데 진단은 엉뚱한 곳을 가리키는 문제**를 **새 호스트에 배포하는 운영자**에게 해결해 줄 것이다.

We'll know we're right when **`.env.example` 만 보고 설정한 새 호스트에서 `mcp_doctor` 가 런타임과 일치하는 결과를 내고, `mcp` 가 없는 인터프리터가 지정되면 그것을 FAIL 로 잡을 때**.

## What We're NOT Building

- **`mcp_agent.config.yaml` 의 치환 지원** — mcp-agent 프레임워크가 `${VAR:-default}` 를 확장하지 않는 것은 측정으로 확인됐고(리터럴 문자열 반환), 프레임워크를 고치는 것은 이 범위 밖이다. 대신 그 파일의 제약을 문서화한다
- **레거시 `mcp_agent.config.yaml` 삭제** — `mcp_doctor` 가 권하지만 mcp-agent 프레임워크(분석 에이전트 경로)가 아직 읽는다. 별건
- **KRX 자격증명 복구** — 이번 작업의 전제가 자격증명 없이 도는 것이다
- **체인 로그의 전면 개편** — FALLBACK 한 줄의 레벨만 다룬다
- **`PRISM_MCP_PYTHON` 자동 탐지** — `sys.executable` 로 추정할 수 있지만, 오케스트레이터와 MCP 서버가 다른 인터프리터여야 하는 경우를 막게 된다. 명시를 요구하고 안내를 개선한다

## Success Metrics

| Metric | Target | How Measured |
|--------|--------|--------------|
| 설정 안내 | 새 변수 **6/6** 가 `.env.example` 에 이유와 함께 | grep |
| 진단 일치 | `mcp_doctor` 결과가 런타임 해석과 **동일** | `.env` 로드 전후 비교 |
| 인터프리터 검증 | `mcp` 없는 인터프리터를 **FAIL 로 잡음** | `/usr/bin/python3` 지정 후 실행 |
| 로그 소음 | 체인이 성공한 호출에서 WARNING **0건** | 기본 체인 종목명 조회 |
| 회귀 | KIS 99/99, 전체 스위트 baseline 동일 | pytest |

## Open Questions

- [ ] 체인 FALLBACK 로그를 INFO 로 내리면, 첫 소스가 상시 실패하는 상태(지금의 KRX)를 **어떻게 알아채는가**? 매 호출 WARNING 은 소음이지만 완전한 침묵은 Phase 4 가 고친 문제로 되돌아간다. 사이클당 1회 요약이 답일 수 있으나 미검증
- [x] ~~인터프리터 검증 범위~~ — **해소.** 레지스트리의 파이썬 서버는 둘 다 `python -m <모듈>` 형태이므로 **그 모듈을 임포트할 수 있는지**를 본다. `import mcp` 보다 강하고 기동이 실제로 하는 일과 같으며, mcp·리포 의존성·PYTHONPATH 를 한 번에 덮는다. npx/uv 서버는 대상이 아니다(패키지 소유자의 문제).
- [ ] 보고된 호스트에서 실제로 종목명이 강등됐는지, 아니면 로그를 보고 추정한 것인지 **확인되지 않았다**. 이 저장소에서는 두 체인 모두 정상 해석한다
- [ ] **이 호스트의 `.env` 에는 네 키가 하나도 없다**(Phase 2 구현 중 확인). 그래서 여기서 보이는 `firecrawl`·`perplexity` FAIL 은 거짓이 아니라 진짜다. 거짓 FAIL 은 키가 `.env` 에 있는 보고자 호스트에서 일어난다 — 프로브 키를 넣어 재현·수정 확인함

---

## Users & Context

**Primary User**
- **Who**: PRISM 을 새 호스트에 배포하는 운영자. 이번 보고를 올린 사람 그 자체
- **Current behavior**: `.env.example` 을 복사해 채우고 기동한다. 리포트 섹션이 `"Analysis failed: ..."` 로 나오거나 지표가 빈다. `mcp_doctor` 를 돌리면 실제와 다른 것을 가리킨다
- **Trigger**: 새 머신 배포, 또는 설정 이전이 포함된 패치 반영
- **Success state**: `.env.example` 만 보고 설정하면 되고, 뭔가 잘못됐을 때 진단이 맞는 곳을 가리킨다

**Job to Be Done**
When **패치를 반영하고 새 호스트에서 처음 돌릴 때**, I want to **무엇을 설정해야 하는지 알고 잘못됐을 때 어디가 잘못됐는지 알기를**, so I can **리포트가 조용히 비는 것을 배포 몇 시간 뒤가 아니라 기동 시점에 안다**.

**Non-Users**
- 이미 돌고 있는 호스트의 운영자 — 설정이 이미 맞춰져 있다. 다만 로그 소음은 이들에게도 해당된다

---

## Solution Detail

### Core Capabilities (MoSCoW)

| Priority | Capability | Rationale |
|----------|------------|-----------|
| Must | `.env.example` 에 새 변수 5종 + **왜 필요한지** | 보고의 1 번. 지금은 알 방법이 없다 |
| Must | `mcp_doctor` 가 `.env` 를 로드 | 진단이 런타임과 다른 것을 보면 진단이 아니다 |
| Must | 인터프리터가 서버를 띄울 수 있는지 검증 | `PRISM_MCP_PYTHON` 미설정의 조용한 죽음은 지금 아무도 못 잡는다 |
| Must | 체인 FALLBACK 로그 레벨 정정 | 성공을 실패처럼 보고해 오독을 낳았다 |
| Should | `mcp_agent.config.yaml` 의 치환 미지원을 문서화 | 같은 함정을 다음 사람이 밟는다 |
| Could | 사이클당 1회 소스 상태 요약 | Open Question 의 답이 될 수 있음 |
| Won't | 프레임워크 치환 지원 | 범위 밖 |
| Won't | `PRISM_MCP_PYTHON` 자동 추정 | 오케스트레이터≠서버 인터프리터 구성을 막는다 |

### MVP Scope

`.env.example` 만 보고 설정한 새 호스트에서 **`mcp_doctor` 가 런타임과 같은 결과를 내고, `mcp` 없는 인터프리터를 FAIL 로 잡으면** 가설이 검증된다.

### User Flow

```
[현재]
패치 반영 → 기동 → 리포트 섹션이 빔
  → mcp_doctor 실행 → 정상 서버를 FAIL, 죽은 서버를 OK 로 보고
  → 원인 추적이 길어짐. PRISM_MCP_PYTHON 은 yaml 주석에만 있다

[수정 후]
패치 반영 → .env.example 이 새 변수를 이유와 함께 안내
  → mcp_doctor 가 런타임과 같은 환경을 보고, 인터프리터까지 검증
  → 잘못된 것이 잘못된 것으로 나온다
```

---

## Technical Approach

**Feasibility**: **HIGH** — 네 항목 모두 원인이 특정됐고 국소적이다

**Architecture Notes**

- **`mcp_doctor` 는 `load_dotenv` 가 없다**(측정). `config_loader` 도 없다 — `.env` 로드는 각 진입점(`cores/analysis.py`, `cores/market_data/mcp_server.py` 등)이 한다. 진단 도구는 진입점이 아니므로 아무도 안 해준다
- **`mcp_doctor` 는 `command_found` 만 본다**(`tools/mcp_doctor.py:213-219`). `python3` 는 PATH 에 있으므로 OK 로 나오고, 그 `python3` 가 `mcp` 를 못 가져도 알 수 없다. 이 호스트에서 `/usr/bin/python3` = 3.9.6, `import mcp` 실패
- **체인은 성공한 폴백도 WARNING 으로 찍는다**(`cores/market_data/source.py:123,129,138`). 메시지 자체(`fdr answered ticker_name after krx`)는 정확하지만 레벨이 사실과 다르다
- **`mcp_agent.config.yaml` 은 치환이 안 된다**(측정: `${HOME:-fallback}` 이 리터럴로 반환). 그래서 그 파일의 `kospi_kosdaq` 은 리터럴 `python3` 이고 **`PRISM_MCP_PYTHON` 같은 탈출구가 없다** — 이 호스트에서 분석 에이전트 경로가 같은 위험에 노출돼 있다

**Technical Risks**

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| FALLBACK 을 INFO 로 내리면 상시 실패가 안 보인다 | **H** | Open Question. 사이클당 1회 요약을 검토. 완전한 침묵은 금지 |
| 인터프리터 검증이 서브프로세스를 띄워 진단이 느려진다 | M | 서버당 1회, 짧은 타임아웃. `--all` 에서만 하는 선택지도 있음 |
| `mcp_agent.config.yaml` 경로는 고칠 수단이 없다 | **H** | 문서화가 전부다. 프레임워크가 치환을 지원하지 않는다 |
| `.env.example` 에 실제 키를 적는 실수 | M | 플레이스홀더만. 이번 세션에서 자격증명 노출 사고가 이미 있었다 |

---

## Implementation Phases

| # | Phase | Description | Status | Parallel | Depends | PRP Plan |
|---|-------|-------------|--------|----------|---------|----------|
| 1 | 설정 안내 | `.env.example` + 문서에 새 변수 **6종**과 이유 | complete | with 2 | - | [plan](../plans/completed/mcp-env-setup-guidance.plan.md) |
| 2 | 진단이 런타임과 같은 것을 보게 | `mcp_doctor` 가 `.env` 로드 | complete | with 1 | - | [plan](../plans/completed/mcp-doctor-loads-dotenv.plan.md) |
| 3 | 인터프리터 검증 | 명령 존재가 아니라 서버 기동 가능 여부 | complete | - | 2 | - |
| 4 | 복구된 실패의 로그 레벨 | 체인 FALLBACK, 그리고 상시 실패를 알아채는 법 | pending | - | - | - |

### Phase Details

**Phase 1: 설정 안내**
- **Goal**: 새 호스트 운영자가 `.env.example` 만 보고 설정할 수 있게
- **Scope**: `PRISM_MCP_PYTHON`·`PRISM_REPO_ROOT`·`FIRECRAWL_API_KEY`·`PERPLEXITY_API_KEY`·`PRISM_REPORT_DATA_SOURCES` 를 **왜 필요한지와 함께**. `PRISM_MCP_PYTHON` 은 미설정 시 무슨 일이 일어나는지 명시. `docs/SETUP_ko.md` 갱신
- **Success signal**: 5/5 가 `.env.example` 에 있고, 각각에 한 줄 설명이 붙는다

**Phase 2: 진단이 런타임과 같은 것을 보게**
- **Goal**: `mcp_doctor` 결과가 런타임 해석과 일치
- **Scope**: `tools/mcp_doctor.py` 가 `.env` 를 로드한다. 어디서 읽었는지 출력에 남긴다(호스트 간 비교가 이 도구의 목적이므로)
- **Success signal**: 셸에서 그냥 실행해도 `set -a; source .env` 한 것과 같은 결과

**Phase 3: 인터프리터 검증**
- **Goal**: 조용히 죽는 서버를 진단이 잡는다
- **Scope**: `command_found` 를 넘어, 지정된 인터프리터가 서버를 띄울 수 있는지 확인. 최소한 `mcp`, 리포 코드를 임포트하는 서버는 그것까지
- **Success signal**: `PRISM_MCP_PYTHON=/usr/bin/python3` 로 두면 `kospi_kosdaq` 이 FAIL 로 나온다

**Phase 4: 복구된 실패의 로그 레벨**
- **Goal**: 성공한 호출이 경고를 내지 않게, 그러나 상시 실패는 여전히 보이게
- **Scope**: `cores/market_data/source.py` 의 FALLBACK 로그. **Open Question 을 먼저 답한 뒤 구현한다** — 단순히 INFO 로 내리면 Phase 4(이전 PRD)가 고친 침묵으로 되돌아간다
- **Success signal**: 기본 체인 종목명 조회에서 WARNING 0건, 그러면서 KRX 가 상시 실패 중임을 운영자가 알 수 있는 수단이 남는다

### Parallelism Notes

- **1 ∥ 2**: 문서와 도구는 독립적이다
- **3 은 2 이후**: 진단이 올바른 환경을 본 뒤에 검증을 얹어야 한다. 순서가 바뀌면 `.env` 를 못 읽는 상태에서 인터프리터를 검증하게 된다
- **4 는 독립**: 체인 로깅은 진단 도구와 무관하다. 다만 Open Question 이 해소돼야 시작할 수 있다

---

## Decisions Log

| Decision | Choice | Alternatives | Rationale |
|----------|--------|--------------|-----------|
| `PRISM_MCP_PYTHON` | **명시 요구 + 안내 개선** | `sys.executable` 자동 추정 | 자동 추정은 오케스트레이터와 서버가 다른 인터프리터여야 하는 구성을 막는다 |
| `mcp_doctor` 의 `.env` | **도구가 로드** | 사용자에게 `source .env` 안내 | 안내로 해결하면 안내를 안 읽은 사람에게 여전히 거짓말을 한다 |
| 인터프리터 검증 범위 | **Phase 3 에서 결정** | 지금 확정 | 자체 서버는 리포 코드를 임포트한다. `import mcp` 만으로 충분한지 미검증 |
| 체인 FALLBACK 레벨 | **Open Question 선행** | 즉시 INFO 로 하향 | 침묵은 직전 PRD 가 고친 문제다. 되돌리지 않는다 |

---

## Research Summary

**Technical Context**

- `tools/mcp_doctor.py` 에 `load_dotenv` 없음. `config_loader` 에도 없음 — `.env` 로드는 진입점의 일이고 진단 도구는 진입점이 아니다
- `mcp_doctor` 의 문제 판정은 `command_found`(존재) / `MISSING_PATH` / `UNSET_ENV` / `ABSOLUTE_PATH` 네 가지. **실행 가능성은 검사하지 않는다**
- 이 호스트 `/usr/bin/python3` = Python 3.9.6, `import mcp` → `ModuleNotFoundError`. 보고된 맥미니와 같은 조건
- `.env.example` 에 새 변수 5종 전부 없음
- `cores/market_data/source.py:123,129,138` 이 폴백 성공을 WARNING 으로 기록. 기본 체인에서 종목명은 정상 반환(`'삼성전자'`)되므로 **강등은 일어나지 않는다** — 보고된 증상은 이 로그의 오독
- `mcp_agent.config.yaml` 은 `${VAR:-default}` 를 확장하지 않는다(측정). 그 경로에는 인터프리터 지정 수단이 없다

---

*Generated: 2026-08-18*
*Status: DRAFT — 네 항목 전부 재현·측정됨, 미해결 질문 3건*

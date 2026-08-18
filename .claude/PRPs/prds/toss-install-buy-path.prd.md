# 토스 전용 설치에서 배치가 아무것도 못 하는 세 가지

> **출처**: 다른 호스트의 전체 배치 실행 보고 (2026-08-18). 세 항목 전부 이 저장소에서 재현·측정했다.
>
> **전제**: 세 가지 모두 직전 두 PR(#6 `fix/toss-only-startup`, #7 `docs/mcp-config-migration-prd`)이 남긴 것이다. 새로 발견된 별개 결함이 아니라 그 작업의 미완이다.

---

## Problem Statement

토스 전용 설치에서 배치가 **산출물 0건**으로 끝난다. MCP 서버가 즉사해 분석 에이전트가 오류 130건을 내고, 매수 후보 3종목은 전부 현재가 조회 실패로 탈락해 **매수 0건**이다. 그리고 이 상태를 진단하라고 만든 도구가 레거시 설정 파일을 "더 이상 쓰이지 않으니 삭제하라"고 **틀리게** 안내한다 — 그 말을 따르면 분석 에이전트가 전부 깨진다.

## Evidence

**보고 (다른 호스트, 전체 배치)**

> `command: "python3"` … 이 호스트에서 `/usr/bin/python3` 3.9.6 으로 잡혀 mcp 임포트조차 못 하고 MCP 서버가 즉사했습니다 (**오류 130건, 산출물 0건**). mcp-agent 가 이를 `Connection closed` 로만 보고해 원인이 감춰져 있었습니다.
>
> `mcp_doctor` 는 `mcp_agent.config.yaml` 을 "no longer used" 라고 안내하지만, **실제 배치 로그에는 25회 참조**됩니다.
>
> **Purchased: 0 items.** 매수 후보 3종목 전부 `current price query failed` 로 탈락했습니다.

**이 저장소에서 재현 (2026-08-18)**

| 주장 | 확인 |
|---|---|
| `mcp_agent.config.yaml` 이 리터럴 `python3` | ✅ `command='python3'`, `args=['-m','cores.market_data.mcp_server']` |
| 이 호스트의 `python3` 가 `mcp` 를 못 가짐 | ✅ `/usr/bin/python3` = 3.9.6, `import mcp` → `ModuleNotFoundError` |
| `mcp_doctor` 가 삭제를 권함 | ✅ `tools/mcp_doctor.py:411` — "still present but no longer used; delete it" |
| 레거시를 실제로 읽는 코드가 있음 | ✅ `MCPApp(...)` — `stock_analysis_orchestrator.py`, `prism-us/*`, `events/*` |
| `helpers.py` 가 체인을 안 탐 | ✅ 세 소스 전부 `cores.market_data` 밖 |

**현재가 조회 체인의 실제 구성** (`tracking/helpers.py`)

| 순위 | 출처 | 코드 | 토스 설치에서 |
|---|---|---|---|
| 1 | KRX 직접 | `krx_data_client.get_market_ohlcv_by_ticker` (63행) | 로그인 필요 — 의도적으로 비활성 |
| 2 | KIS 하드코딩 | `trading.domestic_stock_trading.AsyncTradingContext` (116행) | placeholder 자격증명 → 403 |
| 3 | DB 최종가 | `_get_last_price_from_db` | 신규 후보는 `stock_holdings` 행이 없어 **0** |

`_get_price_from_kis()` 의 도크스트링이 전제를 밝히고 있다 — *"KIS credentials are already configured wherever the tracking agents run"*. 토스 전용 설치에서 그 전제가 성립하지 않는다.

**고칠 재료는 이미 있다 (실측)**

```
broker=toss mode=real
  [factory] 005930 -> 271250      # 브로커 팩토리 경유 실시간가
  [factory] 000660 -> 1680000
  [factory] 042660 -> 91800
  [chain]   042660 last_close=91700  # 소스 체인 경유 최근 종가
```

## 왜 이것이 직전 작업의 미완인가

세 가지 모두 이미 다룬 문제의 **남은 조각**이다. 새로운 종류가 아니다.

**①** PR #6 Phase 2 에서 `mcp_agent.config.yaml` 의 `kospi_kosdaq` 을 자체 서버로 바꾸면서 `command` 를 리터럴 `"python3"` 로 뒀다. mcp-agent 가 `${VAR:-기본값}` 을 확장하지 않는다는 것을 측정으로 확인했고, 그래서 "설정 가능하게 만들 수 없다" 는 주석까지 달았다. **주석을 다는 것과 문제를 없애는 것은 다르다.**

**②** PR #7 Phase 2 에서 `mcp_doctor` 가 `.env` 를 읽게 했지만, 그 도구가 출력하는 "legacy is no longer used" 문구는 손대지 않았다. 나 자신이 그 문구를 사용자에게 그대로 옮겨 말하기도 했다. 리포트 경로만 이전됐다는 사실은 PR #7 에서 이미 밝혀냈는데, 그 발견이 도구의 문구에 반영되지 않았다.

**③** PR #6 이 `stock_tracking_agent.py` 와 `prism-us/us_stock_tracking_agent.py` 의 브로커 하드코딩을 고쳤다. `tracking/helpers.py` 는 같은 종류의 하드코딩인데 **범위에 들어가지 않았다.** PR #6 의 tripwire 는 *모듈 스코프 임포트*를 막지 `PRISM_BROKER` 무시를 막지 않는다.

## Proposed Solution

세 가지를 각각 끊되, 공통 원칙은 **"고칠 수 없으면 최소한 보이게 한다"** 이다.

1. **인터프리터**: mcp-agent 가 치환을 안 하므로 파일에 리터럴 절대경로를 적어야 한다. 그 값이 호스트마다 다르므로 `.example` 은 플레이스홀더로 두고, **설정하지 않으면 진단이 잡도록** 한다
2. **거짓 안내 제거**: `mcp_doctor` 가 레거시 삭제를 권하지 않는다. 두 파일이 각각 무엇을 담당하는지 말한다
3. **현재가 체인**: `helpers.py` 가 브로커·소스 체인을 타게 한다. KIS 하드코딩을 팩토리로 바꾸고, KRX 실패 시 체인을 경유한다

## Key Hypothesis

We believe **인터프리터 경로를 명시 가능하게 만들고 현재가 조회를 브로커 인식으로 바꾸는 것**이 **토스 전용 설치에서 배치가 산출물 0건으로 끝나는 문제**를 **KIS·KRX 자격증명이 없는 운영자**에게 해결해 줄 것이다.

We'll know we're right when **`PRISM_BROKER=toss` 에 KIS·KRX 자격증명이 없는 상태에서 매수 후보의 현재가가 조회되고, MCP 서버가 기동하며, 진단이 삭제하면 안 되는 파일을 삭제하라고 하지 않을 때**.

## What We're NOT Building

- **mcp-agent 프레임워크의 치환 지원** — 상류 수정이다. 측정으로 미지원을 확인했고 우회한다
- **`mcp_agent.config.yaml` 삭제 또는 통합** — 분석 에이전트가 실제로 읽는다(측정). 통합은 별건이며 이 PRD 는 **두 파일이 공존한다는 사실을 정직하게 다루는 것**이 목표다
- **KIS 경로 제거** — KIS 사용자에게는 그대로 동작해야 한다. 팩토리 경유로 바꿀 뿐이다
- **`get_market_ohlcv_by_ticker` 를 체인 capability 로 승격** — 전 종목 스냅샷은 체인에 없는 형태다. 폴백만 고친다
- **`stock_holdings` 없는 종목의 DB 폴백 개선** — 신규 후보에 과거 가격이 없는 것은 정상이다. 그 앞 단계를 고친다

## Success Metrics

| Metric | Target | How Measured |
|--------|--------|--------------|
| 현재가 조회 | KIS·KRX 자격증명 없이 후보 3종목 **3/3 성공** | `helpers` 현재가 함수 직접 호출 |
| MCP 서버 기동 | `mcp_agent.config.yaml` 경유 서버가 **기동 유지** | 서브프로세스 기동 확인 |
| 진단 정확성 | 레거시 삭제 권고 **0건**, 두 파일의 역할이 출력에 | `mcp_doctor` 출력 |
| 잘못된 인터프리터 검출 | `mcp` 없는 인터프리터 지정 시 **FAIL** | `mcp_doctor` |
| 회귀 | KIS 99/99, 전체 스위트 baseline 동일 | pytest |

## Open Questions

- [ ] **`helpers.py` 의 1순위(KRX 전 종목 스냅샷)를 어떻게 할 것인가?** 폴백만 고치면 KRX 가 살아 있는 설치는 지금과 같고 토스 설치는 폴백으로 돈다. 1순위 자체를 체인으로 바꾸면 KIS 사용자의 동작이 바뀐다 — 범위 판단 필요
- [ ] **오류 130건이 전부 이 원인인가?** 보고는 `Connection closed` 만 남았다고 했다. 인터프리터를 고친 뒤 재실행해야 나머지가 있는지 알 수 있다
- [ ] `mcp_agent.config.yaml` 은 gitignore 대상이라 **커밋으로 고칠 수 없다.** `.example` 과 문서, 그리고 진단으로만 유도할 수 있는데 그것으로 충분한가

---

## Users & Context

**Primary User**
- **Who**: KIS·KRX 자격증명이 없는 상태로 PRISM 을 돌리는 운영자. 직전 두 PR 이 약속한 "자격증명 없이 동작" 을 믿고 배포한 사람
- **Current behavior**: 패치를 반영하고 배치를 돌린다. 오류 130건이 나고 산출물이 0건이며, 매수도 0건이다. 로그의 `Connection closed` 는 원인을 말하지 않는다
- **Trigger**: 아침 배치. 또는 새 호스트 배포 직후 첫 실행
- **Success state**: 자격증명 없이 후보 분석과 매수 판단이 실제로 이뤄진다

**Job to Be Done**
When **KIS·KRX 계정 없이 아침 배치를 돌릴 때**, I want to **현재가가 조회되고 MCP 서버가 뜨기를**, so I can **"자격증명 없이 동작한다" 는 약속이 리포트 생성뿐 아니라 매매까지 이어진다**.

**Non-Users**
- KIS 자격증명이 정상인 운영자 — 셋 다 그들에게는 지금도 동작한다. 변경으로 잃는 것이 없어야 한다

---

## Solution Detail

### Core Capabilities (MoSCoW)

| Priority | Capability | Rationale |
|----------|------------|-----------|
| Must | `helpers.py` 현재가 폴백을 브로커 팩토리로 | **매수 0건의 직접 원인** |
| Must | KRX 실패 시 소스 체인 경유 추가 | KIS 도 없는 설치에는 팩토리만으로 부족할 수 있다 |
| Must | `mcp_doctor` 의 레거시 삭제 권고 제거 | 따르면 에이전트가 깨지는 안내다 |
| Must | `mcp_agent.config.yaml` 의 인터프리터를 진단이 검사 | 이미 Phase 3 에서 만든 검사를 이 파일에도 |
| Should | `.example` 과 문서에 절대경로 안내 | 치환이 안 되므로 사람이 적어야 한다 |
| Could | `Connection closed` 뒤의 원인을 로그에 | mcp-agent 가 감추는 것을 우리가 보완 |
| Won't | 프레임워크 치환 지원 | 상류 |
| Won't | 두 설정 파일 통합 | 별건 |

### MVP Scope

**KIS·KRX 자격증명이 없는 상태에서 매수 후보 3종목의 현재가가 조회되면** 가설의 핵심이 검증된다. 매수 0건의 직접 원인이 그것이다.

### User Flow

```
[현재]
배치 시작
  → MCP 서버 기동 실패 (python3 에 mcp 없음) → Connection closed → 오류 130건
  → 매수 후보 3종목 → 현재가 조회 (KRX✗ KIS✗ DB=0) → 전부 탈락
  → Purchased: 0 items, 산출물 0건

[수정 후]
배치 시작
  → MCP 서버 기동 (진단이 잘못된 인터프리터를 미리 잡음)
  → 현재가 조회: KRX 실패 → 브로커 팩토리(토스) → 91,800원
  → 매수 판단이 실제로 이뤄짐
```

---

## Technical Approach

**Feasibility**: **HIGH** — 고칠 재료가 이미 있고 실측으로 확인됐다

**Architecture Notes**

- **브로커 팩토리가 현재가를 준다**(측정): `domestic_trader().get_current_price(ticker)` 가 토스에서 005930→271,250, 042660→91,800 반환. `trading/brokers/factory.py` 는 `PRISM_BROKER` 를 보고 KIS·토스를 고른다 — `helpers.py:116` 의 하드코딩을 이것으로 바꾸면 된다
- **소스 체인도 답한다**(측정): `get_market_ohlcv_by_date` 로 042660 최근 종가 91,700. 실시간가는 아니지만 KRX·KIS·토스가 모두 막힌 경우의 마지막 방어선이 된다
- **`mcp_agent.config.yaml` 은 gitignore 대상**이다. 커밋으로 고칠 수 없고 `.example` 과 문서·진단으로만 유도할 수 있다. Phase 3 에서 만든 인터프리터 검사를 이 파일에도 적용하는 것이 실효적인 수단이다
- **`mcp_doctor` 의 안내가 틀렸다**(`tools/mcp_doctor.py:411`). 리포트 경로만 native 레지스트리로 이전됐고 `MCPApp` 을 쓰는 분석 에이전트는 레거시를 읽는다

**Technical Risks**

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| 폴백만 고치면 KRX 살아있는 설치는 검증 안 됨 | **H** | Open Question. KIS 회귀 99/99 로 최소한 기존 동작 보존은 확인 |
| 토스 실시간가와 KRX 종가의 의미가 다름 | **M** | 폴백 순서를 명시하고 로그에 어느 소스인지 남긴다 (기존 KIS 폴백도 그렇게 한다) |
| `mcp_agent.config.yaml` 을 고칠 수단이 문서뿐 | **H** | 진단이 잡게 한다. Phase 3 검사를 이 파일에 확장 |
| 오류 130건의 나머지 원인이 있음 | **M** | Open Question. 인터프리터 수정 후 재실행 필요 |
| 이 저장소에서 매수 경로 전체를 재현할 수 없음 | **H** | 현재가 조회 함수 단위로 검증. 배치 전체는 보고 호스트에서 확인 |

---

## Implementation Phases

| # | Phase | Description | Status | Parallel | Depends | PRP Plan |
|---|-------|-------------|--------|----------|---------|----------|
| 1 | 현재가 조회를 브로커 인식으로 | `helpers.py` 폴백을 팩토리 + 체인으로 | complete | with 2 | - | [plan](../plans/completed/broker-aware-price-lookup.plan.md) |
| 2 | 거짓 안내 제거 | `mcp_doctor` 가 레거시 삭제를 권하지 않음 | in-progress | with 1 | - | [plan](../plans/mcp-doctor-legacy-note.plan.md) |
| 3 | 레거시 설정도 진단 대상으로 | 인터프리터 검사를 `mcp_agent.config.yaml` 에 | pending | - | 2 | - |
| 4 | 설정 안내 | `.example`·문서에 절대경로와 이유 | pending | - | 3 | - |

### Phase Details

**Phase 1: 현재가 조회를 브로커 인식으로**
- **Goal**: 매수 0건의 직접 원인을 없앤다
- **Scope**: `tracking/helpers.py:107-125` 의 `_get_price_from_kis()` 를 브로커 팩토리 경유로 바꾸고, 그마저 실패하면 소스 체인의 최근 종가를 쓴다. 함수명과 도크스트링도 실제 동작에 맞게 고친다(지금 이름이 KIS 를 못박고 있다)
- **Success signal**: `PRISM_BROKER=toss`, KIS·KRX 자격증명 없음 상태에서 **후보 3종목 현재가 3/3 조회**
- **Note**: 1 순위(KRX 전 종목 스냅샷)는 건드리지 않는다 — Open Question

**Phase 2: 거짓 안내 제거**
- **Goal**: 따르면 시스템이 깨지는 안내를 없앤다
- **Scope**: `tools/mcp_doctor.py:409-413`. 삭제 권고 대신 **두 파일이 각각 무엇을 담당하는지** 말한다 — native 는 리포트 경로, legacy 는 mcp-agent 를 쓰는 분석 에이전트
- **Success signal**: 출력에 "delete" 권고가 없고, 두 경로가 구분돼 나온다

**Phase 3: 레거시 설정도 진단 대상으로**
- **Goal**: 잘못된 인터프리터를 배치 전에 잡는다
- **Scope**: PR #7 Phase 3 에서 만든 `_check_launch` 를 `mcp_agent.config.yaml` 의 서버에도 적용. `--all` 이 native 만 보는 현재 동작을 legacy 까지 확장
- **Success signal**: `command: "python3"` 이고 그 python3 에 `mcp` 가 없으면 **FAIL 로 나온다**

**Phase 4: 설정 안내**
- **Goal**: 치환이 안 되는 파일을 사람이 올바로 채우게
- **Scope**: `mcp_agent.config.yaml.example` 의 `command` 에 절대경로 예시와 이유. `docs/SETUP_ko.md` 에 두 파일의 차이(이미 PR #7 에서 넣은 절 보강)
- **Success signal**: `.example` 만 보고 올바른 값을 적을 수 있다

### Parallelism Notes

- **1 ∥ 2**: 현재가 조회와 진단 문구는 완전히 독립적이다. 1 이 매수 0건을 직접 고치므로 **1 이 우선순위가 높다**
- **3 은 2 이후**: 레거시의 역할을 바로 말한 뒤에 그것을 검사해야 앞뒤가 맞는다
- **4 는 3 이후**: 진단이 잡는 것을 문서가 안내하는 순서

---

## Decisions Log

| Decision | Choice | Alternatives | Rationale |
|----------|--------|--------------|-----------|
| 현재가 폴백 | **브로커 팩토리 + 소스 체인** | KIS 유지, 체인만 | 팩토리는 `PRISM_BROKER` 를 이미 본다. 체인은 둘 다 막힌 경우의 방어선 |
| `mcp_agent.config.yaml` | **공존을 인정하고 진단·문서로 유도** | 삭제, 통합, 프레임워크 수정 | 분석 에이전트가 실제로 읽는다(측정). 통합은 별건 |
| 1 순위 KRX 스냅샷 | **이번엔 건드리지 않음** | 체인으로 교체 | KIS 사용자 동작을 바꾼다. Open Question 으로 남김 |
| 진단 확장 범위 | **레거시까지** | native 만 | 오류 130건이 레거시 경로에서 났다 |

---

## Research Summary

**Technical Context**

- `tracking/helpers.py` 는 `cores.market_data` 를 **전혀 쓰지 않는다**. 1순위 `krx_data_client`(63행), 2순위 `trading.domestic_stock_trading`(116행), 3순위 DB. `data_prefetch` 가 MCP 를 우회하던 것과 같은 형태의 누락이다
- `_get_price_from_kis()` 도크스트링이 *"KIS credentials are already configured wherever the tracking agents run"* 을 전제로 명시한다. 토스 전용 설치에서 깨지는 전제다
- 브로커 팩토리 실측: 토스에서 005930→271,250 / 000660→1,680,000 / 042660→91,800
- 소스 체인 실측: 042660 최근 종가 91,700
- `mcp_agent.config.yaml` 의 `kospi_kosdaq` 은 `command='python3'` 리터럴. mcp-agent 가 `${VAR:-기본값}` 을 확장하지 않는 것은 PR #6 에서 측정 확인됨
- `tools/mcp_doctor.py:411` 이 레거시 삭제를 권한다. `MCPApp` 을 쓰는 `stock_analysis_orchestrator.py`·`prism-us/*`·`events/*` 가 그 파일을 읽는다

---

*Generated: 2026-08-18*
*Status: DRAFT — 세 항목 전부 재현·측정됨, 미해결 질문 3건*

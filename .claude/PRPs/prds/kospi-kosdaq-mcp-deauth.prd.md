# kospi_kosdaq MCP 를 자격증명 없이 돌리기

> **출발 질문**: "`kospi_kosdaq_stock_server` 가 여전히 id/pw 방식인데, openapi auth key 로 변경 불가한가?"
>
> **답**: KRX OpenAPI 인증키로는 **완전 대체가 안 된다**(투자자별 거래실적 미제공). 그리고 **그럴 필요도 없다** — 자격증명이 아예 필요 없는 대체본이 이미 이 저장소에 있고, 배선만 안 돼 있다.

---

## Problem Statement

리포트 에이전트와 매수/매도 스페셜리스트가 시세·수급을 받는 `kospi_kosdaq` MCP 서버가 **지금 이 순간 완전히 죽어 있다.** KRX 가 로그인을 필수화하면서 id/pw 세션 방식이 깨졌고, 패키지의 pykrx 폴백은 같은 이유로 비활성화되어 있다. 7 개 도구 전부가 오류를 반환한다.

증상이 조용하다는 것이 문제의 핵심이다. 배치는 끝까지 돌고, 리포트는 생성되고, 매매 판단도 내려진다 — **근거 데이터만 빠진 채로.**

## Evidence

**측정 (2026-08-18, 이 저장소·이 머신)**

```
$ python -c "import kospi_kosdaq_stock_server as k; print(k._use_pykrx_fallback); print(k._get_krx_client())"
  pykrx 폴백: False          # 소스 주석: "pykrx 폴백 비활성화 (KRX에서 로그인 필수화됨)"
  KRX client: None (초기화 실패)
```

매매 루프 실행 중 실제로 찍힌 로그:

```
ERROR krx_data_client: KRX 직접 로그인 실패: 데이터 조회 페이지에서 로그인 페이지로 리다이렉트됨.
ERROR kospi_kosdaq_stock_server: KRX Data Client 초기화 실패: 로그인 실패 (최대 재시도 횟수 초과)
WARNING cores.data_prefetch: Sector map not available from get_sector_info
```

**영향 범위 — 분석용이 아니라 매매 판단용이다**

| 도구 | 소비처 | 파일 |
|---|---|---|
| `get_stock_trading_volume` | Trading Flow Analyst (#2) | `cores/agents/stock_price_agents.py:186,243` |
| `get_stock_trading_volume` | **Buy Specialist (#12)** — "외국인/기관 순매수 중인가?" | `cores/agents/trading_agents.py:734,948` |
| `get_stock_trading_volume` | **Sell Specialist (#13)** — "기관/외국인 매매 동향 확인" | `cores/agents/trading_agents.py:880,1097` |
| `get_sector_info` | 섹터맵 prefetch | `cores/data_prefetch.py:219-224` |

**전례가 있다.** `cores/market_data/mcp_server.py` 의 모듈 도크스트링이 2026-08-05 사건을 기록하고 있다 — KRX 가 IP 를 막자 리포트에서 이동평균·RSI·MACD·볼린저밴드·지지저항·투자자별 순매수가 **통째로 비었고, 차트는 정상으로 그려졌다.** 같은 실패가 지금 재발한 상태다.

## 왜 KRX OpenAPI 가 답이 아닌가

`openapi.krx.co.kr` 는 실재하고 `AUTH_KEY` 헤더 방식이다. 그러나 서비스 목록에 **투자자별 거래실적이 없다.**

| KRX OpenAPI 서비스 | 대응 도구 |
|---|---|
| 지수 일별시세 | `get_index_ohlcv` ✅ |
| 유가증권/코스닥 일별매매정보 | `get_stock_ohlcv` ✅ |
| 종목기본정보 | `load_all_tickers` ✅ |
| — | **`get_stock_trading_volume` ❌ 없음** |

투자자별 거래실적(`MDCSTAT022/023/024`)은 `data.krx.co.kr` — 즉 지금 깨진 id/pw 스크래핑 경로 — 에만 있다. 인증키로 바꿔도 **가장 중요한 도구가 그대로 남는다.**

부수적으로, 상류 패키지(`kospi-kosdaq-stock-server`)의 최신 버전은 `0.4.2` 이고 인증 방식은 `krx` / `kakao` **둘 다 id/pw** 다. 인증키 지원은 상류 수정이 선행돼야 한다.

## Proposed Solution

`mcp_agent.config.yaml` 의 `kospi_kosdaq` 를 **이미 존재하는 저장소 내 대체본**으로 바꾼다. `cores/market_data/mcp_server.py` 는 도구명·인자·반환 형태·서버명을 기존 서버와 동일하게 맞춰 만들어졌으므로 **에이전트 프롬프트는 한 글자도 바뀌지 않는다.** 데이터 출처만 KRX 스크래핑에서 소스 체인(`PRISM_MARKET_DATA_SOURCES=toss,krx,fdr`)으로 바뀐다.

그 위에 측정으로 드러난 두 구멍을 메운다: 토스 수급 호출의 페이지 크기 버그와, 체인에 아예 없는 섹터 capability.

## Key Hypothesis

We believe **자격증명이 필요 없는 소스 체인 위의 MCP 서버로 전환하는 것**이 **시세·수급 데이터가 조용히 비는 문제**를 **KRX 계정을 유지할 수 없는 운영자**에게 해결해 줄 것이다.

We'll know we're right when **`KRX_ID`/`KRX_PW` 를 지운 상태에서 6 개 도구가 전부 실데이터를 반환하고, 리포트에 이동평균·RSI·MACD·투자자별 순매수가 채워질 때**.

## What We're NOT Building

- **KRX OpenAPI 인증키 지원** — 투자자별 거래실적을 제공하지 않아 핵심 도구를 못 메운다. 인증키 신청·승인(1일)까지 필요하다. 체인의 KRX 소스를 인증키 기반으로 교체하는 것은 별건으로 남긴다
- **상류 패키지(`kospi-kosdaq-stock-server`) 수정** — 저장소 내 대체본이 이미 있어 상류를 기다릴 이유가 없다
- **`get_stock_fundamental`** — 소비처가 `cores/archive/insight_agent.py` 뿐이다(아카이브 코드). 체인에 `fundamentals` capability 는 있으므로 필요해지면 나중에 추가한다
- **`load_all_tickers`** — 모든 에이전트 프롬프트가 **"절대 사용 금지"** 로 명시하고 있다 (`stock_price_agents.py:64,125`, `market_index_agents.py:28,165`)
- **KRX 소스 제거** — 체인에 남겨 둔다. 로그인이 복구되면 자연히 다시 쓰인다

## Success Metrics

| Metric | Target | How Measured |
|--------|--------|--------------|
| 자격증명 없는 도구 동작 | `KRX_ID`/`KRX_PW` 없이 **6/6 도구 OK** | 각 도구 직접 호출 |
| 수급 데이터 복구 | `get_stock_trading_volume` 가 실행 반환 (현재 `error`) | 005930 기준 호출 |
| 섹터맵 복구 | `sector_map` 종목 수 > 0 (현재 경고 후 빈 값) | `data_prefetch` 로그 |
| 리포트 지표 | 이동평균·RSI·MACD·볼린저·투자자별 순매수 **전부 채워짐** | 리포트 1 건 생성 후 육안 |
| 회귀 | KIS 99/99, 전체 스위트 baseline 동일 | pytest |

## Open Questions

- [x] ~~토스 수급 조회 기간이 짧을 수 있다~~ — **해소됨.** "2 행" 은 측정 오류였다(응답 dict 의 키 개수 `records`/`nextUntil` 를 행 수로 읽음). `count=100` 1 페이지에 100 거래일(약 5 개월)이 오고, `_MAX_PAGES=20` 으로 약 8 년에 닿는다. 실측: 2 년 범위 639 행 (2024-01-02 ~ 2026-08-18)
- [ ] 네이버 업종분류(79 개)가 KRX 업종분류와 **분류 체계가 다르다**. DB 의 기존 `sector` 값·`MAX_SAME_SECTOR=3` 제약과 어떻게 맞출 것인가
- [ ] 네이버 스크래핑의 안정성·차단 위험. 일 1 회 캐시로 충분한가

---

## Users & Context

**Primary User**
- **Who**: KRX 계정을 유지할 수 없거나 유지하고 싶지 않은 운영자. 이 저장소를 clone 해서 돌리는 사람 전부
- **Current behavior**: `KRX_ID`/`KRX_PW` 를 `mcp_agent.config.yaml` 에 넣는다. 세션이 자주 무효화되고(다른 프로세스 로그인, IP 차단), 실패해도 배치는 정상 종료하므로 **데이터가 빠진 줄 모른다**
- **Trigger**: 매일 아침 배치. 또는 리포트를 열어보고 지표가 비어 있는 것을 발견했을 때
- **Success state**: 자격증명 없이 돌고, 실패하면 조용히 비는 대신 눈에 띈다

**Job to Be Done**
When **아침 배치가 리포트와 매매 판단을 만들 때**, I want to **증권사 계정 로그인 없이 시세와 수급을 받기를**, so I can **세션이 끊겼다는 이유로 근거 없는 매매 판단이 내려지지 않게 한다**.

**Non-Users**
- KRX 계정이 정상 동작하는 운영자 — 체인에 KRX 소스가 남아 있어 그대로 쓰인다. 이번 변경으로 잃는 것이 없다

---

## Solution Detail

### Core Capabilities (MoSCoW)

| Priority | Capability | Rationale |
|----------|------------|-----------|
| Must | `mcp_agent.config.yaml` 를 자체 서버로 전환, `KRX_ID`/`KRX_PW` 제거 | 문제의 직접 해소. 문서는 이미 이 방향을 안내 중 |
| Must | 토스 `investor-trading` 페이지 크기 수정 (`_PAGE` 200 → 100) | 전환해도 수급이 그대로 실패한다. 매매 판단이 쓰는 데이터 |
| Must | `get_sector_info` 를 자체 서버에 구현 | 사용자 확정. 현재 이미 실패 중인 기능의 실복구 |
| Must | 자격증명 없는 상태의 도구 전수 검증 | 표본이 아니라 전수. 이번 조사에서 표본이 놓친 것이 나왔다 |
| Should | 도구 실패 시 조용히 비지 않도록 | 이 문제가 오래 안 보였던 이유 그 자체 |
| Could | `get_stock_fundamental` 추가 | 체인에 capability 는 있음. 소비처가 아카이브뿐이라 급하지 않음 |
| Won't | KRX OpenAPI 인증키 | 핵심 도구 미제공 |
| Won't | `load_all_tickers` | 프롬프트가 사용 금지 |

### MVP Scope

`KRX_ID`/`KRX_PW` 없이 **`get_stock_ohlcv`·`get_stock_market_cap`·`get_stock_trading_volume`·`get_index_ohlcv`·`get_ticker_name` 5 개가 실데이터를 반환**하면 가설이 검증된다. 섹터는 그다음이다.

### User Flow

```
[현재]
mcp_agent.config.yaml (KRX_ID/KRX_PW)
  → python -m kospi_kosdaq_stock_server
  → KRX 로그인 실패 → 폴백 없음 → 7개 도구 전부 error
  → 배치는 정상 종료, 리포트는 생성됨, 지표만 빔

[수정 후]
mcp_agent.config.yaml (env 없음)
  → python -m cores.market_data.mcp_server
  → 소스 체인 (toss → krx → fdr)
  → 자격증명 불필요
```

---

## Technical Approach

**Feasibility**: **HIGH** — 대체본이 이미 있고, 남은 것은 측정으로 원인이 특정된 버그 하나와 신규 capability 하나

**Architecture Notes**

- **대체본은 이미 계약이 맞춰져 있다.** 도구명·인자·반환·**서버명(`kospi_kosdaq`)** 이 동일하다. `report_generator` 프롬프트 무수정
- **문서가 이미 이 방향을 안내한다** — `README_DOCKER.md:200`, `README_DOCKER_ko.md:198`, `docs/SETUP_ko.md:180`, `cores/llm/mcp_servers.yaml:35`. **config 만 옛 경로에 머물러 있다**
- **수급 400 의 원인이 특정됐다.** `cores/market_data/toss_source.py:41` 의 `_PAGE = 200` 이 `investor-trading` 의 `count` 상한 100 을 초과한다. 이분 탐색으로 100 OK / 101 FAIL 확인. `_PAGE` 는 캔들(154 행)과 공유되고 캔들은 200 이 통하므로 **엔드포인트별로 분리해야 한다**
- **섹터는 체인에 없다.** 어떤 소스도 sector capability 가 없다(측정). FDR `StockListing` 컬럼에 없고, 토스 `/api/v1/stocks` 에도 없다. 네이버가 인증 없이 제공한다

**측정된 데이터 출처 후보 (섹터)**

| 출처 | 결과 | 비고 |
|---|---|---|
| FDR `StockListing("KOSPI")` | ❌ 컬럼 없음 | 942 행, 섹터 컬럼 0 |
| 토스 `/api/v1/stocks` | ❌ 없음 | `koreanMarketDetail` 은 거래정지 관련뿐 |
| pykrx `get_market_sector_classifications` | ❌ | KRX 로그인 필요 (이 머신에선 의존성 오류로 미검증) |
| **네이버 금융** | ✅ **HTTP 200, 인증 없음** | 업종 79 개, '반도체와반도체장비' 170 종목, 005930 정상 매핑 |

**Technical Risks**

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| ~~토스 수급 조회 기간이 짧다~~ | ~~H~~ **해소** | 측정 오류였다. 2 년 범위 639 행 확인 → Phase 1 계획의 Notes 참조 |
| 네이버 업종 체계가 KRX 와 달라 기존 `sector` 값과 불일치 | **H** | Phase 3 에서 DB 기존 값과 대조. `MAX_SAME_SECTOR` 판정에 영향 |
| 네이버 스크래핑 차단 | M | 일 1 회 캐시. 실패 시 기존처럼 경고 후 degrade |
| 자체 서버가 덮지 않는 도구를 에이전트가 호출 | L | `load_all_tickers` 는 프롬프트가 금지, `get_stock_fundamental` 은 아카이브 전용 (측정 완료) |
| 조용한 실패가 남는다 | **H** | Phase 4. 이 문제가 오래 안 보인 이유가 그것이다 |

---

## Implementation Phases

| # | Phase | Description | Status | Parallel | Depends | PRP Plan |
|---|-------|-------------|--------|----------|---------|----------|
| 1 | 수급 페이지 크기 수정 | `_PAGE` 를 엔드포인트별로 분리, 수급 100 | complete | with 2 | - | [plan](../plans/completed/toss-investor-flow-page-size.plan.md) |
| 2 | MCP 서버 전환 | config·프리페치를 자체 서버로, 자격증명 제거, 전수 검증 | in-progress | with 1 | - | [plan](../plans/kospi-kosdaq-mcp-switch.plan.md) |
| 3 | 섹터 capability 신설 | 네이버 소스 + 체인 capability + `get_sector_info` | pending | - | 2 | - |
| 4 | 조용한 실패 제거 | 도구 실패가 눈에 띄게, 회귀 고정 | pending | - | 1, 2, 3 | - |

### Phase Details

**Phase 1: 수급 페이지 크기 수정**
- **Goal**: 전환해도 수급이 실패하는 상태를 먼저 없앤다
- **Scope**: `cores/market_data/toss_source.py:41`. `_PAGE` 는 캔들과 공유되므로 **분리**한다 (캔들 200 유지, 수급 100). 조회 기간 커버리지도 함께 측정
- **Success signal**: `get_stock_trading_volume("20260811","20260818","005930")` 가 `error` 대신 실데이터를 반환
- **Note**: Phase 2 와 독립. 이것만으로도 현재 체인이 개선된다

**Phase 2: MCP 서버 전환**
- **Goal**: 자격증명 의존을 끊는다
- **Scope**: `mcp_agent.config.yaml` 의 `kospi_kosdaq` 를 `python -m cores.market_data.mcp_server` 로, `env` 블록 제거. `KRX_ID`/`KRX_PW` 를 지운 상태에서 **6 개 도구 전수** 호출 검증
- **Success signal**: 자격증명 없이 6/6 OK. 에이전트 프롬프트 변경 0

**Phase 3: 섹터 capability 신설**
- **Goal**: `data_prefetch` 의 `sector_map` 을 실제로 채운다 (현재도 실패 중)
- **Scope**: `MarketDataSource` 에 sector capability 추가, `naver_source.py` 에 구현(일 1 회 캐시), `mcp_server.py` 에 `get_sector_info` 노출. 반환 계약은 기존과 동일한 `{ticker: sector_name}`
- **Success signal**: `sector_map` 종목 수 > 0, 그리고 **DB 의 기존 `sector` 값과의 차이를 문서화**
- **Risk gate**: 업종 체계 불일치가 `MAX_SAME_SECTOR` 판정을 바꿀 수 있다. 구현 전에 대조부터 한다

**Phase 4: 조용한 실패 제거**
- **Goal**: 다음번엔 데이터가 빈 것을 즉시 알 수 있게
- **Scope**: 도구가 `error` 를 반환할 때의 가시성, 자격증명 없는 기동 회귀 테스트
- **Success signal**: 전 도구 실패 상태를 만들었을 때 배치가 조용히 성공하지 않는다

### Parallelism Notes

- **Phase 1 ∥ 2**: 페이지 크기 버그는 체인 내부, config 전환은 배선. 서로 독립적이다. 다만 **둘 다 끝나야** "자격증명 없이 6/6" 이 성립한다 — 2 만 하면 수급이 여전히 실패하고, 1 만 하면 여전히 자격증명을 요구한다
- **Phase 3 은 2 이후**: 자체 서버가 배선된 뒤에 그 위에 도구를 얹어야 의미가 있다
- **Phase 4 는 마지막**: 고친 뒤에 고정한다

---

## Decisions Log

| Decision | Choice | Alternatives | Rationale |
|----------|--------|--------------|-----------|
| 인증 방식 | **자격증명 자체를 없앤다** | KRX OpenAPI 인증키, id/pw 유지 | 사용자 확정. 인증키는 투자자별 거래실적을 제공하지 않아 핵심 도구를 못 메운다 |
| 구현 위치 | **저장소 내 대체본 사용** | 상류 패키지 수정, 신규 작성 | 이미 존재하고 계약이 맞춰져 있다. 문서도 이미 이 방향 |
| `get_sector_info` | **자체 서버에 구현** | 그대로 두기, 조사 후 판단 | 사용자 확정. 현재 이미 실패 중이라 복구에 해당 |
| 섹터 출처 | **네이버** (Phase 3 에서 확정) | FDR, 토스, pykrx | 셋 다 섹터 미제공(측정). 네이버만 인증 없이 전체 매핑 제공 |
| KRX 소스 | **체인에 유지** | 제거 | 로그인이 복구되면 자연히 쓰인다. 제거할 이유가 없다 |

---

## Research Summary

**Market Context**

KRX 는 2025 년경 데이터 접근을 로그인 필수로 전환했고, 별도로 `openapi.krx.co.kr` 에서 `AUTH_KEY` 기반 OPEN API 를 운영한다. 인증키는 가입 후 마이페이지에서 신청하며 승인에 약 1 일이 걸리고, **서비스별로 이용 신청을 따로 해야 한다**. 제공 범위는 일별 시세·지수·종목기본정보 중심이며 **투자자별 거래실적은 포함되지 않는다** — 그 데이터는 `data.krx.co.kr` 의 `MDCSTAT022/023/024` 화면에만 있다.

**Technical Context**

- 설치본 `kospi-kosdaq-stock-server==0.4.2` (PyPI 최신). 인증 방식 `krx`/`kakao` 모두 id/pw. 인증키 경로 없음
- `_use_pykrx_fallback = False` — 소스 주석이 "KRX에서 로그인 필수화됨" 이라고 밝히고 있다. **폴백 없음**
- `cores/market_data/mcp_server.py` 가 2026-08-05 사건(KRX IP 차단 → 리포트 지표 전멸) 이후 드롭인 대체본으로 작성돼 있으나 `mcp_agent.config.yaml` 에 배선되지 않았다
- 자체 서버 실측: `get_stock_ohlcv`·`get_stock_market_cap`·`get_index_ohlcv`·`get_ticker_name` **정상**, `get_stock_trading_volume` **실패**
- 그 실패의 원인은 KRX 가 아니라 토스였다: `_PAGE = 200` 이 `investor-trading` 의 `count` 상한 100 을 초과 (100 OK / 101 FAIL, 이분 탐색)
- 체인의 어떤 소스도 sector capability 를 갖고 있지 않다. 네이버가 인증 없이 업종 79 개와 종목 매핑을 제공(측정)

---

*Generated: 2026-08-18*
*Status: DRAFT — 측정 근거 있음, 미해결 질문 3건*

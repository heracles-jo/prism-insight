# 토스증권 브로커 지원 (Toss Securities Broker Support)

## Problem Statement

PRISM-INSIGHT의 매매 실행 계층은 KIS(한국투자증권) API에 하드코딩되어 있다. `trading/domestic_stock_trading.py`(2,300+ LOC)와 `prism-us/trading/us_stock_trading.py`(2,250+ LOC)가 KIS TR ID·헤더·응답 포맷을 코드 전반에 직접 박아 쓰고 있어, KIS 계좌가 없거나 다른 증권사를 쓰는 사용자는 시스템의 AI 분석 파이프라인은 쓸 수 있어도 **자동매매 기능은 전혀 쓸 수 없다**. 브로커를 추가하려면 현재로서는 2,000줄짜리 모듈을 통째로 복제해야 하며, 그 비용 때문에 브로커 확장이 사실상 막혀 있다.

## Evidence

- **코드 검증됨**: `trading/domestic_stock_trading.py`에서 KIS TR ID가 `mode == "real"` 분기와 함께 최소 6곳에 하드코딩 (382, 520, 672, 906, 1064행 등). `_request(api_url, tr_id, params)` 시그니처(239행) 자체가 KIS의 TR ID 개념에 종속.
- **코드 검증됨**: 브로커 선택 개념이 코드베이스 어디에도 없음. `AsyncTradingContext`(2234행)·`MultiAccountTradingContext`(2203행) 모두 KIS 전용 생성자.
- **코드 검증됨**: `trading/config/kis_devlp.yaml.example`의 설정 스키마 전체가 KIS 전용 (`my_app`/`paper_app`/`prod`/`vps` 도메인, TR 환경 개념).
- **문서 검증됨**: 토스증권 Open API가 2026년 8월 기준 주문 생성/정정/취소·잔고·시세를 모두 공개 제공 (`https://openapi.tossinvest.com`, 33개 엔드포인트).
- **가설 — 검증 필요**: "KIS 계좌가 없어서 매매를 못 쓰는 사용자가 실제로 존재한다"는 점은 아직 사용자 데이터로 확인되지 않음. 저장소 이슈/사용자 인터뷰로 검증 필요.

## Proposed Solution

브로커 추상화 계층(`BrokerPort`)을 도입하고 기존 KIS 구현을 그 뒤로 옮긴 뒤, 토스증권 어댑터를 두 번째 구현으로 추가한다. 브로커는 설치 단위 전역 설정(`PRISM_BROKER=kis|toss`) 하나로 선택되며, 호출측 코드(`stock_tracking_agent.py`, `prism_core/execution_service.py` 등)는 **한 줄도 바뀌지 않는다**.

이 접근을 택한 이유는 코드베이스에 이미 두 개의 검증된 seam이 있기 때문이다. 첫째, `prism_core/execution_service.py:108`의 `ExecutionService`가 이미 `.domestic()`/`.us()` 팩토리와 `__getattr__` 위임(224–234행)으로 트레이더 객체를 감싸는 파사드 역할을 한다. 둘째, `cores/market_data/source.py:43`에 `MarketDataSource` Protocol과 `SourceChain` 폴백 체인이 이미 존재하며 `kis_source`·`krx_source`·`naver_source`·`fdr_source` 4개 구현이 돌아가고 있다. 즉 "같은 인터페이스 뒤에 다른 구현을 끼운다"는 패턴이 이 저장소에서 이미 두 번 검증되었다. 대안이었던 "토스용 모듈 전체 복제"는 4,500줄 중복과 영구적인 이중 유지보수 부담을 만들기 때문에 기각한다.

## Key Hypothesis

We believe **설정 한 줄로 교체 가능한 브로커 추상화 계층**이 **KIS 계좌 종속으로 자동매매를 못 쓰던 문제**를 **PRISM 운영자**에게 해결해 줄 것이다.

We'll know we're right when **`PRISM_BROKER=toss`로 바꾼 뒤 호출측 코드 수정 0줄로 KR 매수·매도·잔고조회가 성공하고, 동시에 기존 KIS 테스트 스위트가 100% 통과**할 때.

## What We're NOT Building

- **시간 기반 예약주문 (Toss 경로)** — 토스 API에 해당 개념 자체가 없음 (조건주문은 가격 트리거라 의미가 다름). 호출 시 `NotSupported` 명시적 실패. *결정 ②*
- **KR 종가주문 (Toss 경로)** — 토스 `timeInForce=CLS`는 스펙상 **US + LIMIT 조합만** 지원. `buy_closing_price`/`sell_all_closing_price`의 KR 경로는 Toss에서 미지원.
- **조건주문 (SINGLE/OCO/OTO)** — 토스 고유 기능이지만 PRISM에 대응 개념이 없어 v1 제외. 향후 손절 자동화에 활용 여지 있음.
- **브로커 혼합 운용** — 계좌별로 다른 브로커를 쓰는 구성. 설치 단위 전역 선택으로 확정. *결정, 사용자 확인됨*
- **KIS 제거 또는 리팩터링에 따른 동작 변경** — KIS는 무손실 유지가 성공 기준. 추상화 추출 시 동작 변경 금지.
- **소수점 주문 / 금액 기반 주문** — 토스 US 전용 기능(`orderAmount`)이나 PRISM 포지션 관리 모델이 정수 수량 전제라 v1 제외.

## Success Metrics

| Metric | Target | How Measured |
|--------|--------|--------------|
| 무수정 브로커 전환 | 호출측 코드 변경 **0줄** | `PRISM_BROKER` 전환 전후 `git diff`가 설정 파일 외 0 |
| KIS 회귀 무손실 | 기존 테스트 **100% 통과** | `tests/test_async_trading.py`, `test_multi_account_domestic.py`, `test_execution_service.py`, `test_sell_quantity_guard.py`, `prism-us/tests/test_phase6_trading.py` 전량 green |
| 브로커 확장성 | 신규 브로커 추가 시 **어댑터 1개 파일**만 작성 | `BrokerPort` 계약 문서 + KIS/Toss 2개 구현이 동일 계약 준수 (계약 테스트 통과) |
| Toss KR 매매 정확성 | 매수·매도·잔고 **3종 실거래 성공** | 최소 수량 실계좌 검증 (모의투자 환경 부재로 실돈 필요) |
| dry-run 안전성 | demo 모드에서 실주문 API 호출 **0건** | HTTP 레이어 목킹 후 `POST /api/v1/orders` 호출 카운트 assert |

## Open Questions

- [ ] **[BLOCKING] US 매수 범위 확정** — 아래 "US 시간대 충돌" 참조. 현재 가정으로 진행 중이며 사용자 확인 필요.
- [ ] KIS 계좌가 없어 자동매매를 못 쓰는 사용자가 실제로 존재하는가? (문제 자체가 아직 가설)
- [x] ~~토스 OAuth2 액세스 토큰의 만료 시간(TTL)이 문서에 명시되지 않음~~ → **Phase 2에서 해소**: `expires_in`이 토큰 응답에 함께 온다(공개 예시 86400초). 만료 300초 전 사전 갱신으로 구현. 더불어 **"client 당 유효 토큰 1개, 재발급 시 이전 토큰 즉시 무효화"** 규칙을 발견해 스레드락+파일락 기반 공유 캐시로 대응했다.
- [ ] 토스 계좌 개설·Open API 신청에 별도 승인 절차나 자격 요건이 있는가? 문서상 "WTS > 설정 > Open API"에서 client_id 발급만 언급.
- [ ] PRISM을 클라우드/크론에서 돌릴 때 **고정 IP 확보 방안**. 토스는 IP allowlist 미등록 시 403.
- [ ] `us_stock_holdings` / `stock_holdings` DB 테이블에 `broker` 컬럼을 추가할 것인가? 브로커 전환 시 기존 보유 종목 정합성 문제.
- [ ] **[Phase 1에서 발견]** `USStockTrading`에 `get_holding_quantity_checked`가 없다 (KR에만 존재, `domestic_stock_trading.py:1712`). 즉 **US 경로는 "잔고 0"과 "잔고 조회 실패"를 구분하지 못한다.** KR에는 이를 막는 안전장치가 있는데 US에는 없는 상태. 브로커 작업과 독립된 기존 리스크이며, US 매도 로직 점검이 필요하다.

---

## Users & Context

**Primary User**
- **Who**: PRISM-INSIGHT를 자기 계좌에 연결해 자동매매를 돌리는 개인 운영자. 파이썬 실행·YAML 설정 편집은 가능하지만 2,000줄짜리 트레이딩 모듈을 직접 포팅할 의사는 없음.
- **Current behavior**: KIS 계좌가 있으면 자동매매까지 사용. 없으면 AI 분석 리포트·텔레그램 알림만 쓰고 매매는 수동으로 따라 하거나 아예 포기.
- **Trigger**: 저장소를 처음 셋업하며 `trading/config/kis_devlp.yaml`을 채우려는 순간, 또는 주거래 증권사가 토스인 상태에서 KIS 계좌를 새로 트기 싫은 순간.
- **Success state**: `.env`에서 `PRISM_BROKER=toss` 한 줄 바꾸고 토스 client_id/secret만 넣으면 기존과 동일하게 전체 파이프라인이 동작.

**Job to Be Done**
When **주거래 증권사가 토스인데 PRISM의 자동매매를 쓰고 싶을 때**, I want to **설정만 바꿔서 매매 실행 대상을 토스로 돌리고**, so I can **KIS 계좌를 새로 개설하지 않고도 AI 분석부터 주문 집행까지 한 파이프라인으로 굴릴 수 있다**.

**Non-Users**
- **KIS로 이미 잘 쓰고 있는 기존 사용자** — 이들에게 이 작업은 순수 무변화여야 한다. 마이그레이션을 요구하지 않는다.
- **브로커 혼합 운용을 원하는 사용자** — 설치 단위 전역 선택으로 확정했으므로 명시적 비대상.
- **토스 조건주문(OCO/OTO) 같은 고급 주문을 원하는 트레이더** — PRISM의 전략 모델에 대응 개념이 없다.
- **모의투자로만 검증하고 싶은 사용자 (Toss 경로 한정)** — 토스는 모의투자 서버가 없다. dry-run 시뮬레이터는 주문 집행이 아니라 파이프라인 검증용이다.

---

## Solution Detail

### Core Capabilities (MoSCoW)

| Priority | Capability | Rationale |
|----------|------------|-----------|
| Must | `BrokerPort` 추상 계약 + 브로커 레지스트리 | 전체 작업의 토대. 이게 없으면 나머지가 KIS 복제로 퇴화 |
| Must | 기존 KIS 구현을 `BrokerPort` 뒤로 이전 (동작 무변경) | "KIS 회귀 무손실"이 성공 기준. 리팩터링이지 재작성이 아님 |
| Must | 토스 OAuth2 인증 + HTTP 클라이언트 (토큰 캐시·레이트리밋·재시도) | 모든 토스 호출의 전제. 09:00–09:10 3 req/s 제약 대응 포함 |
| Must | 토스 매매 어댑터 — KR 매수/매도/잔고/매수가능금액 | MVP의 핵심 가치 |
| Must | `PRISM_BROKER` 전역 설정 + `toss_config.yaml` | "설정 한 줄로 전환" 가설의 검증 수단 |
| Must | demo 모드 dry-run 시뮬레이터 | 토스에 모의투자가 없음. 실돈 사고 방지 안전장치. *결정 ①* |
| Should | 토스 매매 어댑터 — US (장중 한정) | 사용자가 KR+US 동시를 요청. 단 시간대 제약 있음 (아래 참조). *결정 ③* |
| Should | 토스 시세 소스 (`cores/market_data/toss_source.py`) | 기존 `SourceChain`에 끼우기만 하면 됨. 저비용 고효용 |
| Should | 미지원 기능의 명시적 `NotSupported` 실패 | 조용한 실패(silent failure) 방지. 예약주문·KR 종가주문 대상 |
| Could | 토스 고유 데이터 활용 (투자자별 매매동향·공매도·프로그램매매) | Trading Flow Analyst 에이전트 품질 향상 여지. v1 이후 |
| Could | `broker` 컬럼 DB 마이그레이션 | 브로커 전환 시 보유종목 정합성. Open Question 해소 후 |
| Won't | 조건주문(SINGLE/OCO/OTO) | PRISM에 대응 개념 부재 |
| Won't | 계좌별 브로커 혼합 | 설치 단위 전역 선택으로 확정 |
| Won't | 소수점/금액 기반 주문 | PRISM 포지션 모델이 정수 수량 전제 |

### MVP Scope

가설 검증에 필요한 최소치:

1. `BrokerPort` 계약 + KIS 어댑터(기존 코드 이전) + Toss 어댑터(KR)
2. `PRISM_BROKER=kis|toss` 전역 스위치
3. demo 모드 dry-run 시뮬레이터
4. KIS 기존 테스트 전량 green + 두 어댑터가 동일 계약 테스트 통과

이 4개가 되면 "설정 한 줄로 무수정 전환"과 "KIS 무손실"이 동시에 증명된다. US·시세소스는 그 다음.

### User Flow

```
[셋업]
1. 토스증권 WTS > 설정 > Open API → client_id / client_secret 발급
2. 같은 화면에서 서버 IP를 allowlist에 등록  ← 누락 시 전 요청 403
3. trading/config/toss_config.yaml 작성 (client_id, client_secret, account_seq)
4. .env 에 PRISM_BROKER=toss

[검증 — 실돈 없이]
5. PRISM_BROKER=toss 상태로 mode=demo 실행
   → dry-run 시뮬레이터가 실시세만 조회하고 가상 체결을 DB에 기록
   → 전체 파이프라인(분석→매수신호→추적→매도판단) 무사고 확인

[운영]
6. mode=real 전환 → 동일 코드 경로가 실제 POST /api/v1/orders 호출
```

---

## Technical Approach

**Feasibility**: **HIGH**

근거는 추측이 아니라 코드에서 확인한 두 개의 기존 seam이다.

- `prism_core/execution_service.py:108` — `ExecutionService`가 이미 브로커 파사드. `.domestic()`(147행)/`.us()`(176행) 팩토리와 `__getattr__` 위임(224–234행)이 있어, 여기서 브로커를 분기하면 상위 호출자 전체가 무변경으로 커버된다.
- `cores/market_data/source.py:43` — `MarketDataSource` Protocol + `SourceChain`(79행) 폴백 체인이 이미 가동 중이며 구현체가 4개(`kis_source`·`krx_source`·`naver_source`·`fdr_source`). 시세 계층은 `toss_source.py` 한 파일 추가로 끝난다. `Unsupported`(25행)/`Unavailable`(33행) 예외 규약도 이미 정의되어 있어 `NotSupported` 처리에 그대로 재사용 가능.
- `MultiAccountDomesticStockTrading`(`trading/domestic_stock_trading.py:2068`) — 동일 인터페이스 뒤에 다른 트레이더 객체를 끼우는 패턴이 이미 프로덕션에서 검증됨.

**Architecture Notes**

- **브로커 선택 지점은 `ExecutionService` 팩토리 한 곳으로 단일화**한다. 호출측(`stock_tracking_agent.py`, `tracking/helpers.py`, `stance_server.py` 등 30+ 파일)은 손대지 않는다. 이것이 "무수정 전환" 지표의 구현적 정의.
- **토스는 KR/US를 단일 API로 제공**(`symbol`이 KRX 6자리 숫자 / US 영문 티커로 구분). KIS는 국내·해외 모듈이 분리되어 있다. 따라서 Toss 어댑터는 **하나의 구현이 두 시장을 모두 커버**하며, KIS 쪽의 `domestic`/`us` 이분법에 억지로 맞추지 않고 `BrokerPort`가 market을 파라미터로 받는 형태가 자연스럽다.
- **`clientOrderId`를 멱등성 키로 활용**한다. 토스는 10분간 동일 키 재요청 시 이전 주문 결과를 재반환한다. PRISM의 기존 재시도 로직(`_request_with_retry`, `domestic_stock_trading.py:1570`)과 `OrderOutcomeUnknown`(`execution_service.py:43`) 처리에 정확히 맞물리는 기능 — 중복 주문 위험을 구조적으로 제거할 수 있다. KIS에는 없는 이점.
- **레이트리밋을 어댑터 내부에 내장**한다. 주문 10 req/s, **09:00–09:10 KST 3 req/s**. PRISM 아침 배치가 이 창과 겹치므로 토큰버킷 + `Retry-After` 준수 + 지수 백오프가 선택이 아니라 필수.
- **dry-run 시뮬레이터는 HTTP 경계에서 차단**한다. 어댑터 상위가 아니라 토스 HTTP 클라이언트 레벨에서 주문 계열 엔드포인트를 가로채야, 상위 로직이 실제 코드 경로 그대로 실행되면서도 주문만 나가지 않는다. 시세 조회는 통과시켜 현실적인 체결가를 쓴다.
- **`confirmHighValueOrder`**: 1억원 이상 주문은 `true` 없이는 `400 confirm-high-value-required`. PRISM의 `buy_amount` 설정이 이 선을 넘을 수 있으므로 어댑터에서 처리 필요.

**US 시간대 충돌 — 명시적 가정으로 진행 중**

사용자 결정 ②(예약주문 v1 미지원)와 ③(KR+US 동시)이 서로 충돌한다. 근거:

- `prism-us/trading/us_stock_trading.py`의 US 매수는 예약주문에 의존한다 — `is_reserved_order_available()`(872행), `buy_reserved_order()`(990행), 창구가 닫히면 `us_pending_orders` 테이블 큐잉 후 `us_pending_order_batch.py`가 10:05 KST 크론으로 집행.
- 토스에는 예약주문이 없고, **`422 order-hours-closed`로 장외 주문을 거부**한다 (OpenAPI 스펙에서 확인).
- PRISM이 도는 한국 시간대에는 US 장이 닫혀 있는 것이 정상 상태다.

⇒ **결론: 토스 경로의 US 매수는 US 정규장(23:30–06:00 KST)에만 동작한다.**

**현재 가정 (사용자 확인 필요)**: US는 v1에서 조회·매도·매수를 모두 지원하되 **US 정규장 시간에만** 동작하고, 장외 호출 시 큐잉하지 않고 `NotSupported`로 명시적 실패한다. 반쪽 지원을 조용히 숨기지 않고 드러내는 쪽을 택했다. 대안은 결정 ②를 US에 한해 뒤집어 기존 `us_pending_orders` 큐잉 패턴을 재사용하는 것(기능 동등하나 복잡도 상승) — Phase 6 착수 전 확정 필요.

**Technical Risks**

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| **모의투자 부재로 실돈 검증 불가피** | **H** | dry-run 시뮬레이터를 Phase 1 직후 최우선 배치. 실거래 검증은 최소 수량 1주로 한정. 사용자에게 명시적 고지 |
| KIS 추상화 추출 중 기존 동작 회귀 | H | 리팩터링 전 기존 테스트 전량 green 확인 → 추출 → 재확인. 동작 변경 금지 원칙. 커밋 단위를 잘게 |
| 토스 토큰 TTL 미문서화 | M | 401 발생 시 자동 재발급 + 재시도 구조로 방어. TTL 실측 후 사전 갱신으로 최적화 |
| 09:00–09:10 3 req/s 스로틀이 아침 배치와 충돌 | M | 어댑터 내장 토큰버킷. 주문 큐 직렬화. 초과 시 429 + `Retry-After` 준수 |
| IP allowlist 미등록으로 전 요청 403 | M | 셋업 문서에 1순위로 명시. 기동 시 `GET /api/v1/accounts` 헬스체크로 조기 진단 |
| 토스↔KIS 응답 스키마 정합성 (수수료·평단가·통화) | M | 계약 테스트로 두 어댑터의 반환 스키마 동일성 강제. `_safe_float` 패턴 재사용 |
| 브로커 전환 시 기존 보유종목 DB 정합성 깨짐 | M | Open Question으로 등록. 최소한 전환 시 경고 + 문서화. `broker` 컬럼은 Could |
| 토스 API 자체의 안정성·SLA 미상 (신규 API) | L | 어댑터 경계에서 예외 격리. 실패 시 기존 `Unavailable` 규약으로 상위 전파 |

---

## Implementation Phases

<!--
  STATUS: pending | in-progress | complete
  PARALLEL: phases that can run concurrently
  DEPENDS: phases that must complete first
  PRP: link to generated plan file once created
-->

| # | Phase | Description | Status | Parallel | Depends | PRP Plan |
|---|-------|-------------|--------|----------|---------|----------|
| 0 | 브랜치 + 계약 테스트 하네스 | `feat/toss-securities-broker` 브랜치, 기존 KIS 테스트 baseline 고정 | complete | - | - | - |
| 1 | `BrokerPort` 추상화 + KIS 이전 | 브로커 계약 정의, 기존 KIS를 그 뒤로 이전 (동작 무변경) | complete | - | 0 | [plan](../plans/completed/broker-port-abstraction.plan.md) · [report](../reports/broker-port-abstraction-report.md) |
| 2 | 토스 인증 + HTTP 클라이언트 | OAuth2, 토큰 캐시, 레이트리밋, 재시도, 에러 매핑 | complete¹ | with 1 | 0 | - |

> ¹ 코드·테스트 완료(53건). **실 API 호출 검증만 미완** — 자격증명과 IP 허용등록이 필요하다. `python -m trading.brokers.toss.smoke`로 확인 가능.
| 3 | dry-run 시뮬레이터 | demo 모드에서 주문 엔드포인트 차단 + 가상 체결 | complete | - | 2 | - |
| 4 | 토스 매매 어댑터 (KR) | 매수/매도/잔고/매수가능금액/정정/취소 | pending | - | 1, 2 | - |
| 5 | 브로커 설정 + 배선 | `PRISM_BROKER`, `toss_config.yaml`, `ExecutionService` 분기 | pending | - | 4 | - |
| 6 | 토스 매매 어댑터 (US) | 단일 어댑터 확장, 장중 게이팅, 장외 `NotSupported` | pending | with 7 | 5 | - |
| 7 | 토스 시세 소스 | `cores/market_data/toss_source.py` → `SourceChain` | pending | with 6 | 2 | - |
| 8 | 문서 + 셋업 가이드 | `toss_config.yaml.example`, README, CLAUDE.md, 마이그레이션 노트 | pending | - | 5, 6, 7 | - |

### Phase Details

**Phase 0: 브랜치 + 계약 테스트 하네스** ✅
- **Goal**: 회귀를 잡아낼 안전망을 먼저 친다
- **Scope**: `feat/toss-securities-broker` 브랜치 생성(완료). 기존 KIS 테스트 5종 baseline 실행·기록
- **Success signal**: `tests/test_async_trading.py` 외 4종의 현재 통과 상태가 문서화됨

**Phase 1: `BrokerPort` 추상화 + KIS 이전**
- **Goal**: 브로커 계약을 정의하고 KIS를 첫 구현으로 만든다 — **동작은 한 톨도 바꾸지 않는다**
- **Scope**: `trading/brokers/base.py`(Protocol + 예외 규약), `trading/brokers/kis_adapter.py`(기존 클래스 위임 래퍼), 계약 테스트 스위트
- **Success signal**: KIS 기존 테스트 전량 green + KIS 어댑터가 계약 테스트 통과. 프로덕션 동작 diff 0

**Phase 2: 토스 인증 + HTTP 클라이언트**
- **Goal**: 모든 토스 호출의 기반 인프라
- **Scope**: `trading/brokers/toss/auth.py`(OAuth2 client credentials, 토큰 캐시·401 재발급), `toss/client.py`(토큰버킷 레이트리밋, `Retry-After` 준수, 지수 백오프, 에러 코드 → PRISM 예외 매핑)
- **Success signal**: `GET /api/v1/accounts` 실호출 성공. 429 상황 시뮬레이션에서 백오프 동작 확인

**Phase 3: dry-run 시뮬레이터** ✅
- **Goal**: 모의투자 부재를 메우는 안전장치를 **매매 코드보다 먼저** 확보
- **Scope**: HTTP 클라이언트 레벨 인터셉터. `mode=demo` + `broker=toss`일 때 주문 계열 엔드포인트 차단, 실시세 기반 가상 체결 기록
- **Success signal**: demo 모드 전체 파이프라인 실행 시 `POST /api/v1/orders` 호출 카운트 0, 가상 포지션은 정상 생성
- **결과**: `trading/brokers/toss/dryrun.py`, 테스트 35건. 라우팅 테이블은 **default-deny** — 인식하지 못한 쓰기 요청은 전달하지 않고 차단한다.
- **계획 대비 변경**: 가상 체결을 "기존 DB 테이블"이 아니라 **전용 SQLite 원장**에 기록한다. `stock_holdings`는 추적 에이전트가 브로커 응답으로부터 채우는 PRISM의 *뷰*라서, 시뮬레이터가 같은 테이블에 쓰면 writer가 둘이 되어 실제 장부가 깨진다. 이건 브로커 상태이고, KIS에서는 같은 이유로 모의투자 서버가 그 상태를 들고 있다.

**Phase 4: 토스 매매 어댑터 (KR)**
- **Goal**: MVP의 핵심 가치
- **Scope**: `toss/adapter.py` — `POST /orders`(LIMIT/MARKET), `/modify`, `/cancel`, `GET /holdings`, `/buying-power`, `/sellable-quantity`, `/prices`. `clientOrderId` 멱등성 적용. 예약주문·KR 종가주문은 `NotSupported`
- **Success signal**: 계약 테스트 통과 + dry-run 모드에서 매수→추적→매도 전 사이클 완주

**Phase 5: 브로커 설정 + 배선**
- **Goal**: "설정 한 줄로 전환" 가설의 실제 검증
- **Scope**: `PRISM_BROKER` 환경변수, `trading/config/toss_config.yaml`, `ExecutionService` 팩토리 분기. **호출측 30+ 파일 무수정**
- **Success signal**: `PRISM_BROKER` 전환 전후 `git diff`가 설정 외 0줄. 양쪽 브로커로 동일 시나리오 통과

**Phase 6: 토스 매매 어댑터 (US)**
- **Goal**: US 시장 커버 (시간 제약 명시)
- **Scope**: Phase 4 어댑터를 US 심볼로 확장. `GET /market-calendar/US` 기반 장중 게이팅. 장외 매수는 `NotSupported`(가정 — 착수 전 확정 필요). `timeInForce=CLS`(LOC) 활용 검토
- **Success signal**: US 장중 dry-run 매수·매도 성공, 장외 호출은 명시적 실패로 기록

**Phase 7: 토스 시세 소스**
- **Goal**: 저비용 고효용 — 기존 폴백 체인에 편입
- **Scope**: `cores/market_data/toss_source.py`가 `MarketDataSource` Protocol 구현. `/prices`·`/candles`·`/orderbook` 매핑. 미지원 capability는 기존 `Unsupported` 예외
- **Success signal**: `SourceChain`에 등록 후 기존 시세 소비자들이 무변경으로 동작

**Phase 8: 문서 + 셋업 가이드**
- **Goal**: 사용자가 실제로 셋업할 수 있게
- **Scope**: `toss_config.yaml.example`, IP allowlist 절차, README 5개 언어 반영 검토, CLAUDE.md 버전 히스토리, **"토스는 모의투자가 없다"는 경고 명시**
- **Success signal**: 문서만 보고 처음부터 셋업 완주 가능

### Parallelism Notes

- **Phase 1 ∥ 2**: 서로 완전 독립. Phase 1은 기존 KIS 코드 리팩터링, Phase 2는 신규 토스 인프라 작성. 접점이 없다.
- **Phase 6 ∥ 7**: 매매 어댑터 US 확장과 시세 소스는 별개 파일·별개 계층. Phase 7은 Phase 2(HTTP 클라이언트)만 있으면 되므로 실은 Phase 5보다 먼저 착수해도 무방하다.
- **Phase 3은 4보다 앞선다** — 의존성이 아니라 안전 우선순위. 모의투자가 없는 브로커의 매매 코드를 dry-run 없이 짜는 것은 실돈 사고 위험을 불필요하게 키운다.
- **Phase 1 → 4는 직렬 필수**. 계약이 확정되지 않은 상태에서 두 번째 구현을 쓰면 계약이 KIS 형태로 굳어버린다.

### Git 운영 규칙 (사용자 요청)

- 브랜치: `feat/toss-securities-broker` (생성 완료)
- CLAUDE.md 규칙에 따라 코드 변경은 feature 브랜치 → PR
- **각 Phase 완료 시점마다 commit + push**. Phase 내부에서도 논리 단위(예: 인증 / 레이트리밋 / 에러매핑)로 분할 커밋
- 커밋 프리픽스: `feat:` (신규), `refactor:` (Phase 1 KIS 이전), `docs:` (Phase 8), `test:` (계약 테스트)

---

## Decisions Log

| Decision | Choice | Alternatives | Rationale |
|----------|--------|--------------|-----------|
| 브로커 선택 단위 | 설치 단위 전역 (`PRISM_BROKER`) | 계좌 단위 혼합, 시장 단위 | 사용자 확정. 가장 단순하고 v1 가설 검증에 충분 |
| 지원 범위 | 매매까지 전부 (조회+주문) | 조회 전용, KR 한정 | 사용자 확정. 토스 API가 주문을 온전히 제공하므로 가능 |
| demo 모드 처리 | 로컬 dry-run 시뮬레이터 | toss=real 전용 차단, demo는 KIS 폴백 | 사용자 확정. 실돈 없이 전체 파이프라인 검증이 가능한 유일한 안 |
| 예약주문 | v1 미지원, `NotSupported` | 로컬 스케줄러 에뮬레이션, 조건주문 매핑 | 사용자 확정. 조건주문은 가격 트리거라 의미가 다름 |
| MVP 시장 범위 | KR + US 동시 | KR 우선, 조회 우선 | 사용자 확정. 단 US는 시간대 제약 발생 (아래) |
| **US 장외 매수 처리** | **장중 한정 + 장외 `NotSupported`** | `us_pending_orders` 큐잉 재사용 | **가정 — 확인 필요.** 반쪽 지원을 숨기지 않는 쪽. 결정 ②와의 일관성 |
| 아키텍처 seam | `ExecutionService` 팩토리 단일 분기 | 호출측마다 분기, 모듈 복제 | 기존 파사드 구조 재활용. 호출측 30+ 파일 무변경 확보 |
| Toss KR/US 구현 | 단일 어댑터가 양 시장 커버 | KIS처럼 국내/해외 분리 | 토스 API 자체가 통합. 억지 분리는 불필요한 중복 |
| 중복주문 방지 | `clientOrderId` 멱등성 키 | 기존 재시도 로직에만 의존 | 토스 고유 이점. 10분 윈도우가 PRISM 재시도 주기를 커버 |

---

## Research Summary

**Market Context**

토스증권 Open API(`https://openapi.tossinvest.com`)는 33개 엔드포인트를 공개 제공하며 KIS의 주요 기능을 대부분 대체 가능하다. 인증은 OAuth 2.0 Client Credentials Grant 단일 방식(`POST /oauth2/token`), 계좌 관련 호출은 `X-Tossinvest-Account` 헤더 추가 요구. REST만 지원하며 WebSocket/스트리밍은 없다.

KIS 대비 **우위**: (1) KR·US 통합 API — 심볼 형식으로 시장 구분(KRX 6자리 숫자 / US 티커), (2) `clientOrderId` 멱등성 키(10분) — 중복주문 구조적 방지, (3) 투자자별 매매동향·공매도·프로그램매매·대차거래·신용거래 동향을 정식 엔드포인트로 제공(PRISM이 현재 별도 소스로 수집 중인 데이터), (4) 소수점/금액 기반 주문(US).

KIS 대비 **열위**: (1) **모의투자 환경 없음** — 이 작업의 최대 리스크, (2) 시간 기반 예약주문 없음(가격 트리거 조건주문만), (3) `timeInForce=CLS`가 US+LIMIT 조합만 지원 — KR 종가주문 불가, (4) IP allowlist 필수(미등록 시 403), (5) 09:00–09:10 KST 주문 3 req/s로 스로틀.

레이트리밋: 인증 5/s, 차트 20/s, 주문 10/s(피크 3/s), 조건주문 5/s. 응답에 `X-RateLimit-*` 헤더 제공. 에러는 `requestId`/`code`/`message`/`data` 봉투 규격.

**Technical Context**

- `prism_core/execution_service.py:108` — `ExecutionService` 파사드. `.domestic()`(147) / `.us()`(176) 팩토리, `__getattr__` 위임(224–234), `OrderOutcomeUnknown`(43). **브로커 분기의 단일 지점**.
- `cores/market_data/source.py:43` — `MarketDataSource` Protocol, `SourceChain`(79), `Unsupported`(25)/`Unavailable`(33). 구현체 4종 가동 중. **시세 계층은 파일 1개 추가로 완료**.
- `trading/domestic_stock_trading.py` — `DomesticStockTrading`(100), KIS TR ID가 `mode=="real"` 분기와 함께 382·520·672·906·1064행 등에 하드코딩. `_request(api_url, tr_id, params)`(239) 시그니처가 KIS 종속. `MultiAccountDomesticStockTrading`(2068)이 트레이더 교체 패턴을 이미 검증. `AsyncTradingContext`(2234).
- `prism-us/trading/us_stock_trading.py` — `USStockTrading`(236). `is_market_open()`(852), `is_reserved_order_available()`(872), `_queue_pending_order()`(895), `buy_reserved_order()`(990), `sell_reserved_order()`(1116). US 매수가 예약주문·큐잉에 강하게 의존 → 토스 이식의 핵심 난점.
- 호출측 30+ 파일이 트레이딩 계층을 참조(`stock_tracking_agent.py`, `tracking/helpers.py`, `stance_server.py`, `prism_core/stance_quotes.py`, `weekly_insight_report.py` 등). 이들을 건드리지 않는 것이 설계 제약.
- 기존 테스트 자산: `tests/test_async_trading.py`, `test_multi_account_domestic.py`, `test_execution_service.py`, `test_sell_quantity_guard.py`, `test_sell_denominator_sync.py`, `test_kr_pending_entry.py`, `prism-us/tests/test_phase6_trading.py`, `test_multi_account_us.py`.

**토스 주문 스키마 (OpenAPI 스펙 실측)**

```
POST /api/v1/orders   —  oneOf: OrderCreateQuantityBased | OrderCreateAmountBased

clientOrderId   string(≤36, [a-zA-Z0-9\-_])  멱등성 키, 10분 유효, 서버 자동생성 없음
symbol          string  REQUIRED   KRX: 6자리 숫자 / US: 영문 티커
side            enum    REQUIRED   BUY | SELL
orderType       enum    REQUIRED   LIMIT | MARKET
timeInForce     enum    default=DAY   DAY | CLS
                                   CLS = At the Close. US + LIMIT 조합만 지원 (LIMIT+CLS = LOC)
quantity        decimal REQUIRED(수량기반)  기본 양의 정수
                                   소수점은 US MARKET SELL만, 정규장~종료1시간전만, 6자리까지
price           decimal LIMIT일 때 필수, MARKET일 때 전달 금지
                                   KR: 정수·호가단위 준수 / US: $1미만 4자리, $1이상 2자리
orderAmount     decimal REQUIRED(금액기반)  US MARKET 전용, 정규장~종료1시간전만
confirmHighValueOrder  bool default=false   1억원 이상 주문 시 true 필수

에러: 400 invalid-request / confirm-high-value-required
      409 중복 요청
      422 order-hours-closed, amount-order-outside-regular-hours,
          fractional-quantity-outside-regular-hours, unsupported-market,
          market-not-supported-for-stock
      429 레이트리밋 (Retry-After 준수)
```

`422 order-hours-closed`의 존재가 "토스는 장외 주문을 받지 않는다"를 확정하며, 이것이 US 시간대 충돌의 근거다.

---

*Generated: 2026-08-17*
*Status: DRAFT — Phase 6 착수 전 US 장외 매수 처리 확정 필요*

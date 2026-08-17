# 토스 US 소수점 주식 지원 (보유 + 매매)

> **긴급도**: 🔴 High — 이미 머지된 코드에 보유 포지션을 "없음"으로 보고하는 버그가 있음
> **범위 제약**: **토스 경로만.** KIS 동작은 한 톨도 바뀌지 않아야 한다.

---

## Problem Statement

토스 US 계좌는 소수점 주식을 보유할 수 있고 실제로 보유 중인데, PRISM의 토스 어댑터가 수량을 정수로 절삭한다. 그 결과 **1주 미만 보유 종목이 포트폴리오에서 사라지고, 보유 중인 포지션이 `FLAT`(없음)으로 보고된다.** 매도 경로가 이 값을 신뢰하므로 팔아야 할 포지션을 팔지 못하고, 재진입 판단에서 중복 매수할 수 있다.

## Evidence

**실계좌로 확인됨 (2026-08-18):**

```
실제 보유:  JEPI 0.44519 · SCHD 0.788569 · TQQQ 1.68024 · USMV 0.272965 · VIG 0.105755

어댑터가 반환한 것:
  종목 수 1개  (5개 중 4개 소실)
  TQQQ qty=1   (1.68 → 1, 0.68주 소실)

get_holding_quantity_checked("JEPI") → ('FLAT', 0)     ← 실제로는 보유 중
get_holding_quantity_checked("SCHD") → ('FLAT', 0)     ← 실제로는 보유 중
```

- 원인: `trading/brokers/toss/adapter.py:659` `_int(item.get("quantity"))` + `quantity <= 0` 스킵
- KR 검증과 246개 테스트를 모두 통과했다. **국내는 정수 주식이라 이 결함이 드러나지 않는다.**

## 원화(KRX) 거래에는 이 문제가 있는가 — **없다**

명확히 답하기 위해 두 층을 따로 확인했다.

| 층 | 확인 결과 |
|---|---|
| **주문** | 토스 스펙 원문: *"소수점 수량은 미국 주식 시장가 매도 주문에만 허용됩니다. 그 외(매수/지정가/**국내**) 소수점 수량은 `400 invalid-request` 를 반환합니다."* → **국내는 소수점 주문 자체가 불가** |
| **보유** | 실계좌 KRW 보유 2건 모두 정수 (`498400`=2, `0183J0`=163) |
| **코드** | ⚠️ `_portfolio_checked()`는 **KR/US가 공유**한다. 절삭 로직이 KR 경로에도 있으나, 국내 수량이 정수라 결과가 같아 드러나지 않을 뿐이다 |

⇒ **현재 원화 거래에 실질적 문제는 없다.** 다만 같은 함수를 고치므로 **KR 동작 무변경을 회귀 테스트로 못박아야 한다.**

## Proposed Solution

토스 어댑터가 수량을 `Decimal`로 다루게 하고, 포트 계약이 소수 수량을 표현할 수 있게 확장한다. 매수는 토스의 금액 기반 주문(`orderAmount`)을, 매도는 소수 수량 주문을 사용한다. 소수점 주문이 허용되지 않는 시간대에는 정수 수량으로 자동 강등한다.

`Decimal`을 쓰는 이유는 float이 아니라: 0.1+0.2 문제로 보유 수량이 브로커와 어긋나면 전량 매도가 잔량을 남긴다. 금액과 수량은 이진 부동소수로 다루지 않는다.

KIS는 **항상 정수를 반환**하므로 계약 확장은 KIS 경로에 실질적 영향이 없다. 그래도 회귀 테스트로 고정한다.

## Key Hypothesis

We believe **소수 수량을 계약 수준에서 표현하는 것**이 **보유 포지션이 조용히 사라지는 문제**를 **토스 US 계좌 운영자**에게 해결해 줄 것이다.

We'll know we're right when **실계좌 5종목이 전부 포트폴리오에 정확한 수량으로 나타나고, `get_holding_quantity_checked`가 어느 것도 `FLAT`으로 보고하지 않으며, KIS 테스트가 99/99를 유지**할 때.

## What We're NOT Building

- **KIS 소수점 지원** — KIS 해외주식도 소수점을 지원하지만 이번 범위 밖. 토스 경로만 건드린다
- **국내 소수점** — 토스 API가 거부한다(`400 invalid-request`). 지원할 대상이 없다
- **지정가 소수점 주문** — 토스가 `MARKET`만 허용한다
- **소수점 수량의 피라미딩 분할 매도** — `compute_us_fractional_sell_quantity`는 정수 나눗셈 모델이다. 아래 Open Question 참조
- **`us_stock_holdings` 스키마 재설계** — 행별 수량을 저장하지 않는 현재 모델을 근본적으로 바꾸는 것은 별도 과제 (아래 위험 참조)

## Success Metrics

| Metric | Target | How Measured |
|--------|--------|--------------|
| 소수점 보유 인식 | 실계좌 **5/5 종목** 정확한 수량 | `get_portfolio()` 실계좌 실행 |
| 잘못된 FLAT 보고 | **0건** | 보유 5종목 각각 `get_holding_quantity_checked` |
| KIS 회귀 | **99/99 유지** | 기존 KIS 테스트 7종 |
| KR 토스 경로 회귀 | 국내 2종목 수량·타입 **변경 없음** | 실계좌 KR 포트폴리오 비교 |
| 수량 정밀도 | 브로커 값과 **완전 일치** (6자리) | `Decimal` 동등성 비교 |

## Open Questions

- [ ] **[BLOCKING] `us_stock_holdings`가 행별 수량을 저장하지 않는다.** `prism-us/tracking/db_schema.py:1006`: *"The independent-row model deliberately stores no per-row quantity ... and each add is ~1 unit"*. 0.44주 포지션을 "1행 ≈ 1주" 모델에 넣으면 의미가 깨진다. 추적 DB까지 손댈 것인가, 아니면 소수점 포지션은 추적 행을 만들지 않고 브로커 잔고만 신뢰할 것인가?
- [ ] **1주 미만 포지션의 시간 외 매도.** 결정된 "정수 강등"을 0.44주에 적용하면 `int(0.44)=0` → **매도 불가**. 손절이 필요한 순간에 창(정규장~종료 1시간 전)이 닫혀 있으면 팔 방법이 없다. 강등 대신 거부해야 하는가?
- [ ] `compute_us_fractional_sell_quantity`(정수 나눗셈)를 소수 포지션에 적용하면 `0.44 // 2 = 0`. 피라미딩된 소수 포지션의 분할 매도 규칙이 필요한가, 아니면 소수 포지션은 항상 전량 매도인가?
- [ ] 소수점 매수는 `orderAmount`(금액 확정, 수량 변동)다. PRISM은 `buy_amount`로 금액을 정하므로 오히려 자연스러우나, **체결 수량을 미리 알 수 없다.** 주문 직후 `GET /orders/{id}`로 확인하는 현재 방식으로 충분한가?
- [ ] 소수점 보유가 `MAX_SLOTS`(10) 계산에 어떻게 반영되는가? 0.1주도 한 슬롯인가?

---

## Users & Context

**Primary User**
- **Who**: 토스 US 계좌를 PRISM에 연결한 운영자. 토스 앱에서 소수점 매수를 해왔거나, PRISM이 금액 기반으로 매수하게 될 사람
- **Current behavior**: 소수점 보유가 대시보드·텔레그램 리포트에서 사라진 채로 운영 중
- **Trigger**: `PRISM_BROKER=toss` + US 시장 사용. 토스 US는 소수점 매수가 기본 UX라 대부분의 계좌가 해당된다
- **Success state**: 보유한 것이 보유한 만큼 보이고, 자동매매가 그 포지션을 관리한다

**Job to Be Done**
When **토스로 미국 주식을 소수점 단위로 보유하고 있을 때**, I want to **PRISM이 그 포지션을 정확한 수량으로 인식하고 관리하기를**, so I can **손절·익절이 실제 보유분에 적용되고 리포트가 계좌와 일치한다**.

**Non-Users**
- KIS 사용자 — 이번 변경의 영향을 받지 않아야 한다
- 국내 주식만 하는 사용자 — 토스 API가 국내 소수점을 거부하므로 해당 없음

---

## Solution Detail

### Core Capabilities (MoSCoW)

| Priority | Capability | Rationale |
|----------|------------|-----------|
| Must | 보유 수량을 `Decimal`로 정확히 읽기 | 버그의 직접 원인. 이것만 고쳐도 포지션이 사라지지 않는다 |
| Must | 계약(`normalize_checked_holding`)이 소수 수량 허용 | 안 하면 어댑터가 정확해도 상위에서 `UNKNOWN`으로 뭉개진다 |
| Must | KIS 무변경 회귀 고정 | 범위 제약. 계약을 건드리므로 명시적 테스트 필요 |
| Must | KR 토스 경로 무변경 회귀 고정 | `_portfolio_checked` 공유. 국내가 깨지면 안 된다 |
| Must | 소수 전량 매도 (`quantity` 소수, MARKET) | 인식만 하고 못 팔면 반쪽 |
| Should | 금액 기반 매수 (`orderAmount`) | 토스 US 매수의 기본 방식. `buy_amount` 모델과 잘 맞는다 |
| Should | 소수점 창 밖 정수 강등 | 사용자 결정. 단 1주 미만은 강등 불가 (Open Question) |
| Could | 소수 포지션의 슬롯 계산 반영 | `MAX_SLOTS` 의미 정리 필요 |
| Won't | KIS 소수점 | 범위 밖 |
| Won't | 국내 소수점 | API가 거부 |
| Won't | 지정가 소수점 | API가 거부 |

### MVP Scope

가설 검증에 필요한 최소치는 **읽기 정확성 + 매도 가능**이다:

1. `Decimal` 수량 파싱, 절삭 제거
2. 계약 확장 + KIS/KR 회귀 고정
3. 소수 전량 매도

매수(`orderAmount`)는 그 다음이다. **못 파는 것이 못 사는 것보다 위험하다** — 이미 보유한 포지션이 손절 대상이 될 수 있기 때문이다.

### User Flow

```
[현재 — 버그]
보유 JEPI 0.44519  →  어댑터 int() 절삭  →  0  →  "FLAT"
                                              → 매도 안 됨 / 중복 매수 위험

[수정 후]
보유 JEPI 0.44519  →  Decimal 유지  →  ("HELD", 0.44519)
                                        → 손절 판단 대상
                                        → 매도 시 정규장 창이면 소수 수량 매도
                                        → 창 밖이면? (Open Question)
```

---

## Technical Approach

**Feasibility**: **MEDIUM** — 어댑터 수정은 단순하나, 계약과 추적 DB 모델이 정수를 전제한다

**Architecture Notes**

- **`Decimal` 사용, float 금지.** 0.1+0.2 오차로 보유 수량이 브로커와 어긋나면 전량 매도가 잔량을 남긴다. 토스도 수량을 문자열로 주므로 `Decimal(str)`이 자연스럽다
- **계약 확장은 하위 호환으로.** `normalize_checked_holding`(`prism_core/execution_service.py:34`)이 `type(quantity) is int`를 엄격히 요구한다. `Decimal`을 추가로 허용하되 `int` 경로는 그대로 둔다 — KIS는 항상 `int`를 반환하므로 실질 영향이 없다
- **토스 소수점 제약은 어댑터가 흡수한다** (스펙 실측):

  | 동작 | 조건 |
  |---|---|
  | 소수 **매도** | `quantity` 소수 · **US + MARKET + SELL만** · 소수점 6자리 · 정규장~종료 1시간 전 |
  | 소수 **매수** | `orderAmount` · **US + MARKET만** · 같은 시간 창 |
  | 위반 시 | `400 invalid-request` / `422 fractional-quantity-outside-regular-hours` / `422 amount-order-outside-regular-hours` |

- **KR 경로는 정수 유지.** `_portfolio_checked`가 공유되므로 시장별 분기를 명시적으로 둔다. 국내에서 소수가 오면(있을 수 없지만) 경고 로그를 남긴다

**Technical Risks**

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| **`us_stock_holdings`가 행별 수량 미저장** — 0.44주를 "1행 ≈ 1주" 모델에 넣으면 의미 붕괴 | **H** | Open Question. 최소안: 소수 포지션은 브로커 잔고를 단일 진실로 쓰고 추적 행을 만들지 않음 |
| **1주 미만 + 창 밖 = 매도 불가** | **H** | 정수 강등이 `0`이 되는 경우를 별도 처리. 강등 대신 명시적 실패가 정직할 수 있음 |
| 계약 확장이 KIS에 영향 | M | KIS는 항상 `int` 반환. 회귀 테스트로 고정 |
| `compute_us_fractional_sell_quantity` 정수 나눗셈 (`0.44 // 2 = 0`) | M | 소수 포지션은 전량 매도로 한정하거나 함수 확장 |
| `MAX_SLOTS` 의미 모호 (0.1주도 1슬롯?) | L | 명시적 정의 필요. 기본은 "종목 단위 = 1슬롯" |
| 수량 정밀도 6자리 초과 | L | 어댑터에서 6자리로 절삭(`ROUND_DOWN`). 올리면 보유량 초과 매도 |

---

## Implementation Phases

| # | Phase | Description | Status | Parallel | Depends | PRP Plan |
|---|-------|-------------|--------|----------|---------|----------|
| 1 | 계약 확장 + 회귀 고정 | `normalize_checked_holding`이 `Decimal` 허용, KIS/KR 무변경 테스트 | in-progress | - | - | [plan](../plans/fractional-holding-contract.plan.md) |

> ⚠️ **Phase 1 전제 정정**: PRD는 `normalize_checked_holding`이 US 소수점을 막는 벽이라고 가정했으나, 조사 결과 **US 경로는 그 함수를 쓰지 않는다** (`prism-us`는 `get_holding_quantity`를 사용). 호출자 3곳은 전부 KR 전용(`[POSITION-PENDING][KR]`)이며 각자 정수를 재검사한다. 따라서 Phase 1의 가치는 'US 해방'이 아니라 ①계약이 소수를 표현 가능하게 하고 ②KR이 소수를 안전하게 거부함을 고정하는 것이다. **버그의 실제 해소는 Phase 2다.**
| 2 | 소수 보유 정확 인식 | 어댑터 `Decimal` 파싱, 절삭 제거, 시장별 분기 | pending | - | 1 | - |
| 3 | 소수 매도 | `quantity` 소수 MARKET 매도, 창 검사, 6자리 절삭 | pending | with 4 | 2 | - |
| 4 | 금액 기반 매수 | `orderAmount` 매수, 창 검사, 체결 수량 되읽기 | pending | with 3 | 2 | - |
| 5 | 창 밖 강등 규칙 | 정수 강등, 1주 미만 처리 확정 | pending | - | 3, 4 | - |
| 6 | 추적 DB 정합성 | 소수 포지션과 `us_stock_holdings` 모델 조정 | pending | - | 5 | - |
| 7 | 문서 | 설정 가이드·CLAUDE.md에 소수점 제약 반영 | pending | - | 6 | - |

### Phase Details

**Phase 1: 계약 확장 + 회귀 고정**
- **Goal**: 소수 수량을 표현할 수 있게 하되, KIS와 KR이 바뀌지 않음을 증명
- **Scope**: `normalize_checked_holding`에 `Decimal` 허용. KIS 7종 + 토스 KR 경로 회귀 테스트
- **Success signal**: KIS 99/99 유지, KR 국내 2종목 수량·타입 동일

**Phase 2: 소수 보유 정확 인식**
- **Goal**: 버그의 직접 해소
- **Scope**: `_portfolio_checked`의 `_int` → `Decimal`, `quantity <= 0` 조건 재검토, KR은 정수 유지
- **Success signal**: 실계좌 5종목 전부 정확한 수량, `FLAT` 오보고 0건

**Phase 3: 소수 매도**
- **Goal**: 인식한 포지션을 실제로 정리할 수 있게
- **Scope**: `MARKET`+`SELL`+소수 `quantity`, 정규장 창 검사, 6자리 `ROUND_DOWN`
- **Success signal**: dry-run에서 0.44주 전량 매도 성공, 창 밖에서는 명시적 실패

**Phase 4: 금액 기반 매수**
- **Goal**: 토스 US의 기본 매수 방식 지원
- **Scope**: `orderAmount` 본문, 창 검사, 주문 후 체결 수량 되읽기
- **Success signal**: dry-run에서 금액 매수 → 소수 수량 포지션 생성

**Phase 5: 창 밖 강등 규칙**
- **Goal**: 시간 제약을 사용자 결정대로 처리하되 1주 미만 함정을 막음
- **Scope**: 정수 강등 구현. `int(수량)==0`이면 강등 불가 → 처리 방식 확정
- **Success signal**: 1.68주는 창 밖에서 1주 매도, 0.44주는 정의된 동작

**Phase 6: 추적 DB 정합성**
- **Goal**: 소수 포지션과 행 기반 모델의 충돌 해소
- **Scope**: Open Question 결론에 따름
- **Success signal**: 브로커 잔고와 DB가 어긋나지 않음

**Phase 7: 문서**
- **Goal**: 제약을 문서로 남김
- **Scope**: `docs/TOSS_BROKER_SETUP.md`의 "소수점 미지원" 서술 정정, 시간 창·6자리·국내 불가 명시
- **Success signal**: 문서만 보고 동작을 예측 가능

### Parallelism Notes

- **Phase 3 ∥ 4**: 매도와 매수는 서로 다른 요청 형태(`quantity` vs `orderAmount`)라 독립적이다
- **Phase 1 → 2는 직렬 필수**: 계약이 소수를 못 받으면 어댑터가 정확해도 상위에서 뭉개진다
- **Phase 3이 4보다 우선순위가 높다**: 못 파는 것이 못 사는 것보다 위험하다

---

## Decisions Log

| Decision | Choice | Alternatives | Rationale |
|----------|--------|--------------|-----------|
| 범위 | 소수점 매수까지 전체 지원 | 안전장치만(UNKNOWN 보고), 보유+매도만 | 사용자 확정 |
| 기존 소수점 보유분 | PRISM 자동매매 대상 포함 | 관리 대상 제외 | 사용자 확정. 손절 대상이 될 수 있으므로 방치가 위험 |
| 소수점 창 밖 처리 | 정수 수량 자동 강등 | 명시적 실패, 큐잉 | 사용자 확정. 단 1주 미만은 강등 불가 → Open Question |
| 브로커 범위 | 토스만, KIS 무변경 | 양쪽 동시 | 사용자 확정. KIS는 이미 검증된 경로 |
| 수량 타입 | `Decimal` | `float`, 정수+별도 소수 필드 | float 오차로 전량 매도가 잔량을 남기면 포지션이 영구히 안 닫힌다 |
| 국내(KRX) | 변경 없음 | 방어적 소수 지원 | 토스가 국내 소수점 주문을 거부하고 실보유도 정수 |

---

## Research Summary

**토스 API 제약 (OpenAPI 스펙 실측)**

> 소수점 수량: 미국 주식 시장가 매도(`orderType=MARKET` + `side=SELL`) 주문에만 허용됩니다.
> 그 외(매수/지정가/**국내**) 소수점 수량은 `400 invalid-request` 를 반환합니다.
> 소수점 매수는 `orderAmount` 를 사용하세요.
> 소수점 수량 매도는 정규장 시작부터 정규장 종료 1시간 전까지만 접수 가능하며,
> 그 외 시간 요청 시 `422 fractional-quantity-outside-regular-hours` 를 반환합니다.
> 소수점 수량은 소수점 6자리까지 지원합니다.

`orderAmount`는 US `MARKET` 전용이며 같은 시간 창을 갖는다. 금액을 확정하고 수량이 변동한다.

**코드베이스**

- `trading/brokers/toss/adapter.py:659` — `_int(item.get("quantity"))`. 버그의 위치. KR/US 공유
- `prism_core/execution_service.py:34` — `type(quantity) is int` 엄격 검사. 계약의 벽
- `prism-us/tracking/db_schema.py:1006` — *"stores no per-row quantity ... each add is ~1 unit"*. 소수 포지션과 근본적으로 충돌
- `prism-us/tracking/db_schema.py:1071` — `compute_us_fractional_sell_quantity`는 **정수 나눗셈**이며 "소수점 주식"이 아니라 "포지션의 분할"을 뜻한다. 이름이 혼동을 준다
- `prism-us/trading/us_stock_trading.py:1068,1213` — KIS는 `str(int(quantity))`로 정수 강제. KIS 경로가 정수임을 재확인

**실계좌 검증 (2026-08-18)**

| 통화 | 보유 | 소수점 |
|---|---|---|
| KRW | 498400=2, 0183J0=163 | 없음 |
| USD | JEPI 0.44519, SCHD 0.788569, TQQQ 1.68024, USMV 0.272965, VIG 0.105755 | **전부** |

`buying-power`는 통화별 독립 풀이다 (KRW 4원 / USD $3.58). 통합증거금·환전 개념은 API에 없다.

---

*Generated: 2026-08-18*
*Status: DRAFT — Phase 6 착수 전 추적 DB 모델 결정 필요*

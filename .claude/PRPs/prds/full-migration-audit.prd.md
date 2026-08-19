# 전체 마이그레이션 점검 (Toss 전환 + KRX 탈피 완결성 감사)

## Problem Statement

PRISM-INSIGHT는 두 가지 마이그레이션을 진행했다: (1) KIS 원본 구조를 유지하며 Toss 브로커를 추가(`PRISM_BROKER`), (2) 로그인 방식 KRX 데이터 접근을 OpenAPI로 전환하되 미지원 항목은 네이버 우회. 그러나 두 마이그레이션 모두 **완결되지 않은 채 실계좌 운영에 들어갔고**, 하드코딩·누락으로 인한 긴급 수정이 반복되고 있다. 미해결 결함 중 4건은 실금전 매매 경로에 직접 영향을 준다.

## Evidence

4개 병렬 코드베이스 조사(2026-08-18)로 검증된 사실:

- **[실금전] 매수 금액 설정 사장**: `trading/brokers/factory.py:107`이 env만 확인 → `toss_config.yaml`의 `default_unit_amount`는 주문 경로에서 절대 읽히지 않고 어댑터 하드코딩 `100_000`(`trading/brokers/toss/adapter.py:49`)으로 폴백. 올바른 체인 함수 `settings.buy_amount()`(`settings.py:150-157`)는 **호출부 0곳**.
- **[실금전] 강제청산 감지 무력화**: `cores/corporate_status.py:87`이 KIS 직접 임포트 → Toss에서 `except`로 조용히 `{}` 반환 → 상장폐지/거래정지 TIER0 강제청산이 작동 안 함 (`stock_tracking_agent.py:3103` 호출).
- **[실금전] US 소수점 매도 불가**: `prism-us/tracking/db_schema.py:1111` `int(total_quantity)` 절삭 → Toss `0.44주` 보유가 `0`이 되어 매도 불가. 추가로 `prism-us/us_stock_tracking_agent.py:2802`가 `BrokerPort`에 없는 `is_market_open`을 호출 → Toss에서 `AttributeError` → `will_queue=True` → 피라미딩 종목이 `full_exit` 분기로 빠짐.
- **[실금전] DB 경로 분열**: `stock_tracking_db.sqlite` 리터럴 20곳, 상대경로 그룹(메인 매매 루프 `stock_tracking_agent.py:204` 포함)과 PROJECT_ROOT 그룹(매도 도구들)이 혼재 → cwd에 따라 매매 루프와 매도 루프가 다른 DB를 봄.
- **KIS 우회 잔재 8곳+**: `stance_mark.py:41`, `examples/generate_dashboard_json.py:74-80`(kis_devlp.yaml로 trading mode 결정 → Toss 실계좌를 demo 표기 가능), `tools/check_kr_pending_readiness.py:406`, `examples/messaging/*`(주문 경로 전체 KIS 전용), `cores/archive/data_enricher.py:196` 등.
- **KRX 로그인 탈피 미완**: 체인 기본 1순위 `krx` 소스가 여전히 로그인 클라이언트(`cores/market_data/krx_source.py:61`)이고 **KRX OpenAPI는 `_BUILDERS`에 미등록**. 로그인 클라이언트 직접 의존 10+파일(`trigger_batch.py`, `weekly_market_facts.py`, `tracking/helpers.py`, `performance_tracker_batch.py`, `stock_tracking_enhanced_agent.py` 등). 네이버 우회 코드 4곳 분산, 공유 클라이언트 없음. 운영 서버에서 KRX 로그인은 차단/실패 상태(사용자 확인).
- **KR/US 비대칭**: v2.21.x 수정 ~15커밋 중 US 반영 2커밋. `prism-us/us_stock_tracking_agent.py:195-199`는 모듈 스코프 `spec_from_file_location`으로 `kis_auth` 무조건 로드(Toss-only 설치에서 임포트 즉사) — `prism-us/tests/test_issue_448...py:71-100`은 이 결함을 monkeypatch로 우회 중. `prism-us/tests/`에 브로커 테스트 0개, conftest 브로커 env 미초기화.
- **트립와이어 사각지대 7종**: 함수 스코프 임포트, `spec_from_file_location` 로드(`sys.modules`에 `kis_auth`/`prism_us_stock_trading` 등 다른 이름으로 등록), prism-us 진입점 미포함, `kis_devlp.yaml` 직접 읽기, KIS 응답 형태(`rt_cd`/`output`) 누출, `domestic.ka` 속성 경유, BrokerPort 미정의 메서드 호출(`fill_chaser`는 양 브로커에서 조용히 no-op).
- **드리프트 중복**: `oneil_fallback.py` KR/US 바이트 동일 중복(매도 임계값 8종), 스크리닝 상수 6종 수동 미러, Toss base URL 4곳, `default_unit_amount` 4곳 중 1곳은 10배 다른 값, 모델명 리터럴 48곳/30파일.

## Proposed Solution

**탐지 우선(tripwire-first) 감사**: 먼저 재발 방지 장치를 확장해 알려진 결함 전부를 실패하는 테스트/감사 스크립트로 고정한다(Phase 1). 그 다음 실금전 P0부터 수정하면 각 수정이 새 트립와이어의 green 전환으로 검증된다(Phase 2). 이후 영역별(KIS 잔재 → 데이터 소스 → KR/US 대칭화 → 드리프트 청소)로 일괄 정리한다. 사용자가 선택한 "점검 체계 먼저" 접근이며, 원본 코드 형태 유지 원칙(어댑터/팩토리/체인 패턴 존중, 호출측 무수정)을 따른다.

## Key Hypothesis

We believe **결함을 테스트로 먼저 고정하는 탐지-우선 감사**가 **하드코딩·누락으로 인한 반복 긴급 수정**을 해소한다.
We'll know we're right when: KIS 설정 없는 Toss-only 환경에서 전 진입점(KR+US)이 완주하고, 트립와이어 사각지대 7종이 테스트로 커버되며, 점검 완료 후 2주간 동일 유형 긴급 수정이 0건이다.

## What We're NOT Building

- **새 기능/새 브로커** — 이번 작업은 기존 목표의 완결성 감사이지 기능 추가가 아님
- **KRX 로그인 클라이언트의 완전 삭제** — 사용처를 체인/OpenAPI/네이버로 이전하는 것이 목표. 클라이언트 자체 제거는 이전 완료 후 별도 결정
- **US 데이터의 다중 소스 체인 전면 도입** — yfinance 단일 유지. 단, KR 체인과의 구조 차이는 문서화 (사용자가 "전부 포함"을 선택했으나, US 데이터 체인화는 감사 범위의 "기록·문서화"까지만, 구현은 후속 PRD)
- **운영 인프라 변경** — cron 구성, 서버 이전 등은 범위 외

## Success Metrics

| Metric | Target | How Measured |
|--------|--------|--------------|
| Toss-only 완주 | KR+US 전 운영 진입점이 `kis_devlp.yaml` 부재 환경에서 임포트+드라이런 성공 | 확장된 import census + smoke 테스트 (CI) |
| 트립와이어 커버리지 | 사각지대 7종 모두 테스트로 커버 | Phase 1 신규 테스트 목록 대비 체크 |
| P0 결함 해소 | 실금전 4건 전부 수정 + 회귀 테스트 | Phase 2 완료 기준 |
| KRX 로그인 의존 | 운영 진입점(KR 파이프라인·리포트류)에서 로그인 클라이언트 도달 경로 0 | 감사 스크립트 (import trace) |
| 긴급 수정 빈도 | 점검 완료 후 2주간 하드코딩/누락 유형 긴급 수정 0건 | git log 관찰 |
| 문서화 | 전 발견 항목이 심각도별 감사 보고서로 정리 | `.claude/PRPs/reports/full-migration-audit-report.md` |

## Open Questions

- [x] `examples/messaging/*` — **해소(Phase 3)**: KIS 전용 명시 + 타 브로커에서 기동 거부. 미운영이라 브로커 인식화는 미실시
- [ ] `trading/brokers/kis_adapter.py`의 `KisBroker`가 프로덕션 미사용(팩토리가 raw 클래스 반환) — 실제로 어댑터를 경유시킬지, 계약 테스트 전용으로 문서화할지
- [ ] `kis_devlp.yaml`의 `default_unit_amount: 10000`(타 위치 대비 10배 차이)이 의도된 값인지
- [ ] KRX OpenAPI 미지원 데이터(투자자 수급 등)의 장기 소스 전략: 네이버 유지 vs KIS/Toss 소스 승격
- [ ] `oneil_fallback.py` 단일화 시 공유 모듈 위치 (prism_core? cores?)

---

## Users & Context

**Primary User**
- **Who**: 운영자 본인 (heracles) — Toss 실계좌로 KR+US 자동매매 파이프라인과 리포트류를 cron 운영 중
- **Current behavior**: 결함이 운영 중 드러날 때마다 원인 추적 → 긴급 수정 → PR 반복
- **Trigger**: 환경 변화(브로커 전환, KRX 차단, cwd 변화)마다 숨은 하드코딩/누락이 새로 터짐
- **Success state**: 알려진 결함이 전부 목록화·수정되고, 같은 유형이 재발하면 배포 전 테스트가 잡아줌

**Job to Be Done**
When 브로커·데이터 소스 마이그레이션이 부분 완료 상태로 실계좌 운영에 들어갔을 때, I want to 누락 지점을 체계적으로 전수 조사하고 탐지 장치로 고정하고 싶다, so I can 긴급 수정 없이 안심하고 무인 운영을 지속할 수 있다.

**Non-Users**
외부 기여자/신규 설치자는 부차 수혜자(Toss-only 설치가 실제로 동작하게 됨)이나 이번 감사의 직접 대상은 아님.

---

## Solution Detail

### Core Capabilities (MoSCoW)

| Priority | Capability | Rationale |
|----------|------------|-----------|
| Must | 트립와이어 확장 (사각지대 7종) | 탐지-우선 접근의 기반. 이후 모든 수정의 검증 수단 |
| Must | P0 실금전 4건 수정 | Toss 실계좌 운영 중 — 매수금액 설정 사장, 강제청산 무력화, US 소수점 매도 불가, DB 경로 분열 |
| Must | US 트래킹 에이전트 모듈 스코프 KIS 로드 제거 | Toss-only에서 US 파이프라인 임포트 즉사 + 테스트가 결함을 우회 중 |
| Must | KIS 잔재 제거 (운영 진입점) | 대시보드 mode 오표기, stance_mark, corporate_status 등 |
| Must | KRX OpenAPI를 체인 소스로 등록 + 기본 순서 재정의 | 로그인 소스가 기본 1순위인 모순 해소. 운영 서버에서 로그인은 이미 차단 상태 |
| Must | 로그인 클라이언트 직접 의존 10+파일 체인 이전 | KRX 탈피 목표의 본체 |
| Should | KR/US 대칭화 (v2.21.2 수정 US 반영, retry, _safe_float, checked-holding) | 실금전 경로이나 KIS 브로커 한정 항목 다수 |
| Should | 네이버 우회 코드 공용 클라이언트화 | 4곳 분산 URL/헤더/파싱 통합 |
| Should | 드리프트 상수 단일화 (oneil_fallback, 스크리닝 상수, URL, KST) | 재발 원인 제거 |
| Could | 모델명 48곳 중앙화 (`cores/llm/models.py` 경유) | 매매 경로 아님, 규모 큼 |
| Could | messaging 구독자 브로커 인식화 또는 deprecated 명시 | 현재 미운영 확인 |
| Won't | US 데이터 소스 체인 구현 | 후속 PRD로 분리 (문서화까지만) |

### MVP Scope

Phase 1+2 (트립와이어 확장 + P0 수정). 이것만으로 "실금전 위험 해소 + 재발 시 자동 탐지"라는 핵심 가설이 검증됨.

### User Flow (검증 경로)

`PRISM_BROKER=toss` + `kis_devlp.yaml` 부재 환경에서 → 전 진입점 임포트 census 통과 → KR/US 트래킹 에이전트 드라이런 완주 → 매수 금액이 `toss_config.yaml`에서 읽힘을 로그로 확인 → 소수점 보유분 매도 판단 정상.

---

## Technical Approach

**Feasibility**: HIGH — 모든 결함이 file:line 수준으로 특정됐고, 따라갈 기존 패턴(팩토리 게이트, `selected_broker()` 분기, 소스 체인, `trading_settings()`)이 코드베이스에 이미 존재. 각 수정은 검증된 패턴의 적용.

**Architecture Notes**
- 기존 추상화 계층을 신설하지 않는다. `trading/brokers/factory.py`(브로커 결정), `trading/brokers/settings.py`(설정), `cores/market_data/`(데이터 체인)의 기존 단일 지점으로 호출을 모으는 것이 전부
- 트립와이어 확장은 `tests/test_no_module_scope_kis_import.py`의 기존 2계층(AST 스캔 + import census) 구조를 확장: AST 스캔에 `spec_from_file_location` 호출 패턴 추가, census에 prism-us 진입점 추가 + `sys.modules` 검사를 별칭(`kis_auth`, `prism_us_stock_trading` 등)까지 확장
- `BrokerPort` 계약 위반(미정의 메서드 호출)은 정적 스캔(호출부 grep) + 계약 테스트로 커버
- DB 경로는 단일 상수 모듈로 수렴 (기존 PROJECT_ROOT-derived 그룹의 패턴 채택)

**Technical Risks**

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| 수정 자체가 실계좌 운영 중 매매 동작을 바꿈 | M | Phase별 작은 PR + 트립와이어 green 확인 + `PRISM_TRADING_MODE=demo` 드라이런 후 배포 |
| 트립와이어가 과탐(false positive)으로 CI를 막음 | M | 기존 `ALLOWED` 목록 패턴 유지, 정당한 KIS 사용처는 명시적 allow-list |
| 로그인 클라이언트 이전 시 데이터 공백 (OpenAPI 미지원 항목) | M | 항목별 대체 소스 매핑표를 Phase 4 시작 시 작성 (투자자 수급→네이버/KIS, 섹터→네이버 등 기존 우회 활용) |
| US 대칭화가 KIS 해외주식 경로 회귀 유발 | L | 기존 `prism-us/tests` + 브로커 env 초기화 conftest 추가 후 진행 |

---

## Implementation Phases

| # | Phase | Description | Status | Parallel | Depends | PRP Plan |
|---|-------|-------------|--------|----------|---------|----------|
| 1 | 감사 체계 강화 | 트립와이어 사각지대 7종 커버 + 감사 보고서 초판 (알려진 결함 = 실패 테스트로 고정) | complete | - | - | [plan](../plans/completed/audit-tripwire-hardening.plan.md) · [report](../reports/full-migration-audit-report.md) |
| 2 | P0 실금전 수정 | 매수금액 설정 체인, 강제청산 브로커 인식, US 소수점 매도, DB 경로 단일화, US 모듈 스코프 KIS 로드 제거, BrokerPort 계약 보강 | complete | - | 1 | [plan](../plans/completed/p0-money-path-fixes.plan.md) · [report](../reports/p0-money-path-fixes-report.md) |
| 2.5 | Toss US 주문 경로 복구 | PR #9 2차 리뷰 10건: 인자 철자 불일치로 Toss US 주문이 브로커에 미도달, 응답 Decimal 바인딩 실패, 행 삭제 선행으로 인한 포지션 손실, 누적 롤백 부재 | complete | - | 2 | [plan](../plans/completed/toss-us-order-path-repair.plan.md) · [report](../reports/toss-us-order-path-repair-report.md) |
| 3 | KIS 잔재 제거 (KR) | 대시보드 mode, stance_mark, archive enricher, readiness tool, messaging 구독자 처리 결정 | complete | with 4 | 2.5 | [plan](../plans/completed/kis-residue-removal.plan.md) · [report](../reports/kis-residue-removal-report.md) |
| 4 | 데이터 소스 정리 | KRX OpenAPI 체인 등록 + 기본 순서 재정의, 로그인 의존 10+파일 이전, 네이버 공용 클라이언트, .env.example/docs 정리 | pending | with 3 | 2 | - |
| 5 | KR/US 대칭화 | v2.21.2 수정 US 반영, retry/_safe_float/checked-holding, US conftest 브로커 env, US 브로커 테스트 신설, monkeypatch 우회 제거 | pending | - | 2 | - |
| 6 | 드리프트 청소 + 최종 보고 | oneil_fallback 단일화, 상수/URL/KST 통합, KisBroker 결정, 모델명 중앙화(could), 최종 감사 보고서 + CLAUDE.md 갱신 | pending | - | 3, 4, 5 | - |

### Phase Details

**Phase 1: 감사 체계 강화**
- **Goal**: 알려진 결함 전부를 실패하는 테스트/감사 스크립트로 고정 — 이후 수정의 검증 기준 확립
- **Scope**: ① `test_no_module_scope_kis_import.py` 확장: `spec_from_file_location`/`importlib` 경로 로드 AST 탐지, prism-us 6개 진입점 census 추가, `sys.modules` 별칭 검사, `kis_devlp.yaml` 직접 open 스캔 ② KIS 응답 형태(`rt_cd`/`output`/`msg1`/`ORD_DVSN`) 어댑터 외부 누출 스캔 ③ BrokerPort 계약: 트레이더 객체에 대한 호출 메서드 전수 대조 (fill_chaser의 `get_revisable_orders`/`get_unfilled_orders`, us_tracking의 `is_market_open` 등) ④ 설정 키 생존성 테스트: `toss_config.yaml`의 각 키가 실제 코드 경로에서 읽히는지 ⑤ `prism-us/tests/conftest.py` 브로커 env 초기화 ⑥ 감사 보고서 초판(`.claude/PRPs/reports/full-migration-audit-report.md`) — 4개 조사 결과 전체를 심각도별 정리
- **Success signal**: 신규 테스트가 알려진 결함 위치에서 정확히 실패(xfail 마킹으로 CI는 green), 보고서에 전 항목 file:line 수록

**Phase 2: P0 실금전 수정**
- **Goal**: Toss 실계좌 운영의 즉시 위험 제거
- **Scope**: ① `factory.py:107` → `settings.buy_amount()` 체인 연결 (env → 브로커 설정 파일 → 기본값) + Toss 계좌 dict에 `buy_amount_krw/usd` 채움 (`stock_tracking_agent.py:520`, `us_stock_tracking_agent.py:854`) ② `cores/corporate_status.py` 브로커 인식화 (Toss 지원 또는 명시적 미지원 로그 — 조용한 실패 금지) ③ `compute_us_fractional_sell_quantity` Decimal화 + `us_stock_tracking_agent.py:2802` 미정의 메서드 호출 수정 (BrokerPort에 `is_market_open`/`is_reserved_order_available` 추가 또는 명시적 게이트) ④ DB 경로 단일 상수화 (상대경로 그룹 10곳 → PROJECT_ROOT 방식) ⑤ `us_stock_tracking_agent.py:195-199` 지연 로드 전환 + test_issue_448 monkeypatch 우회 제거 ⑥ `toss/dryrun.py:48` DB 경로 절대화
- **Success signal**: Phase 1의 해당 xfail 테스트들이 pass로 전환, demo 드라이런에서 매수 금액이 `toss_config.yaml` 값으로 로그 확인

**Phase 3: KIS 잔재 제거 (KR)**
- **Goal**: Toss 경로에서 KIS에 닿는 코드 0 (정당한 KIS 브로커 경로 제외)
- **Scope**: `examples/generate_dashboard_json.py:74-80`(mode를 `trading_settings()`로), `examples/generate_us_dashboard_json.py:165`(KIS_US_AVAILABLE 게이트 제거), `stance_mark.py:40-41`(사문 임포트 삭제), `tools/check_kr_pending_readiness.py`(브로커 게이트), `cores/archive/data_enricher.py`(브로커 인식 또는 명시적 KIS 전용 선언), `trigger_batch.py:282`(KIS 스냅샷 시도를 브로커/소스 설정으로 게이트), messaging 구독자 2종 처리(수정 vs deprecated 명시 — open question 해소)
- **Success signal**: 확장된 census가 전 진입점에서 KIS 모듈/설정 파일 미도달 확인

**Phase 4: 데이터 소스 정리**
- **Goal**: 로그인 KRX 의존 제거, 소스 체인을 단일 관문으로
- **Scope**: ① `krx_openapi` 소스를 `_BUILDERS`에 등록, 기본 순서를 로그인 없는 조합으로 재정의 ② 항목별 대체 매핑표 작성(수급/섹터/펀더멘털 등 OpenAPI 미지원 항목의 소스 확정) ③ 로그인 클라이언트 직접 의존 이전: `trigger_batch.py`(모듈 스코프 임포트 포함), `tracking/helpers.py`, `tracking/compression.py`, `weekly_insight_report.py`, `weekly_market_facts.py`(KR 팩트 블록 전체), `performance_tracker_batch.py`, `stock_tracking_enhanced_agent.py`(3곳), `update_stock_data.py`, `events/jeoningu_price_fetcher.py`, `utils/` 2종, pykrx 직접 호출 잔여(`cores/stock_chart.py:1462` 등) ④ 네이버 공용 클라이언트 모듈 (URL/헤더/파싱 4곳 통합) ⑤ `.env.example`(KRX_ID "필수" 표기 제거)·`docs/SETUP.md`(폐기된 PyPI 서버 안내) 갱신
- **Success signal**: 감사 스크립트에서 운영 진입점의 `krx_data_client` 도달 경로 0, KRX 차단 시뮬레이션 하네스(`tools/verify_batch_survives_krx_outage.py`) 통과

**Phase 5: KR/US 대칭화**
- **Goal**: 한쪽에만 적용된 수정/패턴 해소, US 테스트 안전망 구축
- **Scope**: ① v2.21.2 잔여 US 반영(availability 게이트, trading_settings) ② `prism-us/trading/us_stock_trading.py`에 `_request_with_retry`(EGW00215) 및 `get_holding_quantity_checked`(또는 명시적 UNKNOWN 계약) ③ `_safe_float` KR 추가/`_safe_int` 구현 통일 ④ US 가격 조회에 브로커 티어 추가(`tracking/helpers.py` 패턴 미러) ⑤ `prism-us/tests`에 브로커 선택/Toss 경로 테스트 신설 ⑥ `PRISM_TRADING_MODE`의 US pending-order 경로 처리 검토
- **Success signal**: 비대칭 표의 [US-MISSING]/[DIVERGED] 항목이 [SYMMETRIC] 또는 "의도된 차이(문서화)"로 전환

**Phase 6: 드리프트 청소 + 최종 보고**
- **Goal**: 중복 상수 재발 원인 제거, 감사 종결
- **Scope**: ① `oneil_fallback.py` 공유 모듈화(위치는 open question 해소 후) ② 스크리닝 상수 6종·`MAX_SLOTS`(LLM 프롬프트 문자열 포함)·Toss base URL·Telegram URL·KST 정의 단일화 ③ `KisBroker` 어댑터 사용 여부 결정 및 반영 ④ (could) 모델명 중앙화 ⑤ 최종 감사 보고서 완성 + CLAUDE.md/버전 히스토리 갱신
- **Success signal**: 중복 상수 스캔 재실행 시 [DRIFT-RISK] 핵심 항목 해소, 보고서에 전 항목 처리 결과(fixed/deferred/wont-fix) 기록

### Parallelism Notes

Phase 3(KIS 잔재)과 Phase 4(데이터 소스)는 파일 집합이 대부분 분리되어 병렬 가능 (공통 파일은 `trigger_batch.py` 하나 — KIS 스냅샷 게이트는 3, 로그인 클라이언트 이전은 4로 분담하되 순서 조율). Phase 5는 Phase 2의 BrokerPort 계약 확정에 의존하므로 순차. Phase 6은 전 단계의 결정(공유 모듈 위치, KisBroker 방향)을 소비하므로 마지막.

---

## Decisions Log

| Decision | Choice | Alternatives | Rationale |
|----------|--------|--------------|-----------|
| 진행 순서 | 탐지 체계 먼저, 수정은 그 다음 | 위험순 즉시 수정 / 영역별 순차 | 사용자 선택. 결함을 테스트로 고정하면 수정 검증이 자동화되고, P0가 Phase 2로 바로 이어져 위험 노출 기간도 짧음 |
| KRX 로그인 | 차단 상태로 가정, 이전 우선순위 상향 | 정상 동작 가정 | 사용자 확인 (운영 서버에서 차단/실패) |
| 점검 범위 | KR+US 전체, 제외 없음 | KR만 / 매매 경로만 | 사용자 선택. 단 US 데이터 체인화 구현만 후속 PRD로 분리 |
| messaging 구독자 | Phase 3에서 수정 vs deprecated 결정 | 즉시 수정 | 현재 미운영 확인되어 P0 아님 |
| 원본 구조 유지 | 기존 팩토리/체인/설정 단일 지점으로 호출 수렴, 신규 추상화 금지 | 재설계 | 사용자의 일관된 원칙 (호출측 무수정, 자연스러운 확장) |

---

## Research Summary

**Market Context**
해당 없음 — 내부 코드베이스 감사. 근거는 전부 1차 코드 조사.

**Technical Context**
2026-08-18 병렬 조사 4건 (Explore 에이전트):
1. **KIS 직접 의존**: Toss 경로 우회 8곳+, 트립와이어 사각지대 7종 상세 (file:line 전체는 감사 보고서 초판에 수록 예정)
2. **데이터 소스**: 체인 계층 인벤토리(krx/fdr/naver/kis/toss 소스별 커버리지 표), 로그인 의존 call site 전수, 네이버 우회 4곳, 마이그레이션 git 히스토리 3파(2026-07-22 네이버 폴백 → 08-04 IP 차단·체인·OpenAPI → 08-06 네이버 체인 소스 → 08-17 Toss)
3. **하드코딩**: 카테고리 6종(경로/URL/매매 파라미터/브로커 가정/모델명/중복 상수), 매매 경로 우선순위 6건
4. **KR/US 비대칭**: 25개 관심사 대조표, [US-MISSING] 9건·[DIVERGED] 5건, 테스트 인벤토리(KR 브로커 테스트 5,546줄 vs US 0줄)

관련 선행 문서: `.claude/PRPs/prds/kospi-kosdaq-mcp-deauth.prd.md`, `tasks/plan-a-krx-exit-design.md`, `docs/TOSS_BROKER_SETUP.md`

---

*Generated: 2026-08-18*
*Status: DRAFT - needs validation*

# 토스 전용 설치에서 KIS 없이 기동하기

> **범위 제약**: KIS 사용자는 아무것도 바뀌지 않아야 한다. 마이그레이션 요구 금지.

---

## Problem Statement

`PRISM_BROKER=toss`로 설정해도 여러 진입점이 **임포트 시점에 KIS 설정 파일을 읽습니다.** 토스만 쓰려는 사용자는 `kis_devlp.yaml`이 없으면 **메인 매매 루프가 아예 기동하지 않습니다.** 브로커를 설정 한 줄로 고를 수 있다고 문서에 적어놨는데, 실제로는 쓰지도 않을 증권사의 설정 파일을 만들어야 합니다.

## Evidence

**실측 (2026-08-18). `kis_devlp.yaml`을 치우고 `PRISM_BROKER=toss`로 임포트:**

```
stock_tracking_agent                 CRASH FileNotFoundError   ← 메인 매매 루프
trading.portfolio_telegram_reporter  CRASH FileNotFoundError   ← 텔레그램 잔고 리포트
examples.generate_dashboard_json     CRASH FileNotFoundError   ← 대시보드 데이터

stance_server · trigger_batch · execution_service · brokers.factory · market_data  → OK
```

**정확한 지점:**

```
stock_tracking_agent.py:104   from trading import kis_auth as ka
                              → trading/kis_auth.py:118
                                with open(.../kis_devlp.yaml) as f:   ← 모듈 스코프
                                    _cfg = yaml.safe_load(f)
```

`PRISM_BROKER=toss` 상태에서도 `trading.portfolio_telegram_reporter`는 `kis_auth`를 `sys.modules`에 올립니다(실측 확인).

> 이 세션에서 제가 테스트 수집용으로 `kis_devlp.yaml` 플레이스홀더를 만들어 둔 탓에 지금 이 머신에서는 증상이 가려져 있습니다. 신규 토스 전용 사용자는 첫 기동에서 막힙니다.

## 원인이 두 갈래라는 점이 중요합니다

**① 모듈 스코프 임포트** — `kis_auth.py:118`이 임포트 시점에 파일을 읽으므로, `kis_auth`나 `domestic_stock_trading`을 모듈 스코프에서 당기는 곳은 브로커 설정과 무관하게 전부 감염됩니다.

**② `kis_devlp.yaml`이 KIS 전용이 아님** — 안에 브로커 중립 설정이 들어 있고, 코드가 그것을 읽습니다:

```
trading/domestic_stock_trading.py:104   DEFAULT_BUY_AMOUNT = _cfg["default_unit_amount"]
trading/domestic_stock_trading.py:106   AUTO_TRADING       = _cfg["auto_trading"]
trading/domestic_stock_trading.py:108   DEFAULT_MODE       = _cfg["default_mode"]
examples/generate_dashboard_json.py:73  CONFIG_FILE = .../kis_devlp.yaml   (직접 읽음)
```

즉 **KIS를 쓰지 않아도 KIS 파일이 있어야 하는 구조**입니다. ①만 고치면 ② 때문에 여전히 파일이 필요합니다.

## Proposed Solution

두 갈래를 각각 끊습니다.

**①**: KIS 트레이딩 모듈을 모듈 스코프에서 임포트하지 않도록 바꿉니다 — 이미 `trading/brokers/factory.py`가 쓰고 있는 지연 임포트 패턴을 그대로 따릅니다(그래서 `factory`와 `execution_service`는 지금도 KIS 없이 임포트됩니다).

**②**: **매매 설정을 브로커 설정 파일이 각자 갖게** 합니다. `toss_config.yaml`에 `default_unit_amount`·`auto_trading`·`default_mode`를 추가하고, `PRISM_BROKER=toss`일 때는 거기서 읽습니다. KIS는 계속 `kis_devlp.yaml`에서 읽으므로 **기존 사용자는 파일도 값도 그대로**입니다.

공통 파일로 쪼개는 안(예: `trading.yaml` 신설)은 기각합니다. 기존 KIS 사용자 전원에게 마이그레이션을 요구하고, "설정이 어디 있는지"가 두 곳으로 늘어납니다.

## Key Hypothesis

We believe **KIS 임포트를 지연시키고 매매 설정을 브로커별 파일로 옮기는 것**이 **토스 전용 설치가 기동조차 못 하는 문제**를 **KIS 계좌가 없는 운영자**에게 해결해 줄 것이다.

We'll know we're right when **`kis_devlp.yaml`이 전혀 없는 상태에서 모든 진입점이 임포트되고 매매 루프가 돌며, KIS 테스트 99/99가 유지될 때**.

## What We're NOT Building

- **`kis_devlp.yaml` 스키마 변경** — KIS 사용자 마이그레이션을 만들지 않는다
- **공통 설정 파일 신설** — 위와 같은 이유
- **`kis_auth.py`의 모듈 스코프 로딩 자체를 리팩터링** — KIS 경로의 동작을 바꿀 위험. 호출측이 지연 임포트하면 충분하다
- **KIS를 안 쓰는 사용자를 위한 `kis_auth` 기능 제거** — KIS 사용자가 그대로 써야 한다
- **US(`prism-us`) 진입점** — `us_stock_trading.py:46`도 같은 파일을 읽지만, US 토스 경로는 별도 확인이 필요하다 (Open Question)

## Success Metrics

| Metric | Target | How Measured |
|--------|--------|--------------|
| 토스 전용 기동 | `kis_devlp.yaml` 없이 **진입점 8/8 임포트 성공** | 파일을 치우고 각 모듈 임포트 |
| KIS 회귀 | **99/99 유지** | 기존 KIS 테스트 7종 |
| KIS 설정 무변경 | `kis_devlp.yaml` 스키마·값 **변경 0** | diff |
| 매매 설정 출처 | `PRISM_BROKER=toss`일 때 `toss_config.yaml`에서 읽음 | 단위 테스트 |
| 재발 방지 | 모듈 스코프 KIS 임포트를 막는 tripwire 테스트 존재 | 테스트 |

## Open Questions

**해결됨 (구현 중 실측으로 답이 남)**

- [x] **진입점 표본으로 충분한가?** — **아니다.** Phase 3에서 규칙(AST + 임포트 인구조사)을 켜자마자 표본이 놓친 크래셔 2개가 나왔다: `weekly_insight_report.py:20`, `examples/generate_us_dashboard_json.py:90`. 표본으로는 못 잡는다는 근거가 그 자체로 나왔다.
- [x] **`default_unit_amount_usd`를 `toss_config.yaml`로?** — **옮겼다.** Phase 1에서 `default_unit_amount`·`auto_trading`·`default_mode`와 함께 들어갔고, 우선순위는 환경변수 > 파일 > 코드 기본값.
- [x] **`db_schema.py:326` 메시지를 브로커별로?** — **그렇다. 다만 메시지만의 문제가 아니었다.** 구 스키마 DB를 들고 갈아탄 설치는 `RuntimeError`로 **기동이 막혔다**(신규 설치는 마이그레이션을 건너뛰므로 무증상 — 그래서 인구조사에 안 잡혔다). `settings.primary_account_scope()`가 계좌 스코프를 브로커에서 가져오고, `broker_config_hint()`가 안내 문구 4곳을 대체한다.
- [x] **`prism-us` 진입점** — **부분 편입.** `prism-us/tracking/db_schema.py`는 브로커 인식으로 전환했다(루트 `trading` 패키지가 가려지므로 `settings`를 경로로 로드하고 `kis_auth` 로더를 주입). `prism-us/trading/us_stock_trading.py`와 `us_pending_order_batch.py`는 **KIS 전용 코드로 확정**하고 tripwire allowlist에 넣었다 — 토스 설치는 팩토리가 이들을 고르지 않으므로 로드하지 않는다.

**미해결**

- [ ] `accounts:` 다중 계좌는 KIS 고유 개념이다. 토스는 `account_seq` 하나뿐인데 `MultiAccount*` 경로가 토스에서 어떻게 동작해야 하는가? **여전히 미검증.** 이번 작업은 마이그레이션이 쓰는 *단일* 주계좌 스코프만 다뤘다.
- [ ] 실주문 미검증 — 토스에 모의투자 서버가 없어 검증 자체가 실거래다.
- [ ] `us_stock_holdings`에 수량 컬럼이 없다("1행 ≈ 1주"). 소수점·부분 매도 시 DB와 브로커가 어긋날 수 있다 → Issue #5.

---

## Users & Context

**Primary User**
- **Who**: KIS 계좌가 **없는** 운영자. 토스만으로 PRISM을 돌리려는 사람 — 이 기능이 존재하는 이유 그 자체
- **Current behavior**: 가이드대로 `PRISM_BROKER=toss`를 설정하고 기동했다가 `FileNotFoundError: .../kis_devlp.yaml`로 막힌다. 원인이 자기 설정과 무관해 보여 디버깅이 길어진다
- **Trigger**: 신규 설치 직후 첫 실행. 또는 KIS에서 토스로 옮기며 KIS 설정을 지웠을 때
- **Success state**: 토스 자격증명만 넣으면 전부 동작한다

**Job to Be Done**
When **토스 계좌만 가지고 PRISM을 처음 돌릴 때**, I want to **쓰지도 않을 증권사의 설정 파일 없이 기동하기를**, so I can **문서가 약속한 대로 설정 한 줄로 브로커를 고를 수 있다**.

**Non-Users**
- KIS 사용자 — 이번 변경의 영향을 받아서는 안 된다. 파일도 값도 그대로
- KIS·토스를 함께 쓰는 사용자 — 설치 단위 전역 선택이므로 해당 없음

---

## Solution Detail

### Core Capabilities (MoSCoW)

| Priority | Capability | Rationale |
|----------|------------|-----------|
| Must | KIS 트레이딩 모듈의 모듈 스코프 임포트 제거 (진입점 3곳) | 크래시의 직접 원인 |
| Must | `toss_config.yaml`이 매매 설정을 갖도록 확장 | ①만 고치면 ② 때문에 여전히 파일이 필요 |
| Must | `PRISM_BROKER`에 따라 설정 출처를 고르는 로더 | 두 파일을 잇는 지점 |
| Must | KIS 무변경 회귀 고정 | 범위 제약 |
| Must | 모듈 스코프 KIS 임포트 tripwire | 재발 방지 — 없어서 생긴 문제다 |
| Should | `kis_devlp.yaml` 부재 시 오류 메시지 개선 | 토스 사용자에게 KIS 파일을 안내하지 않도록 |
| Could | `prism-us` 진입점까지 확대 | Open Question |
| Won't | 공통 설정 파일 신설 | 기존 사용자 마이그레이션 발생 |
| Won't | `kis_auth.py` 내부 리팩터링 | KIS 동작 변경 위험 |

### MVP Scope

`kis_devlp.yaml` 없이 **크래시하는 3개 진입점이 임포트되고 동작**하면 가설이 검증된다:

1. 세 진입점의 KIS 임포트를 지연시킨다
2. `toss_config.yaml`에 매매 설정을 추가하고 브로커별로 읽는다
3. 파일을 치운 상태에서 기동 확인 + KIS 99/99 유지

### User Flow

```
[현재]
PRISM_BROKER=toss + toss_config.yaml  →  python stock_tracking_agent.py
                                          → FileNotFoundError: kis_devlp.yaml
                                          → 쓰지도 않는 증권사 설정 파일을 만들어야 함

[수정 후]
PRISM_BROKER=toss + toss_config.yaml  →  정상 기동
                                          매매 설정(금액·모드)도 toss_config.yaml 에서 읽음
```

---

## Technical Approach

**Feasibility**: **HIGH** — 고칠 패턴이 이미 저장소 안에 있다

**Architecture Notes**

- **지연 임포트 패턴은 이미 검증됐다.** `trading/brokers/factory.py`가 함수 내부에서 `domestic_stock_trading`을 임포트하며, 그 덕분에 `factory`와 `execution_service`는 지금도 KIS 없이 임포트된다(실측). 같은 방식을 세 진입점에 적용한다
- **설정 로더는 `trading/brokers/settings.py`에 둔다.** 이미 `selected_broker()`·`load_toss_config()`가 있는 곳이라 자연스럽다. `trading_settings()` 같은 함수가 브로커에 따라 출처를 고른다
- **KIS 경로는 손대지 않는다.** `PRISM_BROKER`가 kis(기본)면 지금과 완전히 동일한 코드가 돈다
- **`kis_devlp.yaml`의 값은 그대로 둔다.** 토스 설정에 같은 키를 추가할 뿐이다

**Technical Risks**

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| 지연 임포트가 순환 임포트를 만든다 | M | 함수 내부 임포트라 모듈 로드 순서에 영향 없음. `factory.py`에서 이미 검증됨 |
| 내가 고른 진입점 8개가 전수가 아니다 | **H** | tripwire 테스트로 모듈 스코프 KIS 임포트를 금지. 표본이 아니라 규칙으로 막는다 |
| KIS 사용자의 설정이 바뀐다 | M | `kis_devlp.yaml` diff 0을 성공 지표로 고정 |
| 두 설정 파일의 기본값이 어긋난다 | M | 기본값을 한 곳(`settings.py`)에 두고 파일은 덮어쓰기만 |
| `prism-us`도 같은 문제 | **H** | Open Question. 이번 범위 밖이면 문서에 명시 |

---

## Implementation Phases

| # | Phase | Description | Status | Parallel | Depends | PRP Plan |
|---|-------|-------------|--------|----------|---------|----------|
| 1 | 브로커별 매매 설정 로더 | `toss_config.yaml`에 매매 설정 추가, `settings.trading_settings()` | complete | with 2 | - | [plan](../plans/completed/broker-trading-settings.plan.md) |
| 2 | 진입점 지연 임포트 | 크래시하는 3곳의 KIS 임포트를 함수 내부로 | complete | with 1 | - | `1f6be49` |
| 3 | tripwire + 회귀 고정 | 모듈 스코프 KIS 임포트 금지 테스트, KIS 99/99 | complete | - | 1, 2 | `9fab29c` |
| 4 | 오류 메시지·문서 | 토스 사용자에게 KIS 파일을 안내하지 않도록, 가이드 갱신 | complete | - | 3 | `6b64ab4`, `211cd7c` |

### Phase Details

**Phase 1: 브로커별 매매 설정 로더**
- **Goal**: 매매 설정이 KIS 파일에 묶여 있지 않게
- **Scope**: `toss_config.yaml.example`에 `default_unit_amount`·`auto_trading`·`default_mode` 추가, `settings.py`에 브로커별 로더, 기본값은 코드에
- **Success signal**: `PRISM_BROKER=toss`에서 `kis_devlp.yaml` 없이 매매 설정을 읽는다

**Phase 2: 진입점 지연 임포트**
- **Goal**: 크래시 제거
- **Scope**: `stock_tracking_agent.py:104`, `trading/portfolio_telegram_reporter.py`, `examples/generate_dashboard_json.py:73,83`
- **Success signal**: `kis_devlp.yaml` 없이 세 진입점이 임포트된다

**Phase 3: tripwire + 회귀 고정**
- **Goal**: 표본이 아니라 규칙으로 막는다
- **Scope**: 프로덕션 코드가 `kis_auth`/`domestic_stock_trading`을 모듈 스코프에서 임포트하지 않음을 검사(allowlist는 KIS 전용 모듈). `kis_devlp.yaml` 부재 시 전 진입점 임포트 테스트
- **Success signal**: KIS 99/99 유지, 새 위반이 생기면 테스트가 실패

**Phase 4: 오류 메시지·문서**
- **Goal**: 남은 안내를 브로커에 맞게
- **Scope**: `tracking/db_schema.py:326` 등 KIS 파일을 안내하는 메시지, `docs/TOSS_BROKER_SETUP.md`
- **Success signal**: 토스 사용자가 KIS 설정을 만들라는 안내를 받지 않는다

### Parallelism Notes

- **Phase 1 ∥ 2**: 설정 로더와 임포트 위치는 서로 독립적이다. 다만 **둘 다 끝나야** `kis_devlp.yaml` 없는 기동이 성립한다 — 2만 하면 설정을 못 읽고, 1만 하면 임포트에서 죽는다
- **Phase 3은 1·2 이후**: 고친 뒤에 규칙으로 고정해야 의미가 있다

---

## Decisions Log

| Decision | Choice | Alternatives | Rationale |
|----------|--------|--------------|-----------|
| 목표 수준 | `kis_devlp.yaml` 없이 완전 기동 | 네트워크 접근만 차단, 문서화만 | 사용자 확정. 파일을 요구하는 한 "브로커 선택"이 반쪽이다 |
| 매매 설정 위치 | **각 브로커 설정 파일이 자기 것을 가짐** | 공통 파일 신설, 환경변수 대체 | 사용자 확정. KIS 사용자 마이그레이션이 없고, 설정이 브로커 옆에 있어 찾기 쉽다 |
| 임포트 방식 | 함수 내부 지연 임포트 | `kis_auth` 리팩터링, 조건부 임포트 | `factory.py`에서 이미 검증된 패턴. KIS 동작을 건드리지 않는다 |
| 재발 방지 | tripwire 테스트 | 코드 리뷰 규칙 | 진입점 전수 조사는 표본을 놓친다. 규칙으로 막는다 |

---

## Research Summary

**측정 결과 (2026-08-18)**

`kis_devlp.yaml` 부재 + `PRISM_BROKER=toss`:

| 진입점 | 결과 |
|---|---|
| `stock_tracking_agent` | ❌ CRASH — `:104 from trading import kis_auth as ka` |
| `trading.portfolio_telegram_reporter` | ❌ CRASH (`PRISM_BROKER=toss`에서도 `kis_auth` 로드 확인) |
| `examples.generate_dashboard_json` | ❌ CRASH — `:73` 직접 읽기 + `:83` 모듈 스코프 임포트 |
| `stance_server` · `trigger_batch` · `execution_service` · `brokers.factory` · `cores.market_data` | ✅ OK |

**코드베이스**

- `trading/kis_auth.py:118` — 모듈 스코프 `with open(kis_devlp.yaml)`. 감염의 근원
- `trading/domestic_stock_trading.py:25` — 모듈 스코프 `import kis_auth as ka`
- `trading/domestic_stock_trading.py:104,106,108` — `default_unit_amount`·`auto_trading`·`default_mode`를 `_cfg`에서 읽음. **브로커 중립 설정이 KIS 파일에 있다**
- `examples/generate_dashboard_json.py:73` — `kis_devlp.yaml`을 직접 읽음
- `trading/brokers/factory.py` — **지연 임포트로 이 문제를 이미 피하고 있다.** 따라 할 패턴
- `prism-us/trading/us_stock_trading.py:46` — 같은 파일을 읽음 (범위 밖, Open Question)

---

*Generated: 2026-08-18*
*Status: DRAFT — `prism-us` 범위 결정 필요*

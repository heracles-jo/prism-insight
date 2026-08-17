# 토스증권 브로커 연동 가이드

> PRISM-INSIGHT는 v2.21.0부터 매매 실행 증권사를 **설정 한 줄로** 고를 수 있습니다.
> 기존 설치는 아무것도 바꾸지 않아도 그대로 한국투자증권(KIS)으로 동작합니다.

---

## ⚠️ 시작하기 전에 반드시 읽어야 할 것

**토스증권 Open API에는 모의투자 환경이 없습니다.**

KIS는 모의투자 서버(`openapivts`)를 제공하지만 토스증권은 제공하지 않습니다. 즉 토스 자격증명은
**항상 실계좌·실제 돈에 연결됩니다.**

그래서 PRISM은 `PRISM_TRADING_MODE=demo`일 때 **로컬 dry-run 시뮬레이터**를 대신 세웁니다.

| | 주문 API 호출 | 시세 | 체결 |
|---|---|---|---|
| `demo` (기본) | ❌ 호출 안 함 | ✅ 실제 시세 | 로컬 원장에 가상 기록 |
| `real` | ✅ 실제 주문 | ✅ 실제 시세 | 실제 체결 · **실제 돈** |

`PRISM_TRADING_MODE`는 미설정이거나 인식할 수 없는 값이면 **`demo`로 떨어집니다.** 오타가
실제 주문을 승인하는 일이 없도록 한 안전장치입니다. `real`로 실행하면 기동 시 경고 한 줄이 남습니다.

---

## 1. 자격증명 발급

1. 토스증권 **WTS** 접속 → **설정 > Open API**
2. 클라이언트 등록 → `client_id` / `client_secret` 발급
3. 같은 화면의 **"허용 IP 관리"**에서 이 서버의 공인 IP 등록

> ### 🚨 3번을 빠뜨리면 모든 요청이 403으로 실패합니다
>
> 토스는 등록되지 않은 IP에서 온 요청을 전부 차단하며, 403 응답 본문에는 원인이 IP라는
> 단서가 없습니다. PRISM은 이 경우를 감지해 조치 방법을 로그에 출력합니다.
>
> 공인 IP 확인:
> ```bash
> curl -s https://api.ipify.org
> ```
>
> 클라우드/크론에서 돌린다면 **고정 IP(NAT 게이트웨이 등)를 먼저 확보**해야 합니다.
> IP가 바뀌는 환경에서는 매번 재등록이 필요합니다.

4. `account_seq` 확인 — 주문·잔고 조회에 쓰는 계좌 일련번호입니다.
   설정을 마친 뒤 아래 검증 명령으로 `GET /api/v1/accounts`를 호출하면 확인할 수 있습니다.

---

## 2. 설정

### 방법 A — 설정 파일

```bash
cp trading/config/toss_config.yaml.example trading/config/toss_config.yaml
```

```yaml
client_id: "발급받은_클라이언트_아이디"
client_secret: "발급받은_클라이언트_시크릿"
account_seq: "계좌_일련번호"
base_url: "https://openapi.tossinvest.com"
```

### 방법 B — 환경변수 (컨테이너 권장)

환경변수가 파일보다 **우선**합니다. 디스크에 시크릿을 두지 않아도 됩니다.

```bash
TOSS_CLIENT_ID=...
TOSS_CLIENT_SECRET=...
TOSS_ACCOUNT_SEQ=...
```

### 브로커 선택

`.env`:

```bash
PRISM_BROKER=toss          # kis(기본) | toss
PRISM_TRADING_MODE=demo    # demo(기본) | real
```

> `trading/config/toss_config.yaml`과 토큰 캐시 파일은 `.gitignore`에 등록돼 있습니다.
> 편집기 스왑 파일(`.swp`)도 함께 차단됩니다 — 스왑 파일은 버퍼 내용을 그대로 담습니다.

---

## 3. 검증

```bash
python -m trading.brokers.toss.smoke
```

읽기 전용 확인만 수행하며 **주문은 절대 넣지 않습니다.** 따로 실패하는 세 가지를 나눠서 알려줍니다.

```
OK    token issued
OK    GET /api/v1/accounts → 1 item(s)
OK    GET /api/v1/holdings → keys: [...]

All checks passed.
```

| 실패 | 원인 |
|---|---|
| `FAIL token: access_denied ... IP address not allowed` | 허용 IP 미등록 (1-3번) |
| `FAIL token: invalid_client` | client_id/secret 오타 또는 클라이언트 비활성 |
| `FAIL accounts: ...` | 토큰은 정상, 계정 권한 문제 |
| `SKIP holdings` | `account_seq` 미설정 |

---

## 4. KIS와 다른 점

호출측 코드는 브로커를 몰라도 되지만, **기능 차이는 실재**하므로 알고 있어야 합니다.

| 기능 | KIS | 토스 |
|---|---|---|
| 모의투자 서버 | ✅ | ❌ → 로컬 dry-run |
| 시간 기반 예약주문 | ✅ | ❌ `BrokerUnsupported` |
| KR 종가주문 | ✅ | ❌ (`timeInForce=CLS`는 US+지정가 전용) |
| 조건주문(OCO/OTO) | ❌ | ✅ (PRISM 미사용) |
| 주문 멱등성 키 | ❌ | ✅ `clientOrderId` (10분) |
| 투자자별 매매동향 | 별도 조회 | ✅ 정식 엔드포인트 |
| KR/US API | 분리 | **통합** (심볼로 구분) |

### 예약주문이 없다는 것의 의미

토스에는 "장 열리면 주문 넣기"에 해당하는 기능이 없습니다. 가격 트리거 조건주문은 의미가
다르므로 대체하지 않았습니다. 예약주문 계열 호출은 조용히 실패하지 않고 `BrokerUnsupported`를
던집니다 — 실패 dict를 돌려주면 호출측이 무한 재시도하기 때문입니다.

### 레이트리밋 — 아침 배치와 겹칩니다

| 그룹 | 한도 |
|---|---|
| 주문 | 10 req/s → **09:00–09:10 KST에는 3 req/s** |
| 인증 | 5 req/s |
| 차트 | 20 req/s |

하필 아침 배치가 도는 창에서 가장 조입니다. PRISM은 클라이언트 측 토큰버킷으로 미리
간격을 벌리고, 429가 나면 `Retry-After`를 지키며 지터를 섞어 재시도합니다.

### 토큰은 클라이언트당 1개뿐입니다

> client 당 유효한 access token 은 1 개입니다. 재발급 시 이전에 발급된 token 은 즉시 무효화됩니다.

여러 프로세스(배치·추적 에이전트·stance 서버)가 같은 `client_id`를 공유하므로, PRISM은 토큰을
파일에 캐시하고 **스레드락 + 파일락**으로 보호합니다. 같은 자격증명으로 여러 프로세스를 돌려도
서로의 토큰을 무효화하지 않습니다. 401을 받으면 재발급은 **정확히 1회**만 시도합니다.

---

## 5. 미국 주식 — 데이마켓이 있습니다

**토스는 US를 4개 세션으로 운영하고 전부 KST로 공시합니다.**

| 세션 | KST |
|---|---|
| `dayMarket` | **09:00 – 16:50** |
| `preMarket` | 17:00 – 22:30 |
| `regularMarket` | 22:30 – 05:00 |
| `afterMarket` | 05:00 – 07:00 |

"한국 시간대에는 미국장이 닫혀 있다"는 통념은 **토스에는 해당되지 않습니다.** 한국 근무시간대에
도는 데이마켓이 있어 아침 배치도 US를 매매할 수 있습니다. 거래 가능 시간은 하루 약 22시간이고,
공백은 **07:00–09:00 KST** 뿐입니다.

세션이 모두 닫혀 있으면 주문은 **명시적 실패**로 처리됩니다(큐잉하지 않습니다 — 토스에 예약주문이
없으므로 넣어둘 곳이 없습니다). 이때 결과는 `success=False`이며 `outcome_unknown`은 **붙지 않습니다.**
확실히 전송되지 않은 주문이므로 포지션을 잠그지 않습니다.

---

## 6. 시세 소스로도 쓸 수 있습니다

매매와 별개로, 토스를 시세 폴백 체인에 넣을 수 있습니다.

```bash
PRISM_MARKET_DATA_SOURCES=toss,krx,fdr
```

기본 순서(`krx,fdr`)에는 **포함돼 있지 않습니다.** 자격증명이 필요하므로, 미설정 설치가 조회마다
실패 시도를 낭비하지 않도록 명시적 opt-in으로 두었습니다.

토스가 메우는 구멍:
- **투자자별 매매동향** — 보통 거래소만 제공하며, KRX 장애 시 차트가 비던 원인
- **종목명 조회** — KIS 소스가 제공하지 못하는 기능

지원하지 않는 것은 근사하지 않고 `Unsupported`를 던져 다음 소스로 넘깁니다.
시가총액 시계열이 대표적입니다 — 토스가 주는 `sharesOutstanding`은 **현재 값**이라
과거 종가에 곱하면 그 사이의 자사주 매입·유상증자를 없던 일로 만듭니다.

---

## 7. 문제 해결

| 증상 | 원인 / 조치 |
|---|---|
| 모든 요청 403 `access_denied` | 허용 IP 미등록. WTS > 설정 > Open API > 허용 IP 관리 |
| `invalid_client` (401) | client_id/secret 오타, 또는 클라이언트 비활성 |
| 401이 반복됨 | 다른 프로세스가 같은 `client_id`로 토큰을 재발급 중. PRISM 경로 밖에서 토큰을 발급하고 있지 않은지 확인 |
| 429가 잦음 | 09:00–09:10 창인지 확인. 주문 동시성을 낮추세요 |
| `order-hours-closed` (422) | 장 시간 밖. US는 5절의 세션표 참고 |
| `BrokerUnsupported: reserved order` | 정상 동작. 토스에 예약주문이 없습니다 |
| demo인데 실제로 주문될까 걱정 | `[TOSS_DRYRUN] simulation active` 로그를 확인하세요. 주문 엔드포인트는 HTTP 경계에서 차단되며, 인식하지 못한 쓰기 요청도 기본 차단(default-deny)입니다 |
| `PRISM_BROKER=kiwoom` 등 | 지원하지 않는 값은 조용히 넘어가지 않고 오류로 중단됩니다 |

### 테스트가 로컬 `.env`에 영향을 받는다면

일부 테스트 모듈이 임포트 시점에 `load_dotenv()`를 호출해 세션 전체 환경을 오염시킵니다.
`tests/conftest.py`가 브로커 관련 환경변수를 테스트마다 초기화하므로, 로컬 `.env`에
`PRISM_BROKER=toss`가 있어도 KIS 테스트는 정상 통과합니다.

---

## 8. 알려진 제약

- **실주문 검증 미완** — 읽기 전용 경로(인증·계좌·잔고·시세·세션)는 실 API로 확인했으나,
  실제 주문 체결까지는 검증하지 않았습니다. 토스에 모의투자가 없어 검증 자체가 실거래입니다.
- **수수료는 0으로 시뮬레이션** — dry-run은 수수료·세금을 0으로 둡니다. 토스의 실제 요율을
  추정해 넣으면 그럴듯하지만 틀린 손익이 나오므로, 눈에 보이는 0을 택했습니다.
- **소수점/금액 기반 주문 미지원** — 토스 US 전용 기능이나 PRISM 포지션 모델이 정수 수량 전제입니다.
- **`ExecutionService`를 거치지 않는 조회 4곳은 KIS 고정** — `tracking/helpers.py`,
  `cores/corporate_status.py`, `examples/messaging/` 2개. 모두 주문이 아닌 시세·상태 조회이고
  (`corporate_status`는 토스가 제공하지 않는 KIS 고유 필드 `iscd_stat_cls_code`를 읽습니다),
  실패해도 안전하게 넘어갑니다.

---

## 참고

- 토스증권 Open API 문서: https://developers.tossinvest.com/docs
- OpenAPI 스펙(정본): https://openapi.tossinvest.com/openapi-docs/latest/openapi.json

# CLAUDE.md - AI Assistant Guide for PRISM-INSIGHT

> **Version**: 2.22.0 | **Updated**: 2026-08-19

## Quick Overview

**PRISM-INSIGHT** = AI-powered Korean/US stock analysis & automated trading system

```yaml
Stack: Python 3.10+, mcp-agent, GPT-5/Claude 4.6, SQLite, Telegram, KIS API
Scale: ~75,000+ LOC, 13+ AI agents, KR/US dual market support
```

## Project Structure

```
prism-insight/
├── cores/                    # AI Analysis Engine
│   ├── agents/              # 13 specialized AI agents
│   ├── chatgpt_proxy/       # ChatGPT OAuth Proxy (Codex endpoint)
│   ├── analysis.py          # Core orchestration
│   └── report_generation.py # Report templates
├── trading/                  # Trading (KR)
│   ├── brokers/             # Broker abstraction — pick with PRISM_BROKER
│   │   ├── base.py          #   BrokerPort contract + exception types
│   │   ├── factory.py       #   the one place "which broker" is answered
│   │   ├── kis_adapter.py   #   KIS behind the contract (delegating wrapper)
│   │   └── toss/            #   Toss: auth, client, ratelimit, dryrun, adapter
│   ├── domestic_stock_trading.py  # KIS 국내주식
│   └── kis_auth.py
├── prism-us/                # US Stock Module (mirror of KR)
│   ├── cores/agents/        # US-specific agents
│   ├── trading/             # KIS Overseas API
│   └── us_stock_analysis_orchestrator.py
├── examples/                 # Dashboards, messaging
└── tests/                    # Test suite
```

## Analysis Pipeline

```
[Morning Run]
trigger_batch.py / us_trigger_batch.py
    → Surge/momentum detection → stock candidates (JSON)
    ↓
stock_analysis_orchestrator.py
    → data_prefetch (parallel data fetch)
    → cores/analysis.py — 6 analysis agents (sequential)
        Technical Analyst → Trading Flow → Financial → Industry → News → Market
    → Investment Strategist (integrates all 6 reports)
    → report_generation.py → PDF
    → telegram_summary_agent → Telegram message (Korean)
    ↓
stock_tracking_agent.py  (runs independently, cron)
    → sell_decision_agent → KIS sell order
    → buy via trigger signal → KIS buy order
```

> **Multi-account (v2.9.0)**: `stock_tracking_agent` fans out buy/sell to all accounts in `kis_devlp.yaml`. Telegram report is sent from primary account only.

---

## AI Agents

13 specialized agents organized in 4 teams. Full details → [`docs/CLAUDE_AGENTS.md`](docs/CLAUDE_AGENTS.md)

| # | Agent | File | Purpose |
|---|-------|------|---------|
| 1 | Technical Analyst | `cores/agents/stock_price_agents.py` | Price/volume, RSI, MACD, Bollinger |
| 2 | Trading Flow Analyst | `cores/agents/stock_price_agents.py` | Institutional/foreign/individual flows |
| 3 | Financial Analyst | `cores/agents/company_info_agents.py` | PER, PBR, ROE, valuation |
| 4 | Industry Analyst | `cores/agents/company_info_agents.py` | Business model, competitive position |
| 5 | News Analyst | `cores/agents/news_strategy_agents.py` | News, catalysts, disclosures |
| 6 | Market Analyst | `cores/agents/market_index_agents.py` | KOSPI/KOSDAQ, macro (result cached) |
| 7 | Investment Strategist | `cores/agents/news_strategy_agents.py` | Synthesizes 1-6 into actionable strategy |
| 8 | Macro Intelligence | `cores/agents/macro_intelligence_agent.py` | Market regime, leading/lagging sectors |
| 9 | Summary Optimizer | `cores/agents/telegram_summary_optimizer_agent.py` | Report → 400-char Telegram message |
| 10 | Quality Evaluator | `cores/agents/telegram_summary_evaluator_agent.py` | Summary QA loop until EXCELLENT |
| 11 | Translation Specialist | `cores/agents/telegram_translator_agent.py` | KR→EN/JA/ZH/ES broadcast |
| 12 | Buy Specialist | `cores/agents/trading_agents.py` | Entry decision, score threshold |
| 13 | Sell Specialist | `cores/agents/trading_agents.py` | Hold/sell decision, stop-loss |

> US agents mirror KR under `prism-us/cores/agents/` (no Macro Intelligence, Trading Journal, Translation agents).

---

## Key Entry Points

| Command | Purpose |
|---------|---------|
| `python stock_analysis_orchestrator.py --mode morning` | KR morning analysis |
| `python stock_analysis_orchestrator.py --mode morning --no-telegram` | Local test (no Telegram) |
| `PRISM_OPENAI_AUTH_MODE=chatgpt_oauth python stock_analysis_orchestrator.py --mode morning` | ChatGPT OAuth proxy mode |
| `python prism-us/us_stock_analysis_orchestrator.py --mode morning` | US morning analysis |
| `python trigger_batch.py morning INFO` | KR surge detection only |
| `python prism-us/us_trigger_batch.py morning INFO` | US surge detection only |
| `python demo.py 005930` | Single stock report (KR) |
| `python demo.py AAPL --market us` | Single stock report (US) |
| `python prism-us/us_pending_order_batch.py` | US pending order batch (10:05 KST cron) |
| `python prism-us/us_pending_order_batch.py --dry-run` | US pending order dry run |
| `python weekly_insight_report.py --dry-run` | Weekly insight report (print only) |
| `python weekly_insight_report.py --broadcast-languages en,ja` | Weekly report + broadcast |

## Configuration Files

| File | Purpose |
|------|---------|
| `.env` | Telegram tokens, channel IDs, Redis/GCP settings, `PRISM_OPENAI_AUTH_MODE` |
| `mcp_agent.secrets.yaml` | API keys (OpenAI, Anthropic, Firecrawl, etc.) |
| `mcp_agent.config.yaml` | MCP server configuration |
| `trading/config/kis_devlp.yaml` | KIS trading API credentials + trading settings (only when `PRISM_BROKER=kis`) |
| `trading/config/toss_config.yaml` | Toss trading API credentials + trading settings (only when `PRISM_BROKER=toss`) |

**Setup**: Copy `*.example` files and fill in credentials.

### Key Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | ✅ | Telegram bot token |
| `TELEGRAM_CHANNEL_ID` | ✅ | KR channel ID. Bot must be a channel **administrator** with "Post Messages" |
| `PRISM_OPENAI_AUTH_MODE` | ✅ | `api_key` (default) or `chatgpt_oauth` |
| `PRISM_BROKER` | ⬜ | `kis` (default) or `toss`. Installation-wide; unset = unchanged behaviour |
| `PRISM_TRADING_MODE` | ⬜ | `demo` (default) or `real`. Unrecognised values fall back to `demo` |
| `TOSS_CLIENT_ID` / `TOSS_CLIENT_SECRET` / `TOSS_ACCOUNT_SEQ` | ⬜ | Toss credentials; override `toss_config.yaml` |
| `PRISM_MARKET_DATA_SOURCES` | ⬜ | Source chain order, e.g. `toss,krx,fdr`. Default `krx,fdr` |
| `ADANOS_API_KEY` | ⬜ | US social sentiment (Adanos). Omit to disable |
| `ENABLE_TRADING_JOURNAL` | ⬜ | `true` to enable trading journal agent |
| `GCP_CREDENTIALS_PATH` | ⬜ | GCP service account JSON for Pub/Sub |

### Broker Selection (v2.21.0)

```bash
PRISM_BROKER=toss          # kis (default) | toss
PRISM_TRADING_MODE=demo    # demo (default) | real
```

> ⚠️ **Toss has no paper-trading server.** Its credentials are always live money.
> `demo` therefore runs a local dry-run simulator that blocks order endpoints at the
> HTTP boundary while using real market data. An unrecognised `PRISM_TRADING_MODE`
> resolves to `demo` so a typo cannot authorise a real order.
>
> Toss also requires the server's public IP to be registered (WTS > 설정 > Open API >
> 허용 IP 관리) — every request 403s otherwise.
>
> A Toss install needs **no `kis_devlp.yaml`** (v2.21.2). Trading settings live in each
> broker's own config file, and every entry point reaches KIS lazily. The rule is pinned
> by `tests/test_no_module_scope_kis_import.py` — add a module-scope KIS import and it
> fails, naming the file and line.
>
> Full guide → [`docs/TOSS_BROKER_SETUP.md`](docs/TOSS_BROKER_SETUP.md)
> Verify with `python -m trading.brokers.toss.smoke` (read-only; places no order).

### Multi-Account Setup (v2.9.0)

```yaml
# trading/config/kis_devlp.yaml
accounts:
  - id: primary       # Telegram reports use this account
    app_key: ...
    app_secret: ...
    account_no: XXXXXXXX-XX
  - id: secondary
    app_key: ...
    app_secret: ...
    account_no: YYYYYYYY-YY
```

> DB migration (`account_id` column) runs automatically on first start.

## Code Conventions

### Async Pattern (Required)
```python
# ✅ Correct
async with AsyncTradingContext(mode="demo") as trader:
    result = await trader.async_buy_stock(ticker)

# ❌ Wrong - blocks event loop
result = requests.get(url)  # Use aiohttp instead
```

### Safe Type Conversion (v2.2 - KIS API)
```python
# KIS API may return '' instead of 0 - always use safe helpers
from trading.us_stock_trading import _safe_float, _safe_int
price = _safe_float(data.get('last'))  # Handles '', None, invalid strings
```

### Korean Report Tone (v2.3.0)
All Korean (ko) report sections must use formal polite style (합쇼체):
```python
# ✅ Correct - 높임말
"상승세를 보이고 있습니다"
"주목할 필요가 있습니다"

# ❌ Wrong - 반말
"상승세를 보인다"
"주목할 필요가 있다"
```
Rule is enforced in `cores/report_generation.py` (common prompts) and each agent's instruction.

### Sequential Agent Execution
```python
# ✅ Correct - respects rate limits
for section in sections:
    report = await generate_report(agent, section)

# ❌ Wrong - hits rate limits
reports = await asyncio.gather(*[generate_report(a, s) for s in sections])
```

## Trading Constraints

```python
MAX_SLOTS = 10              # Max stocks to hold
MAX_SAME_SECTOR = 3         # Max per sector
DEFAULT_MODE = "demo"       # Always default to demo

# Stop Loss (Trigger-based)
TRIGGER_CRITERIA = {
    "intraday_surge": {"sl_max": 0.05},  # -5%
    "volume_surge": {"sl_max": 0.07},    # -7%
    "default": {"sl_max": 0.07}          # -7%
}
```

## KR vs US Differences

| Item | KR | US |
|------|----|----|
| Data Source | pykrx, kospi_kosdaq MCP | yfinance, sec-edgar MCP |
| Market Hours | 09:00-15:30 KST | 09:30-16:00 EST |
| Market Cap Filter | 5000억 KRW | $20B USD |
| DB Tables | `stock_holdings` | `us_stock_holdings` |
| Trading API | KIS 국내주식 / Toss 통합 | KIS 해외주식 (예약주문 지원) / Toss 통합 |

> Toss serves KR and US through **one** API, distinguishing market by symbol form
> (KRX = 6 digits, US = ticker). One adapter covers both; the market is bound per instance.

## US Reserved Orders (Important)

### KIS

US market operates on different timezone. When market is closed:
- **Buy**: Requires `limit_price` for reserved order
- **Sell**: Can use `limit_price` or `use_moo=True` (Market On Open)

```python
# Smart buy/sell auto-selects method based on market hours
result = await trading.async_buy_stock(ticker=ticker, limit_price=current_price)
result = await trading.async_sell_stock(ticker=ticker, limit_price=current_price)
```

### Toss — no reserved orders, but four sessions

Toss has **no time-based reserved order** (its conditional orders are price-triggered,
which means something else), so those calls raise `BrokerUnsupported` rather than
returning a failure dict that a caller would retry forever.

It does not need them as much, because Toss runs **four US sessions, all published in KST**:

| Session | KST |
|---------|-----|
| `dayMarket` | **09:00–16:50** |
| `preMarket` | 17:00–22:30 |
| `regularMarket` | 22:30–05:00 |
| `afterMarket` | 05:00–07:00 |

The usual "US market is shut while a Korean batch runs" assumption **does not hold for Toss** —
the day market covers Korean working hours. Tradeable ~22h/day; the only gap is 07:00–09:00 KST.

**Fractional shares (v2.21.1)**: Toss US holdings are routinely fractional. Quantities are
`Decimal`; a sub-share budget buys via `orderAmount`. The fractional window is narrower than
the session — it closes an hour before the regular close — so outside it a position of 1.68
sells its whole part (1) and reports the 0.68 remainder, while a sub-share position cannot be
sold at all. KR stays integer; Toss rejects domestic fractional orders.
Outside every session an order fails explicitly (`success=False`, no `outcome_unknown`) rather
than being queued, since there is nowhere to queue it.

## Database Tables

| Table | Purpose |
|-------|---------|
| `stock_holdings` / `us_stock_holdings` | Current portfolio |
| `trading_history` / `us_trading_history` | Trade records |
| `watchlist_history` / `us_watchlist_history` | Analyzed but not entered |
| `analysis_performance_tracker` / `us_analysis_performance_tracker` | 7/14/30-day tracking |
| `us_holding_decisions` | US AI holding analysis (v2.2.0) |
| `us_pending_orders` | US queued reserved orders (v2.7.1) |

## Quick Troubleshooting

| Issue | Solution |
|-------|----------|
| `could not convert string to float: ''` | Fixed in v2.2 - use `_safe_float()` |
| Playwright PDF fails | `python3 -m playwright install chromium` |
| Korean fonts missing | `sudo dnf install google-nanum-fonts && fc-cache -fv` |
| KIS auth fails | Check `trading/config/kis_devlp.yaml` |
| prism-us import error | v2.9.0: `importlib.util` 기반 임포트로 해결됨. 직접 수정 시 `cores/openai_debug.py` 참고 |
| Telegram message in English | v2.2.0 restored Korean templates - pull latest |
| Broadcast translation empty | gpt-5-mini fallback added in v2.2.0 |
| `/report` 오류 후 재사용 불가 | v2.5.0 수정 - 서버 오류 시 자동 환급됨, 재시도 가능 |
| US 예약주문 시간외 실패 | v2.7.1 - 10시 이전 주문은 자동 큐잉 → 10:05 KST 배치 실행 |
| ChatGPT OAuth 404 | Codex 엔드포인트 미지원 모델 → `_MODEL_MAP` 자동 매핑 (v2.7.0) |
| ChatGPT OAuth proxy 무반응 | `python -m cores.chatgpt_proxy.oauth_login`으로 토큰 갱신 |
| Toss 전 요청 403 `access_denied` | 허용 IP 미등록. WTS > 설정 > Open API > 허용 IP 관리 (`curl -s https://api.ipify.org`) |
| Toss 401 반복 | 토큰은 client당 1개이고 재발급 시 이전 토큰이 무효화됨. PRISM 경로 밖에서 발급 중인지 확인 |
| Toss 429 급증 | 09:00–09:10 KST 주문 한도가 3 req/s로 낮아짐. 주문 동시성을 줄일 것 |
| `BrokerUnsupported: reserved order` | 정상. 토스에 시간 기반 예약주문이 없음 |
| demo인데 실주문 걱정 | `[TOSS_DRYRUN] simulation active` 로그 확인. 주문은 HTTP 경계에서 차단되며 미인식 쓰기는 기본 차단 |
| 토스인데 기동 시 `kis_devlp.yaml` 없다고 죽음 | v2.21.2에서 해결. 진입점이 KIS를 지연 임포트하며, 매매 설정은 `toss_config.yaml`에서 읽음 |
| 토스인데 마이그레이션이 KIS 계좌를 요구 | v2.21.2에서 해결. 구 스키마 DB는 `account_seq`로 스코프를 만듦 → `docs/TOSS_BROKER_SETUP.md` §2 |
| 토스인데 대시보드·텔레그램에 보유 종목 없음 | v2.21.2에서 해결. 가용성 판정이 KIS 임포트 가능 여부를 묻던 버그 |
| 토스인데 US 매도가 한 번도 안 됨 | v2.22.0 해결. `execute_sell(ticker=...)`가 `TossBroker.async_sell_stock(stock_code, ...)`에 닿지 못해 TypeError. 종목은 **위치 인자**로 넘긴다(`BrokerPort` 규약) |
| 체결됐는데 `OrderOutcomeUnknown`으로 기록됨 | v2.22.0 해결. 소수 수량 `Decimal`이 sqlite 바인딩에서 실패하던 것. `_normalize_quantity()`가 요청·응답 양쪽을 정규화 |
| `[SELL_BLOCKED]` 로그가 뜬다 | 배치가 장 마감 후에 매도 루프에 도달했다는 뜻. v2.22.0에서 KR 오후 배치 15:40→**14:00**, US 오후 배치 06:30→**09:10**(토스 US 세션 공백 07:00–09:00 회피)로 조정. 여전히 뜨면 파이프라인이 예상(~40분)보다 오래 걸리는 것이니 로그로 실제 소요를 재고 시각을 더 앞당길 것 |
| `toss_config.yaml`에서 매수 금액을 바꿔도 안 먹힘 | v2.22.0 해결. 팩토리가 env만 보던 것을 `settings.buy_amount()` 체인(env→브로커 파일→기본값)으로 교체 |
| 토스인데 대시보드가 demo로 표기 | v2.22.0 해결. 모드를 `kis_devlp.yaml`에서 읽던 것을 `configured_mode()`로 교체 — 실계좌면 이제 real로 나온다 |
| 로컬 `.env` 때문에 KIS 테스트 실패 | `tests/conftest.py`가 브로커 환경변수를 테스트마다 초기화함 (일부 테스트가 임포트 시 `load_dotenv()` 호출) |
| Telegram `chat not found` | 봇을 채널 **관리자**로 추가하고 "메시지 게시" 권한 부여 필요 |

## i18n Strategy (v2.2.0)

- **Code comments/logs**: English
- **Telegram messages**: Korean templates (default channel is KR)
- **Broadcast channels**: Translation agent converts to target language (`--broadcast-languages en,ja,zh,es`)

## Branch & Commit Convention

### Branch Rule
- **코드 파일 변경** (`.py`, `.ts`, `.tsx`, `.js`, `.jsx` 등): 반드시 feature 브랜치에서 작업 후 PR 생성
- **문서만 변경** (`.md` 등): main 직접 커밋 허용
- 브랜치 네이밍: `feat/`, `fix/`, `refactor/`, `test/` + 설명 (예: `fix/us-dashboard-ai-holding`)

### Commit Message
```
feat: New feature
fix: Bug fix
docs: Documentation
refactor: Code refactoring
test: Tests
```

---

## Version History

| Ver | Date | Changes |
|-----|------|---------|
| 2.22.0 | 2026-08-19 | **마이그레이션 완결성 감사 (Phase 1–3)** - 토스 전환·KRX 탈피가 미완인 채 실계좌 운영에 들어간 상태를 전수 점검. **탐지 우선**: 알려진 결함을 strict xfail·동결 allowlist로 고정하는 트립와이어 7종 추가(별칭 census, `spec_from_file_location` AST 규칙, `kis_devlp.yaml` 직접 읽기, KIS 응답 형태 누출, BrokerPort 계약, 설정 키 사장, 위치 인자 규약). **실금전 P0**: `toss_config.yaml`의 매수 금액이 주문 경로에 도달하지 않던 문제(`settings.buy_amount()` 체인 연결), TIER0 강제청산 감지의 조용한 실패, US 소수점 매도 불가(`int(Decimal('0.44'))==0`), DB 경로 cwd 의존, US 에이전트 모듈 스코프 KIS 로드. **Toss US 주문 경로 복구**: 인자 철자 불일치로 주문이 브로커에 닿지 못하던 것(`ticker=` vs `stock_code`), 체결 응답 `Decimal`의 sqlite 바인딩 실패, 행 삭제 선행으로 인한 포지션 손실(세션 선검증·누적 롤백), quantity 컬럼 `INTEGER`→`TEXT` 마이그레이션(FK-safe, fail-open). **KIS 잔재 제거**: 대시보드 2종(모드 오표기·빈 포트폴리오), stance_mark 사문 임포트, readiness·archive enricher 브로커 게이트, 스냅샷 조건부화, messaging 구독자 KIS 전용 선언. 코드 리뷰 5라운드로 회귀 2건 포함 30여 건 교정. **미해결(운영 조치)**: KR 오후 배치가 정규장 밖이라 토스에서 매도 불가 → 크론 조정 필요, `us_stock_tracking_agent` cores 섀도잉으로 클린 임포트 불가(Phase 5) |
| 2.21.2 | 2026-08-18 | **토스 전용 기동** - `PRISM_BROKER=toss`인데도 기동 시 KIS 설정 파일을 요구하던 문제 해결. `trading/kis_auth.py`가 모듈 스코프에서 `kis_devlp.yaml`을 여는 탓에 `stock_tracking_agent`·`portfolio_telegram_reporter`·`generate_dashboard_json`·`weekly_insight_report`·`generate_us_dashboard_json` 5개 진입점이 임포트 단계에서 즉사 → 지연 임포트로 전환. 매매 설정(`default_unit_amount`·`auto_trading`·`default_mode`)을 브로커별 설정 파일에서 읽는 `trading/brokers/settings.trading_settings()` 도입. 다중계좌 마이그레이션이 계좌 스코프를 KIS에 직접 묻던 것을 브로커 인식 `primary_account_scope()`로 교체(구 스키마 DB를 들고 갈아탄 설치가 `kis_devlp.yaml` 생성을 요구받던 문제). 오류 안내는 `broker_config_hint()`로 실제 선택된 브로커 파일을 가리킴. **대시보드·텔레그램 가용성 판정 버그 수정** — "KIS 모듈이 임포트되나"를 실거래 데이터 가용성의 대리로 써서 토스에서 빈 포트폴리오·US 포지션 누락이 발생하던 것을 팩토리 질의로 교체. 재발 방지 tripwire 추가(AST 모듈 스코프 검사 + 진입점 임포트 인구조사). 가이드 → `docs/TOSS_BROKER_SETUP.md` §2 |
| 2.21.1 | 2026-08-18 | **토스 US 소수점 주식 지원** - 어댑터가 보유 수량을 정수로 절삭해 1주 미만 포지션이 포트폴리오에서 사라지고 `FLAT`으로 보고되던 버그 수정(실계좌 5종목 중 4종목 소실). 수량을 `Decimal`로 처리(`float`은 전량 매도 시 잔량을 남김), 소수 매도는 시장가·6자리 내림, 1주 미만 예산은 `orderAmount` 금액 매수로 전환. **소수점 거래 창은 정규장 종료 1시간 전까지**라, 창 밖에서는 1주 이상만 정수 부분 매도(잔여는 `residual_quantity`로 보고)하고 1주 미만은 매도 불가. KR은 정수 유지(토스가 국내 소수점 주문을 거부). 가이드 → `docs/TOSS_BROKER_SETUP.md` §9 |
| 2.21.0 | 2026-08-17 | **토스증권 브로커 지원 (선택형)** - `PRISM_BROKER=kis\|toss` 설치 단위 전역 전환, 호출측 무수정. `trading/brokers/` 브로커 추상화(`BrokerPort`) 도입 후 KIS를 첫 어댑터로 이전(동작 무변경), 토스 OAuth2·레이트리밋·재시도 전송 계층, **모의투자 서버 부재를 메우는 로컬 dry-run 시뮬레이터**(주문은 HTTP 경계에서 default-deny 차단), KR/US 매매 어댑터, `cores/market_data/toss_source.py` 시세 소스(기본 순서 미포함, opt-in). 토스는 예약주문·KR 종가주문 미지원 → `BrokerUnsupported`. US는 4개 세션(**데이마켓 09:00–16:50 KST 포함**)으로 한국 시간대에도 매매 가능. 설정 가이드 → `docs/TOSS_BROKER_SETUP.md` |
| 2.9.0 | 2026-03-31 | **외부 기여 3종 + 매매 안정성 수정** - 다중 계좌 지원 (tkgo11, #228): 주·부계좌 병렬 팬아웃 + DB 마이그레이션, US 소셜 센티먼트 (alexander-schneider, #229): Adanos API 통합, US 모듈 네임스페이스 충돌 수정 (lifrary, #227): `importlib.util` 기반 임포트, KIS API 오류 3종 (APTR0057·APBK1234) + Telegram JSON sanitize + 손절 방어 강화 (#239), US 매도 ORD_DVSN 누락 수정 (#238), Telegram 타임아웃 지수 백오프 재시도 (#237), OpenAI 400 디버그 로깅 (#232) |
| 2.7.0 | 2026-03-24 | **ChatGPT OAuth Proxy + README 전면 업데이트** - ChatGPT Plus/Pro 구독으로 API 키 없이 분석 실행 가능 (`cores/chatgpt_proxy/`), Codex 엔드포인트 모델 매핑·SSE 파싱·response_format 변환 (#224), README 5개 언어 전면 개편 (모바일 앱·홍보영상·매매실적·Macro Intelligence 반영), 대시보드 스크린샷 교체 |
| 2.6.0 | 2026-03-12 | **거시경제 인텔리전스 + 하이브리드 종목선정 + 텔레그램 얼럿 강화** - Macro Intelligence 에이전트 도입 (시장 체제 판단, 주도/낙후 섹터 식별), 탑다운+바텀업 하이브리드 종목 선정 (#202), US score-decision override 버그 수정 (#203), US trigger results 파일 경로 통일 (#204), KR/US 텔레그램 시그널 얼럿에 시장국면·선정채널·점수/R·R/손절 정보 추가 + PDF 커버 날짜 regex 수정 (#205) |
| 2.5.2 | 2026-03-04 | **FCM NOT_FOUND 토큰 삭제 + Telegram Evaluator 다중 JSON 파싱 수정** - `firebase_bridge.py` `_INVALID_TOKEN_CODES`에 `NOT_FOUND` 추가 (만료 토큰 0/8 실패 반복 해결, #196), `telegram_summary_agent.py` GPT-5.x reasoning 모델 다중 JSON 응답 파싱 실패 → `_RobustEvaluatorLLM` 래퍼 + `generate_str()` fallback 추가 (#197) |
| 2.5.1 | 2026-02-22 | **Claude Sonnet 4.6 업그레이드** - `report_generator.py` 내 모델 `claude-sonnet-4-5-20250929` → `claude-sonnet-4-6` (5곳), knowledge cutoff Jan 2025 → Aug 2025 |
| 2.5.0 | 2026-02-22 | **Telegram /report 일일 횟수 환급 + 한국어 메시지 복원** - 서버 오류(서브프로세스 타임아웃, 내부 AI 에이전트 오류) 시 `/report`·`/us_report` 일일 사용 횟수 자동 환급 (`refund_daily_limit`, `_is_server_error` 추가, `send_report_result` 내 환급 처리), `AnalysisRequest`에 `user_id` 필드 추가, Telegram 봇 사용자 대면 메시지 한국어 템플릿 복원 |
| 2.4.9 | 2026-02-21 | **US 분석 버그 5종 수정** - `data_prefetch._df_to_markdown` tabulate 의존성 제거 (직접 마크다운 테이블 생성), `us_telegram_summary_agent` evaluator 프롬프트에 `needs_improvement` JSON 형식 명세 추가 + 평가 등급 0-3으로 정정 (Pydantic validation 오류 해결), `create_us_sell_decision_agent` US holding 매도 판단에 연결 (규칙 기반→AI 기반, fallback 유지), `redis_signal_publisher` 로그 KRW 하드코딩→`market` 필드 기반 USD/KRW 동적 출력, GCP Pub/Sub credentials 경로 로그 추가 + `GCP_CREDENTIALS_PATH` 미설정 경고 (401 진단 개선) |
| 2.4.8 | 2026-02-19 | **US 매수 가격 수정 + GCP 인증 + Firebase Bridge 타입 감지 버그 3종 수정** - `get_current_price()` KIS `last` 빈 문자열 시 `base`(전일종가) fallback 추가, `async_buy_stock()` KIS 가격 조회 실패 시 `limit_price` fallback (예약주문 보장), GCP Pub/Sub 401 → 명시적 `service_account.Credentials` 인증으로 전환, `detect_type()` 포트폴리오 키워드 구체화 (`포트폴리오 관점` 오탐 방지), `detect_type()` 트리거 키워드(`트리거/급등/급락/surge`) analysis 이전에 체크 (매수신호 포함 트리거 알림 정상 분류), `extract_title()` 파일경로 체크를 markdown 정리 이전으로 이동 (PDF 파일명 언더바 보존) |
| 2.4.7 | 2026-02-16 | **주간 리포트 확장 + 압축 후행평가** - 주간 매매 요약, 매도 후 평가, AI 장기 학습 인사이트, L1→L2 압축 후행 교훈, 다국어 broadcast 지원 |

For full history, see git log.

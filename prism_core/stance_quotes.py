"""KIS 시세를 Stance 서버에 물려주는 제공자.

`stance/` 는 시세를 어디서 가져오는지 모른다. 이 파일이 그 간극을 메운다.
`stance_adapter.py` 와 마찬가지로 **PRISM 과 Stance 양쪽을 아는 바깥 파일**이다.

── 왜 접수 직후에 찍어야 하는가 ─────────────────────────────────────────

Stance 의 위조 방지는 접수시각 하나에 달려 있다.
접수시각보다 앞선 가격은 인정될 수 없어야 하므로,
서버가 선언을 받은 **그 자리에서** 시세를 찍는다.

시세를 못 구하면 거부가 아니라 보류(PENDING)다.
소스 장애는 서버 책임이지 참여자 책임이 아니다.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from stance.server.models import Quote

logger = logging.getLogger(__name__)

# KIS 종목상태 코드. 58 이 거래정지다.
HALTED = {"58"}


class KisQuoteProvider:
    """KIS 현재가 조회를 Stance 의 시세 제공자 규약에 맞춘다.

    사용
        from trading.domestic_stock_trading import DomesticStockTrading
        provider = quote_provider_for_broker("real")   # PRISM_BROKER 를 따른다
        service = StanceService(ledger=..., quote_provider=provider)

    현재가 API 응답에 상한가(stck_mxpr)·하한가(stck_llam)가 함께 오므로
    **추가 호출 없이** 방향별 체결 가능성을 판정한다.

    ⚠️ 정확히는 "상한가 도달" 이지 "상한가 잠김(매도 잔량 0)" 이 아니다.
       호가 잔량은 별도 API(inquire-asking-price)에만 있다.
       상한가에 닿았으나 물량이 남아 살 수 있는 경우까지 거부하므로 보수적이다.
       그 편이 "못 사는데 샀다고 인정" 하는 것보다 낫다고 판단했다.
    """

    def __init__(self, trading_client, source: str = "kis"):
        self.client = trading_client
        self.source = source

    def __call__(self, market: str, symbol: str) -> Quote | None:
        if market != "KRX":
            logger.warning("[stance] KIS 제공자는 KRX 전용입니다: %s", market)
            return None

        try:
            data = self.client.get_current_price(symbol)
        except Exception:
            logger.exception("[stance] 시세 조회 실패 (%s) — 보류 처리된다", symbol)
            return None

        if not data:
            return None

        price = data.get("current_price") or 0
        if price <= 0:
            logger.warning("[stance] 유효하지 않은 가격 (%s): %r", symbol, price)
            return None

        status = str(data.get("iscd_stat_cls_code", "")).strip()
        current = Decimal(str(price))
        upper = _to_decimal(data.get("upper_limit"))
        lower = _to_decimal(data.get("lower_limit"))

        return Quote(
            symbol=symbol,
            price=current,
            tradable=status not in HALTED,
            at_upper_limit=upper is not None and current >= upper,
            at_lower_limit=lower is not None and current <= lower,
            source=self.source,
        )


def _to_decimal(value) -> Decimal | None:
    """KIS 는 숫자를 문자열로 준다. 비었거나 0 이면 판정에 쓰지 않는다."""
    try:
        d = Decimal(str(value).strip())
    except Exception:
        return None
    return d if d > 0 else None


class TossQuoteProvider:
    """토스 시세를 Stance 의 시세 제공자 규약에 맞춘다.

    `KisQuoteProvider` 를 토스 클라이언트로 재사용하지 않는 이유가 있다.
    토스의 현재가 응답에는 상·하한가도 거래정지 상태도 들어있지 않다.
    그대로 물리면 `at_upper_limit` 이 항상 False, `tradable` 이 항상 True 가 되어
    **"못 사는데 샀다고 인정"** 하는 쪽으로 조용히 틀린다 —
    KisQuoteProvider 가 보수적으로 판정하며 피하려던 바로 그 실패다.

    그래서 토스가 따로 제공하는 것을 각각 가져온다.

        /api/v1/prices              현재가
        /api/v1/price-limits        상·하한가
        /api/v1/stocks              거래정지 여부 (koreanMarketDetail)

    선언 접수는 고빈도가 아니므로 호출 3회를 감수한다.
    조회에 실패하면 거부가 아니라 보류다 — 소스 장애는 참여자 책임이 아니다.
    """

    def __init__(self, client, source: str = "toss"):
        # `client` 는 TossClient(또는 dry-run 래퍼). TossBroker 를 받으면 그 안의
        # 클라이언트를 쓴다 — 호출측이 어느 쪽을 넘기든 동작하게 한다.
        self.client = getattr(client, "client", client)
        self.source = source

    def __call__(self, market: str, symbol: str) -> Quote | None:
        if market != "KRX":
            logger.warning("[stance] 토스 제공자는 현재 KRX 전용입니다: %s", market)
            return None

        price = self._price(symbol)
        if price is None:
            return None

        upper, lower = self._limits(symbol)
        return Quote(
            symbol=symbol,
            price=price,
            tradable=self._tradable(symbol),
            at_upper_limit=upper is not None and price >= upper,
            at_lower_limit=lower is not None and price <= lower,
            source=self.source,
        )

    def _price(self, symbol: str) -> Decimal | None:
        try:
            rows = self.client.request(
                "GET", "/api/v1/prices", params={"symbols": symbol}
            )
        except Exception:
            logger.exception("[stance] 토스 시세 조회 실패 (%s) — 보류 처리된다", symbol)
            return None

        for row in rows if isinstance(rows, list) else [rows]:
            if isinstance(row, dict) and str(row.get("symbol")) == symbol:
                value = _to_decimal(row.get("lastPrice"))
                if value is not None:
                    return value
        logger.warning("[stance] 토스가 유효한 가격을 주지 않았습니다 (%s)", symbol)
        return None

    def _limits(self, symbol: str) -> tuple[Decimal | None, Decimal | None]:
        """상·하한가. 못 구하면 (None, None) — 제한 판정을 하지 않는다.

        미국 종목은 가격제한이 없어 두 값이 null 로 온다.
        """
        try:
            data = self.client.request(
                "GET", "/api/v1/price-limits", params={"symbol": symbol}
            )
        except Exception:
            logger.warning("[stance] 토스 상하한가 조회 실패 (%s)", symbol, exc_info=True)
            return None, None
        if not isinstance(data, dict):
            return None, None
        return _to_decimal(data.get("upperLimitPrice")), _to_decimal(data.get("lowerLimitPrice"))

    def _tradable(self, symbol: str) -> bool:
        """거래 가능 여부. 확인하지 못하면 거래 가능으로 둔다.

        KIS 쪽과 같은 판단이다 — 조회 실패로 선언을 거부하면 소스 장애가
        참여자 불이익이 된다.
        """
        try:
            rows = self.client.request(
                "GET", "/api/v1/stocks", params={"symbols": symbol}
            )
        except Exception:
            logger.warning("[stance] 토스 종목상태 조회 실패 (%s)", symbol, exc_info=True)
            return True

        for row in rows if isinstance(rows, list) else [rows]:
            if not isinstance(row, dict) or str(row.get("symbol")) != symbol:
                continue
            if str(row.get("status", "ACTIVE")).upper() != "ACTIVE":
                return False
            detail = row.get("koreanMarketDetail")
            if isinstance(detail, dict) and detail.get("krxTradingSuspended") is True:
                return False
            return True
        return True


def quote_provider_for_broker(mode: str | None = None):
    """설정된 브로커에 맞는 시세 제공자를 만든다.

    `PRISM_BROKER` 를 보지 않고 KIS 를 직접 물리던 곳들이 있었다.
    토스로 매매하면서 시세는 KIS 에서 받아오는 상태가 되므로 여기서 갈라준다.
    실패하면 None — 서버는 뜨고 선언은 보류로 쌓인다.
    """
    from trading.brokers import settings as broker_settings

    broker = broker_settings.selected_broker()
    if broker == broker_settings.TOSS:
        from trading.brokers.factory import build_toss_broker

        return TossQuoteProvider(build_toss_broker("KR", mode=mode))

    from trading.domestic_stock_trading import DomesticStockTrading

    return KisQuoteProvider(DomesticStockTrading(mode=mode or "real"))


class StaticQuoteProvider:
    """테스트·데모용. 고정 가격을 돌려준다."""

    def __init__(self, prices: dict[str, float]):
        self.prices = {k: Decimal(str(v)) for k, v in prices.items()}

    def __call__(self, market: str, symbol: str) -> Quote | None:
        price = self.prices.get(symbol)
        return None if price is None else Quote(symbol, price, source="static")

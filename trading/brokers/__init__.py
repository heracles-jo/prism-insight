"""Broker adapters behind one contract.

Only the contract is re-exported here. Adapters are not, because importing one
pulls in its broker's auth module, which reads credential files at import time —
a cost the contract itself should not carry. Import an adapter explicitly:

    from trading.brokers.kis_adapter import KisBroker
"""

from trading.brokers.base import (
    BrokerError,
    BrokerPort,
    BrokerUnavailable,
    BrokerUnsupported,
    HoldingState,
    OrderOutcome,
)

__all__ = [
    "BrokerError",
    "BrokerPort",
    "BrokerUnavailable",
    "BrokerUnsupported",
    "HoldingState",
    "OrderOutcome",
]

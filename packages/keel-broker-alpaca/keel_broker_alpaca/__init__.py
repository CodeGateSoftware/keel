"""Alpaca US-equities adapter for keel, registered as the `alpaca` broker plugin.

Not affiliated with, endorsed by, or sponsored by Alpaca. This is an original
implementation of keel's broker port against Alpaca's publicly documented Trading and
Market Data APIs -- no Alpaca SDK, no third-party adapter code.
"""

from keel_broker_alpaca.adapter import AlpacaAdapter, CashAccountRequired

__all__ = ["AlpacaAdapter", "CashAccountRequired"]

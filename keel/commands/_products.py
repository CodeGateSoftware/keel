"""Product-id derivation shared across CLI commands.

`fetch`, `simulate`, `screen`, `holdings` and `rules seed` must all agree on which product id an
allowlist asset means, in the deployment's settlement currency. Keeping that derivation in one
leaf module (depended on by both `keel/cli.py` and the extracted command groups, importing
neither) is what prevents them from disagreeing.
"""

from __future__ import annotations

from keel.config import Config


def _history_product(asset: str, quote: str) -> str:
    """The product id for an asset, in the deployment's settlement currency.

    ONE source of truth, shared with `_default_sim_products`. Hardcoding `-USD` here while the
    screen compared against `config.quote_currency` is what let a `quote_currency: USDC` config
    reject every asset on a settlement failure it could never fix -- a default change does not
    change configs already on disk. Deriving both from the same setting means the worst case is
    an honest "no local history, run `keel fetch`", not a silent unfixable rejection.
    """
    return f"{asset}-{quote.upper()}"


def _default_sim_products(config: Config) -> list[str]:
    """Allowlist assets as product ids, in the configured settlement currency.

    Shares `_history_product`'s derivation so `fetch`, `simulate`, `screen` and `holdings`
    cannot disagree about which product an asset means.
    """
    return [_history_product(asset, config.quote_currency) for asset in config.allowlist]

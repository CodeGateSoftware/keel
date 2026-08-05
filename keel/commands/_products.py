"""Product-id derivation and validation shared across CLI commands.

`fetch`, `simulate`, `screen`, `holdings` and `rules seed` must all agree on which product id an
allowlist asset means, in the deployment's settlement currency. Keeping that derivation in one
leaf module (depended on by both `keel/cli.py` and the extracted command groups, importing
neither) is what prevents them from disagreeing.

The same module owns the check applied to an id the operator TYPES (`--products`), for the same
reason: a derivation and a validation that disagree about what a product id is would let the CLI
refuse an id keel itself constructs, or accept one it cannot trade.
"""

from __future__ import annotations

from keel_core.products import parse_spot_product_id, quote_currency_of

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


def validate_product_ids(ids: list[str], settlement_currencies: frozenset[str]) -> list[str]:
    """Return `ids` unchanged, or raise `ValueError` naming every id keel could not trade.

    The two questions here are the two the hard rails ask, deliberately and in the same order:

    1. **Shape** (`parse_spot_product_id`, rail 19) -- is it a spot pair, `BASE-QUOTE`?
    2. **Settlement** (`quote_currency_of` vs `settlement_currencies`, rail 18) -- is its quote
       leg one this deployment settles in?

    Asking them where the operator TYPES the id, rather than only where the agent trades it, is
    the point (feasibility study R2). `keel rules seed --products XLM-28AUG26-CDE --status live`
    otherwise writes a row that looks seeded, that the agent then polls every cycle and rails
    18/19 veto forever -- the reason visible only in a log line nobody is reading. The rails stay
    exactly as they are: this is an ergonomics check standing in front of them, never a
    replacement for them, and it runs on the operator's list only. Nothing reads it at order time.

    ⚠️ **A lowercase id is REJECTED, with a hint -- never silently uppercased.** `quote_currency_of`
    case-folds because it is identifying the currency of an id that already exists; accepting
    `btc-USD` here would mean the id the operator typed is not the id keel goes on to trade, and
    a product id is a venue identifier, not free text. Guessing at one is how a typo becomes a
    position. The hint costs a line and leaves the operator holding the fix.

    Reports EVERY bad id in one message. An operator fixing a list one error per invocation
    learns it slowly and abandons it fast. Raises `ValueError` and nothing else, so callers can
    wrap it in `click.BadParameter` and get a usage error rather than a traceback.
    """
    reasons: list[str] = []
    for product_id in ids:
        if parse_spot_product_id(product_id) is None:
            # The hint fires only when case is the ONLY thing wrong, so it can never suggest an
            # id that is itself inadmissible -- `xlm-28aug26-cde` gets the refusal, not advice.
            hint = ""
            if isinstance(product_id, str) and parse_spot_product_id(product_id.upper()):
                hint = f" -- did you mean {product_id.upper()}?"
            reasons.append(
                f"{product_id!r} is not a spot product id (expected BASE-QUOTE, uppercase, "
                f"exactly one hyphen; keel is spot-only, so futures BASE-DDMMMYY-CDE and equity "
                f"hashes are refused){hint}"
            )
            continue
        settlement = quote_currency_of(product_id)
        if settlement not in settlement_currencies:
            reasons.append(
                f"{product_id!r} settles in {settlement}, which is not one of this deployment's "
                f"settlement_currencies {sorted(settlement_currencies)} -- rail 18 would veto "
                f"every order for it"
            )
    if reasons:
        raise ValueError(
            "unusable product id(s):\n" + "\n".join(f"  - {reason}" for reason in reasons)
        )
    return ids


def parse_products_option(
    products: str | None, config: Config, *, validate: bool = True
) -> list[str]:
    """A `--products` option value as a validated product list; the allowlist when it is absent.

    The one parse of that option, shared by `fetch`/`monitor`/`simulate` (via
    `cli._parse_products_option`) and `rules seed`, which used to split it inline and so could
    not have been given this check without growing a second copy of the derivation.

    `validate=False` is for `assets screen` ALONE, and is not a convenience. Screening is the
    diagnostic that ANSWERS "may keel trade this, and why not" -- `screen_asset` has a settlement
    criterion of its own and reports `REJECT` with a reason. Refusing the id at the option would
    replace that reasoned verdict with a usage error, i.e. the one command whose entire job is to
    explain an inadmissible asset would become the one command that cannot be asked about one.
    Screening writes nothing and orders nothing; rails 18/19 stop the id if it ever reaches an
    order by another route.

    Raises `ValueError` listing every unusable id. Callers are CLI commands and wrap it in
    `click.BadParameter`, so the operator gets a usage error rather than a traceback.

    ⚠️ Validation applies to what the operator TYPED, not to the allowlist-derived default. The
    default is `_history_product`'s output over `config.allowlist`, and its shape is config's
    question, checked once at load (`load_config` already refuses a `quote_currency` outside
    `settlement_currencies` for exactly this reason). Validating it here would mean `keel fetch`
    -- which places no orders and needs no rail -- started refusing configs it has always
    accepted, for a defect the trading path already reports. Rails 18/19 remain the backstop for
    an id that reaches an order by any route, typed or derived.
    """
    if not products:
        return _default_sim_products(config)
    ids = [p.strip() for p in products.split(",") if p.strip()]
    if not validate:
        return ids
    return validate_product_ids(ids, config.settlement_currencies)

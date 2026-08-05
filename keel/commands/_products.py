"""Product-id derivation and validation shared across CLI commands.

`fetch`, `simulate`, `screen`, `holdings` and `rules seed` must all agree on which product id an
allowlist asset means, in the deployment's settlement currency. Keeping that derivation in one
leaf module (depended on by both `keel/cli.py` and the extracted command groups, importing
neither) is what prevents them from disagreeing.

The same module owns the check applied to an id the operator TYPES (`--products`), for the same
reason: a derivation and a validation that disagree about what a product id is would let the CLI
refuse an id keel itself constructs, or accept one it cannot trade.

Nothing here imports `click`. The checks raise `ValueError` or return data; turning either into a
usage error is the calling command's job, so this module stays testable without a CLI runner and
usable from anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass

from keel_core.products import parse_spot_product_id, quote_currency_of

from keel.config import Config

#: A `--products` id that is not `BASE-QUOTE` at all. **Always a typo**, for every caller: no
#: config edit rescues it, and no command has a legitimate use for one.
SHAPE = "shape"

#: A well-formed spot pair whose quote leg is not in this deployment's `settlement_currencies`.
#: A real product, and a real answer -- `BTC-EUR` is listed on Coinbase. Rail 18 vetoes an ORDER
#: for it; a command that places none is entitled to weigh it as a warning.
SETTLEMENT = "settlement"


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


@dataclass(frozen=True)
class ProductIdProblem:
    """One reason one typed `--products` id is unusable, tagged with WHICH question it failed.

    The tag is the point. Both questions are worth asking wherever an operator types an id, but
    they do not carry the same consequence -- see `SHAPE` and `SETTLEMENT` above -- and a caller
    that could only string-match the message would have to grep prose to weigh them. `reason` is
    a complete, self-contained sentence naming `product_id`, so a caller reporting a subset of
    the problems still reports each of them in full.
    """

    product_id: str
    kind: str
    reason: str


def check_product_ids(
    ids: list[str], settlement_currencies: frozenset[str]
) -> list[ProductIdProblem]:
    """Every reason keel could not trade one of `ids`, in order. Raises nothing; decides nothing.

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

    **Shape is asked first and stops there**, per id, and that ordering is load-bearing rather
    than incidental: `quote_currency_of("XLM-28AUG26-CDE")` is `"CDE"`, which would otherwise be
    reported as "settles in CDE, not in your settlement_currencies" -- an accurate sentence that
    invites the operator to widen `settlement_currencies` until a futures contract is admitted.
    A malformed id has exactly one true diagnosis.

    ⚠️ **A lowercase id is REPORTED, with a hint -- never silently uppercased.**
    `quote_currency_of` case-folds because it is identifying the currency of an id that already
    exists; accepting `btc-USD` here would mean the id the operator typed is not the id keel goes
    on to trade, and a product id is a venue identifier, not free text. Guessing at one is how a
    typo becomes a position. The hint costs a line and leaves the operator holding the fix.

    EVERY bad id is reported, not just the first. An operator fixing a list one error per
    invocation learns it slowly and abandons it fast.
    """
    problems: list[ProductIdProblem] = []
    for product_id in ids:
        if parse_spot_product_id(product_id) is None:
            # The hint fires only when case is the ONLY thing wrong, so it can never suggest an
            # id that is itself inadmissible -- `xlm-28aug26-cde` gets the refusal, not advice.
            hint = ""
            if isinstance(product_id, str) and parse_spot_product_id(product_id.upper()):
                hint = f" -- did you mean {product_id.upper()}?"
            problems.append(
                ProductIdProblem(
                    product_id=str(product_id),
                    kind=SHAPE,
                    reason=(
                        f"{product_id!r} is not a spot product id (expected BASE-QUOTE, "
                        f"uppercase, exactly one hyphen; keel is spot-only, so futures "
                        f"BASE-DDMMMYY-CDE and equity hashes are refused){hint}"
                    ),
                )
            )
            continue
        settlement = quote_currency_of(product_id)
        if settlement not in settlement_currencies:
            problems.append(
                ProductIdProblem(
                    product_id=product_id,
                    kind=SETTLEMENT,
                    reason=(
                        f"{product_id!r} settles in {settlement}, which is not one of this "
                        f"deployment's settlement_currencies "
                        f"{sorted(settlement_currencies)} -- rail 18 would veto every order "
                        f"for it"
                    ),
                )
            )
    return problems


def _unusable_message(problems: list[ProductIdProblem]) -> str:
    return "unusable product id(s):\n" + "\n".join(f"  - {p.reason}" for p in problems)


def validate_product_ids(ids: list[str], settlement_currencies: frozenset[str]) -> list[str]:
    """Return `ids` unchanged, or raise `ValueError` naming every id keel could not trade.

    BOTH failure kinds are fatal here. This is the check for a command that WRITES something the
    agent will act on -- `rules seed` -- where a rule the rails veto forever is not a lesser
    problem than a typo, only a quieter one. `parse_products_option(settlement_is_fatal=False)`
    is the softer variant, and it exists for commands that write no such thing.

    Raises `ValueError` and nothing else, so callers can wrap it in `click.BadParameter` and get
    a usage error rather than a traceback.
    """
    problems = check_product_ids(ids, settlement_currencies)
    if problems:
        raise ValueError(_unusable_message(problems))
    return ids


def parse_products_option(
    products: str | None,
    config: Config,
    *,
    validate: bool = True,
    settlement_is_fatal: bool = True,
) -> tuple[list[str], list[str]]:
    """A `--products` option value as `(product_ids, warnings)`; the allowlist when it is absent.

    The one parse of that option, shared by `fetch`/`simulate` (via `cli._parse_products_option`)
    and `rules seed`, which used to split it inline and so could not have been given this check
    without growing a second copy of the derivation. (`monitor` takes no `--products` at all --
    it polls `_default_sim_products` directly.)

    `warnings` are non-fatal problem reasons the caller must SHOW; returning them rather than
    printing them is what keeps this module free of `click`. It is empty unless
    `settlement_is_fatal=False`.

    **`settlement_is_fatal=False` is for `fetch` and `simulate`.** A `SHAPE` failure stays fatal
    there -- it is always a typo -- but a `SETTLEMENT` mismatch becomes a warning, because
    treating it as a usage error broke the screening workflow outright: `assets screen
    --products BTC-EUR` is exempt from validation precisely so an operator CAN ask about a
    cross-settled pair, but the screen's answer is dominated by "0 daily bars < 1460 required",
    and there was then no way to fetch that history without first widening
    `settlement_currencies`. The config change had to be made before the evidence for making it
    could be gathered. Neither command places an order, so neither needs rail 18 standing in
    front of it -- which is this branch's own stated reason for not validating the derived
    default, applied consistently.

    `validate=False` is for `assets screen` ALONE, and is not a convenience. Screening is the
    diagnostic that ANSWERS "may keel trade this, and why not", and `screen_asset` has both a
    settlement AND a spot-shape criterion of its own (`keel/compliance/screen.py`), so it
    reports `REJECT` with the same reason a usage error would have carried -- plus the history,
    liquidity and attestation verdicts a usage error would have suppressed. Refusing the id at
    the option would make the one command whose entire job is to explain an inadmissible asset
    the one command that cannot be asked about one. Screening writes nothing and orders nothing;
    rails 18/19 stop the id if it ever reaches an order by another route.

    Raises `ValueError` listing every fatal id. Callers are CLI commands and wrap it in
    `click.BadParameter`, so the operator gets a usage error rather than a traceback.

    ⚠️ Validation applies to what the operator TYPED, not to the allowlist-derived default. The
    default is `_history_product`'s output over `config.allowlist`, and both of its legs are
    config's question, answered at load: `_parse_allowlist` shape-checks each asset against the
    base-leg half of the spot grammar, and `load_config` refuses a `quote_currency` outside
    `settlement_currencies`. So a derived id that fails either question here is a config bug that
    `load_config` should have caught, and the fix belongs there -- where the message can name the
    file -- not in a per-command re-check. Rails 18/19 remain the backstop for an id that reaches
    an order by any route, typed or derived.
    """
    if not products:
        return _default_sim_products(config), []
    ids = [p.strip() for p in products.split(",") if p.strip()]
    if not validate:
        return ids, []
    problems = check_product_ids(ids, config.settlement_currencies)
    fatal = [p for p in problems if settlement_is_fatal or p.kind != SETTLEMENT]
    if fatal:
        raise ValueError(_unusable_message(fatal))
    return ids, [p.reason for p in problems]

# keel Broker Port — Phase A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `Broker` port and prove it is not Coinbase-shaped, by implementing it twice — once against Coinbase, once against a deliberately divergent fake — and holding both to a shared conformance suite.

**Architecture:** Implements Phase A (steps 1–5) of `docs/superpowers/specs/2026-07-19-keel-broker-port-design.md`. Three new workspace packages: `keel-broker-api` (port, domain types, `OrderSpec` sum type, capabilities, registry, conformance suite), `keel-broker-coinbase`, and `keel-broker-fake`. `mypy` arrives with strict mode on the new packages only.

**Tech Stack:** Python 3.12, `uv` workspaces, `mypy`, `pytest`, `ruff`, `importlib.metadata` entry points.

## Global Constraints

- Python `>=3.12`; ruff `line-length = 100`, `select = ["E", "F", "I", "UP"]`, `ignore = ["UP042"]`.
- **Phase A is strictly additive. No existing code path may change behaviour.** Nothing in `keel/` calls into the new packages by the end of this plan. `uv run pytest tests/baseline/ -v` (3 tests) must pass with `tests/fixtures/baseline_backtest.json` byte-unchanged at the end of every task.
- **`keel/data/cb_client.py` is not touched in this plan.** See "Transitional duplication" below.
- Distribution names use hyphens (`keel-broker-api`); import names use underscores (`keel_broker_api`).
- `keel-broker-api` depends on `keel-core` and stdlib only. No third-party dependencies.
- **No broker-native type and no raw `dict` crosses the port.**
- `mypy` strict applies to `keel_broker_api.*`, `keel_broker_coinbase.*`, `keel_broker_fake.*` only. `keel.*` stays `ignore_errors = true`.
- Every `match` over `OrderSpec` ends with `case _: assert_never(spec)`.
- Run everything through `uv run`. Never commit `keel.db`, `*.log`, or `transactions/`.

## Transitional duplication (deliberate)

`keel-broker-coinbase` is built standalone. `keel/data/cb_client.py` is left completely untouched, so `_field`, `_candle_from_raw`, and the `Transport` Protocol exist in two places for the duration of Phase A.

This is a deliberate trade. The alternative — having `cb_client.py` import shared helpers from the new package — creates a transitional import graph that must be untangled in Phase B anyway, on the live order path. Temporary duplication with a *scheduled deletion* (Phase B step 6 deletes `cb_client.py` entirely) is the safer of the two, and it keeps Phase A's risk to the running engine at exactly zero.

Do not "fix" this duplication during Phase A.

## Known constraint: `Granularity` is Coinbase's vocabulary

`keel_core.types.Granularity` documents itself as *"Values match the Coinbase Advanced Trade API strings."* The canonical granularity vocabulary is therefore Coinbase's.

This is accepted, not fixed here: a canonical enum has to follow *someone's* naming, and changing it would ripple through the candle store, the strategy layer, and the baseline fixture. What matters is that non-Coinbase adapters must **map** their own granularities onto these names rather than assuming they match. Task 4's fake adapter exercises exactly that, and its divergent granularity set is what proves the mapping is real rather than incidental.

---

### Task 1: `keel-broker-api` — the port

The port and its domain types. Nothing consumes it yet, so this task is pure addition with no behavioural risk.

**Files:**
- Create: `packages/keel-broker-api/pyproject.toml`
- Create: `packages/keel-broker-api/keel_broker_api/__init__.py`
- Create: `packages/keel-broker-api/keel_broker_api/orders.py`
- Create: `packages/keel-broker-api/keel_broker_api/results.py`
- Create: `packages/keel-broker-api/keel_broker_api/capabilities.py`
- Create: `packages/keel-broker-api/keel_broker_api/port.py`
- Create: `packages/keel-broker-api/keel_broker_api/registry.py`
- Create: `tests/broker_api/__init__.py`
- Create: `tests/broker_api/test_orders.py`
- Create: `tests/broker_api/test_results.py`
- Create: `tests/broker_api/test_registry.py`
- Modify: `pyproject.toml` (workspace member + dependency)

**Interfaces:**
- Consumes: `keel_core.types.{Side, Candle, Granularity}`.
- Produces: `keel_broker_api.orders.{MarketIOCByQuote, MarketIOCByBase, LimitGTC, StopLimitGTC, OrderSpec, ORDER_KINDS}`; `keel_broker_api.results.{Balance, Preview, PlaceResult, FeeSummary}`; `keel_broker_api.capabilities.BrokerCapabilities`; `keel_broker_api.port.Broker`; `keel_broker_api.registry.{discover_brokers, load_broker}`. Tasks 3–5 implement and consume these exact names.

- [ ] **Step 1: Create the package skeleton**

```bash
mkdir -p packages/keel-broker-api/keel_broker_api tests/broker_api
touch tests/broker_api/__init__.py
```

Create `packages/keel-broker-api/pyproject.toml`:

```toml
[project]
name = "keel-broker-api"
version = "0.1.0"
description = "Broker port, domain types, capability model, and conformance suite for keel"
requires-python = ">=3.12"
dependencies = ["keel-core"]

[project.optional-dependencies]
conformance = ["pytest>=9.1.1"]

[build-system]
requires = ["uv_build>=0.10.4,<0.11.0"]
build-backend = "uv_build"

[tool.uv.build-backend]
module-root = ""

[tool.uv.sources]
keel-core = { workspace = true }
```

`pytest` is an optional extra, not a hard dependency: the conformance suite (Task 5) ships inside this package so third-party adapters can run it, but installing the port must not drag pytest into a production engine.

Create `packages/keel-broker-api/keel_broker_api/__init__.py`:

```python
"""The broker port: what every venue adapter must implement, and the types crossing it."""
```

- [ ] **Step 2: Write the failing order-model tests**

Create `tests/broker_api/test_orders.py`:

```python
from __future__ import annotations

from decimal import Decimal

import pytest
from keel_core.types import Side

from keel_broker_api.orders import (
    ORDER_KINDS,
    LimitGTC,
    MarketIOCByBase,
    MarketIOCByQuote,
    StopLimitGTC,
)


def test_each_variant_has_a_distinct_kind() -> None:
    kinds = {
        MarketIOCByQuote.kind,
        MarketIOCByBase.kind,
        LimitGTC.kind,
        StopLimitGTC.kind,
    }
    assert kinds == {"market_ioc_quote", "market_ioc_base", "limit_gtc", "stop_limit_gtc"}


def test_order_kinds_lists_every_variant() -> None:
    """ORDER_KINDS is what capabilities are declared against -- it must not drift."""
    assert ORDER_KINDS == frozenset(
        {"market_ioc_quote", "market_ioc_base", "limit_gtc", "stop_limit_gtc"}
    )


def test_variants_are_frozen() -> None:
    spec = MarketIOCByQuote(product_id="BTC-USD", side=Side.BUY, quote_size=Decimal("100"))
    with pytest.raises(Exception):
        spec.quote_size = Decimal("200")  # type: ignore[misc]


def test_market_orders_carry_only_their_own_sizing_field() -> None:
    """The whole point of the sum type: a market order cannot carry a limit price."""
    by_quote = MarketIOCByQuote(product_id="BTC-USD", side=Side.BUY, quote_size=Decimal("100"))
    by_base = MarketIOCByBase(product_id="BTC-USD", side=Side.SELL, base_size=Decimal("0.5"))
    assert not hasattr(by_quote, "limit_price")
    assert not hasattr(by_quote, "base_size")
    assert not hasattr(by_base, "quote_size")


def test_initial_status_per_variant() -> None:
    """Replaces executor._initial_status, which string-matched Coinbase's config key."""
    assert MarketIOCByQuote(
        product_id="BTC-USD", side=Side.BUY, quote_size=Decimal("100")
    ).initial_status == "filled_or_rejected"
    assert LimitGTC(
        product_id="BTC-USD", side=Side.SELL, base_size=Decimal("1"), limit_price=Decimal("70000")
    ).initial_status == "open"
    assert StopLimitGTC(
        product_id="BTC-USD",
        side=Side.SELL,
        base_size=Decimal("1"),
        stop_price=Decimal("60000"),
        limit_price=Decimal("59900"),
    ).initial_status == "open"


def test_rejects_non_positive_size() -> None:
    with pytest.raises(ValueError, match="quote_size must be positive"):
        MarketIOCByQuote(product_id="BTC-USD", side=Side.BUY, quote_size=Decimal("0"))
    with pytest.raises(ValueError, match="base_size must be positive"):
        MarketIOCByBase(product_id="BTC-USD", side=Side.BUY, base_size=Decimal("-1"))
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/broker_api/test_orders.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'keel_broker_api'`

- [ ] **Step 4: Implement the order model**

Create `packages/keel-broker-api/keel_broker_api/orders.py`:

```python
"""The order model: one frozen dataclass per order shape the engine can express.

A sum type rather than one flat dataclass with an `order_type` enum, because a flat shape makes
nonsense representable -- a market order carrying a limit price, a stop-limit with no stop. On
the live-money path a malformed order is not a crash you notice; it can be a *filled* order you
did not intend.

`kind` is a stable string, not a type object: capabilities are declared against it, and strings
are serialisable, loggable, and debuggable in a way `frozenset[type]` is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar

from keel_core.types import Side


def _require_positive(name: str, value: Decimal) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")


@dataclass(frozen=True)
class MarketIOCByQuote:
    """Market order sized in quote currency (spend N USDC). Used for entries."""

    kind: ClassVar[str] = "market_ioc_quote"
    initial_status: ClassVar[str] = "filled_or_rejected"

    product_id: str
    side: Side
    quote_size: Decimal

    def __post_init__(self) -> None:
        _require_positive("quote_size", self.quote_size)


@dataclass(frozen=True)
class MarketIOCByBase:
    """Market order sized in base currency (sell N BTC). Used for exits."""

    kind: ClassVar[str] = "market_ioc_base"
    initial_status: ClassVar[str] = "filled_or_rejected"

    product_id: str
    side: Side
    base_size: Decimal

    def __post_init__(self) -> None:
        _require_positive("base_size", self.base_size)


@dataclass(frozen=True)
class LimitGTC:
    """Resting limit order, good until cancelled. Used for take-profit legs."""

    kind: ClassVar[str] = "limit_gtc"
    initial_status: ClassVar[str] = "open"

    product_id: str
    side: Side
    base_size: Decimal
    limit_price: Decimal

    def __post_init__(self) -> None:
        _require_positive("base_size", self.base_size)
        _require_positive("limit_price", self.limit_price)


@dataclass(frozen=True)
class StopLimitGTC:
    """Stop-limit, good until cancelled. Used for protective stop legs."""

    kind: ClassVar[str] = "stop_limit_gtc"
    initial_status: ClassVar[str] = "open"

    product_id: str
    side: Side
    base_size: Decimal
    stop_price: Decimal
    limit_price: Decimal

    def __post_init__(self) -> None:
        _require_positive("base_size", self.base_size)
        _require_positive("stop_price", self.stop_price)
        _require_positive("limit_price", self.limit_price)


OrderSpec = MarketIOCByQuote | MarketIOCByBase | LimitGTC | StopLimitGTC

ORDER_KINDS: frozenset[str] = frozenset(
    {MarketIOCByQuote.kind, MarketIOCByBase.kind, LimitGTC.kind, StopLimitGTC.kind}
)

__all__ = [
    "ORDER_KINDS",
    "LimitGTC",
    "MarketIOCByBase",
    "MarketIOCByQuote",
    "OrderSpec",
    "StopLimitGTC",
]
```

- [ ] **Step 5: Run the order tests to verify they pass**

Run: `uv run pytest tests/broker_api/test_orders.py -v`
Expected: 6 passed

- [ ] **Step 6: Implement the result types**

Create `packages/keel-broker-api/keel_broker_api/results.py`:

```python
"""Domain types crossing the port in the broker-to-engine direction.

These replace the raw dicts today's `cb_client` returns: `get_accounts() -> list[dict]` probed at
`executor.py:168`, and `place_order`'s dict probed via `place_result.get("success")` at
`executor.py:345`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from keel_core.types import Side


@dataclass(frozen=True)
class Balance:
    """One currency's balance on the venue."""

    currency: str
    available: Decimal
    total: Decimal


@dataclass(frozen=True)
class Preview:
    """What the human approves at the confirm gate (`executor.py:311`).

    `synthetic=True` means these numbers are an estimate the adapter computed, not a quote the
    broker returned. Anything rendering a Preview must surface that distinction: approving an
    estimate must never look identical to approving a broker's own quote.
    """

    product_id: str
    side: Side
    est_base_size: Decimal
    est_quote_size: Decimal
    est_fee: Decimal
    synthetic: bool
    detail: Mapping[str, str] = field(default_factory=dict)
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlaceResult:
    """Outcome of a placement attempt."""

    success: bool
    broker_order_id: str | None
    reason: str | None = None


@dataclass(frozen=True)
class FeeSummary:
    """Fees and volume the venue reports for this account.

    Its consumer is subscription lapse detection: Coinbase exposes no subscription endpoint, so
    the engine cannot read a user's tier -- but a fee charged while the user claims a fee-free
    allowance contradicts the claim. See the subscription design spec.

    `volume_window` is explicit because Coinbase's window could not be determined from their
    docs. An adapter that does not know says "unknown", and reconciliation then uses only
    `fees_usd` -- which is window-independent for that test. This field exists so the engine can
    never silently compare a trailing-30-day figure against a calendar-month cap.
    """

    venue: str
    taker_rate: Decimal
    maker_rate: Decimal
    volume_usd: Decimal
    fees_usd: Decimal
    volume_window: str
    fetched_at: int

    def __post_init__(self) -> None:
        allowed = {"trailing_30d", "calendar_month", "unknown"}
        if self.volume_window not in allowed:
            raise ValueError(f"volume_window must be one of {sorted(allowed)}")


__all__ = ["Balance", "FeeSummary", "PlaceResult", "Preview"]
```

- [ ] **Step 6b: Test `FeeSummary`'s window validation**

`Balance`, `Preview`, and `PlaceResult` are pure data and need no tests. `FeeSummary.__post_init__`
is real logic and does need one — it is the guard stopping an adapter declaring a window the
engine cannot interpret.

Create `tests/broker_api/test_results.py`:

```python
from __future__ import annotations

from decimal import Decimal

import pytest

from keel_broker_api.results import FeeSummary


def _summary(window: str) -> FeeSummary:
    return FeeSummary(
        venue="coinbase",
        taker_rate=Decimal("0.012"),
        maker_rate=Decimal("0.006"),
        volume_usd=Decimal("1234.56"),
        fees_usd=Decimal("0"),
        volume_window=window,
        fetched_at=1_700_000_000,
    )


@pytest.mark.parametrize("window", ["trailing_30d", "calendar_month", "unknown"])
def test_accepts_every_legal_window(window: str) -> None:
    assert _summary(window).volume_window == window


def test_rejects_an_unknown_window() -> None:
    """An undeclarable window would let the engine compare mismatched periods silently."""
    with pytest.raises(ValueError, match="volume_window must be one of"):
        _summary("monthly")


def test_unknown_is_a_legal_declaration_not_an_error() -> None:
    """Coinbase's window is undocumented; "unknown" must be sayable, not a failure."""
    assert _summary("unknown").volume_window == "unknown"
```

Run: `uv run pytest tests/broker_api/test_results.py -v`
Expected: 5 passed

- [ ] **Step 7: Implement capabilities and the port Protocol**

Create `packages/keel-broker-api/keel_broker_api/capabilities.py`:

```python
"""What a venue can do, declared by its adapter and checked before the engine sizes an order."""

from __future__ import annotations

from dataclasses import dataclass

from keel_broker_api.orders import ORDER_KINDS


@dataclass(frozen=True)
class BrokerCapabilities:
    """An adapter's self-declaration. The conformance suite verifies it does not lie."""

    venue: str
    supported_orders: frozenset[str]
    supports_native_preview: bool
    synthesizes_preview: bool
    supports_fee_summary: bool
    quote_currencies: frozenset[str]
    asset_classes: frozenset[str]

    def __post_init__(self) -> None:
        unknown = self.supported_orders - ORDER_KINDS
        if unknown:
            raise ValueError(f"unknown order kinds: {sorted(unknown)}")

    @property
    def can_preview(self) -> bool:
        """Whether `confirm` mode is usable against this venue at all."""
        return self.supports_native_preview or self.synthesizes_preview


__all__ = ["BrokerCapabilities"]
```

Create `packages/keel-broker-api/keel_broker_api/port.py`:

```python
"""The `Broker` port. Every venue adapter implements exactly this."""

from __future__ import annotations

from typing import Protocol

from keel_core.types import Candle, Granularity

from keel_broker_api.capabilities import BrokerCapabilities
from keel_broker_api.orders import OrderSpec
from keel_broker_api.results import Balance, FeeSummary, PlaceResult, Preview


class UnsupportedOrder(Exception):
    """Raised when an adapter is handed an `OrderSpec` kind it does not support.

    This is the backstop at the last gate before money moves: capability gating happens earlier,
    at rule evaluation, but an adapter must still refuse rather than substitute a different order
    type. Never catch this and retry with a different spec.
    """


class Broker(Protocol):
    def capabilities(self) -> BrokerCapabilities: ...

    def get_candles(
        self, product_id: str, granularity: Granularity, start_ts: int, end_ts: int
    ) -> list[Candle]: ...

    def get_balances(self) -> list[Balance]: ...

    def preview_order(self, spec: OrderSpec) -> Preview: ...

    def place_order(self, spec: OrderSpec) -> PlaceResult: ...

    def get_fee_summary(self) -> FeeSummary: ...


__all__ = ["Broker", "UnsupportedOrder"]
```

- [ ] **Step 8: Write the failing registry test**

Create `tests/broker_api/test_registry.py`:

```python
from __future__ import annotations

import pytest

from keel_broker_api.registry import discover_brokers, load_broker


def test_discover_brokers_returns_a_mapping() -> None:
    """Real adapters are registered by later tasks; the call itself must work with none."""
    assert isinstance(discover_brokers(), dict)


def test_load_broker_rejects_unknown_venue() -> None:
    with pytest.raises(LookupError, match="no broker adapter registered for 'nonesuch'"):
        load_broker("nonesuch")
```

- [ ] **Step 9: Implement the registry**

Create `packages/keel-broker-api/keel_broker_api/registry.py`:

```python
"""Entry-point discovery for broker adapters.

Adapters are separate distributions registering under the `keel.brokers` group, so installing a
venue is `uv add keel-broker-<venue>` -- no core change, no rebuild.

Security note: entry points execute arbitrary code. When engines run server-side, only
first-party adapters may be installed. See the design spec's trust-boundary section.
"""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any

ENTRY_POINT_GROUP = "keel.brokers"


def discover_brokers() -> dict[str, Any]:
    """Map venue name to the adapter class registered under `keel.brokers`."""
    return {ep.name: ep.load() for ep in entry_points(group=ENTRY_POINT_GROUP)}


def load_broker(venue: str) -> Any:
    """Return the adapter class registered for `venue`, or raise `LookupError`."""
    found = discover_brokers()
    if venue not in found:
        available = ", ".join(sorted(found)) or "none installed"
        raise LookupError(
            f"no broker adapter registered for {venue!r} (available: {available})"
        )
    return found[venue]


__all__ = ["ENTRY_POINT_GROUP", "discover_brokers", "load_broker"]
```

- [ ] **Step 10: Register the workspace member**

Modify the root `pyproject.toml` — add `"keel-broker-api"` to `[project] dependencies`, and under `[tool.uv.sources]` add:

```toml
keel-broker-api = { workspace = true }
```

`[tool.uv.workspace] members = ["packages/*"]` already matches the new directory.

- [ ] **Step 11: Verify**

Run: `uv sync`
Expected: resolves; `keel-broker-api` appears as a workspace member.

Run: `uv run pytest tests/broker_api/ -v`
Expected: 13 passed

Run: `uv run pytest tests/baseline/ -v`
Expected: 3 passed, golden file unchanged.

Run: `uv run pytest -q && uv run ruff check .`
Expected: all pass; ruff clean.

- [ ] **Step 12: Commit**

```bash
git add -A
git commit -m "feat: keel-broker-api port, order model, and capability types"
```

---

### Task 2: mypy, strict on the new packages

Adds type checking. Strict on the new packages from birth; `keel/` stays exempt until its modules move out in later steps. Turning strict mode on across 11.4k legacy lines at once would produce a wall of errors that stalls the actual work.

**Files:**
- Modify: `pyproject.toml` (dev dependency + `[tool.mypy]` config)

**Interfaces:**
- Consumes: the packages created in Task 1.
- Produces: `uv run mypy` as a verification command later tasks must keep green.

- [ ] **Step 1: Add mypy as a dev dependency**

Modify `pyproject.toml`'s `[dependency-groups] dev` list to include `"mypy>=1.18.0"`.

Run: `uv sync`
Expected: resolves with mypy installed.

- [ ] **Step 2: Configure per-module strictness**

Append to `pyproject.toml`:

```toml
[tool.mypy]
python_version = "3.12"
files = ["packages", "keel", "tests"]

# New packages are strict from birth.
[[tool.mypy.overrides]]
module = [
    "keel_broker_api.*",
    "keel_broker_coinbase.*",
    "keel_broker_fake.*",
]
strict = true

# Legacy code and the existing suite are exempt until their modules move out
# (monorepo spec steps 4-7). Tighten one package at a time, never all at once.
[[tool.mypy.overrides]]
module = ["keel.*", "tests.*"]
ignore_errors = true

# keel-core moved in the previous plan but predates strict mode; it is tightened
# when its remaining consumers migrate.
[[tool.mypy.overrides]]
module = "keel_core.*"
ignore_errors = true
```

- [ ] **Step 3: Run mypy and fix what it finds in `keel-broker-api`**

Run: `uv run mypy`
Expected: `Success: no issues found` — or errors confined to `packages/keel-broker-api/`.

Fix any error in `keel_broker_api.*`. Do **not** fix errors reported elsewhere; if any appear outside `keel_broker_api.*`, the overrides above are wrong — correct the config rather than the code.

Common fixes at this stage: add explicit return annotations, and give `discover_brokers`/`load_broker` a narrower type than `Any` if mypy strict objects to the bare `Any` return.

- [ ] **Step 4: Verify nothing else regressed**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Expected: all pass.

Run: `uv run pytest tests/baseline/ -v`
Expected: 3 passed, golden unchanged.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "build: add mypy, strict on the new broker packages only"
```

---

### Task 3: `keel-broker-coinbase`

The Coinbase adapter implementing the port. All translation to `{"market_market_ioc": {...}}` and its siblings lives here, in one module.

**`keel/data/cb_client.py` is NOT modified, deleted, or imported from.** See "Transitional duplication" above — this is deliberate and must not be "fixed."

**Files:**
- Create: `packages/keel-broker-coinbase/pyproject.toml`
- Create: `packages/keel-broker-coinbase/keel_broker_coinbase/__init__.py`
- Create: `packages/keel-broker-coinbase/keel_broker_coinbase/transport.py`
- Create: `packages/keel-broker-coinbase/keel_broker_coinbase/translate.py`
- Create: `packages/keel-broker-coinbase/keel_broker_coinbase/adapter.py`
- Create: `tests/broker_coinbase/__init__.py`
- Create: `tests/broker_coinbase/test_translate.py`
- Create: `tests/broker_coinbase/test_adapter.py`
- Modify: `pyproject.toml` (workspace member; move `coinbase-advanced-py` out of root deps)

**Interfaces:**
- Consumes: everything Task 1 produced; `keel_core.types.{Candle, Granularity, Side}`.
- Produces: `keel_broker_coinbase.CoinbaseAdapter` implementing `Broker`, registered as entry point `coinbase`. `keel_broker_coinbase.translate.to_order_configuration(spec) -> dict`.

- [ ] **Step 1: Create the package**

```bash
mkdir -p packages/keel-broker-coinbase/keel_broker_coinbase tests/broker_coinbase
touch tests/broker_coinbase/__init__.py
```

Create `packages/keel-broker-coinbase/pyproject.toml`:

```toml
[project]
name = "keel-broker-coinbase"
version = "0.1.0"
description = "Coinbase Advanced Trade adapter for keel"
requires-python = ">=3.12"
dependencies = ["keel-core", "keel-broker-api", "coinbase-advanced-py>=1.8.4"]

[project.entry-points."keel.brokers"]
coinbase = "keel_broker_coinbase:CoinbaseAdapter"

[build-system]
requires = ["uv_build>=0.10.4,<0.11.0"]
build-backend = "uv_build"

[tool.uv.build-backend]
module-root = ""

[tool.uv.sources]
keel-core = { workspace = true }
keel-broker-api = { workspace = true }
```

- [ ] **Step 2: Write the failing translation tests**

The translation table is the single highest-value thing to test in this package: it is the entire Coinbase-specific surface, and a wrong key here is a wrong order.

Create `tests/broker_coinbase/test_translate.py`:

```python
from __future__ import annotations

from decimal import Decimal

import pytest
from keel_broker_api.orders import LimitGTC, MarketIOCByBase, MarketIOCByQuote, StopLimitGTC
from keel_core.types import Side

from keel_broker_coinbase.translate import to_order_configuration


def test_market_by_quote() -> None:
    spec = MarketIOCByQuote(product_id="BTC-USD", side=Side.BUY, quote_size=Decimal("100.50"))
    assert to_order_configuration(spec) == {"market_market_ioc": {"quote_size": "100.50"}}


def test_market_by_base() -> None:
    spec = MarketIOCByBase(product_id="BTC-USD", side=Side.SELL, base_size=Decimal("0.25"))
    assert to_order_configuration(spec) == {"market_market_ioc": {"base_size": "0.25"}}


def test_limit_gtc() -> None:
    spec = LimitGTC(
        product_id="BTC-USD", side=Side.SELL, base_size=Decimal("1"), limit_price=Decimal("70000")
    )
    assert to_order_configuration(spec) == {
        "limit_limit_gtc": {"base_size": "1", "limit_price": "70000"}
    }


def test_stop_limit_gtc() -> None:
    spec = StopLimitGTC(
        product_id="BTC-USD",
        side=Side.SELL,
        base_size=Decimal("1"),
        stop_price=Decimal("60000"),
        limit_price=Decimal("59900"),
    )
    config = to_order_configuration(spec)
    assert "stop_limit_stop_limit_gtc" in config
    leg = config["stop_limit_stop_limit_gtc"]
    assert leg["base_size"] == "1"
    assert leg["stop_price"] == "60000"
    assert leg["limit_price"] == "59900"


def test_decimals_are_rendered_as_exact_strings_not_floats() -> None:
    """A float round-trip here would silently change an order's size."""
    spec = MarketIOCByQuote(
        product_id="BTC-USD", side=Side.BUY, quote_size=Decimal("100.123456789")
    )
    assert to_order_configuration(spec)["market_market_ioc"]["quote_size"] == "100.123456789"


def test_unknown_spec_type_raises() -> None:
    with pytest.raises(Exception):
        to_order_configuration(object())  # type: ignore[arg-type]
```

Run: `uv run pytest tests/broker_coinbase/test_translate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'keel_broker_coinbase'`

- [ ] **Step 3: Implement the translation module**

Create `packages/keel-broker-coinbase/keel_broker_coinbase/translate.py`:

```python
"""The one place keel's order model becomes Coinbase's `order_configuration` schema.

Everything Coinbase-specific about order shape lives here. Decimals render via `str()` so an
order's size is never perturbed by a float round-trip.
"""

from __future__ import annotations

from typing import assert_never

from keel_broker_api.orders import (
    LimitGTC,
    MarketIOCByBase,
    MarketIOCByQuote,
    OrderSpec,
    StopLimitGTC,
)
from keel_core.types import Side


def to_order_configuration(spec: OrderSpec) -> dict[str, dict[str, str]]:
    """Render `spec` as a Coinbase Advanced Trade `order_configuration`."""
    match spec:
        case MarketIOCByQuote():
            return {"market_market_ioc": {"quote_size": str(spec.quote_size)}}
        case MarketIOCByBase():
            return {"market_market_ioc": {"base_size": str(spec.base_size)}}
        case LimitGTC():
            return {
                "limit_limit_gtc": {
                    "base_size": str(spec.base_size),
                    "limit_price": str(spec.limit_price),
                }
            }
        case StopLimitGTC():
            return {
                "stop_limit_stop_limit_gtc": {
                    "base_size": str(spec.base_size),
                    "stop_price": str(spec.stop_price),
                    "limit_price": str(spec.limit_price),
                    "stop_direction": _stop_direction(spec),
                }
            }
        case _:
            assert_never(spec)


def _stop_direction(spec: StopLimitGTC) -> str:
    """Coinbase requires the trigger direction explicitly.

    A protective stop on a long exits when price falls, so a SELL stop triggers on the way down.
    """
    return "STOP_DIRECTION_STOP_DOWN" if spec.side is Side.SELL else "STOP_DIRECTION_STOP_UP"
```

Note: `to_order_configuration(object())` falls through to `assert_never`, which raises `AssertionError` at runtime — satisfying Step 2's last test. `UnsupportedOrder` belongs to `adapter.py` (Step 5), not here: this module's job is translation, and refusing an unsupported kind is the adapter's.

- [ ] **Step 4: Run the translation tests**

Run: `uv run pytest tests/broker_coinbase/test_translate.py -v`
Expected: 6 passed

- [ ] **Step 5: Implement the transport Protocol and the adapter**

Create `packages/keel-broker-coinbase/keel_broker_coinbase/transport.py` — copy the `Transport` Protocol and the `_field` helper from `keel/data/cb_client.py:45-80` verbatim. This duplication is deliberate (see the plan's "Transitional duplication" section); `keel/data/cb_client.py` stays untouched.

Create `packages/keel-broker-coinbase/keel_broker_coinbase/adapter.py` implementing `Broker`:

- `capabilities()` returns all four kinds, `supports_native_preview=True`, `synthesizes_preview=False`, `quote_currencies=frozenset({"USD", "USDC"})`, `asset_classes=frozenset({"spot"})`, `venue="coinbase"`.
- `get_candles` mirrors `cb_client.py:125-149` — same transport call, same `_candle_from_raw` mapping, same ascending sort.
- `get_balances` maps Coinbase's account records to `Balance(currency, available, total)`, replacing the dict shape at `cb_client.py:163-183`.
- `preview_order(spec)` calls `to_order_configuration(spec)`, hits the transport, and maps the response to `Preview(..., synthetic=False)` with `errors` from the response's `errs`.
- `place_order(spec)` generates a fresh `client_order_id` per call for idempotency (as `cb_client.py:218` does), and maps the response to `PlaceResult(success, broker_order_id, reason)`.
- Both `preview_order` and `place_order` raise `UnsupportedOrder` if `spec.kind not in self.capabilities().supported_orders`.
- `get_fee_summary()` calls the transport's `get_transaction_summary` and maps the response to
  `FeeSummary`: `taker_rate`/`maker_rate` from `fee_tier.taker_fee_rate`/`maker_fee_rate`,
  `volume_usd` from `advanced_trade_only_volume`, `fees_usd` from `advanced_trade_only_fees`.
  **Set `volume_window="unknown"`.** Coinbase's documentation does not state whether
  `advanced_trade_only_volume` is trailing-30-day or calendar-month, and the honest declaration
  is the one that stops a caller comparing it against a calendar-month cap. Do not guess; the
  subscription spec's §10 tracks confirming it against a live account.
- `capabilities()` sets `supports_fee_summary=True`.

Add `get_transaction_summary` to the copied `Transport` Protocol in `transport.py`.

Export `CoinbaseAdapter` from `keel_broker_coinbase/__init__.py` so the entry point resolves.

- [ ] **Step 6: Write adapter tests against canned fixtures**

Create `tests/broker_coinbase/test_adapter.py`, following the fake-transport pattern already established in `tests/data/test_cb_client.py` and reusing the existing JSON fixtures in `tests/fixtures/cb_*.json`.

Cover: `get_candles` returns ascending `Candle`s; `get_balances` returns `Balance` objects and no dicts; `preview_order` returns `synthetic=False`; `place_order` maps success and failure responses to `PlaceResult`; an unsupported kind raises `UnsupportedOrder`; `get_fee_summary` maps a canned `transaction_summary` response to `FeeSummary` with `volume_window="unknown"` and exact `Decimal` rates.

You will need a new fixture `tests/fixtures/cb_transaction_summary.json`. Build it from the documented response shape — `total_fees`, `fee_tier` (with `pricing_tier`, `taker_fee_rate`, `maker_fee_rate`), `advanced_trade_only_volume`, `advanced_trade_only_fees` — matching the style of the existing `cb_*.json` fixtures.

- [ ] **Step 7: Move `coinbase-advanced-py` out of the root dependencies**

Remove `"coinbase-advanced-py>=1.8.4"` from the root `pyproject.toml`'s `[project] dependencies`, and add `"keel-broker-coinbase"` plus the `[tool.uv.sources]` workspace entry.

The root package still needs the SDK at runtime today (`keel/cli.py:186` and `keel/data/cb_client.py` import it), so depending on `keel-broker-coinbase` keeps it available transitively while removing the direct declaration. Phase B removes even that.

- [ ] **Step 8: Verify**

Run: `uv sync && uv run pytest tests/broker_coinbase/ -v`
Expected: all pass.

Run: `uv run python -c "
from keel_broker_api.registry import discover_brokers
print(sorted(discover_brokers()))
"`
Expected: `['coinbase']`

Run: `uv run pytest tests/baseline/ -v`
Expected: 3 passed, golden unchanged.

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat: keel-broker-coinbase adapter registered as a keel.brokers plugin"
```

---

### Task 4: `keel-broker-fake`

A second adapter that deliberately **disagrees** with Coinbase. Its purpose is to make Coinbase-shaped assumptions in the port fail loudly, and to prove entry-point discovery works with two plugins installed.

If implementing this adapter requires changing `keel-broker-api`, that is the task succeeding, not failing — the port had a Coinbase-ism. Report any such change prominently.

**Files:**
- Create: `packages/keel-broker-fake/pyproject.toml`
- Create: `packages/keel-broker-fake/keel_broker_fake/__init__.py`
- Create: `packages/keel-broker-fake/keel_broker_fake/adapter.py`
- Create: `tests/broker_fake/__init__.py`
- Create: `tests/broker_fake/test_adapter.py`
- Create: `tests/broker_fake/test_two_plugin_discovery.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: Task 1's port and types.
- Produces: `keel_broker_fake.FakeAdapter` implementing `Broker`, registered as entry point `fake`.

- [ ] **Step 1: Create the package**

```bash
mkdir -p packages/keel-broker-fake/keel_broker_fake tests/broker_fake
touch tests/broker_fake/__init__.py
```

`pyproject.toml` mirrors Task 3's but with `name = "keel-broker-fake"`, no `coinbase-advanced-py`, and:

```toml
[project.entry-points."keel.brokers"]
fake = "keel_broker_fake:FakeAdapter"
```

- [ ] **Step 2: Implement the divergent adapter**

Create `packages/keel-broker-fake/keel_broker_fake/adapter.py`. It holds all state in memory — no network, no files.

Its four deliberate disagreements with Coinbase, each of which exists to break a specific assumption:

| Disagreement | Implementation | Assumption it breaks |
|---|---|---|
| No native preview | `supports_native_preview=False`, `synthesizes_preview=False`; `preview_order` raises `NotImplementedError` | That preview is always available |
| Base-size only | `supported_orders` omits `"market_ioc_quote"`; that kind raises `UnsupportedOrder` | That every venue can size a market order in quote currency |
| Stops are separate objects | Internally stores a `StopLimitGTC` as a resting order plus a distinct trigger record, returning one `PlaceResult` | That "a stop is an order type" is universal |
| Different granularities and page size | Supports only `ONE_HOUR` and `ONE_DAY`; `get_candles` returns at most 50 per call | Coinbase's granularity set and pagination limits |
| No fee summary | `supports_fee_summary=False`; `get_fee_summary` raises `NotImplementedError` | That every venue reports fees and volume — subscription lapse detection must degrade to attestation alone |

`capabilities()`: `venue="fake"`, `supported_orders=frozenset({"market_ioc_base", "limit_gtc", "stop_limit_gtc"})`, `supports_fee_summary=False`, `quote_currencies=frozenset({"USD"})`, `asset_classes=frozenset({"spot"})`.

`get_candles` raises `ValueError` for a granularity it does not support, naming the supported set — a venue that cannot serve a timeframe must say so, not return empty.

- [ ] **Step 3: Test the adapter's divergences**

Create `tests/broker_fake/test_adapter.py` asserting each row of the table above: `market_ioc_quote` raises `UnsupportedOrder`; `preview_order` raises; an unsupported granularity raises `ValueError`; `get_candles` never returns more than 50; `place_order` on a `StopLimitGTC` returns a successful `PlaceResult`.

- [ ] **Step 4: Test two-plugin discovery**

Create `tests/broker_fake/test_two_plugin_discovery.py`:

```python
"""The plugin mechanism with one plugin is as much a guess as a port with one adapter."""

from __future__ import annotations

import pytest
from keel_broker_api.registry import discover_brokers, load_broker


def test_both_adapters_are_discovered() -> None:
    assert {"coinbase", "fake"} <= set(discover_brokers())


def test_load_broker_returns_the_right_class_per_venue() -> None:
    assert load_broker("coinbase").__name__ == "CoinbaseAdapter"
    assert load_broker("fake").__name__ == "FakeAdapter"


def test_adapters_declare_different_capabilities() -> None:
    """If these matched, the fake would not be exerting any design pressure."""
    coinbase = load_broker("coinbase")().capabilities()
    fake = load_broker("fake")().capabilities()
    assert coinbase.supported_orders != fake.supported_orders
    assert coinbase.supports_native_preview and not fake.can_preview


def test_unknown_venue_still_raises() -> None:
    with pytest.raises(LookupError):
        load_broker("nonesuch")
```

If `CoinbaseAdapter()` cannot be constructed without credentials, give both adapters a
zero-argument construction path for this test (e.g. the Coinbase adapter accepting an injected
transport that defaults to `None` and is only required at call time). Report it if that forces a
signature change.

- [ ] **Step 5: Verify**

Run: `uv sync && uv run pytest tests/broker_fake/ -v`
Expected: all pass.

Run: `uv run pytest tests/baseline/ -v`
Expected: 3 passed, golden unchanged.

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: keel-broker-fake, a deliberately divergent second adapter"
```

---

### Task 5: The conformance suite

A shared executable contract shipped from `keel-broker-api`, so any adapter — including a third party's — can prove itself against it.

**Files:**
- Create: `packages/keel-broker-api/keel_broker_api/conformance/__init__.py`
- Create: `packages/keel-broker-api/keel_broker_api/conformance/suite.py`
- Create: `tests/conformance/__init__.py`
- Create: `tests/conformance/test_coinbase_conformance.py`
- Create: `tests/conformance/test_fake_conformance.py`

**Interfaces:**
- Consumes: Tasks 1, 3, 4.
- Produces: `keel_broker_api.conformance.BrokerConformanceTests` — a mixin test class an adapter's test module subclasses, supplying a `broker` fixture.

- [ ] **Step 1: Write the conformance suite**

Create `packages/keel-broker-api/keel_broker_api/conformance/suite.py` defining `BrokerConformanceTests`, a class of `test_*` methods that run against whatever `self.broker()` returns. Subclasses supply the adapter.

The contract it enforces:

1. **`capabilities()` cannot lie about orders.** Every kind in `supported_orders` is accepted by `place_order`; every kind in `ORDER_KINDS - supported_orders` raises `UnsupportedOrder`. This is the single most important test in the suite — it is what stops an adapter silently substituting an order type.
2. **`capabilities()` cannot lie about preview.** If `can_preview` is false, `preview_order` raises. If `supports_native_preview`, it returns `synthetic=False`. If `synthesizes_preview`, it returns `synthetic=True`.
3. **No dicts cross the port.** `get_balances()` returns only `Balance` instances; `place_order` returns `PlaceResult`; `preview_order` returns `Preview`.
4. **`get_candles` returns ascending candles** and never exceeds any page limit the adapter declares.
5. **`supported_orders` is a subset of `ORDER_KINDS`** — an adapter cannot invent kinds.
6. **`capabilities()` cannot lie about fee summaries.** If `supports_fee_summary` is false,
   `get_fee_summary` raises. If true, it returns a `FeeSummary` whose `volume_window` is one of
   the three legal values and whose rates are `Decimal`.

Where a check needs to place a real order, the suite requires the adapter to be constructed in a sandbox or in-memory mode; the suite never places live orders. Document that requirement in the module docstring.

- [ ] **Step 2: Wire both adapters into the suite**

Create `tests/conformance/test_fake_conformance.py`:

```python
from __future__ import annotations

from keel_broker_api.conformance.suite import BrokerConformanceTests
from keel_broker_fake import FakeAdapter


class TestFakeConformance(BrokerConformanceTests):
    def broker(self) -> FakeAdapter:
        return FakeAdapter()
```

Create `tests/conformance/test_coinbase_conformance.py` similarly, constructing `CoinbaseAdapter` with the canned fake transport used in `tests/broker_coinbase/test_adapter.py` — never a live client.

- [ ] **Step 3: Run the suite against both**

Run: `uv run pytest tests/conformance/ -v`
Expected: both classes pass every conformance test.

**If the Coinbase adapter passes and the fake does not, read the failure carefully before fixing the fake** — a fake failing a conformance test is the most likely place a Coinbase-ism in the port surfaces. If the port is wrong, fix the port and report it prominently rather than bending the fake to match Coinbase.

- [ ] **Step 4: Verify the suite can actually fail**

Temporarily break the fake adapter — make it accept `market_ioc_quote` despite not declaring it — and confirm the conformance suite fails. Restore, and confirm green.

Record both outcomes. A conformance suite that cannot fail proves nothing, and this suite is the deliverable of the whole plan.

- [ ] **Step 5: Verify**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy`
Expected: all pass.

Run: `uv run pytest tests/baseline/ -v`
Expected: 3 passed, golden unchanged.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: broker conformance suite, passing for both adapters"
```

---

## Done criteria

- `uv run pytest -q`, `uv run ruff check .`, and `uv run mypy` all pass.
- `tests/fixtures/baseline_backtest.json` is byte-identical to its original generation.
- `discover_brokers()` returns both `coinbase` and `fake`.
- Both adapters pass the conformance suite, and the suite has been shown to fail when an adapter lies about its capabilities.
- `coinbase-advanced-py` is no longer a direct root dependency.
- **Nothing under `keel/` imports any of the three new packages.** Phase A changes no behaviour.

Verify the last point explicitly:

```bash
grep -rn "keel_broker" keel/ --include="*.py" | grep -v __pycache__
```
Expected: no output.

## Follow-on

Phase B (spec steps 6–9) migrates the engine onto the validated port: retiring `executor.py`'s three `_*_order_configuration` builders and `_initial_status`, typing `agent.py`'s broker parameter as `Broker`, deleting `keel/data/cb_client.py` and the transitional duplication, capability gating in `engine.evaluate`, and the preview/confirm gate. It carries all of this work's behavioural risk and warrants its own plan.

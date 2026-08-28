"""The eight read-only tools `keel mcp` exposes (#477) -- state, logs and reports, never action.

**Read-only is a shape, not a promise.** Every handler here reads: an already-migrated
database opened WITHOUT `migrate` (the `keel/web/server.py` rule -- a view that calls itself
read-only must not take a schema write lock four times a minute), config files, the engine's
JSONL log, the trials ledger and the research corpora. There is no code path from any of
these handlers to the executor, and `tests/mcp/test_readonly.py` scans this package to keep
it that way.

**Why these seams.** Each tool delegates to the SAME service calls an operator's front-end
makes -- `keel.commands.doctor` for health, `keel.capabilities` for the inventory,
`keel.compliance.purification` for §65.9, `keel.research.ledger` for experiments, and
`keel.corpus` for the corpora (the `research_console` front-end was deleted at #541) -- so
an assistant and a terminal cannot be
shown two different accounts of one deployment. The JSON-safety helper stringifies `Decimal`
recursively, matching the repo's TEXT-money convention: a number that has been through JSON
floating point is a number that has been rounded twice.

**Deployment wiring.** `build_tools()` closes over the three paths (db, config, log) and
resolves any left as `None` AT CALL TIME through `keel_core.paths`, the same callable-default
discipline the CLI's `--db`/`--config` follow -- never at import, which would freeze whatever
directory the process happened to start in. Handlers open the repo PER CALL and drop it;
there are no long-lived handles, exactly as `keel/web/server.py` opens per request.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from keel.capabilities import CAPABILITIES
from keel.compliance.purification import build_report
from keel.research import ledger as trials_ledger

#: The engine log the veto tools read -- `keel doctor`'s own default, kept identical so the
#: two front-ends sweep the same file an operator would hand you.
DEFAULT_LOG_PATH = "logs/keel.log"

#: Boundaries. A research assistant's context is finite and shared with the conversation
#: around it, so every list tool caps what one call can pull, the same spirit as the browser
#: view's per-page caps.
ORDERS_DEFAULT_LIMIT = 50
ORDERS_MAX_LIMIT = 200
VETO_DEFAULT_LIMIT = 100
#: The same cap for the veto log. An UNBOUNDED `limit` here was a context-exhaustion vector:
#: one call with `limit=10**9` would happily marshal a whole engine log into the response.
VETO_MAX_LIMIT = 500
TRIALS_DEFAULT_TAIL = 20
TRIALS_MAX_TAIL = 100
#: `verify_chain` reports one error PER broken row, and a corrupted ledger is broken from the
#: damage onward -- a tool response is not the place to restate every one of them.
TRIALS_MAX_CHAIN_ERRORS = 20

#: The corpora `keel.corpus.corpus_path` resolves -- repeated here only so
#: the schema an MCP client sees can enumerate them.
CORPORA = ("research", "experiments", "reports")

#: One day back, the window the veto tool defaults to. Doctor judges seven; a research
#: assistant asking "what happened" almost always means "recently".
VETO_WINDOW_SEC = 24 * 3600

_EMPTY_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}


@dataclass(frozen=True)
class Tool:
    """One exposed tool: a name, the description a model reads before choosing it, a JSON
    Schema for the arguments, and the handler. Plain dataclass -- no pydantic, no SDK; the
    transport in `keel/mcp/server.py` is stdlib for the same reason `keel/web/` is."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Any]


# -- JSON safety ---------------------------------------------------------------------------------


def json_safe(value: Any) -> Any:
    """`Decimal` and date-ish values to strings, recursively -- the repo's TEXT-money
    convention, applied at the boundary so a handler can stay typed internally."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _deployment(
    db_path: str | None, config_path: str | None, log_path: str | None
) -> tuple[str, str, str]:
    """Resolve the three paths, per call. `None` means "the deployment this process is in",
    answered by `keel_core.paths` at CALL time -- a callable default, never an import-time
    freeze (the CLI's `--db` discipline, see `keel/commands/_common.py`)."""
    db = db_path
    config = config_path
    if db is None or config is None:
        from keel_core import paths

        db = db if db is not None else str(paths.default_db_path())
        config = config if config is not None else str(paths.default_config_path())
    return db, config, log_path if log_path is not None else DEFAULT_LOG_PATH


def _open_readonly_repo(db_path: str) -> Any:
    """A Repository over a plain connection -- deliberately WITHOUT `migrate`, and only over a
    database that already exists. `sqlite3.connect` CREATES the file it cannot find, which
    would make the read-only server a writer the first time it was pointed at a typo; the
    existence check turns that into a calm error instead.

    Read-only is enforced at TWO layers. `connect()` runs `PRAGMA journal_mode = WAL`, which is
    a FILE-level metadata side effect the `total_changes` pin cannot see: opening a
    rollback-journal database flips it to WAL, exactly as the existing keel web surface does
    when it connects the same way -- stated here rather than hidden. `PRAGMA query_only = ON`
    then makes SQLite itself reject every subsequent write on THIS connection (row writes and
    schema writes alike) at the engine level, so read-only does not depend on this package's
    call discipline alone."""
    from keel.data.db import connect
    from keel.data.repository import Repository

    if not Path(db_path).exists():
        raise FileNotFoundError(
            f"no deployment database at {db_path} -- this surface only reads existing state; "
            "see `keel init`"
        )
    conn = connect(db_path)
    conn.execute("PRAGMA query_only = ON")
    return Repository(conn)


def _log_lines(log_path: str) -> list[str]:
    try:
        with open(log_path, encoding="utf-8", errors="replace") as handle:  # noqa: SIM115
            return handle.readlines()
    except OSError:
        return []


# -- the factory ---------------------------------------------------------------------------------


def build_tools(
    db_path: str | None = None, config_path: str | None = None, log_path: str | None = None
) -> tuple[Tool, ...]:
    """The eight tools, their handlers closing over the deployment paths. Called by the
    server with explicit paths and by `TOOLS` below with none (resolved per call)."""

    def _doctor(_args: dict[str, Any]) -> dict[str, Any]:
        from keel.commands import doctor as doctor_mod

        db, config_file, log = _deployment(db_path, config_path, log_path)
        repo = _open_readonly_repo(db)
        from keel.config import load_config

        now_ts = int(time.time())
        findings = doctor_mod.gather_findings(
            repo, load_config(config_file), _log_lines(log), now_ts
        )
        counts = {
            status: sum(1 for finding in findings if finding.status == status)
            for status in ("ok", "warn", "fail", "halted")
        }
        return {
            "counts": counts,
            "findings": [
                {
                    "name": finding.name,
                    "status": finding.status,
                    "headline": finding.headline,
                    "detail": finding.detail,
                    "fix": finding.fix,
                }
                for finding in findings
            ],
        }

    def _capabilities(_args: dict[str, Any]) -> dict[str, Any]:
        return {
            "rows": [
                {
                    "module": cap.module,
                    "function": cap.function,
                    "surface": cap.surface,
                    "invocation": cap.invocation,
                    "increases": cap.increases,
                    "gate": cap.gate,
                }
                for cap in CAPABILITIES
            ]
        }

    def _profiles(_args: dict[str, Any]) -> dict[str, Any]:
        from keel.config import load_config

        _db, config_file, _log = _deployment(db_path, config_path, log_path)
        directory = Path(config_file).parent
        profiles: list[dict[str, Any]] = []
        failed: list[dict[str, str]] = []
        for path in sorted(directory.glob("config*.yaml")):
            try:
                config = load_config(path)
            except Exception as exc:  # one unreadable profile must not hide the rest
                failed.append({"file": path.name, "error": str(exc)})
                continue
            profiles.append(
                {
                    "file": path.name,
                    "allowlist": list(config.allowlist),
                    "risk_pct": config.risk_pct,
                    "caps": {
                        "max_exposure_usd": config.caps.max_exposure_usd,
                        "max_per_order_usd": config.caps.max_per_order_usd,
                        "max_per_day_usd": config.caps.max_per_day_usd,
                        "max_per_asset_pct": config.caps.max_per_asset_pct,
                    },
                    "dca_budget_usd": config.dca.budget_usd,
                    "granularities": [gran.value for gran in config.market_data.granularities],
                    "auto_cycle_interval_sec": config.auto_trade.interval_sec,
                }
            )
        return {"directory": str(directory), "profiles": profiles, "failed": failed}

    def _orders(args: dict[str, Any]) -> dict[str, Any]:
        db, _config, _log = _deployment(db_path, config_path, log_path)
        limit = min(max(int(args.get("limit") or ORDERS_DEFAULT_LIMIT), 1), ORDERS_MAX_LIMIT)
        repo = _open_readonly_repo(db)
        rows = repo.get_orders(
            mode=args.get("mode"), product_id=args.get("product_id"), status=args.get("status")
        )
        return {"count": len(rows), "orders": rows[-limit:]}

    def _veto_log(args: dict[str, Any]) -> dict[str, Any]:
        import json

        _db, _config, log = _deployment(db_path, config_path, log_path)
        since_ts = float(args.get("since_ts") or (time.time() - VETO_WINDOW_SEC))
        limit = min(max(int(args.get("limit") or VETO_DEFAULT_LIMIT), 1), VETO_MAX_LIMIT)
        events: list[dict[str, Any]] = []
        try:
            with open(log, encoding="utf-8", errors="replace") as handle:  # noqa: SIM115
                for line in handle:
                    try:
                        event = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if not isinstance(event, dict) or event.get("event") != "executor.order_vetoed":
                        continue
                    if float(event.get("ts") or 0) < since_ts:
                        continue
                    events.append(event)
        except OSError:
            return {
                "log_path": log,
                "since_ts": since_ts,
                "count": 0,
                "events": [],
                "note": f"no readable log at {log}",
            }
        window = events[-limit:]
        return {"log_path": log, "since_ts": since_ts, "count": len(window), "events": window}

    def _purification(_args: dict[str, Any]) -> dict[str, Any]:
        db, _config, _log = _deployment(db_path, config_path, log_path)
        repo = _open_readonly_repo(db)
        report = build_report(repo.get_transactions())
        return {
            "total_owed_usd": report.total_owed_usd,
            "owed_by_asset": report.owed_by_asset,
            "qty_by_asset": report.qty_by_asset,
            "needs_review": len(report.needs_review),
            "report_only": "the agent never disposes of funds; moving them is the operator's act",
        }

    def _trials(args: dict[str, Any]) -> dict[str, Any]:
        tail = min(max(int(args.get("tail") or TRIALS_DEFAULT_TAIL), 1), TRIALS_MAX_TAIL)
        ledger = Path(trials_ledger.DEFAULT_LEDGER_PATH)
        raw_path = args.get("path")
        if raw_path is None:
            path = ledger
        else:
            # The same confinement `reports` applies: a bare file name resolved inside the
            # ledger's own directory, never a client-chosen filesystem path -- `path` arrives
            # over a pipe from a model, and "/etc/passwd" must be refused, not read.
            name = str(raw_path)
            if name != Path(name).name or name in (".", ".."):
                raise ValueError(
                    f"path: {name!r} -- a bare file name beside the default ledger, not a path"
                )
            path = ledger.parent / name
        trials = trials_ledger.read_trials(path)
        rows, decisions = trials_ledger.trial_counts(trials)
        chain_errors = trials_ledger.verify_chain(path)
        if len(chain_errors) > TRIALS_MAX_CHAIN_ERRORS:
            hidden = len(chain_errors) - TRIALS_MAX_CHAIN_ERRORS
            chain_errors = chain_errors[:TRIALS_MAX_CHAIN_ERRORS] + [f"+{hidden} more chain errors"]
        return {
            "path": str(path),
            "rows": rows,
            "decisions": decisions,
            "chain_errors": chain_errors,
            "recent": [asdict(trial) for trial in trials[-tail:]],
        }

    def _reports(args: dict[str, Any]) -> dict[str, Any]:
        from keel.corpus import corpus_path, list_documents, read_document_lines

        corpus = str(args.get("corpus") or "research")
        if corpus not in CORPORA:
            raise ValueError(f"corpus: {corpus!r} -- expected one of {list(CORPORA)}")
        directory = corpus_path(corpus)
        document = args.get("document")
        if document is None:
            return {
                "corpus": corpus,
                "directory": str(directory),
                "documents": [
                    {"name": doc.path.name, "bytes": doc.size_bytes, "mtime_ts": doc.mtime_ts}
                    for doc in list_documents(directory)
                ],
            }
        name = str(document)
        if name != Path(name).name or name in (".", ".."):
            raise ValueError(f"document: {name!r} -- a bare file name, not a path")
        target = directory / name
        if not target.is_file():
            raise FileNotFoundError(f"no document named {name!r} in the {corpus} corpus")
        return {
            "corpus": corpus,
            "document": name,
            "lines": read_document_lines(target),
        }

    return (
        Tool(
            name="doctor",
            description=(
                "Deployment health findings, exactly as `keel doctor` computes them: subscription "
                "freshness, rail states, allowance headroom, recent veto patterns, data health and "
                "sizing admissibility, each naming the CLI fix that resolves it. Needs the "
                "deployment's db and config."
            ),
            input_schema=dict(_EMPTY_SCHEMA),
            handler=_doctor,
        ),
        Tool(
            name="capabilities",
            description=(
                "The #453 capability inventory as structured rows: every action that widens what "
                "keel can do without asking again, which front-end reaches it, and the single gate "
                "covering it. Needs no database."
            ),
            input_schema=dict(_EMPTY_SCHEMA),
            handler=_capabilities,
        ),
        Tool(
            name="profiles",
            description=(
                "The deployment's config*.yaml files, one row each: allowlist, risk_pct, caps, DCA "
                "budget, granularities and the auto-cycle interval. A file that will not load is "
                "listed with its error. Credentials and .env are never read."
            ),
            input_schema=dict(_EMPTY_SCHEMA),
            handler=_profiles,
        ),
        Tool(
            name="orders",
            description=(
                "Rows from the orders audit log, filtered by mode, product and status, newest "
                "last, bounded by limit. Decimal money fields arrive as strings."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "mode": {"type": "string"},
                    "product_id": {"type": "string"},
                    "status": {"type": "string"},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": ORDERS_MAX_LIMIT,
                        "default": ORDERS_DEFAULT_LIMIT,
                    },
                },
                "additionalProperties": False,
            },
            handler=_orders,
        ),
        Tool(
            name="veto_log",
            description=(
                "Parsed executor.order_vetoed events from the engine's JSONL log since a "
                "timestamp, most recent last, with each event's rail violations. An absent or "
                "unreadable log is a calm empty list."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "since_ts": {"type": "number"},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": VETO_MAX_LIMIT,
                        "default": VETO_DEFAULT_LIMIT,
                    },
                },
                "additionalProperties": False,
            },
            handler=_veto_log,
        ),
        Tool(
            name="purification",
            description=(
                "The KB §65.9 income purification report over the transaction ledger: what is "
                "owed by asset, units received from tainted sources, and the entries awaiting "
                "human classification. Report-only, like the CLI."
            ),
            input_schema=dict(_EMPTY_SCHEMA),
            handler=_purification,
        ),
        Tool(
            name="trials",
            description=(
                "The research trials ledger: row and decision counts, hash-chain verification "
                "errors (the first 20, then a count of the rest), and the most recent rows. "
                "Experiments only -- money has its own ledger."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "a bare file name beside the default ledger -- never a filesystem path"
                        ),
                    },
                    "tail": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": TRIALS_MAX_TAIL,
                        "default": TRIALS_DEFAULT_TAIL,
                    },
                },
                "additionalProperties": False,
            },
            handler=_trials,
        ),
        Tool(
            name="reports",
            description=(
                "The research corpora: list documents under the research, experiments or reports "
                "corpus, or read one bounded document (bare name only, never a path)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "corpus": {"type": "string", "enum": list(CORPORA)},
                    "document": {"type": "string"},
                },
                "required": ["corpus"],
                "additionalProperties": False,
            },
            handler=_reports,
        ),
    )


#: The registry tests and the docs pin read: the same eight tools over the deployment the
#: process resolves at call time. The server never uses this directly -- it calls
#: `build_tools` with the paths it was wired with.
TOOLS: tuple[Tool, ...] = build_tools()

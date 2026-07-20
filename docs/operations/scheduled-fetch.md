# Scheduled data refresh (`keel fetch`)

Keeps cached candle history current for every allowlisted product, on a clock, without
touching money.

## Why this is safe to schedule

`keel fetch` reads the venue's **public market-data endpoints** and writes candles to the local
DB. It places no orders, evaluates no rules, touches no rails, and makes no promotion decision.
That is the whole reason it is allowed to run unattended: it is decoupled from money by
construction, matching the guardrail agreed on 2026-07-18 — *"data-refresh is read-only/safe,
decoupled from money."*

## What it deliberately does NOT do

⛔ **It does not run simulations, and it must not be extended to.**

A simulation on a clock is a different animal. The strategies trade ~6 times a year across the
whole allowlist, so roughly **61 days pass between events that could change a verdict**. A
daily re-run would recompute an unchanged answer some sixty times between two data points that
matter — while creating sixty opportunities to catch a favourable fluctuation and read it as
news.

That is **optional stopping**, and it inflates false positives in exactly the way a parameter
sweep does (KB §73/§78). A sweep searches configurations; a scheduled re-evaluation searches
*dates*. The arithmetic does not care which axis you searched along.

When scheduled validation is eventually built, the agreed shape is:

- **event-driven, not clock-driven** — re-run when N new closed trades have accumulated
  (pre-declared, e.g. 10) or quarterly, whichever comes first;
- **asymmetric** (§5) — continuous monitoring may freely trigger anything that *reduces* risk
  (demote, pause, flag, alert on staleness or edge decay), and may **never** be the trigger for
  promotion or any increase in exposure, which keeps the full backtest → paper → gate → human
  confirm sequence.

## Usage

```bash
# Dry run. NEVER touches the network. Exits non-zero if anything needs a fetch.
uv run keel --db keel.db fetch --check

# Fetch whatever is missing, stale or gapped. No-ops when everything is current.
uv run keel --db keel.db fetch

# Re-pull from scratch, ignoring the cache.
uv run keel --db keel.db fetch --refresh
```

Output states per `(product, granularity)`:

| state | meaning | fails `--check`? |
|---|---|---|
| `ok` | within the lag tolerance, no gaps | no |
| `STALE` | more than `--tolerance-bars` behind the newest complete bar | **yes** |
| `GAPS` | current, but with holes inside the cached range | no (`--fail-on-gaps` to opt in) |
| `MISSING` | nothing cached at all | **yes** |

The detail column always reports **both** lag and gap count, since a series can be stale *and*
gapped and the state label only names the more urgent one.

## Gap repair

`history.ensure_history` fills **forward** from the newest cached bar and probes **backward**
from the oldest. Neither motion touches a hole in the middle, so a series can be perfectly
current and still be missing bars.

```bash
uv run keel --db keel.db fetch --repair-gaps      # probe each hole individually
uv run keel --db keel.db fetch --repair-gaps --reprobe-absent   # ignore prior absence records
```

**Some windows are permanently empty at the venue** — exchange downtime, a thin book, a listing
boundary. Re-asking for those on every scheduled run is a treadmill, so a window that comes back
still-incomplete after a *completed* probe is recorded in `candle_gap_probes` as **absent at
source** and skipped thereafter.

⚠️ That record asserts an **observation** ("we asked and it had nothing"), never an assumption.
It is only written after a request that actually completed — a fetch that raised proves nothing
and is deliberately not recorded. The v5 migration backfills the table with nothing for the same
reason: no pre-existing gap has ever been probed.

**`--fail-on-gaps` judges UNEXPLAINED gaps only** — missing bars not yet proven absent. That is
what makes it satisfiable, and therefore usable as a default alert. Use `--reprobe-absent` if you
suspect a transient venue failure poisoned a record rather than a genuine hole in history.

**On the lag tolerance (default 2 bars).** The most recent bar is still *forming* — at 14:30 the
1-hour bar stamped 14:00 is incomplete and the venue may not serve it — so a correctly-updated
cache is normally one bar behind, and two across a fetch straddling a bar boundary. Alerting at
zero lag would fire constantly and train you to ignore it.

## launchd (macOS)

Save as `~/Library/LaunchAgents/com.keel.fetch.plist`, replacing `<REPO>` with the absolute
repo path and `<UV>` with the output of `which uv`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.keel.fetch</string>

    <key>ProgramArguments</key>
    <array>
        <string><UV></string>
        <string>run</string>
        <string>keel</string>
        <string>--db</string>
        <string><REPO>/keel.db</string>
        <string>fetch</string>
    </array>

    <key>WorkingDirectory</key>
    <string><REPO></string>

    <!-- 06:00 local, daily. Hourly candles lag ~1 bar; there is no benefit to running more
         often, and no decision depends on this being minutes-fresh. -->
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key><integer>6</integer>
        <key>Minute</key><integer>0</integer>
    </dict>

    <!-- Do NOT set RunAtLoad: a fetch firing on every login is noise, and the daily run is
         sufficient. -->
    <key>StandardOutPath</key>
    <string><REPO>/logs/fetch.log</string>
    <key>StandardErrorPath</key>
    <string><REPO>/logs/fetch.err</string>
</dict>
</plist>
```

Load, verify, unload:

```bash
launchctl load   ~/Library/LaunchAgents/com.keel.fetch.plist
launchctl list | grep com.keel.fetch
launchctl start com.keel.fetch          # fire once now, to check it works
launchctl unload ~/Library/LaunchAgents/com.keel.fetch.plist
```

`logs/` is already gitignored.

⚠️ **launchd needs network access and a keychain-unlocked session.** If the laptop is asleep at
06:00 the job runs at next wake, which is fine — nothing here is time-critical.

## Alerting

Run the dry check separately if you want a signal without a fetch:

```bash
uv run keel --db keel.db fetch --check || echo "keel data is stale"
```

`--check` never opens a network connection (enforced by a test that fails if the broker is
constructed), so it is safe to run anywhere, including offline.

## Known state, 2026-07-20

The first real run surfaced two things worth recording.

**Staleness — now fixed.** Hourly series were **69 bars behind** (~3 days), the expected drift
from having had no scheduled refresh. A live `keel fetch` brought all six series to 0 bars
behind.

**Gaps — repaired.** A live `--repair-gaps` pass:

| series | gaps before | recovered | remaining | remaining status |
|---|---:|---:|---:|---|
| BTC-USD ONE_DAY | 6 | 6 | **0** | clean |
| ETH-USD ONE_DAY | 6 | 6 | **0** | clean |
| PAXG-USD ONE_DAY | 1 | 1 | **0** | clean |
| BTC-USD ONE_HOUR | 158 | 145 | 13 | all proven absent at venue |
| ETH-USD ONE_HOUR | 158 | 145 | 13 | all proven absent at venue |
| PAXG-USD ONE_HOUR | 54 | 34 | 20 | all proven absent at venue |

⭐ **Every daily series is now complete**, which is the one that matters most: the Turtle is a
daily rule, and the engine validation and PBO runs are computed on daily bars. The remaining
hourly holes are all *proven* absent at the venue, so `--fail-on-gaps` now **exits 0** and is
usable as the default alerting mode.

**The earlier caveat has been discharged.** The first PBO run was computed over the gapped daily
series; it was re-run on the repaired data and every conclusion held (PBO 0.8812 → 0.8926, slope
−0.0006 → −0.0001, dominance still False/False, G4 still PASS). See
`docs/experiments/2026-07-20-first-pbo-run.md`.

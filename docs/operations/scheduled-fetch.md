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

## Why gaps do not fail `--check` by default

`history.ensure_history` fills **forward** from the newest cached bar and probes **backward**
from the oldest. It does **not** repair holes in the middle. So a gapped-but-current series
would report "needs fetch" forever while fetching changed nothing — a permanently red alert is
an alert you learn to ignore, the same reasoning as the lag tolerance.

Gaps are therefore **reported prominently but do not fail the check**. Pass `--fail-on-gaps` if
you want strictness. Repairing internal gaps needs targeted re-fetching of the specific missing
windows, which this command does not yet do.

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

**Gaps — open, and not repairable by this command.** After the fetch:

| series | bars | internal gaps |
|---|---:|---:|
| BTC-USD ONE_HOUR | 43,642 | 158 |
| ETH-USD ONE_HOUR | 43,642 | 158 |
| PAXG-USD ONE_HOUR | 10,455 | 54 |
| BTC-USD ONE_DAY | 1,819 | 6 |
| ETH-USD ONE_DAY | 1,819 | 6 |
| PAXG-USD ONE_DAY | 438 | 1 |

⚠️ **Every backtest, the engine validation, and the first PBO run were computed over series with
these holes.** At ~0.36% of hourly bars and ~0.33% of daily bars this is unlikely to change any
verdict, and no result is being restated because of it — but it was **invisible before this
command existed**, and it should be measured rather than assumed benign. Repairing it needs
targeted re-fetching of the specific missing windows, which is not yet built.

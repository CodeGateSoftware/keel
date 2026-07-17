# Engine Validation & Trade-Simulation Report

## Verdict

**TRAIN MORE** (IN-SAMPLE)

Failing gates:
- expectancy -289.2840203035414549776956917 <= min_expectancy 0.0
- rr 0.8470970772221014638537441930 < min_rr 1.5
- win_rate 0.16353887399463807 < min_win_rate 0.55

- data_sufficient: True
- G2 (promotion floors): FAIL
- G3 (risk-adjusted edge): PASS

## Data coverage

| Asset | Granularity | First ts | Last ts | Candles | Gaps |
|---|---|---|---|---|---|
| BTC | ONE_DAY | 1626652800 | 1784246400 | 1819 | 6 |
| BTC | ONE_HOUR | 1626627600 | 1784304000 | 43642 | 158 |
| ETH | ONE_DAY | 1626652800 | 1784246400 | 1819 | 6 |
| ETH | ONE_HOUR | 1626627600 | 1784304000 | 43642 | 158 |
| PAXG | ONE_DAY | 1746662400 | 1784246400 | 435 | 1 |
| PAXG | ONE_HOUR | 1746727200 | 1784304000 | 10385 | 54 |

## Edge table

Per-rule and pooled backtest stats (unit-less R-multiples). `__pooled__` is the pooled sample G2 is checked against.

| Rule | N | Win% | Expectancy | Avg win | Avg loss | Profit factor | Max DD | Losing streak | Avg MFE | Avg MAE |
|---|---|---|---|---|---|---|---|---|---|---|
| pullback_continuation:BTC | 84 | 7.1% | -818.9798929860714285714285714 | 606.453726460 | -928.6286329434615384615384615 | 0.05023567548515325455779559373 | 69338.699730350 | 36 | 514.2715614285714285714285714 | 518.4693056547619047619047619 |
| pullback_continuation:ETH | 97 | 12.4% | -33.66125775835051546391752577 | 17.93830454666666666666666667 | -40.94590184847058823529411765 | 0.06184908403305535775867875846 | 3265.142002560 | 18 | 26.15885149484536082474226804 | 28.16152902061855670103092784 |
| pullback_continuation:PAXG | 24 | 0.0% | -50.5899274075 | 0 | -50.5899274075 | 0E+9 | 1214.158257780 | 24 | 25.019345 | 22.99648833333333333333333333 |
| rsi_meanrev:BTC | 51 | 25.5% | -592.3731984052649883673402941 | 1152.156281408370541174117692 | -1189.185915183613985315733816 | 0.3314525700460997302633460096 | 30211.0331186685144067343550 | 9 | 662.1400103921568627450980392 | 875.9389605882352941176470588 |
| rsi_meanrev:ETH | 96 | 26.0% | -39.28201761982726760419644271 | 88.48967778919755398969224 | -84.27205121455431746119950 | 0.3697351233375010839454927144 | 3811.55247201570375303334850 | 10 | 58.52091552083333333333333333 | 69.96614583333333333333333333 |
| rsi_meanrev:PAXG | 21 | 23.8% | -30.82007104185860047348950 | 74.72950579028439652606680 | -63.80431380190328703585084375 | 0.3660092737925073211677642685 | 647.22149187903060994327950 | 9 | 61.76169119047619047619047619 | 31.84346904761904761904761905 |
| dca:BTC | 0 | 0.0% | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| dca:ETH | 0 | 0.0% | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| dca:PAXG | 0 | 0.0% | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| **__pooled__** | 373 | 16.4% | -289.2840203035414549776956917 | 351.1131663165602929120683607 | -414.4898805081126300458867404 | 0.1656183388158595810739692172 | 108447.3282927409627066804930 | 54 | 233.2998068900804289544235925 | 265.1296395442359249329758713 |

## Account results

| Metric | Value |
|---|---|
| Contributed | 30500 |
| Ending value | 30555.04223705079532004855808 |
| Net P&L ($) | 55.04223705079532004855808 |
| Total return | 0.001804663509862141640936330492 |
| IRR | 0.0000562716566026210784912109375 |
| CAGR | 0.000360672438903270440231725 |
| Max drawdown | 0.02893279092586393900329221706 |
| Return / drawdown | 0.06237433210250365857605822185 |
| Sharpe | 1.698275827305566701208128588 |
| Sortino | 64.58191030364757716745045000 |
| Trade count | 0 |
| Avg hold (hrs) | 0 |

## Benchmark comparison

Engine vs `dca_into_allowlist`:

| Metric | Engine | Benchmark |
|---|---|---|
| Total return | 0.001804663509862141640936330492 | -0.09210189319750424078836838967 |
| Max drawdown | 0.02893279092586393900329221706 | 0.5535750275613180037411248517 |
| Sharpe | 1.698275827305566701208128588 | 1.525712327705399159594592505 |
| Sortino | 64.58191030364757716745045000 | 2.893372754087957676231330567 |
| Return / drawdown | 0.06237433210250365857605822185 | -0.1663765318375066403925180304 |

## Knowledge & data gaps -> training backlog

| Kind | Evidence | Recommendation |
|---|---|---|
| idle_through_move | ETH: no rule fired from ts=1626627600 to ts=1626742800 while price moved 6.33% | No existing rule covers this span's regime/phase -- inspect it (e.g. strong-trend-no-pullback, parabolic-blowoff) and implement + backtest the deferred macro-cycle / trailing-exit knowledge for this asset. |
| idle_through_move | BTC: no rule fired from ts=1626627600 to ts=1626750000 while price moved 6.33% | No existing rule covers this span's regime/phase -- inspect it (e.g. strong-trend-no-pullback, parabolic-blowoff) and implement + backtest the deferred macro-cycle / trailing-exit knowledge for this asset. |
| idle_through_move | ETH: no rule fired from ts=1626742800 to ts=1626847200 while price moved 5.31% | No existing rule covers this span's regime/phase -- inspect it (e.g. strong-trend-no-pullback, parabolic-blowoff) and implement + backtest the deferred macro-cycle / trailing-exit knowledge for this asset. |
| idle_through_move | BTC: no rule fired from ts=1626750000 to ts=1626858000 while price moved 5.78% | No existing rule covers this span's regime/phase -- inspect it (e.g. strong-trend-no-pullback, parabolic-blowoff) and implement + backtest the deferred macro-cycle / trailing-exit knowledge for this asset. |
| unfed_cts_factor | CTS factor 'rsi_extreme' was never present (0 populated occurrences this run) | Factor 'rsi_extreme' contributed 0 signal all run -- wire it and backtest, or drop it from the scorer. |
| unfed_cts_factor | CTS factor 'rsi_divergence' was never present (0 populated occurrences this run) | Factor 'rsi_divergence' contributed 0 signal all run -- wire it and backtest, or drop it from the scorer. |
| unfed_cts_factor | CTS factor 'seasonality' was never present (0 populated occurrences this run) | Factor 'seasonality' contributed 0 signal all run -- wire it and backtest, or drop it from the scorer. |
| would_have_traded | 2 evaluated ENTER signals had confluence input 'candlestick_pattern' absent (proxy metric: CTS keys absent from emitted setups, not literal engine gate-rejections -- see analyze_gaps' docstring) | 2 occurrences missing confluence input 'candlestick_pattern' -- prioritize wiring/backtesting that data or feature. |
| would_have_traded | 1 evaluated ENTER signals had confluence input 'condition_aligned' absent (proxy metric: CTS keys absent from emitted setups, not literal engine gate-rejections -- see analyze_gaps' docstring) | 1 occurrences missing confluence input 'condition_aligned' -- prioritize wiring/backtesting that data or feature. |
| would_have_traded | 2 evaluated ENTER signals had confluence input 'deceleration' absent (proxy metric: CTS keys absent from emitted setups, not literal engine gate-rejections -- see analyze_gaps' docstring) | 2 occurrences missing confluence input 'deceleration' -- prioritize wiring/backtesting that data or feature. |
| would_have_traded | 1 evaluated ENTER signals had confluence input 'ema_fan_aligned' absent (proxy metric: CTS keys absent from emitted setups, not literal engine gate-rejections -- see analyze_gaps' docstring) | 1 occurrences missing confluence input 'ema_fan_aligned' -- prioritize wiring/backtesting that data or feature. |
| would_have_traded | 2 evaluated ENTER signals had confluence input 'fib_confluence' absent (proxy metric: CTS keys absent from emitted setups, not literal engine gate-rejections -- see analyze_gaps' docstring) | 2 occurrences missing confluence input 'fib_confluence' -- prioritize wiring/backtesting that data or feature. |
| would_have_traded | 1 evaluated ENTER signals had confluence input 'in_pullback' absent (proxy metric: CTS keys absent from emitted setups, not literal engine gate-rejections -- see analyze_gaps' docstring) | 1 occurrences missing confluence input 'in_pullback' -- prioritize wiring/backtesting that data or feature. |
| would_have_traded | 1 evaluated ENTER signals had confluence input 'round_number_proximity' absent (proxy metric: CTS keys absent from emitted setups, not literal engine gate-rejections -- see analyze_gaps' docstring) | 1 occurrences missing confluence input 'round_number_proximity' -- prioritize wiring/backtesting that data or feature. |
| would_have_traded | 3 evaluated ENTER signals had confluence input 'rsi_divergence' absent (proxy metric: CTS keys absent from emitted setups, not literal engine gate-rejections -- see analyze_gaps' docstring) | 3 occurrences missing confluence input 'rsi_divergence' -- prioritize wiring/backtesting that data or feature. |
| would_have_traded | 3 evaluated ENTER signals had confluence input 'rsi_extreme' absent (proxy metric: CTS keys absent from emitted setups, not literal engine gate-rejections -- see analyze_gaps' docstring) | 3 occurrences missing confluence input 'rsi_extreme' -- prioritize wiring/backtesting that data or feature. |
| would_have_traded | 3 evaluated ENTER signals had confluence input 'seasonality' absent (proxy metric: CTS keys absent from emitted setups, not literal engine gate-rejections -- see analyze_gaps' docstring) | 3 occurrences missing confluence input 'seasonality' -- prioritize wiring/backtesting that data or feature. |
| would_have_traded | 2 evaluated ENTER signals had confluence input 'sr_touches' absent (proxy metric: CTS keys absent from emitted setups, not literal engine gate-rejections -- see analyze_gaps' docstring) | 2 occurrences missing confluence input 'sr_touches' -- prioritize wiring/backtesting that data or feature. |
| data_coverage_limit | BTC: no FIFTEEN_MINUTE candles cached | Pull 15m candles for BTC to sharpen intrabar stop-vs-target resolution (coarse hourly-only fallback is used otherwise). |
| data_coverage_limit | ETH: no FIFTEEN_MINUTE candles cached | Pull 15m candles for ETH to sharpen intrabar stop-vs-target resolution (coarse hourly-only fallback is used otherwise). |
| data_coverage_limit | PAXG: history starts 3.8yr into the requested window (first_ts=1746727200, requested_start_ts=1626625716) | PAXG has partial history -- its edge-table sample is shorter than the nominal window; re-pull as more history accrues. |
| data_coverage_limit | PAXG: no FIFTEEN_MINUTE candles cached | Pull 15m candles for PAXG to sharpen intrabar stop-vs-target resolution (coarse hourly-only fallback is used otherwise). |

## Caveats

- **In-sample**: this run has no holdout period; treat results as an upper bound on edge, not a forward-looking guarantee.
- **USDC stand-in**: USD-quoted candle history stands in for USDC pairs (Coinbase's candle history is USD-denominated); assumed 1:1.
- **Money-management ramp not modeled**: the Phase-4 profit-triggered sizing acceleration is not simulated here -- plain fixed-fractional sizing is used throughout.
- **PAXG partial history**: PAXG's candle history is shorter than the other assets' requested window; its edge-table and account-pass samples are correspondingly smaller.
- Even on a GO-LIVE verdict, run the supervised tiny-cap confirm-mode test before committing real capital.

"""Walk-forward parameter optimization.

Splits data into rolling windows:
  [===== IN-SAMPLE =====][== OUT-OF-SAMPLE ==]
                    [===== IN-SAMPLE =====][== OUT-OF-SAMPLE ==]
                                      [===== IN-SAMPLE =====][== OUT-OF-SAMPLE ==]

Optimizes parameters on in-sample, tests on out-of-sample.
A robust strategy performs consistently across ALL windows.
"""

import logging
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class WalkForwardOptimizer:
    """Walk-forward parameter optimization for the SignalEngine."""

    PARAM_GRID = {
        "MIN_SCORE_TO_TRADE": [45, 50, 55, 60, 65],
        "MIN_RVOL": [1.0, 1.3, 1.5, 2.0],
        "STOP_ATR_MULTIPLIER": [1.0, 1.5, 2.0, 2.5],
        "TARGET_RR_MULTIPLIER": [2.0, 2.5, 3.0],
        "WEIGHT_TREND": [15, 20, 25],
        "WEIGHT_VOLUME": [20, 25, 30],
    }

    def __init__(self, full_start: str, full_end: str,
                 n_windows: int = 4, in_sample_pct: float = 0.70):
        self.full_start = full_start
        self.full_end = full_end
        self.n_windows = n_windows
        self.in_sample_pct = in_sample_pct

    def _prefetch_all_data(self):
        """Pre-fetch all data for the full date range once.

        Returns a preloaded_data dict that can be passed to each sub-window
        BacktestEngine.run() to avoid redundant yfinance/Alpaca API calls.
        """
        from autotrader.backtest.engine import BacktestEngine
        from autotrader.backtest.data_fetcher import (
            fetch_5m_bars, fetch_daily_bars, fetch_spy_daily,
            fetch_vix_daily, get_trading_days,
        )

        print("Pre-fetching all data for full date range...")

        # 1. Trading days
        trading_days = get_trading_days(self.full_start, self.full_end)
        if not trading_days:
            return None
        print(f"  {len(trading_days)} trading days")

        # 2. SPY/VIX daily
        spy_daily = fetch_spy_daily(self.full_start, self.full_end)
        vix_daily = fetch_vix_daily(self.full_start, self.full_end)
        print(f"  SPY daily: {len(spy_daily)} bars, VIX daily: {len(vix_daily)} bars")

        # 3. Build broad universe using a master engine instance
        master = BacktestEngine(
            start=self.full_start,
            end=self.full_end,
            deterministic=True,
        )
        broad_universe = master._build_broad_universe()
        print(f"  Broad universe: {len(broad_universe)} symbols")

        # 4. Fetch daily bars for all symbols
        print(f"  Fetching daily bars for {len(broad_universe)} symbols...")
        daily_bars: dict[str, pd.DataFrame] = {}
        for sym in broad_universe:
            daily_bars[sym] = fetch_daily_bars(sym, self.full_start, self.full_end)
        daily_bars = {s: d for s, d in daily_bars.items() if not d.empty}
        print(f"  Daily data loaded for {len(daily_bars)} symbols")

        # 5. Run scanner for each day and fetch 5m bars for hot list symbols
        bars_5m: dict[str, pd.DataFrame] = {}
        daily_hot_lists: dict[str, list[str]] = {}

        for day_dt in trading_days:
            day_str = day_dt.strftime("%Y-%m-%d")
            hot_list = master._scan_for_day(day_dt, daily_bars)
            daily_hot_lists[day_str] = hot_list

            for sym in hot_list:
                if sym not in bars_5m:
                    bars_5m[sym] = fetch_5m_bars(sym, self.full_start, self.full_end)

        all_selected = set()
        for syms in daily_hot_lists.values():
            all_selected.update(syms)
        print(f"  Scanner selected {len(all_selected)} unique symbols, "
              f"5m data fetched for each")

        return {
            "trading_days": trading_days,
            "spy_daily": spy_daily,
            "vix_daily": vix_daily,
            "daily_bars": daily_bars,
            "bars_5m": bars_5m,
            "daily_hot_lists": daily_hot_lists,
        }

    def run(self):
        """Run walk-forward optimization."""
        from autotrader.backtest.engine import BacktestEngine
        from autotrader.backtest.data_fetcher import get_trading_days

        # Pre-fetch ALL data once for the full range
        preloaded = self._prefetch_all_data()
        if preloaded is None:
            print("No trading days found in range")
            return

        all_days = preloaded["trading_days"]
        window_size = len(all_days) // self.n_windows
        if window_size < 10:
            print(f"Not enough trading days ({len(all_days)}) for {self.n_windows} windows")
            return

        split = int(window_size * self.in_sample_pct)

        # Generate parameter combinations (limit to avoid explosion)
        param_combos = self._generate_smart_combos()
        print(f"\nTesting {len(param_combos)} parameter combinations per window")
        print(f"Total trading days: {len(all_days)}, Window size: {window_size}, "
              f"IS: {split} days, OOS: {window_size - split} days")

        results_by_window = []

        for w in range(self.n_windows):
            start_idx = w * window_size
            end_idx = start_idx + window_size
            if end_idx > len(all_days):
                break

            is_start = all_days[start_idx].strftime("%Y-%m-%d")
            is_end = all_days[start_idx + split - 1].strftime("%Y-%m-%d")
            oos_start = all_days[start_idx + split].strftime("%Y-%m-%d")
            oos_end = all_days[end_idx - 1].strftime("%Y-%m-%d")

            print(f"\n{'='*60}")
            print(f"Window {w+1}/{self.n_windows}")
            print(f"  In-sample:     {is_start} → {is_end}")
            print(f"  Out-of-sample: {oos_start} → {oos_end}")

            # Optimize on in-sample
            best_params = None
            best_pf = 0
            best_trades = 0

            for i, params in enumerate(param_combos):
                if (i + 1) % 10 == 0:
                    print(f"  Testing combo {i+1}/{len(param_combos)}...", end="\r")

                engine = BacktestEngine(
                    start=is_start, end=is_end,
                    signal_params=params,
                    deterministic=True,
                )
                result = engine.run(preloaded_data=preloaded)

                # Require minimum trades and positive PF
                if result.total_trades >= 10 and result.profit_factor > best_pf:
                    best_pf = result.profit_factor
                    best_params = params
                    best_trades = result.total_trades

            print(f"  {'':60s}")  # Clear progress line

            if best_params is None:
                print(f"  No valid parameters found for window {w+1}")
                continue

            print(f"  Best in-sample params: {best_params}")
            print(f"  Best in-sample PF: {best_pf:.2f} ({best_trades} trades)")

            # Test on out-of-sample with best params
            oos_engine = BacktestEngine(
                start=oos_start, end=oos_end,
                signal_params=best_params,
                deterministic=True,
            )
            oos_result = oos_engine.run(preloaded_data=preloaded)

            print(f"  OOS trades: {oos_result.total_trades}")
            print(f"  OOS PF: {oos_result.profit_factor:.2f}")
            print(f"  OOS Return: {oos_result.return_pct:+.2f}%")
            print(f"  OOS Sharpe: {oos_result.sharpe_ratio:.2f}")

            results_by_window.append({
                "window": w + 1,
                "params": best_params,
                "is_pf": best_pf,
                "is_trades": best_trades,
                "oos_pf": oos_result.profit_factor,
                "oos_return": oos_result.return_pct,
                "oos_sharpe": oos_result.sharpe_ratio,
                "oos_trades": oos_result.total_trades,
            })

        # Summary
        print(f"\n{'='*60}")
        print("WALK-FORWARD SUMMARY")
        print(f"{'='*60}")

        if results_by_window:
            oos_returns = [r["oos_return"] for r in results_by_window]
            oos_pfs = [r["oos_pf"] for r in results_by_window]
            profitable_windows = sum(1 for r in oos_returns if r > 0)

            print(f"Windows tested:    {len(results_by_window)}/{self.n_windows}")
            print(f"Windows profitable: {profitable_windows}/{len(results_by_window)}")
            print(f"Avg OOS return: {np.mean(oos_returns):+.2f}%")
            print(f"Avg OOS PF: {np.mean(oos_pfs):.2f}")
            print(f"Worst window: {min(oos_returns):+.2f}%")
            print(f"Best window: {max(oos_returns):+.2f}%")

            # Parameter stability analysis
            print(f"\nParameter choices across windows:")
            for r in results_by_window:
                print(f"  Window {r['window']}: IS PF={r['is_pf']:.2f} → OOS PF={r['oos_pf']:.2f} "
                      f"| OOS Return={r['oos_return']:+.2f}% | {r['oos_trades']} trades")
                for k, v in r["params"].items():
                    print(f"    {k}: {v}")

            if profitable_windows >= len(results_by_window) * 0.75:
                print("\n>>> WALK-FORWARD: PASS <<<")
            else:
                print("\n>>> WALK-FORWARD: FAIL <<<")
        else:
            print("No valid windows completed.")
            print("\n>>> WALK-FORWARD: FAIL <<<")

    def _generate_smart_combos(self) -> list[dict]:
        """Generate parameter combinations without full grid explosion.

        Strategy: fix most params at default, sweep 2 at a time.
        """
        defaults = {
            "MIN_SCORE_TO_TRADE": 55,
            "MIN_RVOL": 1.3,
            "STOP_ATR_MULTIPLIER": 1.5,
            "TARGET_RR_MULTIPLIER": 2.5,
            "WEIGHT_TREND": 20,
            "WEIGHT_VOLUME": 25,
        }

        combos = [defaults.copy()]  # Always include default

        # Sweep pairs
        pairs = [
            ("MIN_SCORE_TO_TRADE", "MIN_RVOL"),
            ("STOP_ATR_MULTIPLIER", "TARGET_RR_MULTIPLIER"),
            ("WEIGHT_TREND", "WEIGHT_VOLUME"),
        ]

        for key1, key2 in pairs:
            for v1 in self.PARAM_GRID[key1]:
                for v2 in self.PARAM_GRID[key2]:
                    combo = defaults.copy()
                    combo[key1] = v1
                    combo[key2] = v2
                    combos.append(combo)

        # Deduplicate
        unique = []
        seen = set()
        for c in combos:
            key = tuple(sorted(c.items()))
            if key not in seen:
                seen.add(key)
                unique.append(c)

        return unique

"""Market-wide scanner — finds day trading opportunities across the entire market.

Modeled after how professional scanners (Trade Ideas, Benzinga) work:
1. Build universe of liquid stocks (daily)
2. Score every stock on day-trading factors
3. Surface the top movers for Claude to analyze

Scoring factors (from research on what successful day traders look for):
- Relative volume (RVOL) — #1 most important signal
- Pre-market gap size
- Intraday momentum
- Volatility (ATR)
- Multi-day trend
- Breakout proximity (near 20-day high/low)
- Float consideration (lower float = more volatile moves)
"""

import logging
import time
from datetime import datetime, timedelta
from dataclasses import dataclass, field

import pandas as pd
import yfinance as yf
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetAssetsRequest
from alpaca.trading.enums import AssetClass, AssetStatus

from autotrader.config import ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_PAPER, SCANNER

logger = logging.getLogger(__name__)


@dataclass
class ScanCandidate:
    """A stock that the scanner has flagged as interesting."""
    symbol: str
    price: float = 0.0
    change_pct: float = 0.0
    volume: int = 0
    avg_volume: int = 0
    relative_volume: float = 0.0
    gap_pct: float = 0.0
    atr_pct: float = 0.0
    float_category: str = ""     # "low", "mid", "high"
    score: float = 0.0
    flags: list[str] = field(default_factory=list)

    # Extra context for Claude
    five_day_change: float = 0.0
    consecutive_green: int = 0
    consecutive_red: int = 0
    near_high: bool = False
    near_low: bool = False


class MarketScanner:
    """Scans the entire market to find the best day trading opportunities."""

    def __init__(self):
        self.trading_client = TradingClient(
            api_key=ALPACA_API_KEY,
            secret_key=ALPACA_SECRET_KEY,
            paper=ALPACA_PAPER,
        )
        self._universe: list[str] = []
        self._universe_built_at: datetime | None = None
        self._hot_list: list[ScanCandidate] = []
        self._hot_list_updated_at: datetime | None = None

    @property
    def universe(self) -> list[str]:
        return self._universe

    @property
    def hot_list(self) -> list[ScanCandidate]:
        return self._hot_list

    def build_universe(self) -> list[str]:
        """Build the daily trading universe from all tradeable US equities."""
        logger.info("Building trading universe — scanning entire market...")
        start = time.time()

        try:
            request = GetAssetsRequest(
                asset_class=AssetClass.US_EQUITY,
                status=AssetStatus.ACTIVE,
            )
            all_assets = self.trading_client.get_all_assets(request)
        except Exception as e:
            logger.error(f"Failed to get assets from Alpaca: {e}")
            return self._universe or []

        symbols = [
            a.symbol for a in all_assets
            if a.tradable
            and a.exchange in ("NASDAQ", "NYSE", "ARCA", "AMEX", "BATS")
            and not any(c in a.symbol for c in "./-")
            and len(a.symbol) <= 5
        ]
        logger.info(f"Found {len(symbols)} tradeable symbols from Alpaca")

        # Download recent data in batches to filter by price/volume
        universe = []
        batch_size = 200
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i + batch_size]
            try:
                data = yf.download(
                    tickers=batch,
                    period="10d",
                    interval="1d",
                    progress=False,
                    threads=True,
                )
                if data.empty:
                    continue

                for sym in batch:
                    try:
                        if len(batch) == 1:
                            close = data["Close"]
                            vol = data["Volume"]
                        else:
                            close = data["Close"][sym] if sym in data["Close"].columns else None
                            vol = data["Volume"][sym] if sym in data["Volume"].columns else None

                        if close is None or vol is None:
                            continue

                        close = close.dropna()
                        vol = vol.dropna()

                        if len(close) < 3:
                            continue

                        last_price = float(close.iloc[-1])
                        avg_vol = float(vol.mean())

                        if (SCANNER["min_price"] <= last_price <= SCANNER["max_price"]
                                and avg_vol >= SCANNER["min_avg_volume"]):
                            universe.append(sym)

                    except Exception:
                        continue

            except Exception as e:
                logger.warning(f"Batch download failed for batch starting at {i}: {e}")
                continue

            if len(universe) >= SCANNER["universe_size"]:
                break

        self._universe = universe[:SCANNER["universe_size"]]
        self._universe_built_at = datetime.now()

        elapsed = time.time() - start
        logger.info(
            f"Universe built: {len(self._universe)} stocks "
            f"(from {len(symbols)} total) in {elapsed:.1f}s"
        )
        return self._universe

    def scan_for_movers(self, symbols: list[str] | None = None) -> list[ScanCandidate]:
        """Scan stocks for unusual activity and rank them.

        This is the core scanning function run every cycle.
        """
        scan_symbols = symbols or self._universe
        if not scan_symbols:
            logger.warning("No universe to scan — using fallback watchlist")
            from autotrader.config import WATCHLIST_FALLBACK
            scan_symbols = WATCHLIST_FALLBACK

        logger.info(f"Scanning {len(scan_symbols)} stocks for opportunities...")
        start = time.time()

        candidates = []
        batch_size = 100
        for i in range(0, len(scan_symbols), batch_size):
            batch = scan_symbols[i:i + batch_size]
            try:
                data = yf.download(
                    tickers=batch,
                    period="22d",
                    interval="1d",
                    progress=False,
                    threads=True,
                )
                if data.empty:
                    continue

                for sym in batch:
                    candidate = self._score_stock(sym, data, len(batch))
                    if candidate and candidate.score > 0:
                        candidates.append(candidate)

            except Exception as e:
                logger.warning(f"Scan batch failed: {e}")
                continue

        candidates.sort(key=lambda c: c.score, reverse=True)

        hot_size = SCANNER["hot_list_size"]
        self._hot_list = candidates[:hot_size]
        self._hot_list_updated_at = datetime.now()

        elapsed = time.time() - start
        logger.info(
            f"Scan complete: {len(candidates)} candidates found, "
            f"top {len(self._hot_list)} on hot list ({elapsed:.1f}s)"
        )

        for i, c in enumerate(self._hot_list[:10]):
            logger.info(
                f"  #{i+1}: {c.symbol} | score={c.score:.1f} | "
                f"chg={c.change_pct:+.1f}% | rvol={c.relative_volume:.1f}x | "
                f"gap={c.gap_pct:+.1f}% | flags={', '.join(c.flags)}"
            )

        return self._hot_list

    def get_top_candidates(self, count: int | None = None) -> list[ScanCandidate]:
        """Get the top N candidates for Claude to analyze."""
        n = count or SCANNER["claude_analyze_count"]
        return self._hot_list[:n]

    def needs_universe_rebuild(self) -> bool:
        if not self._universe or not self._universe_built_at:
            return True
        age = datetime.now() - self._universe_built_at
        return age > timedelta(hours=18)

    def needs_hot_list_refresh(self) -> bool:
        if not self._hot_list or not self._hot_list_updated_at:
            return True
        age = datetime.now() - self._hot_list_updated_at
        return age > timedelta(minutes=SCANNER["hot_list_refresh_minutes"])

    def _score_stock(self, symbol: str, data: pd.DataFrame, batch_len: int) -> ScanCandidate | None:
        """Score a stock on day-trading potential.

        Scoring is based on what real day traders scan for:
        1. RVOL (relative volume) — most important
        2. Gap from prior close
        3. Intraday momentum
        4. Volatility (ATR/price)
        5. Multi-day trend
        6. Proximity to key levels
        7. Consecutive direction (momentum)
        """
        try:
            if batch_len == 1:
                close = data["Close"].dropna()
                volume = data["Volume"].dropna()
                high = data["High"].dropna()
                low = data["Low"].dropna()
                openp = data["Open"].dropna()
            else:
                if symbol not in data["Close"].columns:
                    return None
                close = data["Close"][symbol].dropna()
                volume = data["Volume"][symbol].dropna()
                high = data["High"][symbol].dropna()
                low = data["Low"][symbol].dropna()
                openp = data["Open"][symbol].dropna()

            if len(close) < 5:
                return None

            price = float(close.iloc[-1])
            prev_close = float(close.iloc[-2])
            today_open = float(openp.iloc[-1])
            today_vol = float(volume.iloc[-1])
            avg_vol = float(volume.iloc[:-1].mean()) if len(volume) > 1 else today_vol

            if price <= 0 or avg_vol <= 0:
                return None

            # ── Core Metrics ─────────────────────────
            change_pct = ((price - prev_close) / prev_close) * 100
            gap_pct = ((today_open - prev_close) / prev_close) * 100
            relative_volume = today_vol / avg_vol if avg_vol > 0 else 0

            # ATR as % of price
            tr = pd.DataFrame({
                "hl": high - low,
                "hc": (high - close.shift(1)).abs(),
                "lc": (low - close.shift(1)).abs(),
            }).max(axis=1)
            atr = float(tr.iloc[-14:].mean()) if len(tr) >= 14 else float(tr.mean())
            atr_pct = (atr / price) * 100

            # Consecutive green/red days
            consecutive_green = 0
            consecutive_red = 0
            for i in range(len(close)-1, max(0, len(close)-6), -1):
                if close.iloc[i] > close.iloc[i-1]:
                    if consecutive_red > 0:
                        break
                    consecutive_green += 1
                elif close.iloc[i] < close.iloc[i-1]:
                    if consecutive_green > 0:
                        break
                    consecutive_red += 1
                else:
                    break

            # 5-day change
            five_day_change = ((price - float(close.iloc[-5])) / float(close.iloc[-5])) * 100 if len(close) >= 5 else 0

            # Near 20-day high/low
            near_high = False
            near_low = False
            if len(high) >= 20:
                twenty_high = float(high.iloc[-20:].max())
                twenty_low = float(low.iloc[-20:].min())
                near_high = price >= twenty_high * 0.97
                near_low = price <= twenty_low * 1.03

            # ═══════════════════════════════════════════
            # SCORING (weighted by what matters most)
            # ═══════════════════════════════════════════
            score = 0.0
            flags = []

            # ── 1. RELATIVE VOLUME (weight: 35%) ──
            # This is the #1 signal pro day traders look for
            if relative_volume >= 5.0:
                score += 40
                flags.append("EXTREME_RVOL")
            elif relative_volume >= 3.0:
                score += 30
                flags.append("VERY_HIGH_RVOL")
            elif relative_volume >= 2.0:
                score += 20
                flags.append("HIGH_RVOL")
            elif relative_volume >= SCANNER["min_relative_volume"]:
                score += 10
                flags.append("ABOVE_AVG_RVOL")

            # ── 2. GAP (weight: 25%) ──
            # Gap > 4% with volume = "stock in play"
            abs_gap = abs(gap_pct)
            if abs_gap >= 8.0:
                score += 30
                flags.append(f"HUGE_GAP_{'UP' if gap_pct > 0 else 'DOWN'}")
            elif abs_gap >= 4.0:
                score += 22
                flags.append(f"BIG_GAP_{'UP' if gap_pct > 0 else 'DOWN'}")
            elif abs_gap >= SCANNER["min_gap_pct"]:
                score += 12
                flags.append(f"GAP_{'UP' if gap_pct > 0 else 'DOWN'}")

            # Bonus: gap + volume together (strongest signal)
            if abs_gap >= 3.0 and relative_volume >= 2.0:
                score += 15
                flags.append("GAP_WITH_VOLUME")

            # ── 3. INTRADAY MOMENTUM (weight: 15%) ──
            intraday_move = abs(((price - today_open) / today_open) * 100)
            if intraday_move >= 5.0:
                score += 18
                flags.append("HUGE_INTRADAY_MOVE")
            elif intraday_move >= 3.0:
                score += 12
                flags.append("STRONG_INTRADAY")
            elif intraday_move >= 1.5:
                score += 6
                flags.append("INTRADAY_MOMENTUM")

            # ── 4. VOLATILITY (weight: 10%) ──
            # Higher ATR = more range = more opportunity
            if atr_pct >= 4.0:
                score += 12
                flags.append("VERY_HIGH_VOLATILITY")
            elif atr_pct >= 2.5:
                score += 8
                flags.append("HIGH_VOLATILITY")
            elif atr_pct >= 1.5:
                score += 4

            # ── 5. MULTI-DAY TREND (weight: 8%) ──
            if abs(five_day_change) >= 15:
                score += 10
                flags.append(f"5D_TREND_{'UP' if five_day_change > 0 else 'DOWN'}_{abs(five_day_change):.0f}pct")
            elif abs(five_day_change) >= 8:
                score += 6
                flags.append(f"5D_TREND_{'UP' if five_day_change > 0 else 'DOWN'}")

            # Consecutive direction bonus
            if consecutive_green >= 4:
                score += 5
                flags.append(f"{consecutive_green}D_GREEN_STREAK")
            elif consecutive_red >= 4:
                score += 5
                flags.append(f"{consecutive_red}D_RED_STREAK")

            # ── 6. KEY LEVEL PROXIMITY (weight: 7%) ──
            if near_high:
                score += 8
                flags.append("NEAR_20D_HIGH")
            if near_low:
                score += 8
                flags.append("NEAR_20D_LOW")

            # Penalize if no flags (boring stock)
            if not flags:
                return None

            # Float category estimation (rough, based on avg volume)
            if avg_vol < 2_000_000:
                float_cat = "low"
            elif avg_vol < 10_000_000:
                float_cat = "mid"
            else:
                float_cat = "high"

            # Low float bonus (more explosive moves)
            if float_cat == "low" and relative_volume >= 2.0:
                score += 5
                flags.append("LOW_FLOAT_RVOL")

            return ScanCandidate(
                symbol=symbol,
                price=round(price, 2),
                change_pct=round(change_pct, 2),
                volume=int(today_vol),
                avg_volume=int(avg_vol),
                relative_volume=round(relative_volume, 2),
                gap_pct=round(gap_pct, 2),
                atr_pct=round(atr_pct, 2),
                float_category=float_cat,
                score=round(score, 1),
                flags=flags,
                five_day_change=round(five_day_change, 2),
                consecutive_green=consecutive_green,
                consecutive_red=consecutive_red,
                near_high=near_high,
                near_low=near_low,
            )

        except Exception:
            return None

    def get_scan_summary(self) -> str:
        """Get a text summary of the current scan state."""
        lines = [
            f"Universe: {len(self._universe)} stocks",
            f"Hot list: {len(self._hot_list)} candidates",
        ]
        if self._hot_list:
            lines.append("Top 5:")
            for c in self._hot_list[:5]:
                lines.append(
                    f"  {c.symbol}: {c.change_pct:+.1f}% | "
                    f"rvol={c.relative_volume:.1f}x | gap={c.gap_pct:+.1f}% | "
                    f"{', '.join(c.flags[:3])}"
                )
        return "\n".join(lines)

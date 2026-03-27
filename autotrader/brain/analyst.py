"""Claude-powered trading analyst — the brain of the system.

Feeds Claude:
- Multi-timeframe indicators (daily + 5-min)
- Detected candlestick & chart patterns
- Key support/resistance levels
- Scanner flags and volume data
- Market phase (time-of-day awareness)
- Portfolio context
"""

import json
import logging
from dataclasses import dataclass

import anthropic

from autotrader.config import ANTHROPIC_API_KEY, CLAUDE_MODEL, CLAUDE_MAX_TOKENS
from autotrader.brain.prompts import SYSTEM_PROMPT, build_analysis_prompt, build_ranking_prompt
from autotrader.data.market import get_stock_data, get_current_price, get_intraday_data
from autotrader.data.indicators import (
    calculate_indicators, get_signal_summary,
    calculate_intraday_indicators, get_intraday_signal_summary,
)
from autotrader.data.patterns import (
    detect_all_patterns, get_key_levels,
    format_patterns_for_prompt, format_levels_for_prompt,
)
from autotrader.data.news import get_news, format_news_for_prompt
from autotrader.risk.manager import TradeProposal
from autotrader.config import FINNHUB_API_KEY

logger = logging.getLogger(__name__)


@dataclass
class AnalysisResult:
    """Result of Claude's analysis."""
    symbol: str
    action: str       # BUY, SELL, HOLD
    confidence: float
    reasoning: str
    stop_loss: float | None
    take_profit: float | None
    entry_price: float | None
    pattern: str
    quantity_suggestion: int
    indicators: dict
    detected_patterns: list
    key_levels: dict
    raw_response: str


class ClaudeAnalyst:
    """Uses Claude to analyze stocks and make trading decisions."""

    def __init__(self):
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    def analyze(
        self,
        symbol: str,
        portfolio: dict,
        scanner_flags: str = "",
        trades_today: int = 0,
        market_phase: str = "",
    ) -> AnalysisResult | None:
        """Full multi-timeframe analysis with pattern detection."""
        try:
            # 1. Current price
            price_data = get_current_price(symbol)
            if not price_data:
                logger.warning(f"Could not get price data for {symbol}")
                return None

            # 2. Daily data + indicators
            hist_data = get_stock_data(symbol, period="3mo", interval="1d")
            indicators = calculate_indicators(hist_data)
            signal_summary = get_signal_summary(indicators)

            # 3. Intraday data + indicators
            intraday_data = get_intraday_data(symbol, interval="5m")
            intraday_indicators = calculate_intraday_indicators(intraday_data)
            intraday_summary = get_intraday_signal_summary(intraday_indicators)
            indicators.update(intraday_indicators)

            # 4. Pattern detection (candlestick + chart + intraday)
            prior_day_high = indicators.get("day_high")
            prior_day_low = indicators.get("day_low")
            prior_day_close = indicators.get("prev_close")
            vwap = indicators.get("vwap")

            detected_patterns = detect_all_patterns(
                df_daily=hist_data,
                df_5m=intraday_data if not intraday_data.empty else None,
                prior_day_high=prior_day_high,
                prior_day_low=prior_day_low,
                prior_day_close=prior_day_close,
                vwap=vwap,
            )
            patterns_text = format_patterns_for_prompt(detected_patterns)

            # 5. Key levels (support, resistance, ORB, prior day H/L)
            key_levels = get_key_levels(
                df_daily=hist_data,
                df_5m=intraday_data if not intraday_data.empty else None,
                vwap=vwap,
            )
            levels_text = format_levels_for_prompt(key_levels)

            # 6. News
            news = get_news(symbol, api_key=FINNHUB_API_KEY)
            news_text = format_news_for_prompt(news)

            # 7. Build prompt
            prompt = build_analysis_prompt(
                symbol=symbol,
                price_data=price_data,
                indicators=indicators,
                signal_summary=signal_summary,
                intraday_summary=intraday_summary,
                news_text=news_text,
                portfolio=portfolio,
                scanner_flags=scanner_flags,
                trades_today=trades_today,
                detected_patterns=patterns_text,
                key_levels=levels_text,
                market_phase=market_phase,
            )

            # 8. Ask Claude
            response = self.client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=CLAUDE_MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )

            raw_text = response.content[0].text.strip()

            # 9. Parse
            decision = self._parse_response(raw_text, symbol)
            if not decision:
                return None

            return AnalysisResult(
                symbol=symbol,
                action=decision["action"],
                confidence=decision["confidence"],
                reasoning=decision["reasoning"],
                stop_loss=decision.get("stop_loss"),
                take_profit=decision.get("take_profit"),
                entry_price=decision.get("entry_price"),
                pattern=decision.get("pattern", "unknown"),
                quantity_suggestion=decision.get("quantity", 0),
                indicators=indicators,
                detected_patterns=detected_patterns,
                key_levels=key_levels,
                raw_response=raw_text,
            )

        except anthropic.APIError as e:
            logger.error(f"Claude API error analyzing {symbol}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error analyzing {symbol}: {e}")
            return None

    def rank_candidates(
        self,
        candidates: list,
        spy_change: float,
        qqq_change: float,
        pick_count: int = 5,
        market_phase: str = "",
    ) -> list[dict]:
        """Ask Claude to rank scanner candidates."""
        try:
            prompt = build_ranking_prompt(
                candidates, spy_change, qqq_change, pick_count, market_phase,
            )

            response = self.client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=CLAUDE_MAX_TOKENS,
                system="You are a day trading scanner AI. Rank stocks by intraday potential. Apply the trifecta: PATTERN + LOCATION + VOLUME. Output valid JSON only.",
                messages=[{"role": "user", "content": prompt}],
            )

            raw_text = response.content[0].text.strip()
            return self._parse_ranking(raw_text)

        except Exception as e:
            logger.error(f"Error ranking candidates: {e}")
            return []

    def to_trade_proposal(self, result: AnalysisResult, current_price: float) -> TradeProposal:
        """Convert AnalysisResult to TradeProposal for risk checking."""
        return TradeProposal(
            symbol=result.symbol,
            side=result.action,
            confidence=result.confidence,
            reasoning=result.reasoning,
            stop_loss=result.stop_loss,
            take_profit=result.take_profit,
            entry_price=result.entry_price,
            current_price=current_price,
            pattern=result.pattern,
            quantity_hint=result.quantity_suggestion,
        )

    def _parse_response(self, raw: str, symbol: str) -> dict | None:
        """Parse Claude's JSON response."""
        try:
            text = raw.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1]
                text = text.rsplit("```", 1)[0]
                text = text.strip()

            decision = json.loads(text)

            action = decision.get("action", "HOLD").upper()
            if action not in ("BUY", "SELL", "HOLD"):
                logger.warning(f"Invalid action '{action}' from Claude for {symbol}")
                action = "HOLD"

            confidence = float(decision.get("confidence", 0))
            confidence = max(0.0, min(1.0, confidence))

            return {
                "action": action,
                "symbol": symbol,
                "confidence": confidence,
                "reasoning": decision.get("reasoning", "No reasoning provided"),
                "pattern": decision.get("pattern", "unknown"),
                "quantity": int(decision.get("quantity", 0)),
                "entry_price": _safe_float(decision.get("entry_price")),
                "stop_loss": _safe_float(decision.get("stop_loss")),
                "take_profit": _safe_float(decision.get("take_profit")),
            }

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Claude response for {symbol}: {e}\nRaw: {raw}")
            return None
        except Exception as e:
            logger.error(f"Error parsing response for {symbol}: {e}")
            return None

    def _parse_ranking(self, raw: str) -> list[dict]:
        """Parse Claude's ranking response."""
        try:
            text = raw.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1]
                text = text.rsplit("```", 1)[0]
                text = text.strip()

            rankings = json.loads(text)
            if isinstance(rankings, list):
                return rankings
            return []
        except Exception as e:
            logger.error(f"Failed to parse ranking response: {e}\nRaw: {raw}")
            return []


def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        return f if f > 0 else None
    except (ValueError, TypeError):
        return None

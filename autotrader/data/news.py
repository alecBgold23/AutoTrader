"""News and catalyst detection via Alpaca's built-in News API.

Replaces Finnhub (which required a separate API key we never had).
Alpaca News is included with the trading account — no extra key needed.
"""

import logging
from datetime import datetime, timedelta, timezone

from alpaca.data.news import NewsClient
from alpaca.data.requests import NewsRequest
from alpaca.common.exceptions import APIError

from autotrader.config import ALPACA_API_KEY, ALPACA_SECRET_KEY

logger = logging.getLogger(__name__)


def _get_news_client() -> NewsClient:
    """Create an Alpaca News client using existing broker credentials."""
    return NewsClient(
        api_key=ALPACA_API_KEY,
        secret_key=ALPACA_SECRET_KEY,
    )


def get_news(symbol: str, hours_back: int = 24) -> list[dict]:
    """Fetch recent news for a symbol via Alpaca News API.

    Args:
        symbol: Stock ticker
        hours_back: How far back to look (default 24h for day trading)

    Returns:
        List of dicts: [{headline, summary, source, url, datetime, sentiment}, ...]
    """
    if not ALPACA_API_KEY:
        logger.debug("No Alpaca API key — news unavailable")
        return []

    try:
        client = _get_news_client()
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=hours_back)

        request = NewsRequest(
            symbols=[symbol],
            start=start,
            end=end,
            limit=10,
            sort="desc",
        )
        news = client.get_news(request)

        results = []
        for article in news.news:
            results.append({
                "headline": article.headline or "",
                "summary": article.summary or "",
                "source": article.source or "",
                "url": article.url or "",
                "datetime": article.created_at.isoformat() if article.created_at else "",
                "sentiment": _basic_headline_sentiment(article.headline or ""),
            })

        return results

    except APIError as e:
        logger.error(f"Alpaca News API error for {symbol}: {e}")
        return []
    except Exception as e:
        logger.error(f"Failed to fetch news for {symbol}: {e}")
        return []


def get_market_news(hours_back: int = 12) -> list[dict]:
    """Fetch general market news (no symbol filter)."""
    if not ALPACA_API_KEY:
        return []

    try:
        client = _get_news_client()
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=hours_back)

        request = NewsRequest(
            start=start,
            end=end,
            limit=10,
            sort="desc",
        )
        news = client.get_news(request)

        results = []
        for article in news.news:
            results.append({
                "headline": article.headline or "",
                "summary": article.summary or "",
                "source": article.source or "",
                "url": article.url or "",
            })
        return results

    except Exception as e:
        logger.error(f"Failed to fetch market news: {e}")
        return []


def format_news_for_prompt(news: list[dict]) -> str:
    """Format news as CATALYST or NO RECENT CATALYST for Claude.

    Day trading context: recent news = potential catalyst driving the move.
    No news = be more cautious about why the stock is moving.
    """
    if not news:
        return "NO RECENT CATALYST — No news in last 24h. If this stock is moving, the reason is unclear. Be cautious about entries without a known catalyst."

    # Check if any headline has meaningful sentiment
    has_catalyst = False
    lines = []
    for i, article in enumerate(news[:5], 1):
        sentiment = article.get("sentiment", "neutral")
        headline = article["headline"]
        if sentiment != "neutral":
            has_catalyst = True
        tag = sentiment.upper()
        lines.append(f"{i}. [{tag}] {headline}")
        if article.get("summary"):
            summary = article["summary"][:200]
            lines.append(f"   {summary}")

    if has_catalyst:
        header = "CATALYST DETECTED — Recent news may be driving price action:"
    else:
        header = "NEWS (neutral sentiment — may not be driving today's move):"

    return f"{header}\n" + "\n".join(lines)


def _basic_headline_sentiment(headline: str) -> str:
    """Basic keyword-based sentiment scoring."""
    headline_lower = headline.lower()

    bullish_words = [
        "surge", "soar", "rally", "jump", "gain", "rise", "beat",
        "upgrade", "buy", "bullish", "record", "high", "growth",
        "profit", "revenue beat", "outperform", "breakout",
    ]
    bearish_words = [
        "crash", "plunge", "drop", "fall", "decline", "loss",
        "downgrade", "sell", "bearish", "miss", "cut", "warning",
        "layoff", "lawsuit", "investigation", "concern", "fear",
    ]

    bull_count = sum(1 for w in bullish_words if w in headline_lower)
    bear_count = sum(1 for w in bearish_words if w in headline_lower)

    if bull_count > bear_count:
        return "bullish"
    elif bear_count > bull_count:
        return "bearish"
    return "neutral"

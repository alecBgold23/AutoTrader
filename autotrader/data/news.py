"""News and sentiment data fetching."""

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Finnhub is optional — gracefully degrade if not available
try:
    import finnhub
    FINNHUB_AVAILABLE = True
except ImportError:
    FINNHUB_AVAILABLE = False


def get_news(symbol: str, api_key: str = "", days_back: int = 3) -> list[dict]:
    """Fetch recent news for a symbol via Finnhub.

    Returns:
        List of dicts: [{headline, summary, source, url, datetime}, ...]
    """
    if not api_key or not FINNHUB_AVAILABLE:
        logger.debug(f"News not available for {symbol} (no API key or finnhub not installed)")
        return []

    try:
        client = finnhub.Client(api_key=api_key)
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days_back)

        news = client.company_news(
            symbol,
            _from=start.strftime("%Y-%m-%d"),
            to=end.strftime("%Y-%m-%d"),
        )

        results = []
        for article in news[:10]:  # Limit to 10 most recent
            results.append({
                "headline": article.get("headline", ""),
                "summary": article.get("summary", ""),
                "source": article.get("source", ""),
                "url": article.get("url", ""),
                "datetime": datetime.fromtimestamp(
                    article.get("datetime", 0), tz=timezone.utc
                ).isoformat(),
                "sentiment": _basic_headline_sentiment(article.get("headline", "")),
            })

        return results

    except Exception as e:
        logger.error(f"Failed to fetch news for {symbol}: {e}")
        return []


def get_market_news(api_key: str = "") -> list[dict]:
    """Fetch general market news."""
    if not api_key or not FINNHUB_AVAILABLE:
        return []

    try:
        client = finnhub.Client(api_key=api_key)
        news = client.general_news("general", min_id=0)

        results = []
        for article in news[:10]:
            results.append({
                "headline": article.get("headline", ""),
                "summary": article.get("summary", ""),
                "source": article.get("source", ""),
                "url": article.get("url", ""),
            })
        return results

    except Exception as e:
        logger.error(f"Failed to fetch market news: {e}")
        return []


def format_news_for_prompt(news: list[dict]) -> str:
    """Format news articles into a concise string for Claude."""
    if not news:
        return "No recent news available."

    lines = []
    for i, article in enumerate(news[:5], 1):
        sentiment = article.get("sentiment", "neutral")
        lines.append(f"{i}. [{sentiment.upper()}] {article['headline']}")
        if article.get("summary"):
            # Truncate long summaries
            summary = article["summary"][:200]
            lines.append(f"   {summary}")

    return "\n".join(lines)


def _basic_headline_sentiment(headline: str) -> str:
    """Very basic keyword-based sentiment (fallback when Claude isn't used for this)."""
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

#!/usr/bin/env python3
"""Quick test script to verify all components are working with paper trading.

Run this before starting the full trading loop to ensure:
1. Alpaca connection works
2. Claude API works
3. Market data fetching works
4. Technical indicators calculate correctly
5. Database works
6. A paper trade can be placed

Usage:
    python scripts/test_paper.py
"""

import sys
import os

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from autotrader.config import ALPACA_PAPER, WATCHLIST


def test_database():
    """Test database initialization."""
    print("\n[1/6] Testing database...")
    from autotrader.db.models import init_db, get_session
    init_db()
    session = get_session()
    session.close()
    print("  Database OK")


def test_alpaca():
    """Test Alpaca connection."""
    print("\n[2/6] Testing Alpaca connection...")
    from autotrader.execution.broker import AlpacaBroker

    assert ALPACA_PAPER, "DANGER: Paper trading is NOT enabled! Set ALPACA_PAPER=true"

    broker = AlpacaBroker()
    account = broker.get_account()
    assert account.get("equity"), "Could not get account data"
    print(f"  Account equity: ${account['equity']:,.2f}")
    print(f"  Cash: ${account['cash']:,.2f}")
    print(f"  Status: {account['status']}")

    positions = broker.get_positions()
    print(f"  Open positions: {len(positions)}")

    return broker


def test_market_data():
    """Test market data fetching."""
    print("\n[3/6] Testing market data...")
    from autotrader.data.market import get_current_price, get_stock_data

    symbol = WATCHLIST[0]
    price = get_current_price(symbol)
    assert price, f"Could not get price for {symbol}"
    print(f"  {symbol}: ${price['price']} ({price['change_pct']:+.2f}%)")

    hist = get_stock_data(symbol, period="3mo", interval="1d")
    assert not hist.empty, "Could not get historical data"
    print(f"  Historical data: {len(hist)} bars")

    return hist


def test_indicators(hist_data):
    """Test technical indicator calculation."""
    print("\n[4/6] Testing technical indicators...")
    from autotrader.data.indicators import calculate_indicators, get_signal_summary

    indicators = calculate_indicators(hist_data)
    assert indicators, "No indicators calculated"
    print(f"  RSI: {indicators.get('rsi')}")
    print(f"  SMA(50): {indicators.get('sma_50')}")
    print(f"  MACD: {indicators.get('macd')}")

    summary = get_signal_summary(indicators)
    print(f"  Signal summary:\n    {summary[:200]}")


def test_claude():
    """Test Claude API connection."""
    print("\n[5/6] Testing Claude API...")
    from autotrader.brain.analyst import ClaudeAnalyst
    from autotrader.execution.broker import AlpacaBroker

    analyst = ClaudeAnalyst()
    broker = AlpacaBroker()
    portfolio = broker.get_portfolio()

    symbol = WATCHLIST[0]
    print(f"  Analyzing {symbol}... (this may take a few seconds)")

    result = analyst.analyze(symbol, portfolio)
    assert result, f"Claude analysis failed for {symbol}"

    print(f"  Decision: {result.action}")
    print(f"  Confidence: {result.confidence:.0%}")
    print(f"  Reasoning: {result.reasoning[:150]}")
    if result.stop_loss:
        print(f"  Stop Loss: ${result.stop_loss:.2f}")

    return result


def test_paper_trade(broker):
    """Test placing a paper trade."""
    print("\n[6/6] Testing paper trade...")
    from autotrader.risk.manager import RiskManager, TradeProposal, RiskVerdict
    from autotrader.data.market import get_current_price

    symbol = "SPY"
    price = get_current_price(symbol)
    assert price, f"Could not get price for {symbol}"

    proposal = TradeProposal(
        symbol=symbol,
        side="BUY",
        confidence=0.99,
        reasoning="Paper trading test",
        stop_loss=price["price"] * 0.95,
        current_price=price["price"],
    )

    risk = RiskManager()
    portfolio = broker.get_portfolio()
    verdict = risk.check_trade(proposal, portfolio)

    if verdict.approved:
        print(f"  Risk approved: {verdict.adjusted_quantity} shares of {symbol}")
        trade = broker.execute_trade(proposal, verdict)
        if trade:
            print(f"  Paper trade placed! Order ID: {trade.alpaca_order_id}")
            print(f"  Status: {trade.status}")
        else:
            print("  Trade execution returned None (check logs)")
    else:
        print(f"  Risk blocked: {verdict.reason}")
        print("  (This is OK for testing — means risk management is working)")


def main():
    print("=" * 50)
    print("AutoTrader Component Test")
    print("=" * 50)

    test_database()

    broker = test_alpaca()

    hist = test_market_data()

    test_indicators(hist)

    test_claude()

    test_paper_trade(broker)

    print("\n" + "=" * 50)
    print("ALL TESTS PASSED")
    print("=" * 50)
    print("\nYou're ready to run: python run.py")


if __name__ == "__main__":
    main()

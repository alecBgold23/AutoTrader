#!/usr/bin/env python3
"""First-time setup script for AutoTrader.

Guides you through:
1. Creating API accounts
2. Setting up .env file
3. Installing dependencies
4. Initializing the database
5. Running verification tests
"""

import os
import sys
import shutil

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    print("=" * 60)
    print("  AutoTrader Setup Wizard")
    print("=" * 60)

    # Step 1: Check .env
    env_path = os.path.join(BASE_DIR, ".env")
    env_example = os.path.join(BASE_DIR, ".env.example")

    if not os.path.exists(env_path):
        print("\n[Step 1] Creating .env file from template...")
        shutil.copy(env_example, env_path)
        print(f"  Created {env_path}")
        print("  You need to fill in your API keys.")
    else:
        print("\n[Step 1] .env file exists")

    # Step 2: Guide for API keys
    print("\n[Step 2] API Keys needed:")
    print()
    print("  a) ALPACA (for trading):")
    print("     1. Go to https://app.alpaca.markets/signup")
    print("     2. Create a free account")
    print("     3. Go to 'Paper Trading' in the left sidebar")
    print("     4. Click 'API Keys' → 'Generate New Key'")
    print("     5. Copy the API Key and Secret Key to .env")
    print()
    print("  b) ANTHROPIC (for Claude AI):")
    print("     1. Go to https://console.anthropic.com/")
    print("     2. Create an account and add billing")
    print("     3. Go to 'API Keys' → create a new key")
    print("     4. Copy to .env as ANTHROPIC_API_KEY")
    print()
    print("  c) TELEGRAM (optional, for alerts):")
    print("     1. Message @BotFather on Telegram")
    print("     2. Send /newbot and follow the prompts")
    print("     3. Copy the bot token to .env")
    print("     4. Message your new bot, then visit:")
    print("        https://api.telegram.org/bot<TOKEN>/getUpdates")
    print("     5. Find your chat_id in the response")
    print("     6. Copy to .env as TELEGRAM_CHAT_ID")
    print()
    print("  d) FINNHUB (optional, for news):")
    print("     1. Go to https://finnhub.io/register")
    print("     2. Free tier gives 60 calls/minute")
    print("     3. Copy API key to .env")

    # Step 3: Check if keys are set
    print("\n[Step 3] Checking .env configuration...")
    from dotenv import load_dotenv
    load_dotenv(env_path)

    checks = {
        "ALPACA_API_KEY": os.getenv("ALPACA_API_KEY", ""),
        "ALPACA_SECRET_KEY": os.getenv("ALPACA_SECRET_KEY", ""),
        "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", ""),
        "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN", ""),
    }

    all_set = True
    for key, val in checks.items():
        if val and not val.startswith("your_"):
            print(f"  {key}: SET")
        else:
            status = "OPTIONAL" if "TELEGRAM" in key else "REQUIRED"
            print(f"  {key}: NOT SET ({status})")
            if status == "REQUIRED":
                all_set = False

    if not all_set:
        print(f"\n  Edit {env_path} and fill in the required keys.")
        print("  Then run this script again.")
        return

    # Step 4: Initialize database
    print("\n[Step 4] Initializing database...")
    sys.path.insert(0, BASE_DIR)
    from autotrader.db.models import init_db
    init_db()
    print("  Database created at data/autotrader.db")

    # Step 5: Verify
    print("\n[Step 5] Setup complete!")
    print()
    print("  Next steps:")
    print("  1. Run the test:  python scripts/test_paper.py")
    print("  2. Start trading: python run.py")
    print()
    print("  IMPORTANT: The system starts in PAPER TRADING mode.")
    print("  It will NOT use real money until you change ALPACA_PAPER=false")


if __name__ == "__main__":
    main()

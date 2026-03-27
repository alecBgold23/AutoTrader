#!/usr/bin/env python3
"""Securely set API keys in .env without them appearing in chat logs.

Run this in your terminal:
    cd ~/Desktop/AutoTrader && venv/bin/python scripts/set_keys.py
"""

import getpass
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def main():
    print("=" * 50)
    print("  AutoTrader — Secure Key Setup")
    print("=" * 50)
    print()
    print("Keys are entered securely (hidden input).")
    print("They go straight into .env — never shown on screen.")
    print()

    # Read current .env
    env_content = ENV_PATH.read_text() if ENV_PATH.exists() else ""

    keys = {
        "ALPACA_API_KEY": {
            "prompt": "Alpaca API Key",
            "help": "Get from: https://app.alpaca.markets → Paper Trading → API Keys",
        },
        "ALPACA_SECRET_KEY": {
            "prompt": "Alpaca Secret Key",
            "help": "Shown once when you generate the API key",
        },
        "ANTHROPIC_API_KEY": {
            "prompt": "Anthropic API Key",
            "help": "Get from: https://console.anthropic.com → API Keys",
        },
        "TELEGRAM_BOT_TOKEN": {
            "prompt": "Telegram Bot Token (press Enter to skip)",
            "help": "Get from: message @BotFather on Telegram → /newbot",
            "optional": True,
        },
        "TELEGRAM_CHAT_ID": {
            "prompt": "Telegram Chat ID (press Enter to skip)",
            "help": "After messaging your bot, visit: https://api.telegram.org/bot<TOKEN>/getUpdates",
            "optional": True,
        },
        "FINNHUB_API_KEY": {
            "prompt": "Finnhub API Key (press Enter to skip)",
            "help": "Get from: https://finnhub.io/register (free tier)",
            "optional": True,
        },
    }

    for key_name, info in keys.items():
        print(f"\n--- {info['prompt']} ---")
        print(f"  {info['help']}")

        value = getpass.getpass(f"  Enter {key_name}: ")

        if not value and info.get("optional"):
            print(f"  Skipped (optional)")
            continue

        if not value:
            print(f"  WARNING: {key_name} is required! Skipping for now.")
            continue

        # Replace in env content
        old_line = None
        for line in env_content.split("\n"):
            if line.startswith(f"{key_name}="):
                old_line = line
                break

        if old_line:
            env_content = env_content.replace(old_line, f"{key_name}={value}")
        else:
            env_content += f"\n{key_name}={value}"

        print(f"  {key_name} saved! (length: {len(value)} chars)")

    # Write back
    ENV_PATH.write_text(env_content)
    print("\n" + "=" * 50)
    print(f"  Keys saved to {ENV_PATH}")
    print("=" * 50)
    print()
    print("Next: run the test")
    print("  venv/bin/python scripts/test_paper.py")


if __name__ == "__main__":
    main()

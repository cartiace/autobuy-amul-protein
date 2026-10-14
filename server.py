"""
Multi-User Production Server for Amul Stock Monitoring & Auto-Checkout.
Orchestrates:
- SQLite/PostgreSQL Database initialization (AES-256 encrypted sessions)
- Central Aggregated Stock Poller (polling unique substores, de-duplicated across 500+ users)
- Multi-User Telegram Bot Controller (conversational onboarding, per-user timeouts, remote control)
"""

import os
import sys
import logging
import asyncio
from dotenv import load_dotenv

import database
from central_poller import CentralStockPoller
from multi_user_bot import MultiUserTelegramBot

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("multi_user_server.log", mode="a", encoding="utf-8")
    ]
)
logger = logging.getLogger("MultiUserServer")

async def main():
    load_dotenv()

    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    poll_interval = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))
    gateway = os.getenv("PAYMENT_GATEWAY", "easebuzz").strip().lower()
    payment_timeout = int(os.getenv("PAYMENT_TIMEOUT_SECONDS", "600"))
    post_purchase_pause_months = int(os.getenv("DEFAULT_POST_PURCHASE_PAUSE_MONTHS", "2"))
    target_sku = os.getenv("TARGET_PRODUCT_SKU", "WPCCP05_02").strip()
    target_alias = os.getenv("TARGET_PRODUCT_ALIAS", "amul-chocolate-whey-protein-34-g-or-pack-of-60-sachets").strip()

    if not telegram_token:
        logger.error("FATAL: 'TELEGRAM_BOT_TOKEN' is not configured in .env! Cannot start Telegram bot.")
        return

    print("=" * 65)
    print("      AMUL MULTI-USER AUTO-CHECKOUT & STOCK MONITOR SERVER       ")
    print("=" * 65)
    print(f"Target Product   : {target_alias}")
    print(f"Target SKU       : {target_sku}")
    print(f"Payment Gateway  : {gateway.upper()} (UPI QR Auto-Push)")
    print(f"Poll Interval    : {poll_interval}s per unique warehouse")
    print(f"Post-Buy Snooze  : {post_purchase_pause_months} months default")
    print(f"Database         : {os.getenv('DATABASE_URL', 'sqlite:///amul_bot.db')}")
    print(f"Capacity         : 500+ concurrent users with aggregated polling")
    print("=" * 65)

    # 1. Initialize Database
    logger.info("Initializing multi-tenant database tables...")
    database.init_db()

    # 2. Initialize Telegram Controller
    bot = MultiUserTelegramBot(
        token=telegram_token,
        post_purchase_pause_months=post_purchase_pause_months,
        payment_gateway=gateway,
        payment_timeout_secs=payment_timeout
    )

    # 3. Initialize Central Stock Poller
    poller = CentralStockPoller(
        poll_interval=poll_interval,
        target_alias=target_alias,
        target_sku=target_sku,
        cookies_file=os.getenv("COOKIES_FILE", "cookies.json"),
        on_stock_detected=bot.handle_warehouse_stock
    )

    # 4. Start Telegram listener
    await bot.start()

    # 5. Start Poller
    poller_task = asyncio.create_task(poller.start())

    try:
        # Keep running until interrupted
        await poller_task
    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        logger.info("Server received shutdown signal.")
    finally:
        logger.info("Gracefully shutting down server components...")
        poller.stop()
        await bot.stop()
        logger.info("Multi-User Server shutdown complete.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nMulti-user server exited.")

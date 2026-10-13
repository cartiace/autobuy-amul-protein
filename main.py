"""
Main orchestrator for Amul Stock Monitoring and Auto-Checkout Bot.
Polls Amul's internal product API every 60 seconds and triggers Playwright auto-checkout upon stock detection.
"""

import os
import sys
import time
import logging
import asyncio
from datetime import datetime
from dotenv import load_dotenv

# Load local modules
from notifier import TelegramNotifier
from monitor import StockMonitor
from checkout import AmulAutoCheckout

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("amul_bot.log", mode="a", encoding="utf-8")
    ]
)
logger = logging.getLogger("AmulBot")

async def run_bot():
    # Load environment variables
    load_dotenv()

    substore_id = os.getenv("AMUL_SUBSTORE_ID", "").strip()
    target_url = os.getenv(
        "TARGET_PRODUCT_URL",
        "https://shop.amul.com/en/product/amul-chocolate-whey-protein-34-g-or-pack-of-60-sachets"
    ).strip()
    target_alias = os.getenv(
        "TARGET_PRODUCT_ALIAS",
        "amul-chocolate-whey-protein-34-g-or-pack-of-60-sachets"
    ).strip()
    target_sku = os.getenv("TARGET_PRODUCT_SKU", "WPCCP05_02").strip()
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    cookies_file = os.getenv("COOKIES_FILE", "cookies.json").strip()
    pincode = os.getenv("DELIVERY_PINCODE", "").strip()
    headless_str = os.getenv("HEADLESS", "true").strip().lower()
    headless = headless_str not in ("false", "0", "no")
    poll_interval = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))
    quantity = int(os.getenv("PURCHASE_QUANTITY", "1"))

    print("=" * 65)
    print("        AMUL E-COMMERCE STOCK MONITOR & AUTO-CHECKOUT BOT       ")
    print("=" * 65)
    print(f"Target Product : {target_alias}")
    print(f"Target SKU     : {target_sku}")
    print(f"Substore ID    : {substore_id or 'NOT CONFIGURED (Please set in .env)'}")
    print(f"Pincode        : {pincode or 'Not specified'}")
    print(f"Poll Interval  : {poll_interval}s")
    print(f"Cookies File   : {cookies_file} (Exists: {os.path.exists(cookies_file)})")
    print(f"Headless Mode  : {headless}")
    print(f"Telegram Alerts: {'CONFIGURED' if (telegram_token and telegram_chat_id) else 'DISABLED'}")
    print("=" * 65)

    if not substore_id:
        logger.error(
            "ERROR: 'AMUL_SUBSTORE_ID' is not set in your .env file! "
            "Please refer to README.md to find your regional substore ID."
        )
        return

    if not os.path.exists(cookies_file):
        logger.warning(
            f"⚠️ NOTICE: '{cookies_file}' not found in current directory. "
            "Auto-checkout requires active cookies to bypass OTP login at runtime. "
            "See README.md for export instructions."
        )

    # Initialize components
    notifier = TelegramNotifier(token=telegram_token, chat_id=telegram_chat_id)
    monitor = StockMonitor(
        substore_id=substore_id,
        target_alias=target_alias,
        target_sku=target_sku,
        cookies_file=cookies_file if os.path.exists(cookies_file) else None
    )

    checkout_engine = AmulAutoCheckout(
        cookies_file=cookies_file,
        target_url=target_url,
        substore_id=substore_id,
        pincode=pincode,
        headless=headless,
        quantity=quantity
    )

    logger.info("Initializing session connection with Amul StoreHippo API...")
    monitor.refresh_session_tid()

    logger.info(f"Starting stock polling loop every {poll_interval} seconds...")
    poll_count = 0

    while True:
        try:
            poll_count += 1
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            logger.info(f"[Poll #{poll_count} at {now_str}] Checking stock via internal API...")

            in_stock, stock_qty, details = monitor.check_stock_api()

            if in_stock and stock_qty > 0:
                prod_name = details.get("name", "Amul Chocolate Whey Protein 60 sachets")
                logger.info(f"🚨 TARGET IN STOCK! Stock: {stock_qty} | Product: {prod_name}")

                # Optional alert that stock was found
                await notifier.notify_stock_alert(prod_name, stock_qty)

                logger.info("⚡ Triggering Playwright Auto-Checkout immediate execution...")
                success, order_id, message = await checkout_engine.execute_checkout()

                if success:
                    logger.info("🎉 Order successfully placed via Cash on Delivery!")
                    
                    # Mandatory requirement: Send telegram notification stating:
                    # 'Product was in stock. Order automatically placed.'
                    await notifier.notify_order_placed(
                        order_id=order_id,
                        details=f"Product: {prod_name}\nQuantity: {quantity}\nMethod: Cash on Delivery (COD)"
                    )
                    logger.info("Execution complete. Exiting to avoid duplicate orders.")
                    break
                else:
                    logger.error(f"Checkout attempt failed: {message}. Will re-check on next loop iteration.")
            else:
                logger.info(f"Status: Out of Stock (Substore: {substore_id}). Retrying in {poll_interval}s...")

        except KeyboardInterrupt:
            logger.info("Bot manually terminated by user.")
            break
        except Exception as e:
            logger.error(f"Unexpected error in polling loop: {e}", exc_info=True)

        await asyncio.sleep(poll_interval)

if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        print("\nExiting bot.")

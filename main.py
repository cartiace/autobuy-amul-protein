"""
Main orchestrator for Amul Stock Monitoring and Auto-Checkout Bot.
Polls Amul's internal product API and triggers Playwright auto-checkout upon stock detection.
Supports automated UPI QR Code generation, instant Telegram push notifications,
and interactive Telegram remote control to pause/timeout purchases for 1, 2, or 3 months
in Notify-Only Mode, or cancel purchases in progress on demand.
"""

import os
import sys
import time
import logging
import asyncio
from datetime import datetime
from dotenv import load_dotenv

# Load local modules
from state_manager import BotStateManager
from telegram_control import TelegramBotController
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
    payment_gateway = os.getenv("PAYMENT_GATEWAY", "easebuzz").strip().lower()
    payment_timeout = int(os.getenv("PAYMENT_TIMEOUT_SECONDS", "600"))
    post_purchase_pause_months = int(os.getenv("DEFAULT_POST_PURCHASE_PAUSE_MONTHS", "2"))
    notify_interval_mins = int(os.getenv("NOTIFY_ONLY_ALERT_INTERVAL_MINUTES", "30"))

    # Initialize State Manager
    state_manager = BotStateManager()
    is_active, paused_until, reason = state_manager.is_auto_buy_active()

    print("=" * 65)
    print("        AMUL E-COMMERCE STOCK MONITOR & AUTO-CHECKOUT BOT       ")
    print("=" * 65)
    print(f"Target Product   : {target_alias}")
    print(f"Target SKU       : {target_sku}")
    print(f"Substore ID      : {substore_id or 'NOT CONFIGURED (Please set in .env)'}")
    print(f"Pincode          : {pincode or 'Not specified'}")
    print(f"Payment Gateway  : {payment_gateway.upper()} (UPI QR Auto-Push)")
    print(f"Auto-Buy Status  : {'ACTIVE' if is_active else 'PAUSED (Notify-Only Mode)'}")
    if not is_active:
        print(f"Pause Detail     : {reason}")
    print(f"Post-Buy Snooze  : {post_purchase_pause_months} months default")
    print(f"Poll Interval    : {poll_interval}s")
    print(f"Cookies File     : {cookies_file} (Exists: {os.path.exists(cookies_file)})")
    print(f"Headless Mode    : {headless}")
    print(f"Telegram Control : {'ACTIVE' if (telegram_token and telegram_chat_id) else 'DISABLED'}")
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

    # Cancellation & Force-Buy events
    cancel_event = asyncio.Event()
    force_buy_event = asyncio.Event()

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
        quantity=quantity,
        gateway=payment_gateway,
        notifier=notifier,
        payment_timeout_secs=payment_timeout,
        cancel_event=cancel_event
    )

    telegram_control = TelegramBotController(
        token=telegram_token,
        chat_id=telegram_chat_id,
        state_manager=state_manager,
        checkout_engine=checkout_engine,
        cancel_event=cancel_event,
        force_buy_event=force_buy_event,
        pincode=pincode,
        substore_id=substore_id,
        target_name="Amul Chocolate Whey Protein (60 sachets)",
        target_alias=target_alias
    )

    # Start Telegram background listener
    if telegram_token and telegram_chat_id:
        await telegram_control.start()

    logger.info("Initializing session connection with Amul StoreHippo API...")
    monitor.refresh_session_tid()

    logger.info(f"Starting stock polling loop every {poll_interval} seconds...")
    poll_count = 0

    try:
        while True:
            try:
                poll_count += 1
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # Check auto-buy status
                is_active, paused_until, reason = state_manager.is_auto_buy_active()
                mode_label = "AUTO-BUY ACTIVE" if is_active else f"PAUSED ({reason})"
                logger.info(f"[Poll #{poll_count} at {now_str}] [{mode_label}] Checking stock via internal API...")

                in_stock, stock_qty, details = monitor.check_stock_api()
                state_manager.record_stock_check(in_stock=in_stock, quantity=stock_qty)

                if in_stock and stock_qty > 0:
                    prod_name = details.get("name", "Amul Chocolate Whey Protein 60 sachets")

                    if is_active or force_buy_event.is_set():
                        force_buy_event.clear()
                        logger.info(f"🚨 TARGET IN STOCK! Stock: {stock_qty} | Product: {prod_name}")
                        logger.info("⚡ Auto-buy is active! Triggering Playwright Checkout & UPI payment request...")

                        cancel_event.clear()
                        await notifier.notify_stock_alert(prod_name, stock_qty)

                        MAX_TRIES = 2
                        last_message = "Unknown checkout error"
                        success = False
                        order_id = None

                        for attempt in range(1, MAX_TRIES + 1):
                            if cancel_event.is_set():
                                logger.info("Checkout process cancelled by user via Telegram.")
                                break

                            logger.info(f"🛒 [AUTO-BUY ATTEMPT {attempt}/{MAX_TRIES}] Executing Playwright checkout...")
                            success, order_id, message = await checkout_engine.execute_checkout()
                            last_message = message or last_message

                            if success:
                                break
                            else:
                                if cancel_event.is_set():
                                    logger.info("Checkout process cancelled by user via Telegram.")
                                    break
                                logger.warning(f"⚠️ [AUTO-BUY ATTEMPT {attempt}/{MAX_TRIES} FAILED] Reason: {last_message}")
                                if attempt < MAX_TRIES:
                                    logger.info("Retrying auto-buy in 2s...")
                                    await asyncio.sleep(2)

                        if success:
                            logger.info("🎉 Order successfully placed and verified via UPI!")
                            state_manager.record_order_placed()

                            # Auto-snooze for default duration (e.g. 2 months) so duplicate orders aren't made
                            pause_days = float(post_purchase_pause_months * 30)
                            paused_until, snooze_reason = state_manager.pause_auto_buy(
                                days=pause_days,
                                reason=f"Auto-snoozed for {post_purchase_pause_months} months post-purchase"
                            )
                            expiry_str = paused_until.strftime("%d %b %Y, %H:%M")

                            await notifier.notify_order_placed(
                                order_id=order_id,
                                details=(
                                    f"Product: {prod_name}\n"
                                    f"Quantity: {quantity}\n"
                                    f"Payment: Verified via UPI ({payment_gateway.upper()})"
                                ),
                                paused_until_str=expiry_str
                            )
                            logger.info(
                                f"Order complete. Auto-purchase snoozed until {expiry_str}. "
                                "Monitoring will continue in Notify-Only Mode."
                            )
                        else:
                            if cancel_event.is_set():
                                logger.info("Checkout process cancelled by user via Telegram.")
                            else:
                                logger.error(
                                    f"❌ [AUTO-BUY FAILED AFTER {MAX_TRIES} TRIES]\n"
                                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                    f"• Product      : {prod_name}\n"
                                    f"• Substore ID  : {substore_id}\n"
                                    f"• Pincode      : {pincode}\n"
                                    f"• Total Tries  : {MAX_TRIES}\n"
                                    f"• Problem/Error: {last_message}\n"
                                    f"• Action Taken : Dispatched manual purchase alert with direct link\n"
                                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                                )
                                await notifier.notify_autobuy_failed_manual(
                                    product_name=prod_name,
                                    product_url=target_url,
                                    reason=last_message
                                )
                    else:
                        # Auto-buy is PAUSED: Notify-Only Mode
                        logger.info(
                            f"🔔 Target is IN STOCK ({stock_qty} units), but auto-buy is PAUSED ({reason}). "
                            "Notify-Only Mode."
                        )
                        if state_manager.should_notify_stock(stock_qty, min_interval_seconds=notify_interval_mins * 60):
                            logger.info("Sending stock alert notification to Telegram (Notify-Only)...")
                            await notifier.notify_stock_alert_paused(
                                product_name=prod_name,
                                stock_qty=stock_qty,
                                paused_reason=reason
                            )
                else:
                    logger.info(f"Status: Out of Stock (Substore: {substore_id}). Retrying in {poll_interval}s...")

            except KeyboardInterrupt:
                raise
            except Exception as e:
                logger.error(f"Unexpected error in polling loop: {e}", exc_info=True)

            # Sleep between polls or wake up immediately if force_buy_event is triggered
            try:
                await asyncio.wait_for(force_buy_event.wait(), timeout=poll_interval)
            except asyncio.TimeoutError:
                pass

    except KeyboardInterrupt:
        logger.info("Bot manually terminated by user.")
    finally:
        logger.info("Shutting down bot components...")
        if telegram_control:
            await telegram_control.stop()

if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        print("\nExiting bot.")

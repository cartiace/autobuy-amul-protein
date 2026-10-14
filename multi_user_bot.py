"""
Multi-User Telegram Bot Controller for Amul Stock Monitoring & Auto-Checkout.
Features 100% native conversational onboarding:
- Step 1: User enters 6-digit Pincode -> Auto-resolves regional warehouse substore
- Step 2: User enters 10-digit Phone -> Bot triggers real Amul SMS OTP
- Step 3: User enters 6-digit SMS OTP -> Bot verifies and extracts session cookies
Zero manual JSON files or browser extensions required!
Encrypted with AES-256 Fernet at rest.
"""

import os
import json
import logging
import asyncio
from datetime import datetime
from typing import Optional, List, Dict, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Document
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    Application
)

import database
from database import User
from pincode_resolver import resolver
from amul_auth import auth_manager
from notifier import TelegramNotifier
from checkout import AmulAutoCheckout

logger = logging.getLogger("MultiUserBot")

class MultiUserTelegramBot:
    def __init__(
        self,
        token: str,
        post_purchase_pause_months: int = 2,
        payment_gateway: str = "easebuzz",
        payment_timeout_secs: int = 600
    ):
        self.token = token.strip()
        self.post_purchase_pause_months = post_purchase_pause_months
        self.payment_gateway = payment_gateway
        self.payment_timeout_secs = payment_timeout_secs
        self.app: Optional[Application] = None
        
        # Concurrency limit for simultaneous Playwright checkout workers
        self.checkout_semaphore = asyncio.Semaphore(5)
        # Per-user active checkout cancel events: {chat_id: asyncio.Event}
        self.active_checkouts: Dict[int, asyncio.Event] = {}

    def _get_user_keyboard(self, is_active: bool) -> InlineKeyboardMarkup:
        if is_active:
            keyboard = [
                [
                    InlineKeyboardButton("⏸️ Pause 1 Mo", callback_data="pause_30"),
                    InlineKeyboardButton("⏸️ Pause 2 Mos", callback_data="pause_60"),
                    InlineKeyboardButton("⏸️ Pause 3 Mos", callback_data="pause_90"),
                ],
                [
                    InlineKeyboardButton("📊 My Status", callback_data="status"),
                    InlineKeyboardButton("📍 Change Pincode", callback_data="change_pincode"),
                ]
            ]
        else:
            keyboard = [
                [
                    InlineKeyboardButton("▶️ Resume Auto-Buy", callback_data="resume"),
                    InlineKeyboardButton("📊 My Status", callback_data="status"),
                ],
                [
                    InlineKeyboardButton("📍 Change Pincode", callback_data="change_pincode"),
                ]
            ]
        return InlineKeyboardMarkup(keyboard)

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        username = update.effective_user.username or update.effective_user.first_name
        user = database.get_or_create_user(chat_id=chat_id, username=username)

        if user.onboarding_state == "READY":
            is_active, _, reason = user.is_auto_buy_active()
            mode_str = "🟢 <b>ACTIVE</b>" if is_active else f"⏸️ <b>PAUSED</b> ({reason})"
            phone_str = f"+91 {user.phone_number}" if user.phone_number else "Linked"
            msg = (
                f"👋 <b>Welcome back, {username}!</b>\n\n"
                f"📍 <b>Delivery Pincode:</b> <code>{user.pincode}</code> ({user.substore_name})\n"
                f"📱 <b>Amul Account:</b> {phone_str}\n"
                f"📦 <b>Target:</b> Amul Chocolate Whey Protein (60 sachets)\n"
                f"🛒 <b>Auto-Buy Status:</b> {mode_str}\n\n"
                f"<b>Quick Actions:</b>\n"
                f"• <code>/status</code> - View monitoring details\n"
                f"• <code>/pause [duration]</code> - Snooze purchases (e.g. <code>/pause 2m</code>)\n"
                f"• <code>/resume</code> - Resume auto-buy anytime\n"
                f"• <code>/pincode</code> - Change delivery pincode\n"
                f"• <code>/cancel</code> - Cancel active purchase\n"
                f"• <code>/help</code> - Guide & options"
            )
            await update.message.reply_text(
                msg,
                parse_mode="HTML",
                reply_markup=self._get_user_keyboard(is_active)
            )
        else:
            # Onboarding Step 1: Request Pincode
            database.set_user_onboarding_state(chat_id, "AWAITING_PINCODE")
            msg = (
                f"👋 <b>Welcome to Amul Stock Monitor & Auto-Buy!</b>\n\n"
                f"I monitor 24/7 for <b>Amul Chocolate Whey Protein (60 sachets)</b> restocks "
                f"and automatically checkout as soon as your regional warehouse gets inventory.\n\n"
                f"👉 <b>Step 1 of 2: Delivery Location</b>\n"
                f"Please reply with your <b>6-digit delivery Pincode</b> (e.g. <code>201310</code> or <code>110001</code>):"
            )
            await update.message.reply_text(msg, parse_mode="HTML")

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = (
            "📖 <b>Amul Auto-Buy Commands & Guide</b>\n\n"
            "• <code>/status</code> - View your current warehouse, pincode, and purchase timeout\n"
            "• <code>/pause 1m</code> - Timeout auto-purchase for 1 month (30 days)\n"
            "• <code>/pause 2m</code> - Timeout auto-purchase for 2 months (60 days)\n"
            "• <code>/pause 3m</code> - Timeout auto-purchase for 3 months (90 days)\n"
            "• <code>/resume</code> - Resume auto-purchases immediately\n"
            "• <code>/pincode 110001</code> - Update your delivery pincode\n"
            "• <code>/cancel</code> - Cancel any ongoing purchase or UPI QR payment\n"
            "• <code>/relogin</code> - Re-authenticate your Amul account with SMS OTP"
        )
        await update.message.reply_text(msg, parse_mode="HTML")

    async def cmd_relogin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        database.set_user_onboarding_state(chat_id, "AWAITING_PHONE")
        await update.message.reply_text(
            "📱 <b>Amul Account Re-Login</b>\n\n"
            "Please reply with your <b>10-digit registered Amul Mobile Number</b> (e.g. <code>9876543210</code>):",
            parse_mode="HTML"
        )

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        user = database.get_user(chat_id)
        if not user or user.onboarding_state != "READY":
            await update.message.reply_text("⚠️ Setup not complete. Send /start to begin onboarding.")
            return

        is_active, paused_until, reason = user.is_auto_buy_active()
        mode_str = "🟢 <b>ACTIVE (Auto-buy enabled)</b>" if is_active else f"⏸️ <b>PAUSED (Notify-Only Mode)</b>\n<i>{reason}</i>"
        phone_str = f"+91 {user.phone_number}" if user.phone_number else "Linked"

        msg = (
            f"📊 <b>Your Personal Status Report</b>\n\n"
            f"🛒 <b>Auto-Buy Mode:</b> {mode_str}\n"
            f"📍 <b>Delivery Pincode:</b> <code>{user.pincode}</code>\n"
            f"🏪 <b>Warehouse:</b> <b>{user.substore_name}</b>\n"
            f"📱 <b>Amul Account:</b> {phone_str}\n"
            f"📦 <b>Target Product:</b> Amul Chocolate Whey Protein (60 sachets)\n"
            f"🔢 <b>Quantity:</b> {user.quantity}\n"
            f"📅 <b>Member Since:</b> {user.created_at.strftime('%d %b %Y')}\n"
        )
        await update.message.reply_text(
            msg,
            parse_mode="HTML",
            reply_markup=self._get_user_keyboard(is_active)
        )

    async def cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        user = database.get_user(chat_id)
        if not user or user.onboarding_state != "READY":
            await update.message.reply_text("⚠️ Setup not complete. Send /start to configure your bot.")
            return

        args = context.args
        if not args:
            keyboard = [
                [
                    InlineKeyboardButton("⏸️ 1 Month", callback_data="pause_30"),
                    InlineKeyboardButton("⏸️ 2 Months", callback_data="pause_60"),
                    InlineKeyboardButton("⏸️ 3 Months", callback_data="pause_90"),
                ],
                [
                    InlineKeyboardButton("⏸️ Indefinite", callback_data="pause_indefinite"),
                    InlineKeyboardButton("▶️ Resume Auto-Buy", callback_data="resume"),
                ]
            ]
            await update.message.reply_text(
                "⏸️ <b>Choose auto-purchase timeout duration:</b>\n"
                "While paused, you will still receive stock alerts (Notify-Only Mode), but the bot will not purchase automatically.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        arg_str = " ".join(args).strip().lower()
        from state_manager import BotStateManager
        days = BotStateManager.parse_duration_string(arg_str)

        if days is not None:
            paused_until, reason = database.pause_user_autobuy(chat_id, days=days)
            expiry_str = paused_until.strftime("%d %b %Y, %H:%M UTC")
            msg = (
                f"⏸️ <b>Auto-Purchase Snoozed!</b>\n\n"
                f"⏱️ <b>Duration:</b> {reason}\n"
                f"📅 <b>Paused until:</b> {expiry_str}\n\n"
                f"🔔 <i>You will receive stock notifications when inventory arrives, but no order will be placed automatically.</i>\n\n"
                f"Send <code>/resume</code> anytime to turn auto-buy back on."
            )
        else:
            _, reason = database.pause_user_autobuy(chat_id, days=None)
            msg = (
                "⏸️ <b>Auto-Purchase Paused Indefinitely!</b>\n\n"
                "Monitoring remains active in Notify-Only Mode.\n"
                "Send <code>/resume</code> anytime to turn auto-buy back on."
            )

        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=self._get_user_keyboard(False))

    async def cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        database.resume_user_autobuy(chat_id)
        msg = (
            "▶️ <b>Auto-Purchase Has Been RESUMED!</b>\n\n"
            "As soon as stock is detected in your regional warehouse, the bot will automatically "
            "trigger checkout and send your UPI QR payment code."
        )
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=self._get_user_keyboard(True))

    async def cmd_pincode(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        args = context.args
        if args and len(args[0]) == 6 and args[0].isdigit():
            pincode = args[0]
            res = resolver.resolve(pincode)
            if res:
                database.update_user_pincode(chat_id, pincode, res["substore_id"], res["warehouse_name"])
                await update.message.reply_text(
                    f"✅ <b>Pincode updated to {pincode}!</b>\n"
                    f"📍 Warehouse: <b>{res['warehouse_name']}</b> (Substore: <code>{res['substore_id']}</code>)",
                    parse_mode="HTML"
                )
                return
            else:
                await update.message.reply_text(f"❌ Pincode {pincode} is not currently serviceable by Amul.")
                return

        database.set_user_onboarding_state(chat_id, "AWAITING_PINCODE")
        await update.message.reply_text("📍 Please send your new <b>6-digit delivery pincode</b>:", parse_mode="HTML")

    async def cmd_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        # Check if user is cancelling an active OTP login
        await auth_manager.cancel_session(chat_id)

        # Check if user is cancelling an active checkout
        if chat_id in self.active_checkouts:
            self.active_checkouts[chat_id].set()
            await update.message.reply_text(
                "❌ <b>Purchase Cancelled!</b>\n"
                "Aborting payment waiting loop and closing checkout session.",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text(
                "ℹ️ No purchase is currently in progress for your account.\n"
                "Use <code>/pause</code> if you want to timeout future auto-buys.",
                parse_mode="HTML"
            )

    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        text = update.message.text.strip()
        user = database.get_user(chat_id)

        if not user:
            user = database.get_or_create_user(chat_id, update.effective_user.username)

        # State 1: Awaiting Pincode
        if user.onboarding_state == "AWAITING_PINCODE":
            if text.isdigit() and len(text) == 6:
                await update.message.reply_text("🔍 <i>Verifying pincode with Amul network...</i>", parse_mode="HTML")
                res = resolver.resolve(text)
                if res:
                    database.update_user_pincode(chat_id, text, res["substore_id"], res["warehouse_name"])
                    database.set_user_onboarding_state(chat_id, "AWAITING_PHONE")
                    msg = (
                        f"✅ <b>Pincode {text} Verified!</b>\n"
                        f"📍 <b>Regional Warehouse:</b> {res['warehouse_name']}\n\n"
                        f"👉 <b>Step 2 of 2: Login to Amul</b>\n"
                        f"Please reply with your <b>10-digit registered Amul Mobile Number</b> (e.g. <code>9876543210</code>):\n\n"
                        f"<i>Amul will send you an official SMS OTP to verify.</i>"
                    )
                    await update.message.reply_text(msg, parse_mode="HTML")
                else:
                    await update.message.reply_text(
                        f"❌ Pincode <code>{text}</code> is not currently serviceable by Amul.\n"
                        f"Please try a different delivery pincode:",
                        parse_mode="HTML"
                    )
            else:
                await update.message.reply_text("⚠️ Please send a valid 6-digit numerical pincode (e.g. <code>201310</code>).", parse_mode="HTML")
            return

        # State 2: Awaiting Phone Number -> Send SMS OTP
        if user.onboarding_state == "AWAITING_PHONE":
            phone_digits = "".join(filter(str.isdigit, text))
            if len(phone_digits) in (10, 12):
                normalized = phone_digits[-10:]
                status_msg = await update.message.reply_text(
                    f"⏳ <i>Connecting to Amul to request SMS OTP for +91 {normalized}...</i>",
                    parse_mode="HTML"
                )
                success, msg = await auth_manager.send_otp(chat_id, normalized)
                if success:
                    # Save phone and transition to AWAITING_OTP
                    with database.get_db() as db:
                        u = db.query(User).filter(User.telegram_chat_id == chat_id).first()
                        if u:
                            u.phone_number = normalized
                            u.onboarding_state = "AWAITING_OTP"
                            db.commit()

                    await status_msg.edit_text(
                        f"📲 <b>SMS OTP Sent!</b>\n\n"
                        f"Amul just sent an official 6-digit verification code to <b>+91 {normalized}</b>.\n\n"
                        f"Please reply with the <b>6-digit OTP</b> within 3 minutes:",
                        parse_mode="HTML"
                    )
                else:
                    await status_msg.edit_text(f"⚠️ {msg}\n\nPlease check your phone number and enter it again:")
            else:
                await update.message.reply_text(
                    "⚠️ Please enter a valid 10-digit Indian mobile number (e.g. <code>9876543210</code>).",
                    parse_mode="HTML"
                )
            return

        # State 3: Awaiting SMS OTP -> Verify and Extract Cookies
        if user.onboarding_state == "AWAITING_OTP":
            otp_digits = "".join(filter(str.isdigit, text))
            if len(otp_digits) == 6:
                status_msg = await update.message.reply_text("🔐 <i>Verifying OTP and linking your account...</i>", parse_mode="HTML")
                success, cookies, msg = await auth_manager.verify_otp(chat_id, otp_digits)
                if success and cookies:
                    database.set_user_cookies(chat_id, cookies)
                    u = database.get_user(chat_id)
                    congrats_msg = (
                        f"🎉 <b>Login Successful & Account Linked!</b>\n\n"
                        f"📍 <b>Delivery Pincode:</b> <code>{u.pincode}</code> ({u.substore_name})\n"
                        f"📱 <b>Amul Account:</b> +91 {u.phone_number}\n"
                        f"📦 <b>Target Product:</b> Amul Chocolate Whey Protein (60 sachets)\n"
                        f"🛒 <b>Auto-Purchase:</b> 🟢 <b>ACTIVE</b>\n\n"
                        f"🔒 <i>Your session has been securely encrypted with AES-256.</i>\n\n"
                        f"The bot is now monitoring your warehouse 24/7. When stock arrives, "
                        f"you will receive your dynamic UPI QR code to complete payment in seconds.\n\n"
                        f"Send <code>/status</code> anytime to manage your account."
                    )
                    await status_msg.edit_text(
                        congrats_msg,
                        parse_mode="HTML"
                    )
                    await update.message.reply_text(
                        "👇 <b>Your Dashboard:</b>",
                        reply_markup=self._get_user_keyboard(True)
                    )
                else:
                    await status_msg.edit_text(
                        f"{msg}\n\nPlease enter the 6-digit code again (or type /start to re-enter your number):",
                        parse_mode="HTML"
                    )
            else:
                await update.message.reply_text("⚠️ The OTP must be a 6-digit number. Please enter the code from your SMS.", parse_mode="HTML")
            return

        # Fallback: check if user pasted JSON cookies manually
        try:
            raw_cookies = json.loads(text)
            if isinstance(raw_cookies, list) and len(raw_cookies) > 0:
                database.set_user_cookies(chat_id, raw_cookies)
                await update.message.reply_text(
                    "🎉 <b>Cookies Linked & Encrypted!</b> Your account is ready for auto-checkout.",
                    parse_mode="HTML",
                    reply_markup=self._get_user_keyboard(True)
                )
                return
        except Exception:
            pass

    async def handle_document_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        doc: Document = update.message.document
        user = database.get_user(chat_id)

        if not user:
            await update.message.reply_text("Send /start to begin onboarding.")
            return

        if doc.file_name.endswith(".json"):
            file = await doc.get_file()
            file_bytes = await file.download_as_bytearray()
            try:
                cookies = json.loads(file_bytes.decode("utf-8"))
                if isinstance(cookies, list) and len(cookies) > 0:
                    database.set_user_cookies(chat_id, cookies)
                    msg = (
                        f"🎉 <b>Cookies Verified & Encrypted!</b>\n\n"
                        f"📍 <b>Warehouse:</b> {user.substore_name} (<code>{user.pincode}</code>)\n"
                        f"🛒 <b>Auto-Purchase:</b> 🟢 <b>ACTIVE</b>\n\n"
                        f"Your account is primed. When stock appears, your UPI QR code will be pushed here instantly!"
                    )
                    await update.message.reply_text(
                        msg,
                        parse_mode="HTML",
                        reply_markup=self._get_user_keyboard(True)
                    )
                    return
            except Exception as e:
                logger.error(f"Error parsing uploaded cookies: {e}")

        await update.message.reply_text("⚠️ Invalid cookies file. To log in with OTP instead, simply send /start.")

    async def handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        chat_id = update.effective_chat.id
        data = query.data
        user = database.get_user(chat_id)
        if not user:
            return

        if data.startswith("pause_"):
            days_str = data.split("_")[1]
            days = float(days_str) if days_str != "indefinite" else None
            paused_until, reason = database.pause_user_autobuy(chat_id, days=days)
            until_str = paused_until.strftime("%d %b %Y, %H:%M UTC") if paused_until else "Indefinitely"
            await query.edit_message_text(
                f"⏸️ <b>Auto-Purchase Paused ({reason})!</b>\n\n"
                f"📅 <b>Paused until:</b> {until_str}\n"
                f"🔔 <i>Monitoring continues in Notify-Only Mode.</i>",
                parse_mode="HTML",
                reply_markup=self._get_user_keyboard(False)
            )
        elif data == "resume":
            database.resume_user_autobuy(chat_id)
            await query.edit_message_text(
                "▶️ <b>Auto-Purchase Resumed!</b>\n\n"
                "The bot will automatically purchase next time stock is detected.",
                parse_mode="HTML",
                reply_markup=self._get_user_keyboard(True)
            )
        elif data == "change_pincode":
            database.set_user_onboarding_state(chat_id, "AWAITING_PINCODE")
            await query.message.reply_text("📍 Please reply with your new 6-digit delivery pincode:")
        elif data == "cancel_checkout":
            if chat_id in self.active_checkouts:
                self.active_checkouts[chat_id].set()
                await query.edit_message_text("❌ <b>Purchase Cancelled!</b> Session aborted.")
            else:
                await query.edit_message_text("ℹ️ No active checkout session.")
        elif data == "status":
            is_active, _, reason = user.is_auto_buy_active()
            mode_str = "🟢 <b>ACTIVE</b>" if is_active else f"⏸️ <b>PAUSED</b> ({reason})"
            phone_str = f"+91 {user.phone_number}" if user.phone_number else "Linked"
            await query.edit_message_text(
                f"📊 <b>Status:</b>\n"
                f"• Auto-Buy: {mode_str}\n"
                f"• Pincode: <code>{user.pincode}</code> ({user.substore_name})\n"
                f"• Account: {phone_str}",
                parse_mode="HTML",
                reply_markup=self._get_user_keyboard(is_active)
            )

    async def dispatch_user_checkout(self, user: User, product_name: str, stock_qty: int):
        """
        Executes isolated checkout for a single user under concurrency control.
        """
        chat_id = user.telegram_chat_id
        cancel_event = asyncio.Event()
        self.active_checkouts[chat_id] = cancel_event

        notifier = TelegramNotifier(token=self.token, chat_id=str(chat_id))

        async with self.checkout_semaphore:
            logger.info(f"Launching checkout execution for user {chat_id} (Pincode: {user.pincode})...")
            
            # Decrypt user's cookies to temporary in-memory structure
            cookies_list = database.get_user_cookies(chat_id)
            if not cookies_list:
                logger.error(f"User {chat_id} has no valid session cookies.")
                await notifier.send_message_async("⚠️ Auto-checkout failed: Session expired. Please send /start to re-link your account.")
                return

            # Temporary cookies file in scratch/
            scratch_dir = "scratch"
            os.makedirs(scratch_dir, exist_ok=True)
            user_cookies_path = os.path.join(scratch_dir, f"cookies_{chat_id}.json")
            with open(user_cookies_path, "w", encoding="utf-8") as f:
                json.dump(cookies_list, f)

            try:
                checkout = AmulAutoCheckout(
                    cookies_file=user_cookies_path,
                    target_url=f"https://shop.amul.com/en/product/{user.target_alias}",
                    substore_id=user.substore_id,
                    pincode=user.pincode,
                    headless=True,
                    quantity=user.quantity,
                    gateway=self.payment_gateway,
                    notifier=notifier,
                    payment_timeout_secs=self.payment_timeout_secs,
                    cancel_event=cancel_event
                )

                await notifier.notify_stock_alert(product_name, stock_qty)
                success, order_id, msg = await checkout.execute_checkout()

                if success:
                    # Snooze user auto-buy for configured months post-purchase
                    pause_days = float(self.post_purchase_pause_months * 30)
                    paused_until, snooze_reason = database.pause_user_autobuy(
                        chat_id,
                        days=pause_days,
                        reason=f"Auto-snoozed for {self.post_purchase_pause_months} months post-purchase"
                    )
                    expiry_str = paused_until.strftime("%d %b %Y, %H:%M UTC")

                    await notifier.notify_order_placed(
                        order_id=order_id,
                        details=f"Product: {product_name}\nQuantity: {user.quantity}\nPayment: Verified via UPI",
                        paused_until_str=expiry_str
                    )
                else:
                    if cancel_event.is_set():
                        logger.info(f"Checkout for user {chat_id} cancelled by user.")
                    else:
                        logger.error(f"Checkout for user {chat_id} finished: {msg}")

            except Exception as e:
                logger.error(f"Error during user {chat_id} checkout: {e}", exc_info=True)
            finally:
                if os.path.exists(user_cookies_path):
                    os.remove(user_cookies_path)
                self.active_checkouts.pop(chat_id, None)

    async def handle_warehouse_stock(self, substore_id: str, stock_qty: int, details: dict, users: List[User]):
        """
        Triggered when stock appears in a warehouse.
        Dispatches auto-checkout for active users and notify-only alerts for paused users.
        """
        prod_name = details.get("name", "Amul Chocolate Whey Protein 60 sachets")
        logger.info(f"Broadcasting stock in warehouse {substore_id} to {len(users)} users...")

        for user in users:
            is_active, _, reason = user.is_auto_buy_active()
            if is_active:
                # Spawn background checkout task
                asyncio.create_task(self.dispatch_user_checkout(user, prod_name, stock_qty))
            else:
                # Notify-Only Mode
                notifier = TelegramNotifier(token=self.token, chat_id=str(user.telegram_chat_id))
                asyncio.create_task(notifier.notify_stock_alert_paused(
                    product_name=prod_name,
                    stock_qty=stock_qty,
                    paused_reason=reason
                ))

    async def start(self):
        """Initializes and starts the Multi-User Telegram Bot."""
        logger.info("Starting Multi-User Telegram Bot Application...")
        self.app = ApplicationBuilder().token(self.token).build()

        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("help", self.cmd_help))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("relogin", self.cmd_relogin))
        self.app.add_handler(CommandHandler(["pause", "snooze"], self.cmd_pause))
        self.app.add_handler(CommandHandler(["resume", "unpause"], self.cmd_resume))
        self.app.add_handler(CommandHandler("pincode", self.cmd_pincode))
        self.app.add_handler(CommandHandler(["cancel", "abort"], self.cmd_cancel))

        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_message))
        self.app.add_handler(MessageHandler(filters.Document.ALL, self.handle_document_message))
        self.app.add_handler(CallbackQueryHandler(self.handle_callback_query))

        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)
        logger.info("Multi-User Telegram Bot is active with Native In-Chat OTP Login.")

    async def stop(self):
        if self.app:
            if self.app.updater and self.app.updater.running:
                await self.app.updater.stop()
            if self.app.running:
                await self.app.stop()
            await self.app.shutdown()
            logger.info("Multi-User Telegram Bot stopped.")

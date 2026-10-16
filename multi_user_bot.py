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
from product_catalog import (
    CURATED_PRODUCTS,
    get_product_by_id,
    get_product_selection_keyboard,
    resolve_amul_product,
    extract_alias_from_url
)

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
        self.admin_chat_id = str(os.getenv("ADMIN_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID", "")).strip()

    def _get_user_keyboard(self, is_active: bool) -> InlineKeyboardMarkup:
        if is_active:
            keyboard = [
                [
                    InlineKeyboardButton("⏸️ 1 Mo", callback_data="pause_30"),
                    InlineKeyboardButton("⏸️ 2 Mos", callback_data="pause_60"),
                    InlineKeyboardButton("⏸️ 3 Mos", callback_data="pause_90"),
                ],
                [
                    InlineKeyboardButton("🔍 Check Live Stock", callback_data="check_stock"),
                    InlineKeyboardButton("📦 Change Product", callback_data="change_product"),
                ],
                [
                    InlineKeyboardButton("🔔 Switch to Alerts Only", callback_data="switch_notify_only"),
                    InlineKeyboardButton("📊 My Status", callback_data="status"),
                ],
                [
                    InlineKeyboardButton("📍 Change Pincode", callback_data="change_pincode"),
                    InlineKeyboardButton("🛑 Stop & Delete Account", callback_data="confirm_delete"),
                ]
            ]
        else:
            keyboard = [
                [
                    InlineKeyboardButton("⚡ Enable Auto-Buy", callback_data="resume"),
                    InlineKeyboardButton("🔍 Check Live Stock", callback_data="check_stock"),
                ],
                [
                    InlineKeyboardButton("📦 Change Product", callback_data="change_product"),
                    InlineKeyboardButton("📊 My Status", callback_data="status"),
                ],
                [
                    InlineKeyboardButton("📍 Change Pincode", callback_data="change_pincode"),
                    InlineKeyboardButton("🛑 Stop & Delete Account", callback_data="confirm_delete"),
                ]
            ]
        return InlineKeyboardMarkup(keyboard)

    def _get_stock_report_keyboard(self, is_active: bool) -> InlineKeyboardMarkup:
        keyboard = [
            [
                InlineKeyboardButton("🔄 Refresh Stock", callback_data="check_stock"),
                InlineKeyboardButton("📦 Change Product", callback_data="change_product"),
            ],
            [
                InlineKeyboardButton("📊 Back to Dashboard", callback_data="status"),
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

    def _get_mode_selection_keyboard(self) -> InlineKeyboardMarkup:
        keyboard = [
            [
                InlineKeyboardButton("⚡ Auto-Buy (Instant Checkout + UPI QR)", callback_data="onboard_mode_autobuy")
            ],
            [
                InlineKeyboardButton("🔔 Stock Alerts Only (No Login Required)", callback_data="onboard_mode_notify")
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

    def _get_delete_confirm_keyboard(self) -> InlineKeyboardMarkup:
        keyboard = [
            [
                InlineKeyboardButton("🗑️ Yes, Delete My Account", callback_data="do_delete"),
            ],
            [
                InlineKeyboardButton("↩️ Nevermind, Keep Active", callback_data="cancel_delete"),
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        username = update.effective_user.username or update.effective_user.first_name
        user = database.get_or_create_user(chat_id=chat_id, username=username)

        if user.onboarding_state == "READY":
            is_active, _, reason = user.is_auto_buy_active()
            mode_str = "🟢 <b>AUTO-BUY ACTIVE</b>" if is_active else f"🔔 <b>STOCK ALERTS ONLY</b> (<i>{reason}</i>)"
            phone_str = f"+91 {user.phone_number}" if user.phone_number else "Not Linked (Alerts Only)"
            target_str = user.target_name or "Amul Chocolate Whey Protein (60 sachets)"
            msg = (
                f"👋 <b>Welcome back, {username}!</b>\n\n"
                f"📍 <b>Delivery Pincode:</b> <code>{user.pincode}</code> ({user.substore_name})\n"
                f"📱 <b>Amul Account:</b> {phone_str}\n"
                f"📦 <b>Target Product:</b> {target_str}\n"
                f"🎯 <b>Monitoring Mode:</b> {mode_str}\n\n"
                f"<b>Quick Actions:</b>\n"
                f"• <code>/stock</code> - Check live warehouse stock & quantity\n"
                f"• <code>/product</code> - Choose or change target product\n"
                f"• <code>/mode</code> - Switch between Auto-Buy and Alerts-Only\n"
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
                f"I monitor 24/7 for <b>Amul Store</b> restocks and can either:\n"
                f"• ⚡ <b>Auto-Buy:</b> Automatically checkout and send your UPI payment QR code\n"
                f"• 🔔 <b>Stock Alerts:</b> Send instant notifications without placing orders\n\n"
                f"👉 <b>Step 1 of 2: Delivery Location</b>\n"
                f"Please reply with your <b>6-digit delivery Pincode</b> (e.g. <code>201310</code> or <code>110001</code>):"
            )
            await update.message.reply_text(msg, parse_mode="HTML", disable_web_page_preview=True)

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = (
            "📖 <b>Amul Auto-Buy Commands & Guide</b>\n\n"
            "• <code>/stock</code> - Check live inventory & stock quantity in your warehouse\n"
            "• <code>/product</code> - Choose your target product or paste any custom Amul link\n"
            "• <code>/mode</code> - Switch between Auto-Buy and Stock Alerts Only\n"
            "• <code>/status</code> - View your current warehouse, pincode, and target product\n"
            "• <code>/pause 1m</code> - Timeout auto-purchase for 1 month (30 days)\n"
            "• <code>/pause 2m</code> - Timeout auto-purchase for 2 months (60 days)\n"
            "• <code>/pause 3m</code> - Timeout auto-purchase for 3 months (90 days)\n"
            "• <code>/resume</code> - Resume auto-purchases immediately\n"
            "• <code>/notify</code> - Switch to Stock Alerts Only (Notify-Only)\n"
            "• <code>/pincode 110001</code> - Update your delivery pincode\n"
            "• <code>/cancel</code> - Cancel any ongoing purchase or UPI QR payment\n"
            "• <code>/relogin</code> - Re-authenticate your Amul account with SMS OTP\n"
            "• <code>/stop</code> - Stop monitoring and delete your account from the bot"
        )
        await update.message.reply_text(msg, parse_mode="HTML")

    async def cmd_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        user = database.get_user(chat_id)
        if not user or user.onboarding_state not in ("READY", "AWAITING_PRODUCT_URL"):
            await update.message.reply_text("⚠️ Setup not complete. Send /start to begin onboarding.")
            return

        target_name = user.target_name or "Amul Chocolate Whey Protein (60 sachets)"
        msg = (
            f"📦 <b>Target Product Selection</b>\n\n"
            f"🎯 <b>Currently Monitored:</b>\n"
            f"• <b>{target_name}</b>\n"
            f"• SKU: <code>{user.target_sku}</code>\n"
            f"• Link: <a href='https://shop.amul.com/en/product/{user.target_alias}'>View on Amul Store</a>\n\n"
            f"👇 Tap a quick-select product below, or choose <b>🔗 Paste Custom Amul Link</b> to monitor any item from shop.amul.com:"
        )
        await update.message.reply_text(
            msg,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=get_product_selection_keyboard(user.target_alias)
        )

    async def cmd_mode(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        user = database.get_user(chat_id)
        if not user or user.onboarding_state not in ("READY", "CHOOSE_MODE"):
            await update.message.reply_text("⚠️ Setup not complete. Send /start to begin onboarding.")
            return

        is_active, _, reason = user.is_auto_buy_active()
        curr_mode = "⚡ <b>Auto-Buy Active</b>" if is_active else f"🔔 <b>Stock Alerts Only</b> (<i>{reason}</i>)"
        msg = (
            f"🎯 <b>Choose Monitoring Mode</b>\n\n"
            f"Current Mode: {curr_mode}\n\n"
            f"• ⚡ <b>Auto-Buy:</b> Checks out automatically and sends your UPI payment QR.\n"
            f"• 🔔 <b>Stock Alerts Only:</b> Sends instant alerts when in stock; zero login needed.\n\n"
            f"Select your preferred mode below:"
        )
        await update.message.reply_text(
            msg,
            parse_mode="HTML",
            reply_markup=self._get_mode_selection_keyboard()
        )

    async def cmd_notify_only(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        database.pause_user_autobuy(chat_id, days=None, reason="Notify-Only Mode")
        database.mark_user_stock_notified(chat_id, False)
        msg = (
            "🔔 <b>Switched to Stock Alerts Only (Notify-Only Mode)!</b>\n\n"
            "The bot will monitor 24/7 and alert you when stock arrives, but will not checkout automatically.\n\n"
            "You can enable Auto-Buy anytime by tapping ⚡ Enable Auto-Buy or typing <code>/resume</code>."
        )
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=self._get_user_keyboard(False))

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
        mode_str = "🟢 <b>AUTO-BUY ACTIVE</b>" if is_active else f"🔔 <b>STOCK ALERTS ONLY</b>\n<i>{reason}</i>"
        phone_str = f"+91 {user.phone_number}" if user.phone_number else "Not Linked (Alerts Only)"
        target_name = user.target_name or "Amul Chocolate Whey Protein (60 sachets)"

        saved_addr = database.get_user_delivery_address(chat_id)
        addr_line = None
        if saved_addr:
            name = saved_addr.get("name")
            parts = [saved_addr.get("address1"), saved_addr.get("city")]
            combined = ", ".join(filter(None, parts))
            addr_line = f"{name} ({combined})" if name and combined else (combined or name)
        addr_str = f"🏠 <b>Confirmed Address:</b> {addr_line}\n" if addr_line else ""

        msg = (
            f"📊 <b>Your Personal Status Report</b>\n\n"
            f"🎯 <b>Monitoring Mode:</b> {mode_str}\n"
            f"📍 <b>Delivery Pincode:</b> <code>{user.pincode}</code>\n"
            f"🏪 <b>Warehouse:</b> <b>{user.substore_name}</b>\n"
            f"📱 <b>Amul Account:</b> {phone_str}\n"
            f"{addr_str}"
            f"📦 <b>Target Product:</b> <b>{target_name}</b>\n"
            f"🏷️ <b>Target SKU:</b> <code>{user.target_sku}</code>\n"
            f"🔢 <b>Quantity:</b> {user.quantity}\n"
            f"📅 <b>Member Since:</b> {user.created_at.strftime('%d %b %Y')}\n"
        )
        await update.message.reply_text(
            msg,
            parse_mode="HTML",
            reply_markup=self._get_user_keyboard(is_active)
        )

    async def cmd_check_stock(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        user = database.get_user(chat_id)
        if not user or user.onboarding_state not in ("READY", "CHOOSE_MODE"):
            await update.message.reply_text("⚠️ Setup not complete. Send /start to begin onboarding.")
            return

        if not user.substore_id:
            await update.message.reply_text("⚠️ No delivery warehouse configured. Send /pincode to set your delivery location.")
            return

        status_msg = await update.message.reply_text("🔍 <i>Checking live warehouse stock on Amul Store...</i>", parse_mode="HTML")
        await self._render_stock_report(user, status_msg, is_edit=True)

    async def _render_stock_report(self, user: User, target_message: Any, is_edit: bool = True):
        alias = user.target_alias or "amul-chocolate-whey-protein-34-g-or-pack-of-60-sachets"
        sku = user.target_sku or "WPCCP05_02"
        warehouse_name = user.substore_name or "Regional Warehouse"
        pincode = user.pincode or "N/A"
        prod_url = f"https://shop.amul.com/en/product/{alias}"

        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(None, resolve_amul_product, alias, user.substore_id)

        target_name = (res and res.get("name")) or user.target_name or "Amul Chocolate Whey Protein (60 sachets)"
        now_str = datetime.now().strftime("%H:%M:%S")

        is_active, _, reason = user.is_auto_buy_active()
        mode_str = "🟢 <b>Auto-Buy Active</b>" if is_active else f"🔔 <b>Stock Alerts Only</b> (<i>{reason}</i>)"

        if res and res.get("in_stock"):
            qty = res.get("inventory_quantity", 0)
            price_val = res.get("price")
            price_str = f"₹{price_val:,}" if price_val else "Store Price"
            stock_badge = f"🟢 <b>IN STOCK: {qty} UNITS AVAILABLE!</b>"
            action_tip = (
                "⚡ <i>Auto-buy is active and primed to purchase!</i>"
                if is_active
                else "🔔 <i>Stock is available now! Use the link below to purchase manually.</i>"
            )
        elif res:
            qty = res.get("inventory_quantity", 0)
            price_val = res.get("price")
            price_str = f"₹{price_val:,}" if price_val else "Store Price"
            stock_badge = f"🔴 <b>OUT OF STOCK ({qty} units available)</b>"
            action_tip = (
                "🛡️ <b>24/7 Monitoring Active:</b>\n"
                "Our automated poller is checking your regional warehouse continuously every 60s. "
                "The instant Amul replenishes stock, you will be notified / checked out immediately!"
            )
        else:
            price_str = "Store Price"
            stock_badge = "🔴 <b>OUT OF STOCK (0 units available)</b>"
            action_tip = (
                "🛡️ <b>24/7 Monitoring Active:</b>\n"
                "Our automated poller is checking your regional warehouse continuously every 60s."
            )

        msg = (
            f"📦 <b>Live Warehouse Stock Report</b>\n\n"
            f"🎯 <b>Target Product:</b> <b>{target_name}</b>\n"
            f"🏷️ <b>SKU:</b> <code>{sku}</code> | 💰 <b>Price:</b> {price_str}\n"
            f"📍 <b>Warehouse:</b> <b>{warehouse_name}</b> (Pincode: <code>{pincode}</code>)\n"
            f"🎯 <b>Your Mode:</b> {mode_str}\n\n"
            f"📊 <b>CURRENT INVENTORY:</b>\n"
            f"{stock_badge}\n\n"
            f"{action_tip}\n\n"
            f"🕒 <i>Checked live at {now_str} IST</i>\n"
            f"🔗 <a href='{prod_url}'>View Product on Amul Store</a>"
        )

        keyboard = self._get_stock_report_keyboard(is_active)

        if is_edit:
            try:
                await target_message.edit_text(msg, parse_mode="HTML", disable_web_page_preview=True, reply_markup=keyboard)
            except Exception as e:
                logger.debug(f"Stock report edit note: {e}")
        else:
            await target_message.reply_text(msg, parse_mode="HTML", disable_web_page_preview=True, reply_markup=keyboard)

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
        user = database.get_user(chat_id)
        if not user or user.onboarding_state not in ("READY", "CHOOSE_MODE"):
            await update.message.reply_text("⚠️ Setup not complete. Send /start to begin onboarding.")
            return

        cookies = database.get_user_cookies(chat_id)
        if not cookies:
            database.set_user_onboarding_state(chat_id, "AWAITING_PHONE")
            await update.message.reply_text(
                "⚡ <b>Enable Auto-Buy: Amul Account Required</b>\n\n"
                "To automatically place orders and generate UPI QR codes, your Amul account needs to be linked.\n\n"
                "Please reply with your <b>10-digit registered Amul Mobile Number</b> (e.g. <code>9876543210</code>) to verify via SMS OTP:",
                parse_mode="HTML"
            )
            return

        database.resume_user_autobuy(chat_id)
        database.mark_user_stock_notified(chat_id, False)
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

        database.set_user_onboarding_state(chat_id, "AWAITING_PINCODE_UPDATE")
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

    async def cmd_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        user = database.get_user(chat_id)
        if not user:
            await update.message.reply_text("ℹ️ You don't have an active account registered with the bot.")
            return

        msg = (
            "🛑 <b>Stop Monitoring & Delete Account?</b>\n\n"
            "This will completely:\n"
            "• Remove you from 24/7 warehouse stock monitoring\n"
            "• Cancel all automatic purchases & alerts\n"
            "• Permanently delete your encrypted session tokens\n\n"
            "<i>Are you sure you want to stop and delete your account?</i>"
        )
        await update.message.reply_text(
            msg,
            parse_mode="HTML",
            reply_markup=self._get_delete_confirm_keyboard()
        )

    async def cmd_users(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        if not self.admin_chat_id or str(chat_id) != str(self.admin_chat_id):
            await update.message.reply_text("⛔ <b>Access Denied:</b> Admin command only.", parse_mode="HTML")
            return

        with database.get_db() as db:
            users = db.query(database.User).order_by(database.User.created_at.desc()).all()
            deleted_logs = database.get_deleted_user_logs(limit=10)

            if not users and not deleted_logs:
                await update.message.reply_text("ℹ️ No users registered yet.", parse_mode="HTML")
                return

            total = len(users)
            autobuy_count = 0
            notify_count = 0
            paused_count = 0
            for u in users:
                is_act, _, reason = u.is_auto_buy_active()
                if is_act:
                    autobuy_count += 1
                elif "notify" in (reason or "").lower():
                    notify_count += 1
                else:
                    paused_count += 1

            lines = [
                f"👑 <b>Admin Dashboard: User Directory</b>",
                f"👥 <b>Total Users:</b> {total} | 🟢 <b>Auto-Buy:</b> {autobuy_count} | 🔔 <b>Notify-Only:</b> {notify_count} | ⏸️ <b>Paused:</b> {paused_count}\n"
            ]

            for i, u in enumerate(users, 1):
                is_act, _, reason = u.is_auto_buy_active()
                if is_act:
                    stat_emoji = "🟢"
                    mode_label = "Auto-Buy"
                elif "notify" in (reason or "").lower():
                    stat_emoji = "🔔"
                    mode_label = "Notify-Only"
                else:
                    stat_emoji = "⏸️"
                    mode_label = f"Paused ({reason})"
                user_tag = f"@{u.telegram_username}" if u.telegram_username else f"User {u.telegram_chat_id}"
                phone_str = f"+91 {u.phone_number}" if u.phone_number else "No Phone"
                pincode_str = u.pincode or "N/A"
                warehouse_str = u.substore_name or "N/A"

                lines.append(
                    f"{i}. {stat_emoji} <b>{user_tag}</b> (<code>{u.telegram_chat_id}</code>)\n"
                    f"   📱 {phone_str} | 📍 <code>{pincode_str}</code>\n"
                    f"   🏬 {warehouse_str} | Mode: <b>{mode_label}</b> | State: <i>{u.onboarding_state}</i>\n"
                )

            if deleted_logs:
                lines.append(f"🗑️ <b>Deleted Users Audit Log ({len(deleted_logs)}):</b>")
                for d in deleted_logs:
                    del_time = d.deleted_at.strftime("%d %b %H:%M") if d.deleted_at else ""
                    tag = f"@{d.telegram_username}" if d.telegram_username else f"<code>{d.telegram_chat_id}</code>"
                    phone = f"+91 {d.phone_number}" if d.phone_number else "N/A"
                    lines.append(f"• 🚫 {tag} ({phone}) | 📍 {d.pincode or 'N/A'} | <i>{del_time}</i>")

            full_msg = "\n".join(lines)
            if len(full_msg) > 4000:
                chunks = [full_msg[i:i+4000] for i in range(0, len(full_msg), 4000)]
                for c in chunks:
                    await update.message.reply_text(c, parse_mode="HTML")
            else:
                await update.message.reply_text(full_msg, parse_mode="HTML")

    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        text = update.message.text.strip()
        user = database.get_user(chat_id)

        if not user:
            user = database.get_or_create_user(chat_id, update.effective_user.username)

        # State 1: Awaiting Pincode (New User Onboarding)
        if user.onboarding_state == "AWAITING_PINCODE":
            if text.isdigit() and len(text) == 6:
                await update.message.reply_text("🔍 <i>Verifying pincode with Amul network...</i>", parse_mode="HTML")
                res = resolver.resolve(text)
                if res:
                    database.update_user_pincode(chat_id, text, res["substore_id"], res["warehouse_name"])
                    database.set_user_onboarding_state(chat_id, "CHOOSE_MODE")
                    msg = (
                        f"✅ <b>Pincode {text} Verified!</b>\n"
                        f"📍 <b>Regional Warehouse:</b> {res['warehouse_name']}\n\n"
                        f"👉 <b>Step 2 of 2: Choose Monitoring Mode</b>\n\n"
                        f"• ⚡ <b>Auto-Buy:</b> Automatically checks out & delivers your UPI payment QR code as soon as stock drops. Requires a 1-time Amul SMS OTP login.\n\n"
                        f"• 🔔 <b>Stock Alerts Only:</b> Instant restock notifications with direct 1-tap links. Zero login or Amul credentials required!\n\n"
                        f"Please choose your preferred mode below:"
                    )
                    await update.message.reply_text(
                        msg,
                        parse_mode="HTML",
                        reply_markup=self._get_mode_selection_keyboard()
                    )
                else:
                    await update.message.reply_text(
                        f"❌ Pincode <code>{text}</code> is not currently serviceable by Amul.\n"
                        f"Please try a different delivery pincode:",
                        parse_mode="HTML"
                    )
            else:
                await update.message.reply_text("⚠️ Please send a valid 6-digit numerical pincode (e.g. <code>201310</code>).", parse_mode="HTML")
            return

        # State 1b: Awaiting Pincode Update for existing user
        if user.onboarding_state == "AWAITING_PINCODE_UPDATE":
            if text.isdigit() and len(text) == 6:
                await update.message.reply_text("🔍 <i>Verifying pincode with Amul network...</i>", parse_mode="HTML")
                res = resolver.resolve(text)
                if res:
                    database.update_user_pincode(chat_id, text, res["substore_id"], res["warehouse_name"])
                    database.set_user_onboarding_state(chat_id, "READY")
                    is_active, _, _ = user.is_auto_buy_active()
                    msg = (
                        f"✅ <b>Pincode updated to {text}!</b>\n"
                        f"📍 <b>Regional Warehouse:</b> {res['warehouse_name']} (Substore: <code>{res['substore_id']}</code>)\n\n"
                        f"Your monitoring target warehouse has been updated successfully."
                    )
                    await update.message.reply_text(
                        msg,
                        parse_mode="HTML",
                        reply_markup=self._get_user_keyboard(is_active)
                    )
                else:
                    await update.message.reply_text(
                        f"❌ Pincode <code>{text}</code> is not currently serviceable by Amul.\n"
                        f"Please try a different delivery pincode:",
                        parse_mode="HTML"
                    )
            else:
                await update.message.reply_text("⚠️ Please send a valid 6-digit numerical pincode (e.g. <code>201310</code>).", parse_mode="HTML")
            return

        # State 1c: Choosing Monitoring Mode (Text Fallback)
        if user.onboarding_state == "CHOOSE_MODE":
            lower_text = text.lower()
            if any(k in lower_text for k in ["1", "auto", "buy", "checkout"]):
                cookies = database.get_user_cookies(chat_id)
                if cookies:
                    database.resume_user_autobuy(chat_id)
                    database.set_user_onboarding_state(chat_id, "READY")
                    target_name = user.target_name or "Amul Chocolate Whey Protein (60 sachets)"
                    msg = (
                        f"⚡ <b>Auto-Buy Mode Activated!</b>\n\n"
                        f"📍 <b>Delivery Pincode:</b> <code>{user.pincode}</code> ({user.substore_name})\n"
                        f"📱 <b>Amul Account:</b> +91 {user.phone_number}\n"
                        f"📦 <b>Target Product:</b> {target_name}\n"
                        f"🛒 <b>Auto-Purchase:</b> 🟢 <b>ACTIVE</b>\n\n"
                        f"The bot is monitoring 24/7. When stock drops, you will receive your dynamic UPI QR instantly!"
                    )
                    await update.message.reply_text(msg, parse_mode="HTML", reply_markup=self._get_user_keyboard(True))
                else:
                    database.set_user_onboarding_state(chat_id, "AWAITING_PHONE")
                    msg = (
                        f"⚡ <b>Auto-Buy Mode Selected!</b>\n\n"
                        f"📍 Delivery to: <code>{user.pincode}</code> ({user.substore_name})\n\n"
                        f"👉 <b>Amul Account Login</b>\n"
                        f"Please reply with your <b>10-digit registered Amul Mobile Number</b> (e.g. <code>9876543210</code>):\n\n"
                        f"<i>(Make sure your delivery address is saved in this Amul account)</i>\n"
                        f"Amul will send you an official SMS OTP to verify."
                    )
                    await update.message.reply_text(msg, parse_mode="HTML")
                return
            elif any(k in lower_text for k in ["2", "notify", "alert"]):
                database.pause_user_autobuy(chat_id, days=None, reason="Notify-Only Mode")
                database.mark_user_stock_notified(chat_id, False)
                database.set_user_onboarding_state(chat_id, "READY")
                target_name = user.target_name or "Amul Chocolate Whey Protein (60 sachets)"
                msg = (
                    f"🎉 <b>Setup Complete — Stock Alerts Only!</b>\n\n"
                    f"📍 <b>Delivery Pincode:</b> <code>{user.pincode}</code> ({user.substore_name})\n"
                    f"📦 <b>Target Product:</b> {target_name}\n"
                    f"🔔 <b>Monitoring Mode:</b> 🔔 <b>STOCK ALERTS ONLY</b>\n\n"
                    f"The bot is now monitoring your regional warehouse 24/7.\n"
                    f"As soon as stock arrives, you will receive an instant alert with a direct 1-tap checkout link!\n\n"
                    f"💡 <i>No login or phone number required. You can upgrade to ⚡ Auto-Buy anytime via the menu below.</i>"
                )
                await update.message.reply_text(msg, parse_mode="HTML", reply_markup=self._get_user_keyboard(False))
                return
            else:
                await update.message.reply_text(
                    "👉 Please choose your monitoring mode using the buttons below, or reply:\n"
                    "• Type <b>1</b> for ⚡ <b>Auto-Buy</b>\n"
                    "• Type <b>2</b> for 🔔 <b>Stock Alerts Only</b>",
                    parse_mode="HTML",
                    reply_markup=self._get_mode_selection_keyboard()
                )
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
                    database.resume_user_autobuy(chat_id)
                    database.mark_user_stock_notified(chat_id, False)
                    u = database.get_user(chat_id)
                    target_name = u.target_name or "Amul Chocolate Whey Protein (60 sachets)"
                    congrats_msg = (
                        f"🎉 <b>Login Successful & Account Linked!</b>\n\n"
                        f"📍 <b>Delivery Pincode:</b> <code>{u.pincode}</code> ({u.substore_name})\n"
                        f"📱 <b>Amul Account:</b> +91 {u.phone_number}\n"
                        f"📦 <b>Target Product:</b> {target_name}\n"
                        f"🛒 <b>Auto-Purchase:</b> 🟢 <b>ACTIVE</b>\n\n"
                        f"🔒 <i>Your session has been securely encrypted with AES-256.</i>\n\n"
                        f"The bot is now monitoring your warehouse 24/7. When stock arrives, "
                        f"you will receive your dynamic UPI QR code to complete payment in seconds.\n\n"
                        f"Send <code>/product</code> to change your target product anytime."
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

        # State 4: Awaiting Custom Product URL or direct Amul product link pasted
        is_amul_link = ("/product/" in text) or ("shop.amul.com" in text and "product" in text)
        if user.onboarding_state == "AWAITING_PRODUCT_URL" or is_amul_link:
            alias = extract_alias_from_url(text)
            if alias:
                status_msg = await update.message.reply_text("🔍 <i>Verifying product details with Amul network...</i>", parse_mode="HTML")
                res = resolve_amul_product(text, substore_id=user.substore_id)
                if res:
                    database.update_user_product(
                        chat_id=chat_id,
                        target_alias=res["alias"],
                        target_sku=res["sku"],
                        target_name=res["name"]
                    )
                    database.set_user_onboarding_state(chat_id, "READY")
                    
                    price_str = f"₹{res['price']:,}" if res.get("price") else "Store Price"
                    stock_str = (
                        f"🟢 <b>In Stock</b> ({res['inventory_quantity']} units)"
                        if res.get("in_stock")
                        else f"🔴 <b>Out of Stock</b> ({res.get('inventory_quantity', 0)} units in your warehouse)"
                    )
                    
                    is_active, _, _ = user.is_auto_buy_active()
                    msg = (
                        f"✅ <b>Target Product Successfully Updated!</b>\n\n"
                        f"📦 <b>Product:</b> <b>{res['name']}</b>\n"
                        f"🏷️ <b>SKU:</b> <code>{res['sku']}</code>\n"
                        f"💰 <b>Price:</b> {price_str}\n"
                        f"📍 <b>Warehouse Stock:</b> {stock_str}\n"
                        f"🔗 <a href='{res['url']}'>View Product on Amul</a>\n\n"
                        f"The bot is now monitoring this item 24/7 for delivery to pincode <code>{user.pincode}</code>. "
                        f"When stock arrives, auto-buy will trigger immediately!"
                    )
                    await status_msg.edit_text(
                        msg,
                        parse_mode="HTML",
                        disable_web_page_preview=True
                    )
                    await update.message.reply_text(
                        "👇 <b>Your Dashboard:</b>",
                        reply_markup=self._get_user_keyboard(is_active)
                    )
                    return
                else:
                    await status_msg.edit_text(
                        f"❌ Could not resolve product for:\n<code>{text}</code>\n\n"
                        f"Please ensure it is an active product page on shop.amul.com (e.g. <code>https://shop.amul.com/en/product/...</code>).\n"
                        f"Or use <code>/product</code> to select from popular protein items.",
                        parse_mode="HTML"
                    )
                    return
            elif user.onboarding_state == "AWAITING_PRODUCT_URL":
                await update.message.reply_text(
                    "⚠️ Please provide a valid Amul product URL (e.g. <code>https://shop.amul.com/en/product/...</code>) "
                    "or type /product to choose from popular presets.",
                    parse_mode="HTML"
                )
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

        if data == "onboard_mode_autobuy":
            cookies = database.get_user_cookies(chat_id)
            if cookies:
                database.resume_user_autobuy(chat_id)
                database.set_user_onboarding_state(chat_id, "READY")
                target_name = user.target_name or "Amul Chocolate Whey Protein (60 sachets)"
                await query.edit_message_text(
                    f"⚡ <b>Auto-Buy Mode Activated!</b>\n\n"
                    f"📍 <b>Delivery Pincode:</b> <code>{user.pincode}</code> ({user.substore_name})\n"
                    f"📱 <b>Amul Account:</b> +91 {user.phone_number}\n"
                    f"📦 <b>Target Product:</b> {target_name}\n"
                    f"🛒 <b>Auto-Purchase:</b> 🟢 <b>ACTIVE</b>\n\n"
                    f"The bot is monitoring 24/7. When stock drops, you will receive your dynamic UPI QR code instantly!",
                    parse_mode="HTML",
                    reply_markup=self._get_user_keyboard(True)
                )
            else:
                database.set_user_onboarding_state(chat_id, "AWAITING_PHONE")
                await query.edit_message_text(
                    f"⚡ <b>Auto-Buy Mode Selected!</b>\n\n"
                    f"📍 Delivery to: <code>{user.pincode}</code> ({user.substore_name})\n\n"
                    f"👉 <b>Step 2 of 2: Login to Amul</b>\n"
                    f"Please reply with your <b>10-digit registered Amul Mobile Number</b> (e.g. <code>9876543210</code>):\n\n"
                    f"<i>(Make sure your delivery address is saved in this Amul account)</i>\n"
                    f"Amul will send you an official SMS OTP to verify.",
                    parse_mode="HTML"
                )
        elif data == "onboard_mode_notify":
            database.pause_user_autobuy(chat_id, days=None, reason="Notify-Only Mode")
            database.mark_user_stock_notified(chat_id, False)
            database.set_user_onboarding_state(chat_id, "READY")
            target_name = user.target_name or "Amul Chocolate Whey Protein (60 sachets)"
            await query.edit_message_text(
                f"🎉 <b>Setup Complete — Stock Alerts Only!</b>\n\n"
                f"📍 <b>Delivery Pincode:</b> <code>{user.pincode}</code> ({user.substore_name})\n"
                f"📦 <b>Target Product:</b> {target_name}\n"
                f"🔔 <b>Monitoring Mode:</b> 🔔 <b>STOCK ALERTS ONLY</b>\n\n"
                f"The bot is now monitoring your regional warehouse 24/7.\n"
                f"As soon as stock arrives, you will receive an instant alert with a direct 1-tap checkout link!\n\n"
                f"💡 <i>No login or phone number required. You can upgrade to ⚡ Auto-Buy anytime via the menu below.</i>",
                parse_mode="HTML",
                reply_markup=self._get_user_keyboard(False)
            )
        elif data == "switch_notify_only":
            database.pause_user_autobuy(chat_id, days=None, reason="Notify-Only Mode")
            database.mark_user_stock_notified(chat_id, False)
            await query.edit_message_text(
                "🔔 <b>Switched to Stock Alerts Only!</b>\n\n"
                "The bot will monitor 24/7 and alert you when stock arrives, without auto-checking out.\n\n"
                "You can re-enable Auto-Buy anytime with the button below.",
                parse_mode="HTML",
                reply_markup=self._get_user_keyboard(False)
            )
        elif data.startswith("pause_"):
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
        elif data in ("resume", "buy_now"):
            cookies = database.get_user_cookies(chat_id)
            if not cookies:
                database.set_user_onboarding_state(chat_id, "AWAITING_PHONE")
                await query.edit_message_text(
                    "⚡ <b>Enable Auto-Buy: Amul Account Required</b>\n\n"
                    "To automatically place orders and generate UPI QR codes, your Amul account needs to be linked.\n\n"
                    "Please reply with your <b>10-digit registered Amul Mobile Number</b> (e.g. <code>9876543210</code>) to verify via SMS OTP:",
                    parse_mode="HTML"
                )
                return
            database.resume_user_autobuy(chat_id)
            database.mark_user_stock_notified(chat_id, False)
            await query.edit_message_text(
                "▶️ <b>Auto-Purchase Resumed!</b>\n\n"
                "The bot will automatically purchase next time stock is detected.",
                parse_mode="HTML",
                reply_markup=self._get_user_keyboard(True)
            )
        elif data == "keep_paused":
            await query.edit_message_text(
                "⏸️ <b>Monitoring Kept in Stock Alerts Only Mode.</b>\n\n"
                "No order was placed. You will continue receiving stock alerts.",
                parse_mode="HTML",
                reply_markup=self._get_user_keyboard(False)
            )
        elif data == "change_pincode":
            database.set_user_onboarding_state(chat_id, "AWAITING_PINCODE_UPDATE")
            await query.message.reply_text("📍 Please reply with your new 6-digit delivery pincode:")
        elif data == "cancel_checkout":
            if chat_id in self.active_checkouts:
                self.active_checkouts[chat_id].set()
                await query.edit_message_text("❌ <b>Purchase Cancelled!</b> Session aborted.")
            else:
                await query.edit_message_text("ℹ️ No active checkout session.")
        elif data == "relogin":
            database.set_user_onboarding_state(chat_id, "AWAITING_PHONE")
            await query.message.reply_text(
                "📱 <b>Amul Account Re-Login</b>\n\n"
                "Please reply with your <b>10-digit registered Amul Mobile Number</b> (e.g. <code>9876543210</code>):\n\n"
                "Amul will send you an official SMS OTP to verify.",
                parse_mode="HTML"
            )
        elif data == "change_product":
            target_name = user.target_name or "Amul Chocolate Whey Protein (60 sachets)"
            msg = (
                f"📦 <b>Target Product Selection</b>\n\n"
                f"🎯 <b>Currently Monitored:</b>\n"
                f"• <b>{target_name}</b>\n"
                f"• SKU: <code>{user.target_sku}</code>\n"
                f"• Link: <a href='https://shop.amul.com/en/product/{user.target_alias}'>View on Amul Store</a>\n\n"
                f"👇 Choose a popular product below, or tap <b>🔗 Paste Custom Amul Link</b> to monitor any item from shop.amul.com:"
            )
            await query.edit_message_text(
                msg,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=get_product_selection_keyboard(user.target_alias)
            )
        elif data.startswith("sel_prod_"):
            prod_id = data.replace("sel_prod_", "")
            prod = get_product_by_id(prod_id)
            if prod:
                database.update_user_product(
                    chat_id=chat_id,
                    target_alias=prod["alias"],
                    target_sku=prod["sku"],
                    target_name=prod["name"]
                )
                database.set_user_onboarding_state(chat_id, "READY")
                is_active, _, _ = user.is_auto_buy_active()
                msg = (
                    f"✅ <b>Target Product Switched!</b>\n\n"
                    f"📦 <b>Active Target:</b> <b>{prod['name']}</b>\n"
                    f"🏷️ <b>SKU:</b> <code>{prod['sku']}</code> | 💰 <b>Price:</b> ₹{prod['price']:,}\n"
                    f"📝 <i>{prod.get('short_desc', '')}</i>\n\n"
                    f"The bot is now monitoring this product for your delivery warehouse. Auto-buy will trigger on the next restock!"
                )
                await query.edit_message_text(
                    msg,
                    parse_mode="HTML",
                    reply_markup=self._get_user_keyboard(is_active)
                )
        elif data == "paste_product_link":
            database.set_user_onboarding_state(chat_id, "AWAITING_PRODUCT_URL")
            msg = (
                f"🔗 <b>Paste Custom Amul Product URL</b>\n\n"
                f"Please reply with the web link of any product on <a href='https://shop.amul.com'>shop.amul.com</a>.\n\n"
                f"<b>Example:</b>\n"
                f"<code>https://shop.amul.com/en/product/amul-whey-protein-32-g-or-pack-of-60-sachets</code>\n\n"
                f"<i>The bot will inspect the product and switch your tracking target instantly.</i>"
            )
            await query.message.reply_text(msg, parse_mode="HTML", disable_web_page_preview=True)
        elif data == "status":
            is_active, _, reason = user.is_auto_buy_active()
            mode_str = "🟢 <b>ACTIVE</b>" if is_active else f"⏸️ <b>PAUSED</b> ({reason})"
            phone_str = f"+91 {user.phone_number}" if user.phone_number else "Linked"
            target_str = user.target_name or "Amul Chocolate Whey Protein (60 sachets)"

            saved_addr = database.get_user_delivery_address(chat_id)
            addr_line = None
            if saved_addr:
                parts = [saved_addr.get("address1"), saved_addr.get("city")]
                addr_line = ", ".join(filter(None, parts))
            addr_str = f"• Address: {addr_line}\n" if addr_line else ""

            await query.edit_message_text(
                f"📊 <b>Status:</b>\n"
                f"• Auto-Buy: {mode_str}\n"
                f"• Target Product: <b>{target_str}</b>\n"
                f"• SKU: <code>{user.target_sku}</code>\n"
                f"• Pincode: <code>{user.pincode}</code> ({user.substore_name})\n"
                f"• Account: {phone_str}\n"
                f"{addr_str}".rstrip(),
                parse_mode="HTML",
                reply_markup=self._get_user_keyboard(is_active)
            )
        elif data == "check_stock":
            if not user.substore_id:
                await query.answer("Delivery warehouse not configured. Send /pincode first.", show_alert=True)
                return
            await query.answer("🔍 Checking live warehouse inventory...")
            await self._render_stock_report(user, query.message, is_edit=True)
        elif data == "confirm_delete":
            msg = (
                "🛑 <b>Stop Monitoring & Delete Account?</b>\n\n"
                "This will completely:\n"
                "• Remove you from 24/7 warehouse stock monitoring\n"
                "• Cancel all automatic purchases & alerts\n"
                "• Permanently delete your encrypted session tokens\n\n"
                "<i>Are you sure you want to stop and delete your account?</i>"
            )
            await query.edit_message_text(
                msg,
                parse_mode="HTML",
                reply_markup=self._get_delete_confirm_keyboard()
            )
        elif data == "cancel_delete":
            is_active, _, _ = user.is_auto_buy_active()
            await query.edit_message_text(
                "✅ <b>Action Cancelled!</b>\n\nYour monitoring and auto-buy settings remain active.",
                parse_mode="HTML",
                reply_markup=self._get_user_keyboard(is_active)
            )
        elif data == "do_delete":
            if chat_id in self.active_checkouts:
                self.active_checkouts[chat_id].set()
            await auth_manager.cancel_session(chat_id)

            log_entry = database.delete_and_log_user(chat_id, reason="User self-deleted via Telegram")

            await query.edit_message_text(
                "👋 <b>Your account has been deleted.</b>\n\n"
                "All monitoring and auto-checkout processes have been stopped, and your session tokens "
                "have been completely removed from our system.\n\n"
                "Thank you for using Amul Auto-Buy! If you ever want to return, simply send /start.",
                parse_mode="HTML"
            )

            if self.admin_chat_id and str(chat_id) != str(self.admin_chat_id) and log_entry:
                try:
                    admin_notifier = TelegramNotifier(token=self.token, chat_id=self.admin_chat_id)
                    u_tag = f"@{log_entry.telegram_username}" if log_entry.telegram_username else f"User {chat_id}"
                    phone = f"+91 {log_entry.phone_number}" if log_entry.phone_number else "N/A"
                    admin_msg = (
                        f"🗑️ <b>Account Deleted Notice:</b>\n"
                        f"• User: {u_tag} (<code>{chat_id}</code>)\n"
                        f"• Phone: {phone} | Pincode: <code>{log_entry.pincode or 'N/A'}</code>\n"
                        f"• Warehouse: {log_entry.substore_name or 'N/A'}"
                    )
                    await admin_notifier.send_message_async(admin_msg)
                except Exception as e:
                    logger.warning(f"Could not send admin deletion alert: {e}")

    async def dispatch_user_checkout(self, user: User, product_name: str, stock_qty: int, details: Optional[dict] = None):
        """
        Executes isolated checkout for a single user under concurrency control.
        Retries up to 2 times if auto-buy fails.
        If both attempts fail, sends a clear notification advising manual purchase
        with direct product link and logs full audit error details.
        """
        chat_id = user.telegram_chat_id
        cancel_event = asyncio.Event()
        self.active_checkouts[chat_id] = cancel_event

        notifier = TelegramNotifier(token=self.token, chat_id=str(chat_id))

        async with self.checkout_semaphore:
            logger.info(f"Launching checkout execution for user {chat_id} (Pincode: {user.pincode})...")
            
            target_prod_name = user.target_name or product_name
            prod_url = f"https://shop.amul.com/en/product/{user.target_alias}"

            # Decrypt user's cookies to temporary in-memory structure
            cookies_list = database.get_user_cookies(chat_id)
            if not cookies_list:
                err_reason = "Amul account not linked or session cookies expired"
                logger.error(
                    f"❌ [AUTO-BUY FAILED - NO COOKIES]\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"• User Chat ID : {chat_id}\n"
                    f"• Username     : @{user.telegram_username or 'N/A'}\n"
                    f"• Phone Number : +91 {user.phone_number or 'Not Linked'}\n"
                    f"• Pincode      : {user.pincode} ({user.substore_name or 'N/A'})\n"
                    f"• Target Item  : {target_prod_name} (SKU: {user.target_sku})\n"
                    f"• Problem/Error: {err_reason}\n"
                    f"• Action Taken : Dispatched manual purchase alert with direct store link\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                )
                await notifier.notify_autobuy_failed_manual(
                    product_name=target_prod_name,
                    product_url=prod_url,
                    reason=f"{err_reason}. Please use /relogin to connect your account."
                )
                self.active_checkouts.pop(chat_id, None)
                return

            # Temporary cookies file in scratch/
            scratch_dir = "scratch"
            os.makedirs(scratch_dir, exist_ok=True)
            user_cookies_path = os.path.join(scratch_dir, f"cookies_{chat_id}.json")
            with open(user_cookies_path, "w", encoding="utf-8") as f:
                json.dump(cookies_list, f)

            MAX_TRIES = 2
            last_error = "Unknown error during auto-checkout"

            try:
                price_val = details.get("price") if details else None
                amount_str = f"{price_val:,}" if price_val else None

                # Dispatch single initial stock alert
                await notifier.notify_stock_alert(target_prod_name, stock_qty)

                for attempt in range(1, MAX_TRIES + 1):
                    if cancel_event.is_set():
                        logger.info(f"Checkout for user {chat_id} was cancelled by user via Telegram command.")
                        return

                    logger.info(
                        f"🛒 [AUTO-BUY ATTEMPT {attempt}/{MAX_TRIES}] Initiating checkout for user {chat_id} "
                        f"(@{user.telegram_username or 'N/A'}, Target: '{target_prod_name}')..."
                    )

                    try:
                        saved_address = database.get_user_delivery_address(chat_id)

                        def on_address_found(extracted_addr):
                            database.update_user_delivery_address(chat_id, extracted_addr)

                        checkout = AmulAutoCheckout(
                            cookies_file=user_cookies_path,
                            target_url=prod_url,
                            substore_id=user.substore_id,
                            substore_name=user.substore_name,
                            pincode=user.pincode,
                            phone_number=user.phone_number,
                            product_name=target_prod_name,
                            product_amount=amount_str,
                            headless=True,
                            quantity=user.quantity,
                            gateway=self.payment_gateway,
                            notifier=notifier,
                            payment_timeout_secs=self.payment_timeout_secs,
                            cancel_event=cancel_event,
                            initial_delivery_address=saved_address,
                            on_address_extracted=on_address_found
                        )

                        success, order_id, msg = await checkout.execute_checkout()
                        last_error = msg or last_error

                        if success:
                            if checkout.delivery_address:
                                database.update_user_delivery_address(chat_id, checkout.delivery_address)

                            # Snooze user auto-buy for configured months post-purchase
                            pause_days = float(self.post_purchase_pause_months * 30)
                            paused_until, snooze_reason = database.pause_user_autobuy(
                                chat_id,
                                days=pause_days,
                                reason=f"Auto-snoozed for {self.post_purchase_pause_months} months post-purchase"
                            )
                            expiry_str = paused_until.strftime("%d %b %Y, %H:%M UTC")

                            addr_info = checkout.delivery_address or saved_address
                            dest_str = ""
                            if addr_info and addr_info.get("address1"):
                                dest_str = f"\nDelivery: {addr_info['address1']}, {addr_info.get('city', '')} ({user.pincode})"
                            elif user.pincode:
                                dest_str = f"\nDelivery: Pincode {user.pincode} ({user.substore_name or 'Regional Warehouse'})"

                            await notifier.notify_order_placed(
                                order_id=order_id,
                                details=f"Product: {target_prod_name}\nQuantity: {user.quantity}\nPayment: Verified via UPI{dest_str}",
                                paused_until_str=expiry_str
                            )
                            logger.info(f"🎉 Auto-buy succeeded on attempt {attempt} for user {chat_id}! Order ID: {order_id}")
                            return
                        else:
                            if cancel_event.is_set():
                                logger.info(f"Checkout for user {chat_id} cancelled by user on attempt {attempt}.")
                                return

                            logger.warning(
                                f"⚠️ [AUTO-BUY ATTEMPT {attempt}/{MAX_TRIES} FAILED] User {chat_id} "
                                f"(@{user.telegram_username or 'N/A'}). Problem: {last_error}"
                            )

                            is_session_expired = (
                                "session expired" in (last_error or "").lower()
                                or "login required" in (last_error or "").lower()
                            )
                            if is_session_expired:
                                logger.warning(
                                    f"Amul session expired for user {chat_id}. Aborting attempt {attempt}/{MAX_TRIES} without further retry."
                                )
                                break

                            if attempt < MAX_TRIES:
                                logger.info(f"Retrying auto-buy in 2s (Attempt {attempt + 1}/{MAX_TRIES})...")
                                await asyncio.sleep(2)

                    except Exception as attempt_exc:
                        last_error = str(attempt_exc)
                        logger.error(
                            f"⚠️ Exception during auto-buy attempt {attempt}/{MAX_TRIES} for user {chat_id}: {attempt_exc}",
                            exc_info=True
                        )
                        if attempt < MAX_TRIES and not cancel_event.is_set():
                            await asyncio.sleep(2)

                # Attempts failed without user cancellation
                if not cancel_event.is_set():
                    is_session_expired = (
                        "session expired" in (last_error or "").lower()
                        or "login required" in (last_error or "").lower()
                    )

                    logger.error(
                        f"❌ [AUTO-BUY FAILED]\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"• User Chat ID : {chat_id}\n"
                        f"• Username     : @{user.telegram_username or 'N/A'}\n"
                        f"• Phone Number : +91 {user.phone_number or 'Not Linked'}\n"
                        f"• Pincode      : {user.pincode} ({user.substore_name or 'N/A'}, ID: {user.substore_id})\n"
                        f"• Target Item  : {target_prod_name} (SKU: {user.target_sku})\n"
                        f"• Problem/Error: {last_error}\n"
                        f"• Action Taken : Dispatched failure alert with manual purchase link\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                    )

                    # If session expired, pause auto-buy so user is prompted to relogin
                    if is_session_expired:
                        database.pause_user_autobuy(
                            chat_id,
                            days=None,
                            reason="Amul session expired (Login required)"
                        )

                    await notifier.notify_autobuy_failed_manual(
                        product_name=target_prod_name,
                        product_url=prod_url,
                        reason=last_error
                    )

                    if self.admin_chat_id and str(chat_id) != str(self.admin_chat_id):
                        try:
                            admin_notifier = TelegramNotifier(token=self.token, chat_id=self.admin_chat_id)
                            admin_msg = (
                                f"⚠️ <b>Auto-Buy Failure Alert:</b>\n"
                                f"• User: @{user.telegram_username or 'N/A'} (<code>{chat_id}</code>)\n"
                                f"• Pincode: <code>{user.pincode}</code> | Phone: +91 {user.phone_number or 'N/A'}\n"
                                f"• Warehouse: {user.substore_name or 'N/A'}\n"
                                f"• Product: <b>{target_prod_name}</b>\n"
                                f"• <b>Problem:</b> <i>{last_error}</i>"
                            )
                            await admin_notifier.send_message_async(admin_msg)
                        except Exception as admin_err:
                            logger.warning(f"Could not dispatch admin auto-buy failure alert: {admin_err}")

            except Exception as e:
                logger.error(f"Error during user {chat_id} checkout lifecycle: {e}", exc_info=True)
            finally:
                if os.path.exists(user_cookies_path):
                    os.remove(user_cookies_path)
                self.active_checkouts.pop(chat_id, None)

    async def handle_warehouse_stock(self, substore_id: str, stock_qty: int, details: dict, users: List[User]):
        """
        Triggered when stock appears in a warehouse.
        Dispatches auto-checkout for active users and notify-only alerts for paused users.
        Strictly limits to 1 notification / checkout dispatch per user per restock event.
        """
        prod_name = details.get("name") or "Amul Product"

        for user in users:
            chat_id = user.telegram_chat_id
            user_target_name = user.target_name or prod_name

            # Rate-limiting: Ensure only 1 notification/dispatch per user for current stock
            if not database.should_notify_user_stock(chat_id):
                continue

            # Mark user notified immediately
            database.mark_user_stock_notified(chat_id, True)

            is_active, _, reason = user.is_auto_buy_active()
            if is_active:
                if chat_id in self.active_checkouts:
                    logger.info(f"User {chat_id} already has checkout in progress. Skipping duplicate dispatch.")
                    continue
                logger.info(f"Triggering auto-checkout for active user {chat_id} (Target: '{user_target_name}', Stock: {stock_qty})...")
                asyncio.create_task(self.dispatch_user_checkout(user, user_target_name, stock_qty, details))
            else:
                # Notify-Only Mode: Send exactly 1 notification
                logger.info(f"Sending single stock alert to paused user {chat_id} (Reason: {reason})")
                notifier = TelegramNotifier(token=self.token, chat_id=str(chat_id))
                asyncio.create_task(notifier.notify_stock_alert_paused(
                    product_name=user_target_name,
                    stock_qty=stock_qty,
                    paused_reason=reason
                ))

    async def handle_warehouse_out_of_stock(self, substore_id: str):
        """
        Triggered when warehouse drops to 0 stock.
        Resets notification state in database so users are alerted on next restock.
        """
        database.reset_stock_notified_for_substore(substore_id)

    async def start(self):
        """Initializes and starts the Multi-User Telegram Bot."""
        logger.info("Starting Multi-User Telegram Bot Application...")
        self.app = ApplicationBuilder().token(self.token).build()

        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("help", self.cmd_help))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler(["stock", "check", "check_stock", "inventory"], self.cmd_check_stock))
        self.app.add_handler(CommandHandler(["mode", "set_mode"], self.cmd_mode))
        self.app.add_handler(CommandHandler(["notify", "alerts", "notify_only"], self.cmd_notify_only))
        self.app.add_handler(CommandHandler(["product", "change_product", "item"], self.cmd_product))
        self.app.add_handler(CommandHandler("relogin", self.cmd_relogin))
        self.app.add_handler(CommandHandler(["pause", "snooze"], self.cmd_pause))
        self.app.add_handler(CommandHandler(["resume", "unpause"], self.cmd_resume))
        self.app.add_handler(CommandHandler("pincode", self.cmd_pincode))
        self.app.add_handler(CommandHandler(["cancel", "abort"], self.cmd_cancel))
        self.app.add_handler(CommandHandler(["stop", "delete_account", "deregister"], self.cmd_stop))
        self.app.add_handler(CommandHandler(["users", "admin"], self.cmd_users))

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

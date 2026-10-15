"""
Telegram Bot Controller for Amul Stock Monitoring and Auto-Checkout.
Provides interactive Telegram commands and inline keyboard buttons to:
- Pause/snooze auto-purchase for 1, 2, 3 months or custom durations (notify-only mode)
- Cancel any ongoing checkout or payment authorization immediately
- Resume auto-purchase
- Check live bot status and stock
- Empty cart on demand
"""

import os
import logging
import asyncio
from datetime import datetime
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    Application
)

from state_manager import BotStateManager
try:
    from product_catalog import resolve_amul_product
except ImportError:
    resolve_amul_product = None

logger = logging.getLogger("TelegramControl")

class TelegramBotController:
    def __init__(
        self,
        token: str,
        chat_id: str,
        state_manager: BotStateManager,
        checkout_engine: Optional[object] = None,
        cancel_event: Optional[asyncio.Event] = None,
        force_buy_event: Optional[asyncio.Event] = None,
        pincode: str = "",
        substore_id: str = "",
        target_name: str = "Amul Chocolate Whey Protein (60 sachets)",
        target_alias: str = "amul-chocolate-whey-protein-34-g-or-pack-of-60-sachets"
    ):
        self.token = token.strip() if token else ""
        self.allowed_chat_id = str(chat_id).strip() if chat_id else ""
        self.state_manager = state_manager
        self.checkout_engine = checkout_engine
        self.cancel_event = cancel_event or asyncio.Event()
        self.force_buy_event = force_buy_event or asyncio.Event()
        self.pincode = pincode
        self.substore_id = substore_id
        self.target_name = target_name
        self.target_alias = target_alias
        self.app: Optional[Application] = None
        self._is_running = False

    def is_authorized(self, update: Update) -> bool:
        if not update.effective_chat:
            return False
        user_chat_id = str(update.effective_chat.id)
        if self.allowed_chat_id and user_chat_id != self.allowed_chat_id:
            logger.warning(f"Unauthorized access attempt from chat_id={user_chat_id}")
            return False
        return True

    def _get_pause_keyboard(self) -> InlineKeyboardMarkup:
        keyboard = [
            [
                InlineKeyboardButton("⏸️ 1 Month", callback_data="pause_30"),
                InlineKeyboardButton("⏸️ 2 Months", callback_data="pause_60"),
                InlineKeyboardButton("⏸️ 3 Months", callback_data="pause_90"),
            ],
            [
                InlineKeyboardButton("🔍 Check Live Stock", callback_data="check_stock"),
                InlineKeyboardButton("▶️ Resume Auto-Buy", callback_data="resume"),
            ],
            [
                InlineKeyboardButton("📊 Status", callback_data="status"),
                InlineKeyboardButton("🧹 Empty Cart", callback_data="empty_cart"),
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_authorized(update):
            return
        msg = (
            "👋 <b>Welcome to Amul Stock Monitor & Auto-Buy Control!</b>\n\n"
            "This bot monitors stock for <b>Amul Chocolate Whey Protein (60 sachets)</b> "
            "and supports automatic checkout and smart purchase timeouts.\n\n"
            "📌 <b>Available Commands:</b>\n"
            "• <code>/stock</code> - Check live warehouse inventory & stock quantity\n"
            "• <code>/status</code> - View monitoring status, stock, and purchase timeout\n"
            "• <code>/pause [duration]</code> - Snooze auto-purchases (e.g. <code>/pause 1m</code>, <code>/pause 2m</code>, <code>/pause 3m</code>, <code>/pause 60d</code>)\n"
            "• <code>/resume</code> - Resume auto-purchases immediately\n"
            "• <code>/cancel</code> - Cancel active purchase / QR authorization\n"
            "• <code>/emptycart</code> - Clear all items from Amul cart\n"
            "• <code>/help</code> - Detailed commands guide\n\n"
            "💡 <i>Tip: While paused, the bot stays active in <b>Notify-Only Mode</b>: it will alert you when stock arrives, but won't buy it!</i>"
        )
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=self._get_pause_keyboard())

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_authorized(update):
            return
        await self.cmd_start(update, context)

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_authorized(update):
            return
        is_active, paused_until, reason = self.state_manager.is_auto_buy_active()
        mode_str = "🟢 <b>ACTIVE (Auto-buy enabled)</b>" if is_active else f"⏸️ <b>PAUSED (Notify-Only Mode)</b>\n<i>{reason}</i>"
        
        last_check = self.state_manager.state.get("last_check_time")
        last_check_str = datetime.fromisoformat(last_check).strftime("%d %b %H:%M:%S") if last_check else "Never"
        last_qty = self.state_manager.state.get("last_stock_qty", 0)
        stock_str = f"🟢 In Stock ({last_qty} units)" if last_qty > 0 else "🔴 Out of Stock"
        orders_placed = self.state_manager.state.get("total_orders_placed", 0)

        msg = (
            f"📊 <b>Amul Bot Status Report</b>\n\n"
            f"🛒 <b>Auto-Purchase Mode:</b> {mode_str}\n\n"
            f"📦 <b>Target:</b> {self.target_name}\n"
            f"📍 <b>Delivery Pincode:</b> <code>{self.pincode}</code>\n"
            f"🏪 <b>Substore ID:</b> <code>{self.substore_id}</code>\n"
            f"🕒 <b>Last Check:</b> {last_check_str}\n"
            f"📦 <b>Last Stock:</b> {stock_str}\n"
            f"🎉 <b>Orders Placed:</b> {orders_placed}\n"
        )
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=self._get_pause_keyboard())

    async def _get_live_stock_text(self) -> str:
        qty = self.state_manager.state.get("last_stock_qty", 0)
        price_str = ""
        in_stock = qty > 0

        if self.substore_id and resolve_amul_product:
            try:
                loop = asyncio.get_running_loop()
                res = await loop.run_in_executor(None, resolve_amul_product, self.target_alias, self.substore_id)
                if res:
                    qty = res.get("inventory_quantity", 0)
                    in_stock = res.get("in_stock", False)
                    price_val = res.get("price")
                    if price_val:
                        price_str = f"💰 <b>Price:</b> ₹{price_val:,}\n"
                    self.state_manager.state["last_stock_qty"] = qty
            except Exception as e:
                logger.debug(f"Error querying live warehouse stock: {e}")

        stock_badge = f"🟢 <b>IN STOCK: {qty} units available!</b>" if in_stock else f"🔴 <b>OUT OF STOCK ({qty} units available)</b>"
        now_str = datetime.now().strftime("%H:%M:%S")
        is_active, _, reason = self.state_manager.is_auto_buy_active()
        mode_str = "🟢 <b>Auto-Buy Active</b>" if is_active else f"🔔 <b>Notify-Only Mode</b> (<i>{reason}</i>)"

        action_tip = (
            "⚡ <i>Auto-purchase is active and primed to trigger immediately on restock!</i>"
            if is_active
            else "🔔 <i>Stock monitoring is running in Notify-Only Mode. You will receive an instant alert when units become available.</i>"
        )

        msg = (
            f"📦 <b>Live Warehouse Stock Report</b>\n\n"
            f"🎯 <b>Target Product:</b> <b>{self.target_name}</b>\n"
            f"{price_str}"
            f"📍 <b>Warehouse:</b> <code>{self.substore_id}</code> (Pincode: <code>{self.pincode}</code>)\n"
            f"🎯 <b>Mode:</b> {mode_str}\n\n"
            f"📊 <b>CURRENT INVENTORY:</b>\n"
            f"{stock_badge}\n\n"
            f"🛡️ <b>24/7 Monitoring Active:</b>\n"
            f"The bot continuously polls your regional warehouse every 60s.\n\n"
            f"{action_tip}\n\n"
            f"🕒 <i>Checked live at {now_str} IST</i>"
        )
        return msg

    async def cmd_check_stock(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_authorized(update):
            return
        status_msg = await update.message.reply_text("🔍 <i>Checking live warehouse stock on Amul Store...</i>", parse_mode="HTML")
        msg = await self._get_live_stock_text()
        await status_msg.edit_text(msg, parse_mode="HTML", reply_markup=self._get_pause_keyboard())

    async def cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_authorized(update):
            return
        args = context.args
        if not args:
            # Show interactive buttons
            msg = (
                "⏸️ <b>Choose Auto-Purchase Timeout Duration:</b>\n\n"
                "When paused, the bot will continue monitoring stock in <b>Notify-Only Mode</b>: "
                "you will receive alerts when stock arrives, but the bot will NOT place an order automatically.\n\n"
                "Select a preset duration below or use <code>/pause 2m</code>, <code>/pause 30d</code>, etc."
            )
            await update.message.reply_text(msg, parse_mode="HTML", reply_markup=self._get_pause_keyboard())
            return

        arg_str = " ".join(args).strip()
        days = self.state_manager.parse_duration_string(arg_str)
        if days is not None:
            paused_until, reason = self.state_manager.pause_auto_buy(days=days)
            expiry_str = paused_until.strftime("%d %b %Y, %H:%M")
            msg = (
                f"⏸️ <b>Auto-Purchase Snoozed!</b>\n\n"
                f"⏱️ <b>Duration:</b> {reason}\n"
                f"📅 <b>Paused until:</b> {expiry_str}\n\n"
                f"🔔 <i>Stock monitoring is still active in <b>Notify-Only Mode</b>. "
                f"You will get alerts when stock is available, but no purchase will be made.</i>\n\n"
                f"To resume auto-purchases earlier, send <code>/resume</code>."
            )
        else:
            if arg_str.lower() in ("indefinite", "forever", "stop", "off"):
                _, reason = self.state_manager.pause_auto_buy(days=None)
                msg = (
                    "⏸️ <b>Auto-Purchase Paused Indefinitely!</b>\n\n"
                    "Stock monitoring remains active in <b>Notify-Only Mode</b>.\n"
                    "To resume anytime, send <code>/resume</code>."
                )
            else:
                msg = (
                    f"⚠️ Could not understand duration <code>{arg_str}</code>.\n\n"
                    "Examples:\n"
                    "• <code>/pause 1m</code> (1 month)\n"
                    "• <code>/pause 2m</code> (2 months)\n"
                    "• <code>/pause 3m</code> (3 months)\n"
                    "• <code>/pause 45d</code> (45 days)\n"
                    "• <code>/pause indefinite</code>"
                )
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=self._get_pause_keyboard())

    async def cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_authorized(update):
            return
        self.state_manager.resume_auto_buy()
        msg = (
            "▶️ <b>Auto-Purchase Has Been RESUMED!</b>\n\n"
            "As soon as stock is detected, the bot will automatically initiate checkout "
            "and push the UPI QR payment code to you.\n\n"
            "Send <code>/pause</code> whenever you want to snooze auto-purchases again."
        )
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=self._get_pause_keyboard())

    async def cmd_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_authorized(update):
            return
        # Signal cancellation
        self.cancel_event.set()
        
        msg = (
            "❌ <b>Purchase Cancellation Triggered!</b>\n\n"
            "• Ongoing checkout / payment waiting loops will abort immediately.\n"
            "• Browser session will be closed.\n"
            "• Any pending items will be removed from your cart.\n\n"
            "Would you like to timeout future auto-purchases as well?"
        )
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=self._get_pause_keyboard())

        # Also trigger cart empty in background if checkout_engine exists
        if self.checkout_engine and hasattr(self.checkout_engine, "empty_cart"):
            asyncio.create_task(self._safe_empty_cart_async())

    async def cmd_emptycart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.is_authorized(update):
            return
        status_msg = await update.message.reply_text("🧹 <i>Cleaning up your Amul cart via Playwright...</i>", parse_mode="HTML")
        if self.checkout_engine and hasattr(self.checkout_engine, "empty_cart"):
            try:
                remaining = await self.checkout_engine.empty_cart()
                await status_msg.edit_text(
                    f"✅ <b>Amul cart emptied successfully!</b> (Items remaining: {remaining})",
                    parse_mode="HTML"
                )
            except Exception as e:
                await status_msg.edit_text(f"⚠️ Error emptying cart: {e}")
        else:
            await status_msg.edit_text("⚠️ Checkout engine not ready.")

    async def _safe_empty_cart_async(self):
        try:
            if self.checkout_engine and hasattr(self.checkout_engine, "empty_cart"):
                await self.checkout_engine.empty_cart()
        except Exception as e:
            logger.warning(f"Error in background empty_cart: {e}")

    async def handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        if not self.is_authorized(update):
            return

        data = query.data
        logger.info(f"Telegram button tapped: {data}")

        if data == "pause_30":
            paused_until, reason = self.state_manager.pause_auto_buy(days=30)
            expiry_str = paused_until.strftime("%d %b %Y, %H:%M")
            await query.edit_message_text(
                f"⏸️ <b>Auto-Purchase Paused for 1 Month!</b>\n\n"
                f"📅 <b>Paused until:</b> {expiry_str}\n"
                f"🔔 <i>Stock monitoring is still active in <b>Notify-Only Mode</b>.</i>",
                parse_mode="HTML",
                reply_markup=self._get_pause_keyboard()
            )
        elif data == "pause_60":
            paused_until, reason = self.state_manager.pause_auto_buy(days=60)
            expiry_str = paused_until.strftime("%d %b %Y, %H:%M")
            await query.edit_message_text(
                f"⏸️ <b>Auto-Purchase Paused for 2 Months!</b>\n\n"
                f"📅 <b>Paused until:</b> {expiry_str}\n"
                f"🔔 <i>Stock monitoring is still active in <b>Notify-Only Mode</b>.</i>",
                parse_mode="HTML",
                reply_markup=self._get_pause_keyboard()
            )
        elif data == "pause_90":
            paused_until, reason = self.state_manager.pause_auto_buy(days=90)
            expiry_str = paused_until.strftime("%d %b %Y, %H:%M")
            await query.edit_message_text(
                f"⏸️ <b>Auto-Purchase Paused for 3 Months!</b>\n\n"
                f"📅 <b>Paused until:</b> {expiry_str}\n"
                f"🔔 <i>Stock monitoring is still active in <b>Notify-Only Mode</b>.</i>",
                parse_mode="HTML",
                reply_markup=self._get_pause_keyboard()
            )
        elif data == "pause_indefinite":
            self.state_manager.pause_auto_buy(days=None)
            await query.edit_message_text(
                "⏸️ <b>Auto-Purchase Paused Indefinitely!</b>\n\n"
                "Stock monitoring remains active in <b>Notify-Only Mode</b>.\n"
                "To resume anytime, tap Resume below.",
                parse_mode="HTML",
                reply_markup=self._get_pause_keyboard()
            )
        elif data == "resume":
            self.state_manager.resume_auto_buy()
            await query.edit_message_text(
                "▶️ <b>Auto-Purchase Has Been RESUMED!</b>\n\n"
                "Next time stock is detected, the bot will automatically proceed to checkout.",
                parse_mode="HTML",
                reply_markup=self._get_pause_keyboard()
            )
        elif data == "cancel_checkout":
            self.cancel_event.set()
            await query.edit_message_text(
                "❌ <b>Purchase Cancelled!</b>\n\n"
                "Closing checkout and clearing cart...",
                parse_mode="HTML",
                reply_markup=self._get_pause_keyboard()
            )
            asyncio.create_task(self._safe_empty_cart_async())
        elif data == "buy_now":
            self.state_manager.resume_auto_buy()
            self.force_buy_event.set()
            await query.edit_message_text(
                "⚡ <b>Initiating Purchase Now!</b>\n\n"
                "Auto-purchase resumed and checkout triggered. Stand by for UPI QR code...",
                parse_mode="HTML"
            )
        elif data == "keep_paused":
            is_active, paused_until, reason = self.state_manager.is_auto_buy_active()
            await query.edit_message_text(
                f"⏸️ <b>Acknowledged. Auto-Purchase Remains Paused.</b>\n\n"
                f"<i>{reason}</i>\n"
                f"Monitoring continues in Notify-Only Mode.",
                parse_mode="HTML",
                reply_markup=self._get_pause_keyboard()
            )
        elif data == "empty_cart":
            await query.edit_message_text(
                "🧹 <i>Cleaning up your Amul cart via Playwright...</i>",
                parse_mode="HTML"
            )
            if self.checkout_engine and hasattr(self.checkout_engine, "empty_cart"):
                try:
                    remaining = await self.checkout_engine.empty_cart()
                    await query.edit_message_text(
                        f"✅ <b>Amul cart emptied successfully!</b> ({remaining} items remaining)",
                        parse_mode="HTML",
                        reply_markup=self._get_pause_keyboard()
                    )
                except Exception as e:
                    await query.edit_message_text(f"⚠️ Error emptying cart: {e}", reply_markup=self._get_pause_keyboard())
        elif data == "check_stock":
            await query.edit_message_text(
                "🔍 <i>Checking live warehouse stock on Amul Store...</i>",
                parse_mode="HTML"
            )
            msg = await self._get_live_stock_text()
            await query.edit_message_text(
                msg,
                parse_mode="HTML",
                reply_markup=self._get_pause_keyboard()
            )
        elif data == "status":
            is_active, paused_until, reason = self.state_manager.is_auto_buy_active()
            mode_str = "🟢 <b>ACTIVE (Auto-buy enabled)</b>" if is_active else f"⏸️ <b>PAUSED</b> ({reason})"
            last_qty = self.state_manager.state.get("last_stock_qty", 0)
            stock_str = f"🟢 In Stock ({last_qty} units)" if last_qty > 0 else "🔴 Out of Stock"
            await query.edit_message_text(
                f"📊 <b>Current Status:</b>\n"
                f"• Auto-Buy: {mode_str}\n"
                f"• Stock: {stock_str}\n"
                f"• Target: {self.target_name}",
                parse_mode="HTML",
                reply_markup=self._get_pause_keyboard()
            )

    async def start(self):
        """Initializes and starts the Telegram listener in the background."""
        if not self.token:
            logger.warning("No Telegram token provided. Command listener not started.")
            return

        logger.info("Starting Telegram Bot Command Controller...")
        self.app = ApplicationBuilder().token(self.token).build()

        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("help", self.cmd_help))
        self.app.add_handler(CommandHandler(["stock", "check", "check_stock", "inventory"], self.cmd_check_stock))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler(["pause", "snooze"], self.cmd_pause))
        self.app.add_handler(CommandHandler(["resume", "unpause"], self.cmd_resume))
        self.app.add_handler(CommandHandler(["cancel", "abort"], self.cmd_cancel))
        self.app.add_handler(CommandHandler("emptycart", self.cmd_emptycart))
        self.app.add_handler(CallbackQueryHandler(self.handle_callback_query))

        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)
        self._is_running = True
        logger.info("Telegram Bot Command Controller is active and listening for user commands.")

    async def stop(self):
        """Gracefully stops the Telegram listener."""
        if self.app and self._is_running:
            logger.info("Stopping Telegram Bot Command Controller...")
            if self.app.updater and self.app.updater.running:
                await self.app.updater.stop()
            if self.app.running:
                await self.app.stop()
            await self.app.shutdown()
            self._is_running = False
            logger.info("Telegram Bot Command Controller stopped.")

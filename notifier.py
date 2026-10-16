"""
Telegram notification module for Amul Stock Monitor & Auto-Checkout Bot.
Supports rich HTML text messages, instant QR code image dispatch,
and interactive inline keyboard buttons.
"""

import os
import logging
import asyncio
import requests
from typing import Optional, Any
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger("AmulNotifier")

def format_delivery_address_caption(
    delivery_address: Optional[Dict[str, Any]] = None,
    recipient_phone: Optional[str] = None,
    pincode: Optional[str] = None,
    warehouse_name: Optional[str] = None,
) -> str:
    """
    Constructs a reassuring, crystal-clear delivery address section for the UPI QR notification.
    """
    lines = []
    
    name = None
    phone = recipient_phone
    addr_line = None
    city_state = None
    pin = pincode

    if isinstance(delivery_address, dict):
        name = delivery_address.get("name")
        phone = delivery_address.get("phone") or phone
        pin = delivery_address.get("pincode") or pin
        
        # Build street address
        parts = []
        if delivery_address.get("address1"):
            parts.append(str(delivery_address["address1"]).strip())
        if delivery_address.get("address2"):
            parts.append(str(delivery_address["address2"]).strip())
        if parts:
            addr_line = ", ".join(parts)
            
        # Build city/state
        cs_parts = []
        if delivery_address.get("city"):
            cs_parts.append(str(delivery_address["city"]).strip())
        if delivery_address.get("state"):
            cs_parts.append(str(delivery_address["state"]).strip())
        if cs_parts:
            city_state = ", ".join(cs_parts)
            
        raw_text = delivery_address.get("raw_text")
        if not addr_line and raw_text:
            cleaned_raw = " ".join(raw_text.split())
            if len(cleaned_raw) > 120:
                cleaned_raw = cleaned_raw[:117] + "..."
            addr_line = cleaned_raw

    if name or addr_line:
        lines.append("📍 <b>CONFIRMED DELIVERY ADDRESS:</b>")
        if name:
            lines.append(f"👤 <b>Deliver To:</b> {name}")
        if phone:
            phone_str = str(phone) if str(phone).startswith("+91") else f"+91 {phone}"
            lines.append(f"📱 <b>Contact Phone:</b> {phone_str}")
        if addr_line:
            lines.append(f"🏠 <b>Address:</b> {addr_line}")
        if city_state:
            lines.append(f"🏙️ <b>City / State:</b> {city_state}")
        if pin:
            pin_extra = f" ({warehouse_name})" if warehouse_name else ""
            lines.append(f"📮 <b>Pincode:</b> <code>{pin}</code>{pin_extra}")
    elif pin or phone:
        lines.append("📍 <b>DELIVERY DESTINATION:</b>")
        if phone:
            phone_str = str(phone) if str(phone).startswith("+91") else f"+91 {phone}"
            lines.append(f"📱 <b>Amul Account:</b> {phone_str}")
        if pin:
            pin_extra = f" ({warehouse_name})" if warehouse_name else ""
            lines.append(f"📮 <b>Delivery Pincode:</b> <code>{pin}</code>{pin_extra}")

    if lines:
        return "\n".join(lines)
    return ""

class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.token = token.strip() if token else ""
        self.chat_id = chat_id.strip() if chat_id else ""
        self._bot = None

        if self.token:
            try:
                import telegram
                self._bot = telegram.Bot(token=self.token)
            except Exception as e:
                logger.warning(f"Failed to initialize python-telegram-bot instance: {e}. Falling back to HTTP requests.")

    def is_configured(self) -> bool:
        return bool(self.token and self.chat_id)

    async def send_message_async(
        self,
        message: str,
        reply_markup: Optional[Any] = None,
        disable_web_page_preview: bool = False
    ) -> bool:
        """Sends a telegram message asynchronously with optional inline keyboard."""
        if not self.is_configured():
            logger.warning("Telegram Bot Token or Chat ID not configured. Skipping notification.")
            return False

        # Attempt 1: Using python-telegram-bot
        if self._bot:
            try:
                kwargs = {
                    "chat_id": self.chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                    "reply_markup": reply_markup
                }
                if disable_web_page_preview:
                    try:
                        import telegram
                        kwargs["link_preview_options"] = telegram.LinkPreviewOptions(is_disabled=True)
                    except Exception:
                        kwargs["disable_web_page_preview"] = True

                await self._bot.send_message(**kwargs)
                logger.info("Telegram notification sent successfully (via python-telegram-bot).")
                return True
            except Exception as e:
                logger.warning(f"python-telegram-bot send_message failed: {e}. Retrying via HTTP API...")

        # Attempt 2: Fallback via direct HTTP POST
        return self._send_http(message, reply_markup, disable_web_page_preview=disable_web_page_preview)

    def send_message_sync(
        self,
        message: str,
        reply_markup: Optional[Any] = None,
        disable_web_page_preview: bool = False
    ) -> bool:
        """Sends a telegram message synchronously."""
        if not self.is_configured():
            logger.warning("Telegram Bot Token or Chat ID not configured. Skipping notification.")
            return False

        return self._send_http(message, reply_markup, disable_web_page_preview=disable_web_page_preview)

    def _send_http(
        self,
        message: str,
        reply_markup: Optional[Any] = None,
        disable_web_page_preview: bool = False
    ) -> bool:
        """Sends message via direct Telegram HTTP Bot API."""
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        if disable_web_page_preview:
            payload["disable_web_page_preview"] = True
        if reply_markup and hasattr(reply_markup, "to_dict"):
            payload["reply_markup"] = reply_markup.to_dict()

        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                logger.info("Telegram notification sent successfully (via HTTP API).")
                return True
            else:
                logger.error(f"Failed to send Telegram message. HTTP {resp.status_code}: {resp.text}")
                return False
        except Exception as e:
            logger.error(f"Exception sending Telegram message: {e}")
            return False

    async def send_photo_async(
        self,
        photo_path: str,
        caption: Optional[str] = None,
        reply_markup: Optional[Any] = None
    ) -> bool:
        """Sends a photo with optional caption and inline buttons asynchronously."""
        if not self.is_configured():
            logger.warning("Telegram Bot Token or Chat ID not configured. Skipping photo notification.")
            return False

        if not os.path.exists(photo_path):
            logger.error(f"Photo path does not exist: {photo_path}")
            return False

        # Attempt 1: python-telegram-bot
        if self._bot:
            try:
                with open(photo_path, "rb") as f:
                    await self._bot.send_photo(
                        chat_id=self.chat_id,
                        photo=f,
                        caption=caption,
                        parse_mode="HTML",
                        reply_markup=reply_markup
                    )
                logger.info("Telegram photo sent successfully (via python-telegram-bot).")
                return True
            except Exception as e:
                logger.warning(f"python-telegram-bot send_photo failed: {e}. Retrying via HTTP API...")

        # Attempt 2: Direct HTTP multipart POST
        return self._send_photo_http(photo_path, caption, reply_markup)

    def _send_photo_http(
        self,
        photo_path: str,
        caption: Optional[str] = None,
        reply_markup: Optional[Any] = None
    ) -> bool:
        """Sends photo via direct HTTP multipart POST."""
        url = f"https://api.telegram.org/bot{self.token}/sendPhoto"
        try:
            with open(photo_path, "rb") as f:
                files = {"photo": f}
                data = {
                    "chat_id": self.chat_id,
                    "caption": caption or "",
                    "parse_mode": "HTML"
                }
                if reply_markup and hasattr(reply_markup, "to_json"):
                    data["reply_markup"] = reply_markup.to_json()
                elif reply_markup and hasattr(reply_markup, "to_dict"):
                    import json
                    data["reply_markup"] = json.dumps(reply_markup.to_dict())

                resp = requests.post(url, files=files, data=data, timeout=20)
                if resp.status_code == 200:
                    logger.info("Telegram photo sent successfully (via HTTP API).")
                    return True
                else:
                    logger.error(f"Failed to send photo. HTTP {resp.status_code}: {resp.text}")
                    return False
        except Exception as e:
            logger.error(f"Exception sending Telegram photo: {e}")
            return False

    async def notify_order_placed(
        self,
        order_id: Optional[str] = None,
        details: Optional[str] = None,
        paused_until_str: Optional[str] = None
    ) -> bool:
        """
        Sends the mandatory order placement alert with post-purchase snooze details:
        'Product was in stock. Order automatically placed.'
        """
        msg = "🎉 <b>Product was in stock. Order automatically placed.</b>"
        if order_id:
            msg += f"\n\n<b>Order ID:</b> <code>{order_id}</code>"
        if details:
            msg += f"\n<b>Details:</b> {details}"

        if paused_until_str:
            msg += (
                f"\n\n⏸️ <b>Auto-Purchase Snoozed!</b>\n"
                f"Auto-buy is paused until <b>{paused_until_str}</b> so you don't accumulate "
                f"duplicate packs while using this supply.\n\n"
                f"Stock monitoring continues in <b>Notify-Only Mode</b>.\n"
                f"Adjust snooze anytime using the buttons below:"
            )
        else:
            msg += "\n\nWould you like to snooze auto-purchases while this pack lasts?"

        keyboard = [
            [
                InlineKeyboardButton("⏸️ 1 Month", callback_data="pause_30"),
                InlineKeyboardButton("⏸️ 2 Months", callback_data="pause_60"),
                InlineKeyboardButton("⏸️ 3 Months", callback_data="pause_90"),
            ],
            [
                InlineKeyboardButton("▶️ Resume Now", callback_data="resume"),
                InlineKeyboardButton("📊 Status", callback_data="status"),
            ]
        ]
        return await self.send_message_async(msg, reply_markup=InlineKeyboardMarkup(keyboard))

    async def notify_stock_alert(self, product_name: str, stock_qty: int) -> bool:
        """Alert when stock is detected before placing order."""
        msg = (
            f"🚨 <b>Stock Alert!</b>\n"
            f"<b>Product:</b> {product_name}\n"
            f"<b>Stock Quantity:</b> {stock_qty}\n"
            f"<i>Triggering auto-checkout & UPI payment request...</i>"
        )
        return await self.send_message_async(msg)

    async def notify_stock_alert_paused(
        self,
        product_name: str,
        stock_qty: int,
        paused_reason: str
    ) -> bool:
        """
        Alert sent when product is in stock, but auto-buy is snoozed/paused (Notify-Only Mode).
        Provides a 1-tap option to buy immediately if desired.
        """
        msg = (
            f"🚨 <b>Amul Stock Alert (Notify-Only Mode)</b>\n\n"
            f"📦 <b>Product:</b> {product_name}\n"
            f"📊 <b>Stock Available:</b> <b>{stock_qty}</b> units!\n"
            f"⏸️ <b>Auto-Purchase:</b> {paused_reason}\n\n"
            f"<i>Auto-purchase is paused so no order was placed. If you want to buy it now, tap below:</i>"
        )
        keyboard = [
            [
                InlineKeyboardButton("⚡ Buy Now (Resume Auto-Buy)", callback_data="buy_now"),
                InlineKeyboardButton("⏸️ Keep Paused", callback_data="keep_paused")
            ]
        ]
        return await self.send_message_async(msg, reply_markup=InlineKeyboardMarkup(keyboard))

    async def notify_upi_payment_required(
        self,
        photo_path: str,
        amount: str,
        product_name: str,
        timeout_mins: int = 10,
        delivery_address: Optional[Dict[str, Any]] = None,
        recipient_phone: Optional[str] = None,
        pincode: Optional[str] = None,
        warehouse_name: Optional[str] = None
    ) -> bool:
        """
        Dispatches urgent UPI QR Code notification to Telegram with confirmed delivery address
        and an instant Cancel button.
        """
        addr_block = format_delivery_address_caption(
            delivery_address=delivery_address,
            recipient_phone=recipient_phone,
            pincode=pincode,
            warehouse_name=warehouse_name
        )
        addr_section = f"\n\n{addr_block}" if addr_block else ""

        caption = (
            f"🚨 <b>URGENT: AMUL PRODUCT IN STOCK!</b>\n\n"
            f"📦 <b>Item:</b> {product_name}\n"
            f"💰 <b>Amount:</b> ₹{amount}\n"
            f"⏰ <b>Expires in:</b> {timeout_mins} minutes"
            f"{addr_section}\n\n"
            f"👉 <b>ACTION REQUIRED:</b>\n"
            f"1. Open any UPI app (GPay / PhonePe / Paytm / Cred / BHIM).\n"
            f"2. <b>Scan the QR code above</b> and authorize the payment.\n"
            f"3. Once authorized, the bot will automatically confirm your order!\n\n"
            f"<i>If you changed your mind, tap Cancel Purchase below.</i>"
        )
        keyboard = [
            [
                InlineKeyboardButton("❌ Cancel This Purchase", callback_data="cancel_checkout")
            ]
        ]
        return await self.send_photo_async(
            photo_path=photo_path,
            caption=caption,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def notify_autobuy_failed_manual(
        self,
        product_name: str,
        product_url: str,
        reason: str
    ) -> bool:
        is_session_expired = (
            "session expired" in (reason or "").lower()
            or "login required" in (reason or "").lower()
        )

        if is_session_expired:
            guidance = (
                "🔑 <b>Action Required:</b> Your Amul login session has expired on StoreHippo.\n"
                "Please tap <b>🔑 Re-login to Amul</b> below to re-enter your mobile number & OTP so Auto-Buy can work next time.\n\n"
                "🛒 <b>In the meantime:</b> You can still buy this restock manually before it sells out using the store link below!"
            )
        else:
            guidance = (
                "🛒 <b>Manual Purchase Recommended:</b>\n"
                "If you still want to buy this item, please buy it manually right now using the link below before stock sells out!"
            )

        msg = (
            f"⚠️ <b>Auto-Buy is not working!</b>\n\n"
            f"We attempted automated checkout for <b>{product_name}</b>, "
            f"but auto-buy could not complete the purchase.\n\n"
            f"🔍 <b>What was the problem:</b>\n"
            f"• <i>{reason}</i>\n\n"
            f"{guidance}"
        )
        keyboard = [
            [
                InlineKeyboardButton("🛒 Buy Manually on Amul Store", url=product_url)
            ]
        ]
        if is_session_expired:
            keyboard.append([
                InlineKeyboardButton("🔑 Re-login to Amul", callback_data="relogin")
            ])

        keyboard.append([
            InlineKeyboardButton("📊 My Status", callback_data="status"),
            InlineKeyboardButton("🔔 Switch to Alerts Only", callback_data="switch_notify_only")
        ])

        return await self.send_message_async(
            msg,
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True
        )


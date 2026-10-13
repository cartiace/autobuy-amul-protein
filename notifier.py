"""
Telegram notification module for Amul Stock Monitor & Auto-Checkout Bot.
"""

import logging
import asyncio
import requests
from typing import Optional

logger = logging.getLogger("AmulNotifier")

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

    async def send_message_async(self, message: str) -> bool:
        """Sends a telegram message asynchronously."""
        if not self.is_configured():
            logger.warning("Telegram Bot Token or Chat ID not configured. Skipping notification.")
            return False

        # Attempt 1: Using python-telegram-bot
        if self._bot:
            try:
                await self._bot.send_message(chat_id=self.chat_id, text=message, parse_mode="HTML")
                logger.info("Telegram notification sent successfully (via python-telegram-bot).")
                return True
            except Exception as e:
                logger.warning(f"python-telegram-bot send_message failed: {e}. Retrying via HTTP API...")

        # Attempt 2: Fallback via direct HTTP POST
        return self._send_http(message)

    def send_message_sync(self, message: str) -> bool:
        """Sends a telegram message synchronously."""
        if not self.is_configured():
            logger.warning("Telegram Bot Token or Chat ID not configured. Skipping notification.")
            return False

        return self._send_http(message)

    def _send_http(self, message: str) -> bool:
        """Sends message via direct Telegram HTTP Bot API."""
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
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

    async def notify_order_placed(self, order_id: Optional[str] = None, details: Optional[str] = None) -> bool:
        """
        Sends the mandatory order placement alert:
        'Product was in stock. Order automatically placed.'
        """
        msg = "<b>Product was in stock. Order automatically placed.</b>"
        if order_id:
            msg += f"\n\n<b>Order ID:</b> <code>{order_id}</code>"
        if details:
            msg += f"\n<b>Details:</b> {details}"
        return await self.send_message_async(msg)

    async def notify_stock_alert(self, product_name: str, stock_qty: int) -> bool:
        """Optional alert when stock is detected before placing order."""
        msg = f"🚨 <b>Stock Alert!</b>\n<b>Product:</b> {product_name}\n<b>Stock Quantity:</b> {stock_qty}\n<i>Triggering auto-checkout...</i>"
        return await self.send_message_async(msg)

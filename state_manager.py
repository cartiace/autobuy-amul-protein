"""
Bot State Manager for Amul Stock Monitoring and Auto-Checkout Bot.
Persists auto-buy timeout, snooze status, and monitoring state to bot_state.json.
"""

import os
import json
import re
import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple

logger = logging.getLogger("StateManager")

STATE_FILE = "bot_state.json"

class BotStateManager:
    def __init__(self, filepath: str = STATE_FILE):
        self.filepath = filepath
        self.state = {
            "auto_buy_enabled": True,
            "paused_until": None,        # ISO format string or None
            "pause_reason": None,
            "last_stock_qty": 0,
            "last_check_time": None,
            "last_notify_time": None,
            "total_orders_placed": 0,
        }
        self.load_state()

    def load_state(self) -> dict:
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    self.state.update(saved)
                    logger.info(f"Loaded state from {self.filepath}")
            except Exception as e:
                logger.error(f"Error loading state from {self.filepath}: {e}")
        return self.state

    def save_state(self) -> None:
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2, default=str)
        except Exception as e:
            logger.error(f"Error saving state to {self.filepath}: {e}")

    def is_auto_buy_active(self) -> Tuple[bool, Optional[datetime], str]:
        """
        Determines if auto-buy is currently active.
        Returns:
            (is_active: bool, paused_until: Optional[datetime], reason: str)
        """
        # If explicitly disabled without date
        if not self.state.get("auto_buy_enabled", True) and not self.state.get("paused_until"):
            return False, None, self.state.get("pause_reason") or "Paused indefinitely"

        paused_until_str = self.state.get("paused_until")
        if paused_until_str:
            try:
                paused_until = datetime.fromisoformat(paused_until_str)
                now = datetime.now()
                if now < paused_until:
                    diff = paused_until - now
                    days = diff.days
                    hours = int(diff.seconds / 3600)
                    mins = int((diff.seconds % 3600) / 60)
                    time_left_str = f"{days}d {hours}h {mins}m" if days > 0 else f"{hours}h {mins}m"
                    reason = self.state.get("pause_reason") or f"Paused until {paused_until.strftime('%d %b %Y, %H:%M')}"
                    return False, paused_until, f"{reason} ({time_left_str} remaining)"
                else:
                    # Timeout expired! Automatically resume
                    logger.info(f"Pause timeout ({paused_until_str}) has expired. Resuming auto-buy.")
                    self.resume()
                    return True, None, "Auto-buy is active (pause expired)."
            except Exception as e:
                logger.error(f"Error parsing paused_until date '{paused_until_str}': {e}")
                self.resume()

        return True, None, "Auto-buy is active."

    def pause_auto_buy(self, days: Optional[float] = None, reason: str = "") -> Tuple[Optional[datetime], str]:
        """
        Pauses auto-buy for a specified number of days or indefinitely if days is None.
        """
        if days is not None and days > 0:
            paused_until = datetime.now() + timedelta(days=days)
            self.state["auto_buy_enabled"] = False
            self.state["paused_until"] = paused_until.isoformat()
            
            # Format friendly reason
            if not reason:
                if abs(days - 30) < 1:
                    reason = "Paused for 1 month"
                elif abs(days - 60) < 1:
                    reason = "Paused for 2 months"
                elif abs(days - 90) < 1:
                    reason = "Paused for 3 months"
                else:
                    reason = f"Paused for {int(days) if days.is_integer() else days} days"
                    
            self.state["pause_reason"] = reason
            self.save_state()
            logger.info(f"Auto-buy paused until {paused_until.isoformat()}: {reason}")
            return paused_until, reason
        else:
            self.state["auto_buy_enabled"] = False
            self.state["paused_until"] = None
            self.state["pause_reason"] = reason or "Paused indefinitely"
            self.save_state()
            logger.info("Auto-buy paused indefinitely.")
            return None, self.state["pause_reason"]

    def resume_auto_buy(self) -> None:
        """Resumes auto-buy immediately."""
        self.state["auto_buy_enabled"] = True
        self.state["paused_until"] = None
        self.state["pause_reason"] = None
        self.save_state()
        logger.info("Auto-buy has been resumed.")

    def record_stock_check(self, in_stock: bool, quantity: int) -> None:
        self.state["last_check_time"] = datetime.now().isoformat()
        self.state["last_stock_qty"] = quantity if in_stock else 0
        self.save_state()

    def record_order_placed(self) -> None:
        self.state["total_orders_placed"] = self.state.get("total_orders_placed", 0) + 1
        self.save_state()

    def should_notify_stock(self, stock_qty: int, min_interval_seconds: int = 1800) -> bool:
        """
        Rate limits notifications in notify-only mode to prevent spamming every 60s.
        Alerts immediately on 0 -> >0 transition or after min_interval_seconds.
        """
        now = datetime.now()
        last_qty = self.state.get("last_stock_qty", 0)
        last_notify_str = self.state.get("last_notify_time")

        # Stock just appeared from 0
        if last_qty == 0 and stock_qty > 0:
            self.state["last_notify_time"] = now.isoformat()
            self.save_state()
            return True

        # Check interval
        if last_notify_str:
            try:
                last_notify = datetime.fromisoformat(last_notify_str)
                if (now - last_notify).total_seconds() < min_interval_seconds:
                    return False
            except Exception:
                pass

        self.state["last_notify_time"] = now.isoformat()
        self.save_state()
        return True

    @staticmethod
    def parse_duration_string(text: str) -> Optional[float]:
        """
        Parses text like '1m', '2 months', '3m', '30d', '60 days', 'indefinite' into number of days.
        Returns None for indefinite or unrecognized.
        """
        text = text.strip().lower()
        if not text or text in ("indefinite", "forever", "stop", "off", "cancel"):
            return None

        # Check months: e.g. 1m, 2m, 3m, 1 month, 2 months
        m = re.match(r"^(\d+)\s*(?:m|mo|mon|month|months)$", text)
        if m:
            months = int(m.group(1))
            return float(months * 30)

        # Check days: e.g. 30d, 60 days
        d = re.match(r"^(\d+)\s*(?:d|day|days)$", text)
        if d:
            return float(d.group(1))

        # Check weeks: e.g. 2w, 4 weeks
        w = re.match(r"^(\d+)\s*(?:w|week|weeks)$", text)
        if w:
            return float(int(w.group(1)) * 7)

        # Plain number: default to months if <= 12, else days
        if text.isdigit():
            val = int(text)
            if val <= 12:
                return float(val * 30)
            return float(val)

        return None

"""
Central Aggregated Stock Poller for Amul Multi-User Auto-Checkout Bot.
Polls each unique regional substore once per cycle instead of per-user,
preventing Cloudflare IP rate limits while scaling to 500+ concurrent users.
"""

import os
import sys
import time
import logging
import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Callable, Any

from monitor import StockMonitor
import database
from database import User

logger = logging.getLogger("CentralPoller")

class CentralStockPoller:
    def __init__(
        self,
        poll_interval: int = 60,
        target_alias: str = "amul-chocolate-whey-protein-34-g-or-pack-of-60-sachets",
        target_sku: str = "WPCCP05_02",
        cookies_file: str = "cookies.json",
        on_stock_detected: Optional[Callable[[str, int, Dict[str, Any], List[User]], Any]] = None
    ):
        self.poll_interval = poll_interval
        self.target_alias = target_alias
        self.target_sku = target_sku
        self.cookies_file = cookies_file
        self.on_stock_detected = on_stock_detected
        self._is_running = False
        
        # Shared monitor instance for TID generation and session management
        self.monitor = StockMonitor(
            substore_id="",
            target_alias=self.target_alias,
            target_sku=self.target_sku,
            cookies_file=self.cookies_file if os.path.exists(self.cookies_file) else None
        )
        self.monitor.refresh_session_tid()

    async def poll_once(self) -> Dict[str, Dict[str, Any]]:
        """
        Polls each unique active regional warehouse once.
        Returns a dict of {substore_id: {'in_stock': bool, 'quantity': int, 'details': dict}}
        """
        substores = database.get_unique_active_substores()
        if not substores:
            logger.info("No active users with configured substores. Sleeping...")
            return {}

        results = {}
        logger.info(f"Central Poller: Checking {len(substores)} unique regional warehouses...")

        for sid in substores:
            try:
                # Update substore id for monitor
                self.monitor.substore_id = sid
                in_stock, stock_qty, details = self.monitor.check_stock_api()
                results[sid] = {
                    "in_stock": in_stock,
                    "quantity": stock_qty,
                    "details": details
                }

                if in_stock and stock_qty > 0:
                    prod_name = details.get("name", "Amul Chocolate Whey Protein 60 sachets")
                    logger.info(f"🚨 WAREHOUSE RESTOCK: Substore {sid} has {stock_qty} units of '{prod_name}'!")

                    # Find all users registered in this warehouse
                    subscribed_users = database.get_users_for_substore(sid)
                    logger.info(f"Found {len(subscribed_users)} users subscribed to warehouse {sid}")

                    if self.on_stock_detected:
                        # Trigger dispatch callback
                        res = self.on_stock_detected(sid, stock_qty, details, subscribed_users)
                        if asyncio.iscoroutine(res):
                            await res
                else:
                    logger.debug(f"Warehouse {sid}: Out of stock.")

                # Friendly delay between warehouse requests to prevent bursting
                await asyncio.sleep(0.5)

            except Exception as e:
                logger.error(f"Error checking warehouse {sid}: {e}")

        return results

    async def start(self):
        """Starts the continuous 24/7 central polling loop."""
        self._is_running = True
        logger.info(f"Central Stock Poller started. Polling every {self.poll_interval}s...")
        poll_count = 0

        while self._is_running:
            try:
                poll_count += 1
                await self.poll_once()
            except Exception as e:
                logger.error(f"Error in Central Poller loop: {e}", exc_info=True)

            try:
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break

    def stop(self):
        self._is_running = False
        logger.info("Central Stock Poller stopped.")

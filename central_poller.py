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
        on_stock_detected: Optional[Callable[[str, int, Dict[str, Any], List[User]], Any]] = None,
        on_out_of_stock: Optional[Callable[[str], Any]] = None
    ):
        self.poll_interval = poll_interval
        self.target_alias = target_alias
        self.target_sku = target_sku
        self.cookies_file = cookies_file
        self.on_stock_detected = on_stock_detected
        self.on_out_of_stock = on_out_of_stock
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
                # Find all unique targets tracked by READY users in this warehouse
                active_targets = database.get_active_targets_for_substore(sid)
                if not active_targets:
                    # Fallback to default product if no custom target is recorded
                    active_targets = [(self.target_alias, self.target_sku, "Amul Chocolate Whey Protein 60 sachets")]

                results[sid] = {}

                for (alias, sku, prod_name) in active_targets:
                    in_stock, stock_qty, details = self.monitor.check_stock_api(
                        target_alias=alias,
                        target_sku=sku,
                        substore_id=sid
                    )
                    prod_display = details.get("name") or prod_name
                    results[sid][alias] = {
                        "in_stock": in_stock,
                        "quantity": stock_qty,
                        "details": details
                    }

                    if in_stock and stock_qty > 0:
                        logger.info(f"🚨 WAREHOUSE RESTOCK: Substore {sid} has {stock_qty} units of '{prod_display}'!")

                        # Find all users registered in this warehouse tracking this specific target product
                        subscribed_users = database.get_users_for_substore_and_target(sid, alias)
                        logger.info(f"Found {len(subscribed_users)} users subscribed to '{alias}' in warehouse {sid}")

                        if self.on_stock_detected:
                            # Trigger dispatch callback with product details and target users
                            res = self.on_stock_detected(sid, stock_qty, details, subscribed_users)
                            if asyncio.iscoroutine(res):
                                await res
                    else:
                        logger.debug(f"Warehouse {sid} - '{alias}': Out of stock.")
                        # Reset notification flag only for this product in this warehouse
                        database.reset_stock_notified_for_substore_and_target(sid, alias)
                        if self.on_out_of_stock:
                            res = self.on_out_of_stock(sid)
                            if asyncio.iscoroutine(res):
                                await res

                    # Friendly delay between product checks to prevent bursting
                    await asyncio.sleep(0.3)

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

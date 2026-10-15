"""
Stock monitoring module for Amul's e-commerce platform (StoreHippo engine).
Polls internal JSON product API with regional substore ID.
"""

import time
import json
import logging
import random
import hashlib
import re
import requests
from typing import Optional, Dict, Any, Tuple

logger = logging.getLogger("AmulMonitor")

STORE_ID = "62fa94df8c13af2e242eba16"
BASE_DOMAIN = "https://shop.amul.com"

class StockMonitor:
    def __init__(
        self,
        substore_id: str,
        target_alias: str = "amul-chocolate-whey-protein-34-g-or-pack-of-60-sachets",
        target_sku: str = "WPCCP05_02",
        cookies_file: Optional[str] = None
    ):
        self.substore_id = substore_id.strip()
        self.target_alias = target_alias.strip()
        self.target_sku = target_sku.strip()
        self.cookies_file = cookies_file

        self.session = requests.Session()
        self.session_tid = None
        self.user_agent = (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
        )
        self.session.headers.update({
            "User-Agent": self.user_agent,
            "Accept-Language": "en-IN,en;q=0.9",
        })

        if cookies_file:
            self._load_cookies(cookies_file)

    def _load_cookies(self, path: str):
        """Loads cookies from json into requests session."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                cookies = json.load(f)
            for c in cookies:
                # Handle cookie dict from browser extensions
                name = c.get("name")
                value = c.get("value")
                domain = c.get("domain", "shop.amul.com").lstrip(".")
                if name and value:
                    self.session.cookies.set(name, value, domain=domain)
            logger.info(f"Loaded {len(cookies)} cookies from {path} into monitor session.")
        except FileNotFoundError:
            logger.debug(f"Cookies file {path} not found for monitor. Proceeding with fresh session.")
        except Exception as e:
            logger.warning(f"Error loading cookies for monitor from {path}: {e}")

    def refresh_session_tid(self) -> bool:
        """
        Fetches session.tid from /user/info.js required by StoreHippo's HMAC-SHA256
        security header algorithm.
        """
        try:
            ts = int(time.time() * 1000)
            info_url = f"{BASE_DOMAIN}/user/info.js?_v={ts}"
            referer = f"{BASE_DOMAIN}/en/product/{self.target_alias}"
            resp = self.session.get(info_url, headers={"Referer": referer}, timeout=10)

            if resp.status_code == 200:
                match = re.search(r'"tid"\s*:\s*"([^"]+)"', resp.text)
                if match:
                    self.session_tid = match.group(1)
                    logger.debug(f"Acquired StoreHippo session TID: {self.session_tid}")
                    return True
            logger.warning(f"Failed to extract session.tid from /user/info.js (status: {resp.status_code})")
        except Exception as e:
            logger.warning(f"Error refreshing session TID: {e}")
        return False

    def _generate_tid_header(self) -> str:
        """
        Generates StoreHippo's internal 'tid' header:
        Algorithm:
          rand = random(0..999)
          timestamp = current_epoch_ms
          raw = storeId + ":" + timestamp + ":" + rand + ":" + session.tid
          hash = SHA256(raw)
          header = timestamp + ":" + rand + ":" + hash
        """
        if not self.session_tid:
            self.refresh_session_tid()

        rand_t = random.randint(0, 999)
        now_ts = int(time.time() * 1000)
        session_tid = self.session_tid or "default_session"
        raw_str = f"{STORE_ID}:{now_ts}:{rand_t}:{session_tid}"
        hash_val = hashlib.sha256(raw_str.encode("utf-8")).hexdigest()
        return f"{now_ts}:{rand_t}:{hash_val}"

    def check_stock_api(
        self,
        target_alias: Optional[str] = None,
        target_sku: Optional[str] = None,
        substore_id: Optional[str] = None
    ) -> Tuple[bool, int, Dict[str, Any]]:
        """
        Polls Amul's internal product API for a specific product and substore.
        Supports checking any target product dynamically across regional warehouses.
        Returns:
            Tuple: (is_in_stock: bool, quantity: int, product_details: dict)
        """
        alias = (target_alias or self.target_alias).strip()
        sku = (target_sku or self.target_sku).strip()
        sid = (substore_id or self.substore_id).strip()

        if not self.session_tid:
            self.refresh_session_tid()

        tid_val = self._generate_tid_header()
        ref_url = f"{BASE_DOMAIN}/en/product/{alias}"

        headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": ref_url,
            "frontend": "1",
            "tid": tid_val,
            "base_url": ref_url,
        }

        # 1. Direct alias filter query with substore (fastest & works for all categories)
        direct_url = (
            f"{BASE_DOMAIN}/api/1/entity/ms.products?"
            f"filters[0][field]=alias&filters[0][value][0]={alias}&filters[0][operator]=in&filters[0][original]=1"
            f"&substore={sid}&v=6"
        )

        try:
            resp = self.session.get(direct_url, headers=headers, timeout=12)

            if resp.status_code == 401:
                logger.debug("Received 401, refreshing session TID and retrying...")
                self.refresh_session_tid()
                headers["tid"] = self._generate_tid_header()
                resp = self.session.get(direct_url, headers=headers, timeout=12)

            if resp.status_code == 200:
                data = resp.json()
                items = data.get("data", [])
                if items:
                    item = items[0]
                    is_available = bool(item.get("available") == 1 or item.get("available") is True)
                    inventory_qty = int(item.get("inventory_quantity") or 0)
                    in_stock = is_available and (inventory_qty > 0)
                    logger.debug(
                        f"Direct query matched: '{item.get('name')}' (SKU: {item.get('sku')}) | "
                        f"Substore: {sid} | Available: {is_available} | Stock: {inventory_qty}"
                    )
                    return in_stock, inventory_qty, item
        except Exception as e:
            logger.debug(f"Direct alias query error for '{alias}': {e}")

        # 2. Fallback: Category query with substore
        cat_url = (
            f"{BASE_DOMAIN}/api/1/entity/ms.products?"
            f"fields[name]=1&fields[brand]=1&fields[categories]=1&fields[collections]=1&"
            f"fields[alias]=1&fields[sku]=1&fields[price]=1&fields[compare_price]=1&"
            f"fields[seller]=1&fields[available]=1&fields[inventory_quantity]=1&"
            f"fields[inventory_allow_out_of_stock]=1&fields[default_variant]=1&fields[variants]=1&"
            f"filters[0][field]=categories&filters[0][value][0]=protein&filters[0][operator]=in&filters[0][original]=1&"
            f"limit=50&start=0&v=6&device_type=other&substore={sid}"
        )

        try:
            resp = self.session.get(cat_url, headers=headers, timeout=12)
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("data", [])
                for item in items:
                    i_alias = item.get("alias", "").lower()
                    i_sku = item.get("sku", "").upper()
                    if (alias.lower() in i_alias) or (sku and sku.upper() == i_sku):
                        is_available = bool(item.get("available") == 1 or item.get("available") is True)
                        inventory_qty = int(item.get("inventory_quantity") or 0)
                        in_stock = is_available and (inventory_qty > 0)
                        return in_stock, inventory_qty, item
        except Exception as e:
            logger.error(f"Category fallback query error: {e}")

        return False, 0, {}

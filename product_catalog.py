"""
Product Catalog & Dynamic URL Resolver for Amul Store.
Provides a curated catalog of high-demand Amul protein products,
plus dynamic parsing and verification of arbitrary Amul store URLs
via StoreHippo API.
"""

import os
import re
import time
import json
import random
import hashlib
import logging
import requests
from typing import Optional, Dict, Any, List
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger("ProductCatalog")

STORE_ID = "62fa94df8c13af2e242eba16"
BASE_DOMAIN = "https://shop.amul.com"

# Curated catalog of top Amul products
CURATED_PRODUCTS: Dict[str, Dict[str, Any]] = {
    "choc_whey_60": {
        "id": "choc_whey_60",
        "name": "Amul Chocolate Whey Protein (60 sachets)",
        "full_name": "Amul Chocolate Whey Protein, 34 g | Pack of 60 sachets",
        "alias": "amul-chocolate-whey-protein-34-g-or-pack-of-60-sachets",
        "sku": "WPCCP05_02",
        "price": 4500,
        "label": "🍫 Choc Whey (60s) - ₹4,500",
        "short_desc": "34g sachet | 60 sachets | 24g protein/serv",
    },
    "choc_whey_30": {
        "id": "choc_whey_30",
        "name": "Amul Chocolate Whey Protein (30 sachets)",
        "full_name": "Amul Chocolate Whey Protein, 34 g | Pack of 30 sachets",
        "alias": "amul-chocolate-whey-protein-34-g-or-pack-of-30-sachets",
        "sku": "WPCCP03_01",
        "price": 2500,
        "label": "🍫 Choc Whey (30s) - ₹2,500",
        "short_desc": "34g sachet | 30 sachets | 24g protein/serv",
    },
    "raw_whey_60": {
        "id": "raw_whey_60",
        "name": "Amul Raw Whey Protein (60 sachets)",
        "full_name": "Amul Whey Protein, 32 g | Pack of 60 Sachets",
        "alias": "amul-whey-protein-32-g-or-pack-of-60-sachets",
        "sku": "WPCCP02_01",
        "price": 4200,
        "label": "🥛 Raw Whey (60s) - ₹4,200",
        "short_desc": "32g sachet | 60 sachets | 25g protein/serv",
    },
    "raw_whey_30": {
        "id": "raw_whey_30",
        "name": "Amul Raw Whey Protein (30 sachets)",
        "full_name": "Amul Whey Protein, 32 g | Pack of 30 Sachets",
        "alias": "amul-whey-protein-32-g-or-pack-of-30-sachets",
        "sku": "WPCCP01_01",
        "price": 2400,
        "label": "🥛 Raw Whey (30s) - ₹2,400",
        "short_desc": "32g sachet | 30 sachets | 25g protein/serv",
    },
    "buttermilk_30": {
        "id": "buttermilk_30",
        "name": "Amul High Protein Buttermilk (Pack of 30)",
        "full_name": "Amul High Protein Buttermilk, 200 mL | Pack of 30",
        "alias": "amul-high-protein-buttermilk-200-ml-or-pack-of-30",
        "sku": "BTMCP11_30",
        "price": 900,
        "label": "🥤 Buttermilk (30 pk) - ₹900",
        "short_desc": "200 mL | Pack of 30 | 15g protein/pack",
    },
    "rose_lassi_30": {
        "id": "rose_lassi_30",
        "name": "Amul High Protein Rose Lassi (Pack of 30)",
        "full_name": "Amul High Protein Rose Lassi, 200 mL | Pack of 30",
        "alias": "amul-high-protein-rose-lassi-200-ml-or-pack-of-30",
        "sku": "LASCP40_30",
        "price": 900,
        "label": "🌹 Rose Lassi (30 pk) - ₹900",
        "short_desc": "200 mL | Pack of 30 | 15g protein/pack",
    },
    "blueberry_shake_30": {
        "id": "blueberry_shake_30",
        "name": "Amul Protein Blueberry Shake (Pack of 30)",
        "full_name": "Amul High Protein Blueberry Shake, 200 mL | Pack of 30",
        "alias": "amul-high-protein-blueberry-shake-200-ml-or-pack-of-30",
        "sku": "DBDCP41_30",
        "price": 1800,
        "label": "🫐 Blueberry Shake - ₹1,800",
        "short_desc": "200 mL | Pack of 30 | 20g protein/pack",
    },
    "paneer_400g_2": {
        "id": "paneer_400g_2",
        "name": "Amul High Protein Paneer (400g x 2)",
        "full_name": "Amul High Protein Paneer, 400 g | Pack of 2",
        "alias": "amul-high-protein-paneer-400-g-or-pack-of-2",
        "sku": "HPPCP01_02",
        "price": 330,
        "label": "🧀 Paneer (400g x 2) - ₹330",
        "short_desc": "400 g | Pack of 2 | 25g protein/100g",
    },
}

DEFAULT_PRODUCT_ID = "choc_whey_60"

def get_product_by_id(product_id: str) -> Optional[Dict[str, Any]]:
    return CURATED_PRODUCTS.get(product_id)

def get_product_by_alias(alias: str) -> Optional[Dict[str, Any]]:
    for p in CURATED_PRODUCTS.values():
        if p["alias"].lower() == alias.lower():
            return p
    return None

def extract_alias_from_url(text: str) -> Optional[str]:
    """
    Extracts the product alias from an Amul URL or raw alias string.
    Supported inputs:
    - https://shop.amul.com/en/product/amul-whey-protein-32-g-or-pack-of-60-sachets
    - https://shop.amul.com/product/amul-whey-protein-32-g-or-pack-of-60-sachets
    - shop.amul.com/en/product/amul-whey-protein-32-g-or-pack-of-60-sachets
    - amul-whey-protein-32-g-or-pack-of-60-sachets
    """
    cleaned = text.strip()
    # Match /product/<alias>
    match = re.search(r'/product/([a-zA-Z0-9_\-]+)', cleaned)
    if match:
        return match.group(1).rstrip("/")

    # Check if raw string is an alias slug
    slug = cleaned.strip("/").split("?")[0]
    if re.match(r'^[a-zA-Z0-9_\-]+$', slug) and "-" in slug:
        return slug

    return None

def resolve_amul_product(
    url_or_alias: str,
    substore_id: Optional[str] = None,
    cookies_path: Optional[str] = "cookies.json"
) -> Optional[Dict[str, Any]]:
    """
    Queries Amul StoreHippo API to resolve, validate, and extract real product details.
    Returns:
        dict with keys: name, sku, alias, price, url, available, inventory_quantity
        or None if invalid / not found.
    """
    alias = extract_alias_from_url(url_or_alias)
    if not alias:
        return None

    # Check if alias is already in curated catalog
    preset = get_product_by_alias(alias)

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-IN,en;q=0.9",
    })

    # Load session cookies if available
    if cookies_path and os.path.exists(cookies_path):
        try:
            with open(cookies_path, "r", encoding="utf-8") as f:
                c_data = json.load(f)
            for c in c_data:
                name = c.get("name")
                value = c.get("value")
                domain = c.get("domain", "shop.amul.com").lstrip(".")
                if name and value:
                    session.cookies.set(name, value, domain=domain)
        except Exception as e:
            logger.debug(f"Resolver cookies load note: {e}")

    # Fetch visitor TID from info.js
    session_tid = "default_session"
    try:
        resp = session.get(f"{BASE_DOMAIN}/user/info.js", timeout=8)
        if resp.status_code == 200:
            m = re.search(r'"tid"\s*:\s*"([^"]+)"', resp.text)
            if m:
                session_tid = m.group(1)
    except Exception as e:
        logger.debug(f"Error fetching TID for resolver: {e}")

    # Generate HMAC tid header
    rand_t = random.randint(0, 999)
    now_ts = int(time.time() * 1000)
    raw_str = f"{STORE_ID}:{now_ts}:{rand_t}:{session_tid}"
    tid_header = f"{now_ts}:{rand_t}:{hashlib.sha256(raw_str.encode('utf-8')).hexdigest()}"

    ref_url = f"{BASE_DOMAIN}/en/product/{alias}"
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Referer": ref_url,
        "base_url": ref_url,
        "frontend": "1",
        "tid": tid_header,
    }

    substore_param = f"&substore={substore_id}" if substore_id else ""
    query_url = (
        f"{BASE_DOMAIN}/api/1/entity/ms.products?"
        f"filters[0][field]=alias&filters[0][value][0]={alias}&filters[0][operator]=in&filters[0][original]=1"
        f"{substore_param}&v=6"
    )

    try:
        r = session.get(query_url, headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json().get("data", [])
            if data:
                item = data[0]
                name = item.get("name") or (preset["name"] if preset else alias)
                sku = item.get("sku") or (preset["sku"] if preset else "N/A")
                price = item.get("price") or (preset["price"] if preset else 0)
                available = bool(item.get("available") == 1 or item.get("available") is True)
                stock_qty = int(item.get("inventory_quantity") or 0)

                return {
                    "name": name,
                    "sku": sku,
                    "alias": alias,
                    "price": price,
                    "url": ref_url,
                    "available": available,
                    "inventory_quantity": stock_qty,
                    "in_stock": available and (stock_qty > 0),
                }
    except Exception as e:
        logger.error(f"Error resolving Amul product '{alias}': {e}")

    # If API call failed but alias is in preset, fall back to preset info
    if preset:
        return {
            "name": preset["full_name"],
            "sku": preset["sku"],
            "alias": preset["alias"],
            "price": preset["price"],
            "url": f"{BASE_DOMAIN}/en/product/{preset['alias']}",
            "available": False,
            "inventory_quantity": 0,
            "in_stock": False,
        }

    return None

def get_product_selection_keyboard(current_alias: Optional[str] = None) -> InlineKeyboardMarkup:
    """Generates the interactive Telegram keyboard for selecting curated products or pasting link."""
    keyboard = []
    # Display curated products in pairs
    items = list(CURATED_PRODUCTS.values())
    for i in range(0, len(items), 2):
        row = []
        p1 = items[i]
        label1 = f"✓ {p1['label']}" if current_alias and p1['alias'].lower() == current_alias.lower() else p1['label']
        row.append(InlineKeyboardButton(label1, callback_data=f"sel_prod_{p1['id']}"))
        
        if i + 1 < len(items):
            p2 = items[i + 1]
            label2 = f"✓ {p2['label']}" if current_alias and p2['alias'].lower() == current_alias.lower() else p2['label']
            row.append(InlineKeyboardButton(label2, callback_data=f"sel_prod_{p2['id']}"))
        keyboard.append(row)

    # Custom URL option & Back button
    keyboard.append([
        InlineKeyboardButton("🔗 Paste Custom Amul Link", callback_data="paste_product_link")
    ])
    keyboard.append([
        InlineKeyboardButton("↩️ Back to Dashboard", callback_data="status")
    ])
    return InlineKeyboardMarkup(keyboard)

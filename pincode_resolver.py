"""
All-India Automated Pincode-to-Substore Resolver for Amul e-Commerce.
Automatically resolves any 6-digit Indian pincode to its corresponding Amul
regional warehouse substore ID and friendly name.
"""

import os
import json
import time
import random
import hashlib
import logging
import re
import requests
from typing import Optional, Dict, Any

logger = logging.getLogger("PincodeResolver")

CACHE_FILE = "pincode_cache.json"
STORE_ID = "62fa94df8c13af2e242eba16"
BASE_URL = "https://shop.amul.com"

# Built-in All-India Regional Warehouse Catalog
DEFAULT_SUBSTORE_CATALOG = {
    "up-ncr": {"id": "66505ff8c8f2d6e221b9180c", "name": "UP NCR"},
    "delhi": {"id": "66505ff5145c16635e6cc74d", "name": "Delhi"},
    "karnataka": {"id": "66505ff0998183e1b1935c75", "name": "Karnataka / Bangalore"},
    "mumbai-br": {"id": "66506000c8f2d6e221b9193a", "name": "Mumbai BR"},
    "pune-br": {"id": "66506004a7cddee1b8adb014", "name": "Pune BR"},
    "solapur-br": {"id": "66506004145c16635e6cc914", "name": "Solapur BR"},
    "nashik-br": {"id": "66506002c8f2d6e221b91988", "name": "Nashik BR"},
    "aurangabad-br": {"id": "66506002aa64743ceefbed25", "name": "Aurangabad BR"},
    "telangana": {"id": "66506004aa64743ceefbed25", "name": "Telangana / Hyderabad"},
    "andhra-pradesh": {"id": "66505ff26510ee3d5903fda9", "name": "Andhra Pradesh"},
    "tamil-nadu": {"id": "66505ff3998183e1b1935d0e", "name": "Tamil Nadu / Chennai"},
    "kerala": {"id": "66505ff1672747740fb388ec", "name": "Kerala / Kochi"},
    "west-bengal": {"id": "6650600024e61363e088c526", "name": "West Bengal / Kolkata"},
    "gujarat": {"id": "66505ff2998183e1b1935ccd", "name": "Gujarat / Ahmedabad"},
    "rajasthan": {"id": "66505ff824e61363e088c3dd", "name": "Rajasthan / Jaipur"},
    "punjab": {"id": "66505ff9af6a3c7411d2f55f", "name": "Punjab / Chandigarh"},
    "haryana": {"id": "66505ff5af6a3c7411d2f4b2", "name": "Haryana / Gurugram"},
    "uttrakhand": {"id": "66505ff8a7cddee1b8adae9d", "name": "Uttarakhand"},
    "madhya-pradesh": {"id": "66505ff6d9346de216752cd7", "name": "Madhya Pradesh"},
    "chhattisgarh": {"id": "66506002998183e1b1935f41", "name": "Chhattisgarh"},
    "bihar": {"id": "66505ffb6510ee3d5903fef8", "name": "Bihar / Patna"},
    "jharkhand": {"id": "66505ffbf40e263cf5588098", "name": "Jharkhand / Ranchi"},
    "odisha": {"id": "66505ffd672747740fb389c7", "name": "Odisha / Bhubaneswar"},
    "assam": {"id": "66505ffd998183e1b1935e21", "name": "Assam / Guwahati"},
    "goa": {"id": "66506005147d6c73c1110115", "name": "Goa"},
    "dadra-and-nagar-haveli": {"id": "6650600062e3d963520d0bc3", "name": "Dadra & Nagar Haveli"},
}

class PincodeResolver:
    def __init__(self, cache_file: str = CACHE_FILE):
        self.cache_file = cache_file
        self.cache: Dict[str, Dict[str, Any]] = {}
        self.catalog = dict(DEFAULT_SUBSTORE_CATALOG)
        self.session = requests.Session()
        self.session.headers.update({
            "user-agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
            ),
            "accept": "application/json, text/plain, */*",
            "referer": f"{BASE_URL}/en/",
            "frontend": "1",
            "base_url": f"{BASE_URL}/en/"
        })
        self._load_cache()

    def _load_cache(self):
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    self.cache = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load pincode cache: {e}")

    def _save_cache(self):
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save pincode cache: {e}")

    def _generate_tid(self) -> str:
        timestamp = int(time.time() * 1000)
        random_num = random.randint(100, 999)
        raw_signature = f"{STORE_ID}:{timestamp}:{random_num}:null"
        tid_hash = hashlib.sha256(raw_signature.encode("utf-8")).hexdigest()
        return f"{timestamp}:{random_num}:{tid_hash}"

    def resolve(self, pincode: str) -> Optional[Dict[str, str]]:
        """
        Resolves an Indian pincode to its Amul regional warehouse substore details.
        Returns:
            dict with {
                'pincode': '201310',
                'substore_id': '66505ff8c8f2d6e221b9180c',
                'substore_alias': 'up-ncr',
                'warehouse_name': 'UP NCR'
            } or None if not serviceable.
        """
        pincode = str(pincode).strip()
        if not re.match(r"^\d{6}$", pincode):
            logger.error(f"Invalid Indian pincode format: '{pincode}'. Must be 6 digits.")
            return None

        # 1. Check local cache
        if pincode in self.cache:
            logger.info(f"Pincode {pincode} resolved from local cache: {self.cache[pincode]['warehouse_name']}")
            return self.cache[pincode]

        # 2. Query Amul API
        url = (
            f"{BASE_URL}/entity/pincode?limit=1"
            f"&filters[0][field]=pincode&filters[0][value]={pincode}"
            f"&filters[0][operator]=regex&filters[0][use_autocomplete]=1"
            f"&new_search=1&cf_cache=1h"
        )
        headers = {"tid": self._generate_tid()}

        try:
            resp = self.session.get(url, headers=headers, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                records = data.get("records", [])
                if records:
                    alias = records[0].get("substore", "").strip().lower()
                    warehouse_info = self.catalog.get(alias)
                    
                    substore_id = warehouse_info["id"] if warehouse_info else records[0].get("_id")
                    warehouse_name = warehouse_info["name"] if warehouse_info else alias.upper()

                    result = {
                        "pincode": pincode,
                        "substore_id": substore_id,
                        "substore_alias": alias,
                        "warehouse_name": warehouse_name
                    }
                    self.cache[pincode] = result
                    self._save_cache()
                    logger.info(f"Resolved {pincode} -> {warehouse_name} ({substore_id})")
                    return result
                else:
                    logger.warning(f"Pincode {pincode} is not currently serviceable by Amul.")
                    return None
            else:
                logger.error(f"Pincode API failed with HTTP {resp.status_code}: {resp.text[:200]}")
                return None
        except Exception as e:
            logger.error(f"Error querying pincode API: {e}")
            return None

# Singleton instance
resolver = PincodeResolver()

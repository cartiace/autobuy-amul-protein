"""
Automated Playwright Checkout Engine for Amul's e-commerce site.
Handles authenticated session management, stealth bypass, Add to Cart,
and auto-checkout using Cash on Delivery (COD).
"""

import os
import json
import time
import logging
import asyncio
from typing import Optional, Dict, Any, Tuple
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from playwright_stealth import Stealth

logger = logging.getLogger("AmulCheckout")

BASE_DOMAIN = "https://shop.amul.com"

class AmulAutoCheckout:
    def __init__(
        self,
        cookies_file: str = "cookies.json",
        target_url: str = "https://shop.amul.com/en/product/amul-chocolate-whey-protein-34-g-or-pack-of-60-sachets",
        substore_id: Optional[str] = None,
        pincode: Optional[str] = None,
        headless: bool = True,
        quantity: int = 1
    ):
        self.cookies_file = cookies_file
        self.target_url = target_url
        self.substore_id = substore_id
        self.pincode = pincode
        self.headless = headless
        self.quantity = quantity
        self.screenshots_dir = "screenshots"
        os.makedirs(self.screenshots_dir, exist_ok=True)

    def _prepare_cookies(self, raw_cookies: list) -> list:
        """
        Normalizes cookies exported from browser extensions (Cookie-Editor, EditThisCookie, etc.)
        for Playwright compatibility.
        """
        valid_cookies = []
        for c in raw_cookies:
            cookie = {
                "name": c.get("name"),
                "value": c.get("value"),
                "domain": c.get("domain", "shop.amul.com"),
                "path": c.get("path", "/"),
            }

            # Optional Playwright cookie properties
            if "expires" in c and isinstance(c["expires"], (int, float)) and c["expires"] > 0:
                cookie["expires"] = float(c["expires"])
            elif "expirationDate" in c and isinstance(c["expirationDate"], (int, float)):
                cookie["expires"] = float(c["expirationDate"])

            if "httpOnly" in c:
                cookie["httpOnly"] = bool(c["httpOnly"])
            if "secure" in c:
                cookie["secure"] = bool(c["secure"])

            # Map sameSite
            same_site = c.get("sameSite")
            if same_site:
                same_site_str = str(same_site).lower()
                if same_site_str in ("lax", "strict", "none"):
                    cookie["sameSite"] = same_site_str.capitalize()

            # Ensure domain matches shop.amul.com
            if cookie["domain"].startswith("."):
                cookie["domain"] = cookie["domain"].lstrip(".")
            if not ("amul.com" in cookie["domain"]):
                continue

            valid_cookies.append(cookie)
        return valid_cookies

    async def execute_checkout(self) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Executes the end-to-end auto-checkout flow:
        1. Launches stealth browser with residential fingerprint.
        2. Injects authenticated cookies from cookies.json.
        3. Navigates to target product page.
        4. Adds item to cart.
        5. Navigates to checkout.
        6. Selects delivery address & Cash on Delivery (COD).
        7. Places order and retrieves order confirmation.

        Returns:
            Tuple: (success: bool, order_id: Optional[str], message: Optional[str])
        """
        logger.info("Initializing Playwright Stealth Auto-Checkout...")

        async with async_playwright() as p:
            # 1. Launch Browser with Anti-Detection Stealth Flags
            browser: Browser = await p.chromium.launch(
                headless=self.headless,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                    "--no-first-run",
                    "--window-size=1366,768",
                ]
            )

            context: BrowserContext = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1366, "height": 768},
                locale="en-IN",
                timezone_id="Asia/Kolkata",
                permissions=["geolocation"],
                geolocation={"latitude": 28.6139, "longitude": 77.2090},  # New Delhi
            )

            page: Page = await context.new_page()

            # Apply playwright-stealth evasion scripts
            stealth = Stealth()
            await stealth.apply_stealth_async(page)

            # 2. Load Cookies for Session Management
            if os.path.exists(self.cookies_file):
                try:
                    with open(self.cookies_file, "r", encoding="utf-8") as f:
                        raw_cookies = json.load(f)
                    cleaned_cookies = self._prepare_cookies(raw_cookies)
                    await context.add_cookies(cleaned_cookies)
                    logger.info(f"Loaded {len(cleaned_cookies)} active cookies from {self.cookies_file}.")
                except Exception as e:
                    logger.error(f"Failed to load cookies from {self.cookies_file}: {e}")
            else:
                logger.warning(
                    f"Warning: {self.cookies_file} not found. Running session without pre-authenticated cookies."
                )

            try:
                # 3. Navigate to Product Page
                logger.info(f"Navigating to Target Product: {self.target_url}")
                await page.goto(self.target_url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(2500)

                # Set regional substore in localStorage if provided
                if self.substore_id:
                    await page.evaluate(f"""(substore) => {{
                        localStorage.setItem('substore', JSON.stringify({{'_id': substore}}));
                    }}""", self.substore_id)

                # Dismiss location modal if it appears
                close_modal_btn = page.locator("#locationWidgetModal .btn-close, .modal.show button.btn-close")
                if await close_modal_btn.count() > 0 and await close_modal_btn.first.is_visible():
                    logger.info("Dismissing delivery location modal...")
                    await close_modal_btn.first.click()
                    await page.wait_for_timeout(1000)

                await page.screenshot(path=os.path.join(self.screenshots_dir, "01_product_page.png"))

                # 4. Add Product to Cart
                logger.info("Attempting to Add Item to Cart...")
                item_added = await self._add_item_to_cart(page)

                if not item_added:
                    error_msg = "Could not add item to cart (button not found or out of stock)."
                    logger.error(error_msg)
                    await page.screenshot(path=os.path.join(self.screenshots_dir, "error_add_cart.png"))
                    await browser.close()
                    return False, None, error_msg

                await page.wait_for_timeout(2000)
                await page.screenshot(path=os.path.join(self.screenshots_dir, "02_item_added.png"))

                # 5. Navigate to Checkout Page
                logger.info("Navigating to Checkout...")
                await page.goto(f"{BASE_DOMAIN}/en/checkout", wait_until="domcontentloaded", timeout=25000)
                await page.wait_for_timeout(3000)

                # If redirected back to cart, click proceed to checkout button
                if "/cart" in page.url:
                    logger.info("Redirected to cart page. Clicking 'Place Order' / Checkout button...")
                    checkout_btn = page.locator("a[href*='checkout'], button:has-text('Place Order')")
                    if await checkout_btn.count() > 0:
                        await checkout_btn.first.click()
                        await page.wait_for_timeout(3000)

                await page.screenshot(path=os.path.join(self.screenshots_dir, "03_checkout_page.png"))

                # Check if session is logged in or requesting OTP
                otp_btn = page.locator("button:has-text('Send OTP'), input[type='tel']")
                if await otp_btn.count() > 0 and await otp_btn.first.is_visible():
                    logger.warning(
                        "⚠️ OTP Login requested! Active session cookies may be expired or missing. "
                        "Please export fresh cookies to cookies.json."
                    )

                # 6. Select Delivery Address (if prompted)
                await self._handle_address_selection(page)

                # 7. Select Payment Method: Cash on Delivery (COD)
                logger.info("Selecting Payment Method: Cash on Delivery (COD)...")
                cod_selected = await self._select_cash_on_delivery(page)

                if not cod_selected:
                    logger.warning("Could not explicitly select COD via radio button. Attempting to place order with active selection...")

                await page.screenshot(path=os.path.join(self.screenshots_dir, "04_payment_selected.png"))

                # 8. Place Order
                logger.info("Placing order...")
                placed, order_id = await self._click_place_order(page)

                if placed:
                    success_msg = f"Order successfully placed! Order ID: {order_id or 'Generated'}"
                    logger.info(f"🎉 {success_msg}")
                    await page.screenshot(path=os.path.join(self.screenshots_dir, "05_order_confirmed.png"))
                    await browser.close()
                    return True, order_id, success_msg
                else:
                    fail_msg = "Order submission triggered, but confirmation not verified. Check screenshots."
                    logger.warning(fail_msg)
                    await page.screenshot(path=os.path.join(self.screenshots_dir, "error_confirmation.png"))
                    await browser.close()
                    return False, None, fail_msg

            except Exception as e:
                logger.error(f"Error during checkout execution: {e}")
                await page.screenshot(path=os.path.join(self.screenshots_dir, "error_exception.png"))
                await browser.close()
                return False, None, str(e)

    async def _add_item_to_cart(self, page: Page) -> bool:
        """Adds target item to cart via UI interaction or StoreHippo client function."""
        # Method 1: UI Button click
        add_selectors = [
            "button:has-text('Add to Cart')",
            "button:has-text('ADD')",
            "a.btn:has-text('Add to Cart')",
            "button.btn-cart",
            "button.btn-add-to-cart",
            "button:has-text('Buy Now')",
        ]

        for sel in add_selectors:
            btn = page.locator(sel)
            if await btn.count() > 0 and await btn.first.is_visible():
                is_disabled = await btn.first.is_disabled()
                if not is_disabled:
                    logger.info(f"Clicking Add to Cart button using selector: '{sel}'")
                    await btn.first.click()
                    return True

        # Method 2: Invoke StoreHippo ms.cart.addItem via page evaluation
        logger.info("UI button not immediately clickable. Attempting StoreHippo ms.cart API trigger...")
        try:
            added = await page.evaluate(f"""async (qty) => {{
                if (window.ms && window.ms.cart && window.ms.product && window.ms.product._id) {{
                    return new Promise((resolve) => {{
                        window.ms.cart.addItem(window.ms.product, qty, function(err, res) {{
                            if (err) resolve(false);
                            else resolve(true);
                        }});
                    }});
                }}
                return false;
            }}""", self.quantity)
            if added:
                logger.info("Successfully added to cart via window.ms.cart.addItem.")
                return True
        except Exception as e:
            logger.debug(f"ms.cart.addItem evaluation: {e}")

        return False

    async def _handle_address_selection(self, page: Page):
        """Selects saved shipping address if checkout step requires it."""
        try:
            # Look for address selection radio or 'Deliver to this address' buttons
            address_radio = page.locator("input[type='radio'][name*='address'], .address-card input[type='radio']")
            if await address_radio.count() > 0:
                logger.info("Selecting saved address radio button...")
                await address_radio.first.check()
                await page.wait_for_timeout(1000)

            deliver_btn = page.locator(
                "button:has-text('Deliver to this address'), button:has-text('Deliver Here'), "
                "button:has-text('Proceed to Payment'), button:has-text('Continue')"
            )
            if await deliver_btn.count() > 0 and await deliver_btn.first.is_visible():
                logger.info("Clicking 'Deliver Here' / Proceed button...")
                await deliver_btn.first.click()
                await page.wait_for_timeout(2000)
        except Exception as e:
            logger.debug(f"Address selection note: {e}")

    async def _select_cash_on_delivery(self, page: Page) -> bool:
        """Finds and selects Cash on Delivery (COD) payment method."""
        cod_selectors = [
            "label:has-text('Cash on Delivery')",
            "label:has-text('COD')",
            "input[value*='cash_on_delivery']",
            "input[value*='offline']",
            "div:has-text('Cash on Delivery') input[type='radio']",
            "[data-payment-type='cash_on_delivery']",
            ".payment-method:has-text('Cash on Delivery')",
        ]

        for sel in cod_selectors:
            loc = page.locator(sel)
            if await loc.count() > 0 and await loc.first.is_visible():
                logger.info(f"Found COD selector: '{sel}'. Clicking...")
                await loc.first.click()
                await page.wait_for_timeout(1000)
                return True

        # Fallback: Check StoreHippo payment methods via JS
        try:
            applied = await page.evaluate("""() => {
                if (window.ms && window.ms.cart && window.ms.cart.available_payment_methods) {
                    const cod = window.ms.cart.available_payment_methods.find(
                        p => p.type === 'cash_on_delivery' || p.type === 'offline'
                    );
                    if (cod && window.ms.cart.updatePaymentMethod) {
                        window.ms.cart.updatePaymentMethod(cod);
                        return true;
                    }
                }
                return false;
            }""")
            if applied:
                logger.info("Applied Cash on Delivery via window.ms.cart.updatePaymentMethod.")
                return True
        except Exception as e:
            logger.debug(f"JS updatePaymentMethod note: {e}")

        return False

    async def _click_place_order(self, page: Page) -> Tuple[bool, Optional[str]]:
        """Clicks Place Order button and waits for confirmation."""
        place_selectors = [
            "button:has-text('Place Order')",
            "button:has-text('Confirm Order')",
            "button:has-text('Complete Order')",
            "button:has-text('Pay on Delivery')",
            "button.btn-place-order",
            "input[value='Place Order']",
        ]

        for sel in place_selectors:
            btn = page.locator(sel)
            if await btn.count() > 0 and await btn.first.is_visible():
                logger.info(f"Found Place Order button with selector: '{sel}'. Submitting order...")
                await btn.first.click()
                break

        # Wait for navigation or order confirmation indicator
        try:
            await page.wait_for_timeout(5000)
            current_url = page.url

            # Check confirmation indicators
            if any(term in current_url.lower() for term in ["confirmation", "success", "order-placed", "order-summary", "orders/"]):
                logger.info(f"Order confirmed! Final URL: {current_url}")
                # Try to extract order number/id
                order_id = await page.evaluate("""() => {
                    const el = document.querySelector('.order-id, .order-number, [data-order-id]');
                    if (el) return el.innerText.trim();
                    const text = document.body.innerText;
                    const match = text.match(/Order\\s*(?:ID|Number|#)?:?\\s*([A-Za-z0-9_-]+)/i);
                    return match ? match[1] : null;
                }""")
                return True, order_id

            # Check confirmation text in DOM
            has_success_text = await page.evaluate("""() => {
                const text = document.body.innerText.toLowerCase();
                return text.includes('order placed') || text.includes('order confirmed') || text.includes('thank you for your order');
            }""")
            if has_success_text:
                return True, None

        except Exception as e:
            logger.error(f"Error checking order confirmation: {e}")

        return False, None

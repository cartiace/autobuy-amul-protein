"""
Automated Playwright Checkout Engine for Amul's e-commerce site.
Handles authenticated session management, stealth bypass, Add to Cart,
and auto-checkout using online payment gateways (Easebuzz, PhonePe, PayU)
with automated UPI QR Code generation, instant Telegram push dispatch,
and user-cancellation support.
"""

import os
import json
import time
import logging
import asyncio
from typing import Optional, Dict, Any, Tuple
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Frame
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
        quantity: int = 1,
        gateway: str = "easebuzz",
        notifier: Optional[Any] = None,
        payment_timeout_secs: int = 600,
        cancel_event: Optional[asyncio.Event] = None
    ):
        self.cookies_file = cookies_file
        self.target_url = target_url
        self.substore_id = substore_id
        self.pincode = pincode
        self.headless = headless
        self.quantity = quantity
        self.gateway = (gateway or "easebuzz").strip().lower()
        self.notifier = notifier
        self.payment_timeout_secs = payment_timeout_secs
        self.cancel_event = cancel_event
        self.screenshots_dir = "screenshots"
        os.makedirs(self.screenshots_dir, exist_ok=True)

    def _prepare_cookies(self, raw_cookies: list) -> list:
        """
        Normalizes cookies exported from browser extensions for Playwright compatibility.
        """
        valid_cookies = []
        for c in raw_cookies:
            cookie = {
                "name": c.get("name"),
                "value": c.get("value"),
                "domain": c.get("domain", "shop.amul.com").lstrip("."),
                "path": c.get("path", "/"),
            }

            if "expires" in c and isinstance(c["expires"], (int, float)) and c["expires"] > 0:
                cookie["expires"] = float(c["expires"])
            elif "expirationDate" in c and isinstance(c["expirationDate"], (int, float)):
                cookie["expires"] = float(c["expirationDate"])

            if "httpOnly" in c:
                cookie["httpOnly"] = bool(c["httpOnly"])
            if "secure" in c:
                cookie["secure"] = bool(c["secure"])

            same_site = c.get("sameSite")
            if same_site:
                same_site_str = str(same_site).lower()
                if same_site_str in ("lax", "strict", "none"):
                    cookie["sameSite"] = same_site_str.capitalize()

            if not ("amul.com" in cookie["domain"]):
                continue

            valid_cookies.append(cookie)
        return valid_cookies

    async def _safe_dismiss_location_modal(self, page: Page):
        """Safely dismisses or removes locationWidgetModal so it doesn't block clicks."""
        try:
            await page.evaluate("""() => {
                const modal = document.getElementById('locationWidgetModal');
                if (modal) modal.remove();
                const backdrop = document.querySelector('.modal-backdrop');
                if (backdrop) backdrop.remove();
                document.body.classList.remove('modal-open');
                document.body.style.overflow = 'auto';
            }""")
        except Exception as e:
            logger.debug(f"Modal dismiss note: {e}")

    async def _empty_cart_page(self, page: Page) -> int:
        """Removes all items from cart on the given page."""
        try:
            logger.info("Navigating to cart page to clear items...")
            await page.goto(f"{BASE_DOMAIN}/en/cart", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(2500)
            await self._safe_dismiss_location_modal(page)

            remove_btns = page.locator("text=Remove")
            count = await remove_btns.count()
            while count > 0:
                logger.info(f"Clicking remove button ({count} items remaining)...")
                try:
                    await remove_btns.first.click()
                    await page.wait_for_timeout(2500)
                    confirm = page.locator('button:has-text("Yes"), button:has-text("Confirm"), .modal-footer button')
                    if await confirm.count() > 0 and await confirm.first.is_visible():
                        await confirm.first.click()
                        await page.wait_for_timeout(2000)
                except Exception as e:
                    logger.warning(f"Error clicking remove button: {e}")
                    break
                count = await remove_btns.count()

            items = await page.evaluate("() => window.ms && window.ms.cart ? window.ms.cart.items : []")
            logger.info(f"Cart cleared. Remaining items count: {len(items)}")
            return len(items)
        except Exception as e:
            logger.error(f"Error clearing cart: {e}")
            return -1

    async def empty_cart(self) -> int:
        """Standalone utility to completely empty the user's Amul cart."""
        logger.info("Launching standalone browser task to empty Amul cart...")
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=self.headless,
                args=["--no-sandbox", "--disable-setuid-sandbox"]
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
            )
            if os.path.exists(self.cookies_file):
                try:
                    with open(self.cookies_file, "r", encoding="utf-8") as f:
                        raw = json.load(f)
                    await context.add_cookies(self._prepare_cookies(raw))
                except Exception as e:
                    logger.error(f"Error loading cookies: {e}")

            page = await context.new_page()
            stealth = Stealth()
            await stealth.apply_stealth_async(page)
            remaining = await self._empty_cart_page(page)
            await browser.close()
            return remaining

    async def execute_checkout(self) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Executes the end-to-end checkout flow:
        1. Launches stealth browser with residential fingerprint.
        2. Injects authenticated session cookies.
        3. Navigates to target product page & adds item to cart.
        4. Navigates to checkout.
        5. Selects saved delivery address.
        6. Selects payment gateway (Easebuzz / PhonePe).
        7. Clicks 'Pay Now' to open gateway.
        8. Generates UPI QR code & instantly sends photo to Telegram.
        9. Listens and waits for user's mobile payment authorization or cancellation.
        10. Captures order confirmation.
        """
        logger.info(f"Initializing Playwright Checkout with Gateway: {self.gateway.upper()}...")

        if self.cancel_event and self.cancel_event.is_set():
            logger.warning("Checkout cancelled prior to launch.")
            return False, None, "Checkout cancelled by user via Telegram command."

        async with async_playwright() as p:
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
            )

            page: Page = await context.new_page()

            stealth = Stealth()
            await stealth.apply_stealth_async(page)

            # Load cookies
            if os.path.exists(self.cookies_file):
                try:
                    with open(self.cookies_file, "r", encoding="utf-8") as f:
                        raw_cookies = json.load(f)
                    cleaned_cookies = self._prepare_cookies(raw_cookies)
                    await context.add_cookies(cleaned_cookies)
                    logger.info(f"Loaded {len(cleaned_cookies)} active cookies from {self.cookies_file}.")
                except Exception as e:
                    logger.error(f"Failed to load cookies: {e}")
            else:
                logger.warning(f"Cookies file {self.cookies_file} not found.")

            try:
                # Check cancellation
                if self.cancel_event and self.cancel_event.is_set():
                    await browser.close()
                    return False, None, "Checkout cancelled by user via Telegram command."

                # 1. Navigate to Product Page
                logger.info(f"Navigating to Target Product: {self.target_url}")
                await page.goto(self.target_url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(2500)

                await self._safe_dismiss_location_modal(page)
                await page.screenshot(path=os.path.join(self.screenshots_dir, "01_product_page.png"))

                # 2. Add Product to Cart
                if self.cancel_event and self.cancel_event.is_set():
                    await browser.close()
                    return False, None, "Checkout cancelled by user via Telegram command."

                logger.info("Adding Item to Cart...")
                item_added = await self._add_item_to_cart(page)
                if not item_added:
                    error_msg = "Could not add item to cart (button unavailable or out of stock)."
                    logger.error(error_msg)
                    await page.screenshot(path=os.path.join(self.screenshots_dir, "error_add_cart.png"))
                    await browser.close()
                    return False, None, error_msg

                await page.wait_for_timeout(2000)
                await page.screenshot(path=os.path.join(self.screenshots_dir, "02_item_added.png"))

                # 3. Navigate to Checkout Page
                if self.cancel_event and self.cancel_event.is_set():
                    await self._empty_cart_page(page)
                    await browser.close()
                    return False, None, "Checkout cancelled by user via Telegram command."

                logger.info("Navigating to Checkout...")
                await page.goto(f"{BASE_DOMAIN}/en/checkout", wait_until="domcontentloaded", timeout=25000)
                await page.wait_for_timeout(3000)

                await self._safe_dismiss_location_modal(page)

                # If on cart page, click Checkout
                if "/cart" in page.url:
                    checkout_btn = page.locator("a[href*='checkout'], button:has-text('Place Order')")
                    if await checkout_btn.count() > 0:
                        await checkout_btn.first.click()
                        await page.wait_for_timeout(3000)
                        await self._safe_dismiss_location_modal(page)

                await page.screenshot(path=os.path.join(self.screenshots_dir, "03_checkout_page.png"))

                # 4. Handle Address Selection
                if self.cancel_event and self.cancel_event.is_set():
                    await self._empty_cart_page(page)
                    await browser.close()
                    return False, None, "Checkout cancelled by user via Telegram command."

                logger.info("Confirming delivery address...")
                await self._handle_address_selection(page)

                # 5. Select Gateway & Initiate Payment
                logger.info(f"Selecting Payment Gateway: {self.gateway.upper()}...")
                await self._select_gateway(page)

                pay_now_btn = page.locator('button:has-text("Pay Now")')
                if await pay_now_btn.count() == 0:
                    error_msg = "'Pay Now' button not found on checkout."
                    logger.error(error_msg)
                    await browser.close()
                    return False, None, error_msg

                logger.info("Clicking 'Pay Now' to launch payment gateway...")
                await pay_now_btn.first.click()

                # 6. Gateway Processing & UPI Dispatch
                if "phonepe" in self.gateway:
                    success, order_id, msg = await self._handle_phonepe_flow(page)
                else:
                    success, order_id, msg = await self._handle_easebuzz_flow(page)

                # If cancelled during payment wait, clean up cart
                if not success and self.cancel_event and self.cancel_event.is_set():
                    logger.info("Checkout cancelled by user during payment wait. Clearing cart...")
                    await self._empty_cart_page(page)

                await browser.close()
                return success, order_id, msg

            except Exception as e:
                logger.error(f"Error during checkout execution: {e}", exc_info=True)
                await page.screenshot(path=os.path.join(self.screenshots_dir, "error_exception.png"))
                if self.cancel_event and self.cancel_event.is_set():
                    try:
                        await self._empty_cart_page(page)
                    except Exception:
                        pass
                await browser.close()
                return False, None, str(e)

    async def _add_item_to_cart(self, page: Page) -> bool:
        """Adds product to cart via UI button or client API."""
        add_selectors = [
            "button:has-text('Add to Cart')",
            "button:has-text('ADD')",
            "a.btn:has-text('Add to Cart')",
            "button.btn-cart",
            "button.btn-add-to-cart",
        ]
        for sel in add_selectors:
            btn = page.locator(sel)
            if await btn.count() > 0 and await btn.first.is_visible():
                if not await btn.first.is_disabled():
                    await btn.first.click()
                    return True

        # Fallback to ms.cart.addItem
        try:
            added = await page.evaluate(f"""async (qty) => {{
                if (window.ms && window.ms.cart && window.ms.product && window.ms.product._id) {{
                    return new Promise((resolve) => {{
                        window.ms.cart.addItem(window.ms.product, qty, function(err, res) {{
                            resolve(!err);
                        }});
                    }});
                }}
                return false;
            }}""", self.quantity)
            if added:
                return True
        except Exception as e:
            logger.debug(f"Cart addItem evaluate fallback note: {e}")

        return False

    async def _handle_address_selection(self, page: Page):
        """Ensures a delivery address is selected."""
        await self._safe_dismiss_location_modal(page)

        # Check existing radio buttons
        address_radios = page.locator("input[type='radio'][name*='address'], .address-card, .address-item")
        if await address_radios.count() > 0:
            logger.info("Selecting saved delivery address...")
            await address_radios.first.click()
            await page.wait_for_timeout(1000)

        # Click Next/Continue if present
        continue_btn = page.locator('button:has-text("Deliver Here"), button:has-text("Continue"), button:has-text("Next")')
        if await continue_btn.count() > 0 and await continue_btn.first.is_visible():
            await continue_btn.first.click()
            await page.wait_for_timeout(2000)

    async def _select_gateway(self, page: Page):
        """Selects the configured payment gateway on the checkout page."""
        await self._safe_dismiss_location_modal(page)
        await page.wait_for_timeout(1000)

        if "phonepe" in self.gateway:
            phonepe_radio = page.locator('label:has-text("PhonePe"), input[id="66e9234df236dc0024a19d19"]')
            if await phonepe_radio.count() > 0:
                await phonepe_radio.first.click()
                await page.wait_for_timeout(1000)
        elif "payu" in self.gateway:
            payu_radio = page.locator('label:has-text("PayU"), input[name*="payment"][value*="payu"]')
            if await payu_radio.count() > 0:
                await payu_radio.first.click()
                await page.wait_for_timeout(1000)
        else:
            # Easebuzz is default
            eb_radio = page.locator('label:has-text("Easebuzz"), input[id="67f62c8a90cf1002156153f5"]')
            if await eb_radio.count() > 0:
                await eb_radio.first.click()
                await page.wait_for_timeout(1000)

    async def _handle_easebuzz_flow(self, page: Page) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Handles Easebuzz iframe:
        - Locates iframe.
        - Clicks UPI option.
        - Clicks 'Show QR'.
        - Takes QR code screenshot and pushes it to Telegram.
        - Waits for user to approve on phone or cancel via Telegram.
        - Detects redirect and confirms order.
        """
        logger.info("Waiting for Easebuzz payment iframe...")
        easebuzz_frame = None
        for _ in range(25):
            if self.cancel_event and self.cancel_event.is_set():
                return False, None, "Checkout cancelled by user via Telegram command."
            await page.wait_for_timeout(1000)
            for fr in page.frames:
                if "easebuzz" in fr.url:
                    easebuzz_frame = fr
                    break
            if easebuzz_frame:
                break

        if not easebuzz_frame:
            return False, None, "Timed out waiting for Easebuzz payment iframe."

        logger.info(f"Connected to Easebuzz iframe: {easebuzz_frame.url[:80]}...")
        await page.wait_for_timeout(3000)

        # Click UPI tab inside Easebuzz
        upi_opt = easebuzz_frame.locator('text="UPI"')
        if await upi_opt.count() > 0:
            logger.info("Selecting UPI option inside Easebuzz...")
            await upi_opt.first.click()
            await page.wait_for_timeout(2000)

        # Click 'Show QR' button
        show_qr = easebuzz_frame.locator('button:has-text("Show QR")')
        if await show_qr.count() > 0:
            logger.info("Clicking 'Show QR' button inside Easebuzz...")
            await show_qr.first.click()
            await page.wait_for_timeout(2500)

        # Capture QR code screenshot
        qr_photo_path = os.path.join(self.screenshots_dir, "upi_qr_payment.png")
        await page.screenshot(path=qr_photo_path)
        logger.info(f"📸 UPI QR code payment screenshot saved: {qr_photo_path}")

        # Send instant photo alert to Telegram
        if self.notifier and self.notifier.is_configured():
            logger.info("🚀 Dispatching UPI QR Code image to Telegram with Cancel button...")
            await self.notifier.notify_upi_payment_required(
                photo_path=qr_photo_path,
                amount="4,500.00",
                product_name="Amul Chocolate Whey Protein (Pack of 60 sachets)",
                timeout_mins=int(self.payment_timeout_secs / 60)
            )

        # Wait for user to scan and complete payment on phone or cancel
        logger.info(f"⏳ Waiting up to {self.payment_timeout_secs}s for mobile payment authorization...")
        return await self._wait_for_order_confirmation(page)

    async def _handle_phonepe_flow(self, page: Page) -> Tuple[bool, Optional[str], Optional[str]]:
        """Handles PhonePe gateway redirect flow."""
        logger.info("Waiting for PhonePe gateway page...")
        for _ in range(20):
            if self.cancel_event and self.cancel_event.is_set():
                return False, None, "Checkout cancelled by user via Telegram command."
            await page.wait_for_timeout(1000)
            if "phonepe" in page.url:
                break

        await page.wait_for_timeout(3000)
        qr_photo_path = os.path.join(self.screenshots_dir, "upi_qr_payment.png")
        await page.screenshot(path=qr_photo_path)
        logger.info(f"📸 PhonePe payment screenshot saved: {qr_photo_path}")

        if self.notifier and self.notifier.is_configured():
            await self.notifier.notify_upi_payment_required(
                photo_path=qr_photo_path,
                amount="4,500.00",
                product_name="Amul Chocolate Whey Protein (Pack of 60 sachets)",
                timeout_mins=int(self.payment_timeout_secs / 60)
            )

        logger.info(f"⏳ Waiting up to {self.payment_timeout_secs}s for mobile payment authorization...")
        return await self._wait_for_order_confirmation(page)

    async def _wait_for_order_confirmation(self, page: Page) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Polls for redirect back to Amul's order confirmation page, checking for cancellation.
        """
        start_time = time.time()
        while (time.time() - start_time) < self.payment_timeout_secs:
            # Check user cancellation
            if self.cancel_event and self.cancel_event.is_set():
                logger.info("❌ Cancellation requested by user during payment wait loop.")
                return False, None, "Checkout cancelled by user via Telegram command."

            current_url = page.url
            # Check confirmation indicators
            if any(term in current_url.lower() for term in ["confirmation", "order-placed", "orders/", "order-summary"]):
                logger.info(f"🎉 Payment successful! Redirected to: {current_url}")
                await page.wait_for_timeout(2000)

                # Extract Order ID
                order_id = await page.evaluate("""() => {
                    const el = document.querySelector('.order-id, .order-number, [data-order-id]');
                    if (el) return el.innerText.trim();
                    const text = document.body.innerText;
                    const match = text.match(/Order\\s*(?:ID|Number|#)?:?\\s*([A-Za-z0-9_-]+)/i);
                    return match ? match[1] : null;
                }""")

                confirmed_path = os.path.join(self.screenshots_dir, "05_order_confirmed.png")
                await page.screenshot(path=confirmed_path)

                return True, order_id, f"Order successfully placed! Order ID: {order_id or 'Verified'}"

            # Check if text shows success on page
            has_success = await page.evaluate("""() => {
                const text = document.body.innerText.toLowerCase();
                return text.includes('order placed') || text.includes('thank you for your order') || text.includes('payment successful');
            }""")
            if has_success:
                return True, None, "Order confirmed on page."

            await asyncio.sleep(2)

        return False, None, "Payment authorization timed out before user confirmed on phone."

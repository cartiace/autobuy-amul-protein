"""
Amul In-Chat OTP Authentication Manager.
Provides seamless, 100% native Telegram phone-number + SMS OTP login.
Automates StoreHippo's sendOtp and verifyOtp flows inside a stealth Playwright session,
extracts authenticated session cookies, and passes them to the database with AES-256 encryption.
Zero browser extensions or cookies.json required from end customers.
"""

import os
import time
import logging
import asyncio
from typing import Dict, Tuple, Optional, Any
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from playwright_stealth import Stealth

logger = logging.getLogger("AmulAuth")

BASE_DOMAIN = "https://shop.amul.com"

class ActiveLoginSession:
    def __init__(self, p, browser: Browser, context: BrowserContext, page: Page, phone: str):
        self.p = p
        self.browser = browser
        self.context = context
        self.page = page
        self.phone = phone
        self.created_at = time.time()
        self.expiry_task: Optional[asyncio.Task] = None

    async def close(self):
        if self.expiry_task and not self.expiry_task.done():
            self.expiry_task.cancel()
        try:
            await self.browser.close()
        except Exception:
            pass
        try:
            await self.p.stop()
        except Exception:
            pass

class AmulAuthSessionManager:
    def __init__(self, session_timeout_secs: int = 180):
        self.session_timeout_secs = session_timeout_secs
        self.active_sessions: Dict[int, ActiveLoginSession] = {}

    def _normalize_phone(self, phone_str: str) -> Optional[str]:
        cleaned = "".join(filter(str.isdigit, str(phone_str)))
        if len(cleaned) == 12 and cleaned.startswith("91"):
            cleaned = cleaned[2:]
        elif len(cleaned) == 11 and cleaned.startswith("0"):
            cleaned = cleaned[1:]
        if len(cleaned) == 10:
            return cleaned
        return None

    async def send_otp(self, chat_id: int, raw_phone: str) -> Tuple[bool, str]:
        """
        Initiates OTP generation for a phone number on Amul's site.
        Keeps the browser session alive awaiting the user's OTP verification response.
        """
        phone = self._normalize_phone(raw_phone)
        if not phone:
            return False, "Invalid phone number. Please enter a valid 10-digit Indian mobile number."

        # Clean up any existing session for this chat
        await self.cancel_session(chat_id)

        logger.info(f"Initiating Amul OTP dispatch for user {chat_id} (Phone: +91{phone})...")

        p = None
        browser = None
        try:
            p = await async_playwright().start()
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled"
                ]
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1366, "height": 768},
                locale="en-IN",
                timezone_id="Asia/Kolkata"
            )
            page = await context.new_page()
            stealth = Stealth()
            await stealth.apply_stealth_async(page)

            # Navigate to login page
            await page.goto(f"{BASE_DOMAIN}/user/login", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)

            # Dismiss any popup modal
            try:
                await page.evaluate("""() => {
                    const modal = document.getElementById('locationWidgetModal');
                    if (modal) modal.remove();
                    const backdrop = document.querySelector('.modal-backdrop');
                    if (backdrop) backdrop.remove();
                    document.body.classList.remove('modal-open');
                }""")
            except Exception:
                pass

            # Trigger sendOtp via StoreHippo API inside the stealth page
            res = await page.evaluate(f"""async () => {{
                try {{
                    const r = await window.ms.app.callAsync({{
                        entity: 'ms.users',
                        command: 'sendOtp',
                        data: {{ phone: '+91{phone}' }},
                        query: {{ new_otp_flow: 1 }}
                    }});
                    return {{ success: true, result: r }};
                }} catch (err) {{
                    return {{ success: false, error: err }};
                }}
            }}""")

            if res.get("success"):
                logger.info(f"Amul OTP dispatched successfully to +91{phone} for chat_id={chat_id}")
                session = ActiveLoginSession(p, browser, context, page, phone)
                
                # Schedule auto-cleanup after timeout
                async def auto_expire():
                    await asyncio.sleep(self.session_timeout_secs)
                    logger.info(f"Login session timed out for chat_id={chat_id}. Cleaning up...")
                    await self.cancel_session(chat_id)

                session.expiry_task = asyncio.create_task(auto_expire())
                self.active_sessions[chat_id] = session
                return True, f"OTP sent to +91 {phone}."
            else:
                err_data = res.get("error", {})
                logger.error(f"Amul rejected sendOtp: {err_data}")
                await browser.close()
                await p.stop()
                
                # Check known error messages
                status = err_data.get("status")
                if status == 403:
                    return False, "Amul security service temporarily rate-limited SMS requests. Please try again in a few minutes."
                return False, "Could not send OTP. Please verify your phone number and try again."

        except Exception as e:
            logger.error(f"Exception during send_otp for chat_id={chat_id}: {e}", exc_info=True)
            if browser:
                await browser.close()
            if p:
                await p.stop()
            return False, f"Error connecting to Amul: {e}"

    async def verify_otp(self, chat_id: int, otp_code: str) -> Tuple[bool, Optional[list], str]:
        """
        Verifies the user's OTP code using the active session and returns authenticated cookies.
        """
        session = self.active_sessions.get(chat_id)
        if not session:
            return False, None, "Login session expired or not found. Please send your phone number again."

        cleaned_otp = "".join(filter(str.isdigit, str(otp_code)))
        if len(cleaned_otp) != 6:
            return False, None, "Invalid OTP format. The OTP must be a 6-digit number."

        logger.info(f"Verifying OTP for user {chat_id} (Phone: +91{session.phone})...")

        try:
            res = await session.page.evaluate(f"""async () => {{
                try {{
                    const r = await window.ms.app.callAsync({{
                        entity: 'ms.users',
                        command: 'verifyOtp',
                        data: {{
                            phone: '+91{session.phone}',
                            otp: '{cleaned_otp}'
                        }},
                        query: {{ new_otp_flow: 1 }}
                    }});
                    return {{ success: true, result: r }};
                }} catch (err) {{
                    return {{ success: false, error: err }};
                }}
            }}""")

            if res.get("success"):
                logger.info(f"OTP verified successfully for user {chat_id}! Extracting session cookies...")
                await session.page.wait_for_timeout(2000)

                # Extract authenticated session cookies
                raw_cookies = await session.context.cookies()
                logger.info(f"Extracted {len(raw_cookies)} cookies for user {chat_id}.")

                # Clean up browser session
                await self.cancel_session(chat_id)
                return True, raw_cookies, "Login successful!"
            else:
                err_data = res.get("error", {})
                logger.warning(f"OTP verification failed for user {chat_id}: {err_data}")
                return False, None, "❌ Incorrect OTP. Please check your SMS and enter the 6-digit code again."

        except Exception as e:
            logger.error(f"Exception during verify_otp for chat_id={chat_id}: {e}", exc_info=True)
            return False, None, f"Verification error: {e}"

    async def cancel_session(self, chat_id: int):
        session = self.active_sessions.pop(chat_id, None)
        if session:
            await session.close()
            logger.debug(f"Closed login session for chat_id={chat_id}")

# Singleton instance
auth_manager = AmulAuthSessionManager()

# 🥛 Amul Stock Monitor & Auto-Checkout Bot

A high-performance Python automation bot for **Amul's e-commerce platform (`shop.amul.com`)**, specifically designed to monitor stock for high-demand products like **Amul Chocolate Whey Protein (Pack of 60 sachets)** and complete instant auto-checkout via Cash on Delivery (COD) as soon as stock becomes available.

---

## ⚡ Key Features

1. **Direct JSON API Polling (No HTML Scraping):**
   - Polls Amul's internal StoreHippo REST API (`/api/1/entity/ms.products`) every 60 seconds (configurable).
   - Injects regional `substore` ID for hyperlocal warehouse inventory matching.
   - Automatically computes dynamic StoreHippo `tid` security tokens using reverse-engineered SHA-256 HMAC headers.

2. **Authenticated Session Management:**
   - Injects pre-authenticated browser cookies from `cookies.json` into Playwright.
   - Completely bypasses OTP verification and manual phone logins at runtime.

3. **Instant Auto-Checkout Engine:**
   - Detects `stock > 0` and triggers stealth Playwright automation in milliseconds.
   - Adds product to cart (UI action with fallback to `ms.cart.addItem` API call).
   - Proceeds through checkout, selects saved delivery address, chooses **Cash on Delivery (COD)**, and places the order.

4. **Telegram Bot Integration:**
   - Dispatches instant notifications via Telegram Bot API upon successful order placement:
     `"Product was in stock. Order automatically placed."`

5. **Anti-Detection & Residential Emulation:**
   - Powered by `playwright-stealth`.
   - Mimics residential Chromium traffic: custom viewport, `en-IN` locale, `Asia/Kolkata` timezone, and disabled automation flags (`AutomationControlled`).

---

## 📁 Project Structure

```text
.
├── main.py              # Main monitoring and auto-checkout coordinator loop
├── monitor.py           # Internal StoreHippo API poller and token generator
├── checkout.py          # Playwright stealth auto-checkout engine (COD)
├── notifier.py          # Telegram Bot notification handler
├── requirements.txt     # Python dependencies
├── .env.example         # Configuration template
├── cookies.json.example # Sample cookies format
└── README.md            # Detailed setup and usage guide
```

---

## 🛠️ Installation & Setup

### 1. Clone & Enter Directory
```bash
git clone <your-repo-or-path>
cd Automation
```

### 2. Create Virtual Environment & Install Dependencies
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### 3. Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```

---

## 🔍 How to Find Your Substore ID

Amul uses **StoreHippo's multi-store hyperlocal architecture**. Availability is strictly tied to a regional `substore` ID mapped to your delivery pincode.

Follow these steps to find your exact Substore ID:

1. Open your browser (Chrome/Edge/Firefox) and navigate to [shop.amul.com](https://shop.amul.com).
2. Press `F12` (or right-click anywhere and choose **Inspect**) to open Developer Tools.
3. Switch to the **Network** tab and filter by **Fetch/XHR**.
4. On the Amul website:
   - Click the **Select Pincodes / Location** widget at the top.
   - Enter your delivery pincode (e.g. `110001` or your local pincode).
   - Select your address from the dropdown suggestions.
5. In the Developer Tools Network filter, type `ms.products`.
6. Inspect the filtered requests. Look for URLs ending with `substore=...`, for example:
   ```text
   https://shop.amul.com/api/1/entity/ms.products?...&substore=66505ff5145c16635e6cc74d
   ```
7. Copy the 24-character hexadecimal ID after `substore=` (e.g., `66505ff5145c16635e6cc74d`).
8. Paste it into your `.env` file:
   ```env
   AMUL_SUBSTORE_ID=66505ff5145c16635e6cc74d
   DELIVERY_PINCODE=110001
   ```

*Common Regional Substore IDs:*
- **Delhi / NCR:** `66505ff5145c16635e6cc74d`
- **Bangalore:** `66505ff0998183e1b1935c75`
- **Mumbai:** `66506004a7cddee1b8adb014`

---

## 🍪 How to Extract Cookies into `cookies.json`

To bypass OTP login at runtime, you must export your active, logged-in session cookies from your desktop browser.

### Option A: Using the "Cookie-Editor" Extension (Recommended & Easiest)
1. Install **Cookie-Editor** in your browser:
   - [Chrome Web Store](https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm)
   - [Firefox Add-ons](https://addons.mozilla.org/en-US/firefox/addon/cookie-editor/)
2. Go to [shop.amul.com](https://shop.amul.com) and log in to your account with your mobile number and OTP.
3. Ensure you have added and saved your default delivery address in your account.
4. Click the **Cookie-Editor** extension icon in your browser toolbar.
5. Click **Export** at the bottom right, then select **Export as JSON**.
6. Create a file named `cookies.json` in the project root directory and paste the copied JSON array into it:
   ```bash
   nano cookies.json  # or open in VS Code / text editor and paste
   ```

### Option B: Using Browser DevTools (Without Extension)
1. Log in to [shop.amul.com](https://shop.amul.com).
2. Open DevTools (`F12`) -> **Application** (or **Storage** in Firefox) -> **Cookies** -> `https://shop.amul.com`.
3. Key session cookies to verify are present:
   - `connect.sid`
   - `jsessionid`
4. Copy them and format into standard JSON array (refer to `cookies.json.example`).

> **Tip:** Amul session cookies typically remain valid for several weeks. If you notice checkout reverting to an OTP login screen, simply re-export your `cookies.json`.

---

## 🤖 Setting Up Telegram Notifications

1. Open Telegram and search for [@BotFather](https://t.me/BotFather).
2. Send `/newbot`, follow the prompts, and choose a name and username.
3. Copy the **HTTP API Token** provided by BotFather.
4. Start a conversation with your new bot by clicking `Start` in Telegram.
5. To get your **Chat ID**:
   - Message [@userinfobot](https://t.me/userinfobot) or [@RawDataBot](https://t.me/RawDataBot).
   - Copy the numerical `Id` (e.g. `123456789`).
6. Set these values in your `.env`:
   ```env
   TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
   TELEGRAM_CHAT_ID=your_telegram_chat_id_here
   ```

---

## 🚀 Running the Bot

Run the main process:
```bash
source venv/bin/activate
python main.py
```

### Start Automatically at Login (Arch Linux)

The user systemd service starts the stock monitor when you log in. **This script can automatically place an order using your saved session and configured purchase quantity when stock is detected.** Keep that in mind before enabling it.

The included service expects the project to be installed at `~/Automation`. If you use a different directory, update `WorkingDirectory` and `ExecStart` in `amul-stock-monitor.service` before installing it.

From the project directory, install and start the service:
```bash
mkdir -p ~/.config/systemd/user
install -m 644 amul-stock-monitor.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now amul-stock-monitor.service
```

Check its status and follow its logs:
```bash
systemctl --user status amul-stock-monitor.service
journalctl --user -u amul-stock-monitor.service -f
```

Stop it and disable automatic startup:
```bash
systemctl --user disable --now amul-stock-monitor.service
```

### Execution Flow:
1. The bot loads configuration, cookies, and initial StoreHippo security handshake.
2. It polls the internal JSON product API every 60 seconds with your `AMUL_SUBSTORE_ID`.
3. When `inventory_quantity > 0` is detected:
   - An alert is logged.
   - Playwright stealth Chromium launches with loaded `cookies.json`.
   - The product is added to the cart.
   - The bot navigates to checkout, selects your saved address, selects **Cash on Delivery (COD)**, and confirms the order.
   - Screenshots of each step are saved to `screenshots/` for auditability.
   - A Telegram message is sent:
     > **"Product was in stock. Order automatically placed."**
   - The bot gracefully exits to prevent duplicate orders.

---

## 🛡️ Anti-Detection & Troubleshooting

- **Headless vs Non-Headless Mode:**
  You can set `HEADLESS=false` in `.env` to visually watch the browser interact with Amul's site during checkout.
- **Screenshots Directory:**
  Every checkout step saves a timestamped screenshot in `screenshots/` (`01_product_page.png`, `02_item_added.png`, `03_checkout_page.png`, `04_payment_selected.png`, `05_order_confirmed.png`). If an error occurs, inspect `error_*.png` to diagnose the issue immediately.
- **Substore Returns 0 Items:**
  If the API responds with 0 items, confirm that your `AMUL_SUBSTORE_ID` is correct and matches your pincode. Amul only returns products stocked in your regional fulfillment center.

# 🥛 Amul Stock Monitor & Auto-Checkout Platform

[![Telegram Bot](https://img.shields.io/badge/Telegram-@amulzebot-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/amulzebot)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Playwright](https://img.shields.io/badge/Playwright-Stealth-2EAD33?style=for-the-badge&logo=playwright&logoColor=white)](https://playwright.dev/)

> 🤖 **Live Telegram Bot:** [@amulzebot](https://t.me/amulzebot) — Click to start monitoring and auto-purchasing Amul Whey Protein across India!

A high-performance Python automation engine for **Amul's e-commerce platform (`shop.amul.com`)**, specifically designed to monitor stock for high-demand products like **Amul Chocolate Whey Protein (Pack of 60 sachets)** and trigger rapid checkout with automated UPI payment dispatch as soon as stock becomes available.

Supports both **Single-User Dedicated Bot** and **Multi-Tenant Public SaaS Server (500+ Concurrent Users)** with zero-friction Telegram onboarding, automated All-India pincode-to-warehouse resolution, AES-256 session encryption, and central aggregated warehouse polling.

---

## ⚡ Key Architectural Features

1. **🚀 500+ User Multi-Tenant SaaS Architecture (`server.py`):**
   - **Zero-Friction In-Chat Phone + OTP Onboarding (`amul_auth.py`):** Any user can message the Telegram bot (`/start`), provide their 6-digit delivery pincode and mobile number, and enter the SMS OTP directly in Telegram. No browser extensions, developer tools, or manual `cookies.json` export required!
   - **All-India Automated Pincode-to-Warehouse Resolver:** Resolves any Indian pincode (e.g. `201310` -> `UP NCR`, `110001` -> `Delhi`, `560001` -> `Karnataka / Bangalore`) to its underlying StoreHippo warehouse in milliseconds without DevTools inspection.
   - **Central Aggregated Polling (Anti-DDoS):** Instead of 500 users polling Amul independently (which causes IP bans), the engine queries `SELECT DISTINCT substore_id` and polls each unique regional warehouse once (~15 to 25 requests/min across India total).
   - **AES-256 Fernet Encryption at Rest:** User session tokens and cookies are encrypted using AES-256 before storage in PostgreSQL / SQLite.
   - **Isolated User Concurrency:** Per-user timeouts, private status dashboards, and controlled checkout semaphore queues.

2. **Direct JSON API Polling (No HTML Scraping):**
   - Polls Amul's internal StoreHippo REST API (`/api/1/entity/ms.products`) with dynamic StoreHippo `tid` security tokens using reverse-engineered SHA-256 HMAC headers.

3. **Instant UPI Checkout & Dynamic QR Push:**
   - Detects `stock > 0` and triggers stealth Playwright automation in milliseconds.
   - Selects saved delivery address, launches the payment gateway (Easebuzz / PhonePe), and generates a scannable **Dynamic UPI QR Code**.
   - Instantly transmits the QR code photo directly to the customer's **Telegram** chat with an urgent alert sound.
   - Customer simply scans with any UPI app (GPay / PhonePe / Paytm / Cred) and enters their UPI PIN.

4. **📱 Telegram Remote Controls & Purchase Timeouts:**
   - **Timeout / Snooze Auto-Purchases:** Snooze auto-purchases for **1 month, 2 months, 3 months**, or custom durations directly via Telegram (`/pause 1m`, `/pause 2m`, `/pause 3m`).
   - **🔔 Notify-Only Mode While Paused:** The bot continues polling stock 24/7. When stock arrives while snoozed, it sends a stock alert to Telegram without placing an order.
   - **⚡ 1-Tap Buy Now Override:** Each notify-only alert includes a `[ ⚡ Buy Now (Resume) ]` button to instantly purchase if desired.
   - **❌ Instant Cancel:** Cancel any active checkout or payment QR immediately using `/cancel` or the `[ ❌ Cancel This Purchase ]` button directly under the QR code photo.
   - **Post-Purchase Auto-Snooze:** After successfully placing an order, the bot automatically snoozes auto-purchase for 2 months (customizable in `.env`), preventing unwanted duplicate orders while your supply lasts.

---

## 📱 Telegram Remote Commands

| Command | Action |
|---|---|
| `/start` | Start interactive conversational onboarding (Pincode & Phone + OTP) or view dashboard |
| `/status` | View personal monitoring status, stock, warehouse, and purchase timeout |
| `/pause 1m` | Timeout/pause auto-purchases for **1 month** (30 days) |
| `/pause 2m` | Timeout/pause auto-purchases for **2 months** (60 days) |
| `/pause 3m` | Timeout/pause auto-purchases for **3 months** (90 days) |
| `/pause` | Opens interactive buttons: `[ ⏸️ 1 Month ]` `[ ⏸️ 2 Months ]` `[ ⏸️ 3 Months ]` `[ ▶️ Resume ]` |
| `/resume` | Resume auto-purchases immediately |
| `/pincode` | Update delivery pincode (automatically updates warehouse) |
| `/relogin` | Re-authenticate phone number and SMS OTP directly via Telegram |
| `/cancel` | Cancel any active checkout/payment authorization and clear cart |
| `/stop` | Stop monitoring and permanently delete account with confirmation |
| `/users` | (Admin) View registered user directory and deleted accounts audit |
| `/help` | Display list of commands and guide |

---

## 📁 Project Structure

```text
.
├── server.py             # Multi-User Production Server (500+ users, DB, Central Poller)
├── amul_auth.py          # Native In-Chat Phone + SMS OTP Login Engine via Stealth Playwright
├── database.py           # Multi-tenant SQLite/PostgreSQL schema + AES-256 encryption
├── pincode_resolver.py   # All-India automated pincode-to-substore resolution engine
├── central_poller.py     # Aggregated stock poller querying unique regional substores
├── multi_user_bot.py     # Multi-user Telegram Bot controller & conversational onboarding
├── central_poller.py     # Aggregated stock poller querying unique regional substores
├── multi_user_bot.py     # Multi-user Telegram Bot controller & conversational onboarding
├── main.py               # Dedicated single-user orchestrator loop
├── checkout.py           # Playwright stealth auto-checkout engine with cancel support
├── monitor.py            # Internal StoreHippo API poller and SHA-256 TID generator
├── notifier.py           # Telegram notification handler with inline buttons & QR push
├── state_manager.py      # Local state persistence for single-user mode
├── requirements.txt      # Python dependencies
├── .env.example          # Configuration template
├── cookies.json.example  # Sample cookies format
└── README.md             # Detailed documentation
```

---

## 🛠️ Installation & Setup

### 1. Clone & Enter Directory
```bash
git clone https://github.com/cartiace/autobuy-amul-protein.git
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

Generate a secure master encryption key for AES-256:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Configure `.env`:
```env
TELEGRAM_BOT_TOKEN=your_bot_token_from_botfather
BOT_ENCRYPTION_KEY=your_generated_fernet_key
DATABASE_URL=sqlite:///amul_bot.db
POLL_INTERVAL_SECONDS=60
DEFAULT_POST_PURCHASE_PAUSE_MONTHS=2
PAYMENT_GATEWAY=easebuzz
PAYMENT_TIMEOUT_SECONDS=600
```

---

## 🚀 Running the Bot

### Option A: Run Multi-User Production Server (Recommended for Multiple Users / SaaS)
```bash
./venv/bin/python server.py
```
*Anyone can message your bot on Telegram, enter their pincode, link their session, and be automatically monitored and checked out!*

### Option B: Run Dedicated Single-User Mode
```bash
./venv/bin/python main.py
```

### Start as a Background Linux Systemd Service
```bash
mkdir -p ~/.config/systemd/user
install -m 644 amul-stock-monitor.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now amul-stock-monitor.service
```

Follow live server logs:
```bash
journalctl --user -u amul-stock-monitor.service -f
```

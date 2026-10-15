#!/usr/bin/env python3
"""
CLI script to view all registered users and their status in Amul Bot.
Usage:
    ./venv/bin/python users.py
"""

import sys
from datetime import datetime
import database
from database import User, Order, get_db

def view_all_users():
    database.init_db()
    with get_db() as db:
        users = db.query(User).order_by(User.created_at.desc()).all()
        if not users:
            print("\n⚠️ No users found in database.\n")
            return

        total = len(users)
        autobuy_count = 0
        notify_count = 0
        paused_count = 0
        for u in users:
            is_act, _, reason = u.is_auto_buy_active()
            if is_act:
                autobuy_count += 1
            elif "notify" in (reason or "").lower():
                notify_count += 1
            else:
                paused_count += 1

        print("\n" + "=" * 115)
        print(f"👥 AMUL BOT USER DIRECTORY — Total: {total} | 🟢 Auto-Buy: {autobuy_count} | 🔔 Notify-Only: {notify_count} | ⏸️ Paused: {paused_count}")
        print("=" * 115)

        fmt = "{:<12} {:<15} {:<12} {:<8} {:<18} {:<26} {:<16}"
        print(fmt.format("Chat ID", "Username", "Phone", "Pincode", "Warehouse", "Target Product", "Mode"))
        print("-" * 115)

        for u in users:
            is_active, _, reason = u.is_auto_buy_active()
            if is_active:
                status_str = "🟢 AUTO-BUY"
            elif "notify" in (reason or "").lower():
                status_str = "🔔 NOTIFY-ONLY"
            else:
                status_str = f"⏸️ PAUSED"
            username_str = f"@{u.telegram_username}" if u.telegram_username else "-"
            phone_str = u.phone_number or "-"
            pincode_str = u.pincode or "-"
            warehouse_str = (u.substore_name or "-")[:17]
            target_str = (u.target_name or "Choc Whey 60s")[:25]

            print(fmt.format(
                str(u.telegram_chat_id),
                username_str[:14],
                phone_str[:12],
                pincode_str[:8],
                warehouse_str,
                target_str,
                status_str
            ))

        print("=" * 115)

        deleted_logs = database.get_deleted_user_logs()
        if deleted_logs:
            print(f"\n🗑️  DELETED USER AUDIT LOG — Total: {len(deleted_logs)}")
            print("=" * 95)
            del_fmt = "{:<12} {:<16} {:<14} {:<9} {:<22} {:<18}"
            print(del_fmt.format("Chat ID", "Username", "Phone", "Pincode", "Warehouse", "Deleted At"))
            print("-" * 95)
            for d in deleted_logs:
                d_time = d.deleted_at.strftime("%Y-%m-%d %H:%M") if d.deleted_at else "-"
                print(del_fmt.format(
                    str(d.telegram_chat_id),
                    (f"@{d.telegram_username}" if d.telegram_username else "-")[:15],
                    (d.phone_number or "-")[:14],
                    (d.pincode or "-")[:8],
                    (d.substore_name or "-")[:21],
                    d_time
                ))
            print("=" * 95)

        print(f"💡 Tip: Run './venv/bin/python users.py' anytime to view updated user list.\n")

if __name__ == "__main__":
    view_all_users()

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
        active_count = sum(1 for u in users if u.is_auto_buy_active()[0])
        paused_count = total - active_count

        print("\n" + "=" * 95)
        print(f"👥 AMUL BOT USER DIRECTORY — Total: {total} | Active: {active_count} | Paused: {paused_count}")
        print("=" * 95)

        fmt = "{:<12} {:<16} {:<12} {:<9} {:<22} {:<12} {:<12}"
        print(fmt.format("Chat ID", "Username", "Phone", "Pincode", "Warehouse", "State", "Auto-Buy"))
        print("-" * 95)

        for u in users:
            is_active, _, reason = u.is_auto_buy_active()
            status_str = "🟢 ACTIVE" if is_active else "⏸️ PAUSED"
            username_str = f"@{u.telegram_username}" if u.telegram_username else "-"
            phone_str = u.phone_number or "-"
            pincode_str = u.pincode or "-"
            warehouse_str = (u.substore_name or "-")[:21]
            state_str = u.onboarding_state or "-"

            print(fmt.format(
                str(u.telegram_chat_id),
                username_str[:15],
                phone_str[:12],
                pincode_str[:8],
                warehouse_str,
                state_str[:11],
                status_str
            ))

        print("=" * 95)

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

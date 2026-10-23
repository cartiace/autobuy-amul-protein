"""
Database and Encryption Module for Amul Multi-User Auto-Checkout Bot.
Supports SQLite (development/embedded) and PostgreSQL (production).
Provides AES-256 encryption at rest for session cookies and multi-tenant user isolation.
"""

import os
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Tuple
from contextlib import contextmanager

from dotenv import load_dotenv
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    BigInteger,
    String,
    Boolean,
    DateTime,
    Text,
    ForeignKey,
    select,
    distinct,
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session
from cryptography.fernet import Fernet

load_dotenv()
logger = logging.getLogger("Database")

# ==========================================
# Encryption Key Management (AES-256 Fernet)
# ==========================================
def _get_or_create_secret_key() -> bytes:
    key_str = os.getenv("BOT_ENCRYPTION_KEY", "").strip()
    if not key_str:
        # Generate new 32-byte urlsafe base64 key
        new_key = Fernet.generate_key().decode("utf-8")
        logger.warning(
            "⚠️ No 'BOT_ENCRYPTION_KEY' found in .env. Generated a new encryption key. "
            "Please persist this in your .env to decrypt existing user sessions across restarts."
        )
        return new_key.encode("utf-8")
    return key_str.encode("utf-8")

FERNET_KEY = _get_or_create_secret_key()
_cipher = Fernet(FERNET_KEY)

def encrypt_data(plain_text: str) -> str:
    """Encrypts a plaintext string into an AES-256 cipher string."""
    if not plain_text:
        return ""
    return _cipher.encrypt(plain_text.encode("utf-8")).decode("utf-8")

def decrypt_data(cipher_text: str) -> str:
    """Decrypts an AES-256 cipher string back into plaintext."""
    if not cipher_text:
        return ""
    try:
        return _cipher.decrypt(cipher_text.encode("utf-8")).decode("utf-8")
    except Exception as e:
        logger.error(f"Decryption error: {e}")
        return ""

# ==========================================
# SQLAlchemy Models & Engine Setup
# ==========================================
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///amul_bot.db").strip()
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    pool_pre_ping=True
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"

    telegram_chat_id = Column(BigInteger, primary_key=True, index=True)
    telegram_username = Column(String(100), nullable=True)
    pincode = Column(String(10), nullable=True, index=True)
    substore_id = Column(String(50), nullable=True, index=True)
    substore_name = Column(String(100), nullable=True)
    phone_number = Column(String(20), nullable=True)
    
    # Encrypted session cookies (AES-256)
    encrypted_cookies = Column(Text, nullable=True)
    
    # Auto-purchase & Snooze state
    auto_buy_enabled = Column(Boolean, default=True)
    paused_until = Column(DateTime, nullable=True)
    pause_reason = Column(String(255), nullable=True)
    
    # Target configurations
    quantity = Column(Integer, default=1)
    target_sku = Column(String(50), default="WPCCP05_02")
    target_alias = Column(
        String(255),
        default="amul-chocolate-whey-protein-34-g-or-pack-of-60-sachets"
    )
    target_name = Column(
        String(255),
        default="Amul Chocolate Whey Protein (60 sachets)"
    )
    
    # Conversational Onboarding State: START, AWAITING_PINCODE, AWAITING_AUTH, READY
    onboarding_state = Column(String(50), default="START")
    
    # Delivery address details from Amul account (JSON formatted)
    delivery_address_json = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_order_at = Column(DateTime, nullable=True)
    in_stock_notified = Column(Boolean, default=False)
    last_notified_at = Column(DateTime, nullable=True)

    # Secret loving girlfriend good morning service (daily custom time, default 08:00 IST)
    good_morning_enabled = Column(Boolean, default=False)
    morning_time = Column(String(5), default="08:00")
    last_morning_msg_date = Column(String(10), nullable=True)

    orders = relationship("Order", back_populates="user", cascade="all, delete-orphan")

    def is_auto_buy_active(self) -> Tuple[bool, Optional[datetime], str]:
        """Calculates whether this user currently has auto-buy active or snoozed."""
        if not self.auto_buy_enabled and not self.paused_until:
            return False, None, self.pause_reason or "Paused indefinitely"

        if self.paused_until:
            now = datetime.utcnow()
            if now < self.paused_until:
                diff = self.paused_until - now
                days = diff.days
                hours = int(diff.seconds / 3600)
                mins = int((diff.seconds % 3600) / 60)
                time_left = f"{days}d {hours}h {mins}m" if days > 0 else f"{hours}h {mins}m"
                reason = self.pause_reason or f"Paused until {self.paused_until.strftime('%d %b %Y, %H:%M')}"
                return False, self.paused_until, f"{reason} ({time_left} left)"
            else:
                # Expired
                self.auto_buy_enabled = True
                self.paused_until = None
                self.pause_reason = None
                return True, None, "Auto-buy is active (pause expired)"

        return True, None, "Auto-buy is active"

class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_chat_id = Column(BigInteger, ForeignKey("users.telegram_chat_id"), index=True)
    order_id = Column(String(100), nullable=True)
    product_name = Column(String(255))
    product_sku = Column(String(50))
    quantity = Column(Integer, default=1)
    amount = Column(String(50), nullable=True)
    status = Column(String(50), default="PENDING_PAYMENT") # PENDING_PAYMENT, CONFIRMED, CANCELLED, FAILED
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="orders")

class DeletedUserLog(Base):
    __tablename__ = "deleted_user_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_chat_id = Column(BigInteger, index=True)
    telegram_username = Column(String(100), nullable=True)
    phone_number = Column(String(20), nullable=True)
    pincode = Column(String(10), nullable=True)
    substore_name = Column(String(100), nullable=True)
    reason = Column(String(255), default="User requested deletion")
    deleted_at = Column(DateTime, default=datetime.utcnow)

# ==========================================
# Database Helper Utilities
# ==========================================
@contextmanager
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    """Initializes tables in database."""
    Base.metadata.create_all(bind=engine)
    # Safe migration for existing SQLite database
    from sqlalchemy import text
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE users ADD COLUMN in_stock_notified BOOLEAN DEFAULT 0"))
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE users ADD COLUMN last_notified_at DATETIME"))
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE users ADD COLUMN target_name VARCHAR(255) DEFAULT 'Amul Chocolate Whey Protein (60 sachets)'"))
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE users ADD COLUMN delivery_address_json TEXT"))
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE users ADD COLUMN good_morning_enabled BOOLEAN DEFAULT 0"))
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE users ADD COLUMN last_morning_msg_date VARCHAR(10)"))
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE users ADD COLUMN morning_time VARCHAR(5) DEFAULT '08:00'"))
        except Exception:
            pass
        conn.commit()
    logger.info("Database initialized successfully.")

def get_or_create_user(chat_id: int, username: Optional[str] = None) -> User:
    with get_db() as db:
        user = db.query(User).filter(User.telegram_chat_id == chat_id).first()
        if not user:
            user = User(
                telegram_chat_id=chat_id,
                telegram_username=username,
                onboarding_state="START"
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            logger.info(f"Created new user record for chat_id={chat_id}")
        elif username and user.telegram_username != username:
            user.telegram_username = username
            db.commit()
            db.refresh(user)
        return user

def get_user(chat_id: int) -> Optional[User]:
    with get_db() as db:
        return db.query(User).filter(User.telegram_chat_id == chat_id).first()

def update_user_pincode(chat_id: int, pincode: str, substore_id: str, substore_name: str) -> bool:
    with get_db() as db:
        user = db.query(User).filter(User.telegram_chat_id == chat_id).first()
        if user:
            user.pincode = pincode
            user.substore_id = substore_id
            user.substore_name = substore_name
            db.commit()
            logger.info(f"Updated pincode={pincode}, substore={substore_name} ({substore_id}) for chat_id={chat_id}")
            return True
        return False

def update_user_delivery_address(chat_id: int, address_data: Dict[str, Any]) -> bool:
    """Saves or updates the user's confirmed Amul delivery address."""
    with get_db() as db:
        user = db.query(User).filter(User.telegram_chat_id == chat_id).first()
        if user and address_data:
            user.delivery_address_json = json.dumps(address_data)
            db.commit()
            logger.info(f"Updated delivery address record for chat_id={chat_id}")
            return True
        return False

def get_user_delivery_address(chat_id: int) -> Optional[Dict[str, Any]]:
    """Retrieves the user's saved Amul delivery address."""
    with get_db() as db:
        user = db.query(User).filter(User.telegram_chat_id == chat_id).first()
        if user and user.delivery_address_json:
            try:
                return json.loads(user.delivery_address_json)
            except Exception as e:
                logger.error(f"Error parsing delivery address json for chat_id={chat_id}: {e}")
                return None
        return None

def set_user_cookies(chat_id: int, cookies_list: list) -> bool:
    with get_db() as db:
        user = db.query(User).filter(User.telegram_chat_id == chat_id).first()
        if user:
            raw_json = json.dumps(cookies_list)
            user.encrypted_cookies = encrypt_data(raw_json)
            user.onboarding_state = "READY"
            db.commit()
            logger.info(f"Encrypted and stored session cookies for chat_id={chat_id}")
            return True
        return False

def get_user_cookies(chat_id: int) -> Optional[list]:
    with get_db() as db:
        user = db.query(User).filter(User.telegram_chat_id == chat_id).first()
        if user and user.encrypted_cookies:
            decrypted = decrypt_data(user.encrypted_cookies)
            if decrypted:
                try:
                    return json.loads(decrypted)
                except Exception as e:
                    logger.error(f"Error parsing decrypted cookies: {e}")
        return None

def set_user_onboarding_state(chat_id: int, state: str) -> None:
    with get_db() as db:
        user = db.query(User).filter(User.telegram_chat_id == chat_id).first()
        if user:
            user.onboarding_state = state
            db.commit()

def pause_user_autobuy(chat_id: int, days: Optional[float] = None, reason: str = "") -> Tuple[Optional[datetime], str]:
    with get_db() as db:
        user = db.query(User).filter(User.telegram_chat_id == chat_id).first()
        if not user:
            return None, "User not found"

        if days is not None and days > 0:
            paused_until = datetime.utcnow() + timedelta(days=days)
            user.auto_buy_enabled = False
            user.paused_until = paused_until
            if not reason:
                if abs(days - 30) < 1:
                    reason = "Paused for 1 month"
                elif abs(days - 60) < 1:
                    reason = "Paused for 2 months"
                elif abs(days - 90) < 1:
                    reason = "Paused for 3 months"
                else:
                    reason = f"Paused for {int(days) if days.is_integer() else days} days"
            user.pause_reason = reason
            db.commit()
            return paused_until, reason
        else:
            user.auto_buy_enabled = False
            user.paused_until = None
            user.pause_reason = reason or "Paused indefinitely"
            db.commit()
            return None, user.pause_reason

def resume_user_autobuy(chat_id: int) -> bool:
    with get_db() as db:
        user = db.query(User).filter(User.telegram_chat_id == chat_id).first()
        if user:
            user.auto_buy_enabled = True
            user.paused_until = None
            user.pause_reason = None
            db.commit()
            return True
        return False

def get_unique_active_substores() -> List[str]:
    """
    Returns only unique substore IDs across all users who are in READY state.
    This enables central polling of 15-25 warehouses instead of 500 individual polls.
    """
    with get_db() as db:
        substores = (
            db.query(User.substore_id)
            .distinct()
            .filter(User.substore_id.isnot(None))
            .filter(User.onboarding_state == "READY")
            .all()
        )
        return [s[0] for s in substores if s[0]]

def get_users_for_substore(substore_id: str) -> List[User]:
    """Returns all users registered under a specific substore."""
    with get_db() as db:
        return (
            db.query(User)
            .filter(User.substore_id == substore_id)
            .filter(User.onboarding_state == "READY")
            .all()
        )

def delete_and_log_user(chat_id: int, reason: str = "User self-deleted via Telegram") -> Optional[DeletedUserLog]:
    """
    Permanently deletes a user from the active database and writes an audit log
    entry with their information and deletion timestamp.
    """
    with get_db() as db:
        user = db.query(User).filter(User.telegram_chat_id == chat_id).first()
        if not user:
            return None

        # Create audit log record
        log_entry = DeletedUserLog(
            telegram_chat_id=user.telegram_chat_id,
            telegram_username=user.telegram_username,
            phone_number=user.phone_number,
            pincode=user.pincode,
            substore_name=user.substore_name,
            reason=reason,
            deleted_at=datetime.utcnow()
        )
        db.add(log_entry)

        # Log to file / stdout logger
        logger.warning(
            f"🚫 [ACCOUNT DELETED] chat_id={user.telegram_chat_id} | username=@{user.telegram_username} | "
            f"phone={user.phone_number} | pincode={user.pincode} | warehouse={user.substore_name} | reason='{reason}'"
        )

        db.delete(user)
        db.commit()
        return log_entry

def get_deleted_user_logs(limit: int = 50) -> List[DeletedUserLog]:
    """Returns the most recent audit records of deleted accounts."""
    with get_db() as db:
        return (
            db.query(DeletedUserLog)
            .order_by(DeletedUserLog.deleted_at.desc())
            .limit(limit)
            .all()
        )

def should_notify_user_stock(chat_id: int) -> bool:
    """Returns True if the user has NOT yet been notified for the current in-stock event."""
    with get_db() as db:
        user = db.query(User).filter(User.telegram_chat_id == chat_id).first()
        if not user:
            return False
        return not bool(user.in_stock_notified)

def mark_user_stock_notified(chat_id: int, notified: bool = True) -> None:
    """Marks user as notified (or resets notification flag) for current stock."""
    with get_db() as db:
        user = db.query(User).filter(User.telegram_chat_id == chat_id).first()
        if user:
            user.in_stock_notified = notified
            if notified:
                user.last_notified_at = datetime.utcnow()
            db.commit()

def reset_stock_notified_for_substore(substore_id: str) -> None:
    """Resets in_stock_notified flag for all users in a warehouse when it goes out of stock."""
    with get_db() as db:
        db.query(User).filter(
            User.substore_id == substore_id,
            User.in_stock_notified == True
        ).update({"in_stock_notified": False})
        db.commit()

def update_user_product(chat_id: int, target_alias: str, target_sku: str, target_name: str) -> bool:
    """Updates target product configuration for a specific user and resets notification flag."""
    with get_db() as db:
        user = db.query(User).filter(User.telegram_chat_id == chat_id).first()
        if user:
            user.target_alias = target_alias
            user.target_sku = target_sku
            user.target_name = target_name
            user.in_stock_notified = False
            user.last_notified_at = None
            db.commit()
            logger.info(
                f"Updated target product for chat_id={chat_id}: '{target_name}' "
                f"(SKU: {target_sku}, Alias: {target_alias})"
            )
            return True
        return False

def get_active_targets_for_substore(substore_id: str) -> List[Tuple[str, str, str]]:
    """
    Returns unique (target_alias, target_sku, target_name) tracked by READY users
    in the specified regional warehouse.
    """
    with get_db() as db:
        targets = (
            db.query(User.target_alias, User.target_sku, User.target_name)
            .distinct()
            .filter(User.substore_id == substore_id)
            .filter(User.onboarding_state == "READY")
            .filter(User.target_alias.isnot(None))
            .all()
        )
        return [(t[0], t[1], t[2] or "Amul Product") for t in targets if t[0]]

def get_users_for_substore_and_target(substore_id: str, target_alias: str) -> List[User]:
    """Returns all users in a specific warehouse monitoring a specific product alias."""
    with get_db() as db:
        return (
            db.query(User)
            .filter(User.substore_id == substore_id)
            .filter(User.target_alias == target_alias)
            .filter(User.onboarding_state == "READY")
            .all()
        )

def reset_stock_notified_for_substore_and_target(substore_id: str, target_alias: str) -> None:
    """Resets in_stock_notified flag for users monitoring a specific product in a warehouse."""
    with get_db() as db:
        db.query(User).filter(
            User.substore_id == substore_id,
            User.target_alias == target_alias,
            User.in_stock_notified == True
        ).update({"in_stock_notified": False})
        db.commit()

def set_user_good_morning(chat_id: int, enabled: bool) -> bool:
    """Enables or disables the daily 8:00 AM loving Good Morning message service."""
    with get_db() as db:
        user = db.query(User).filter(User.telegram_chat_id == chat_id).first()
        if user:
            user.good_morning_enabled = enabled
            db.commit()
            logger.info(f"Updated good_morning_enabled={enabled} for chat_id={chat_id}")
            return True
        return False

def get_good_morning_users() -> List[User]:
    """Returns all users who have enabled the 8:00 AM Good Morning service."""
    with get_db() as db:
        return db.query(User).filter(User.good_morning_enabled == True).all()

def record_user_morning_sent(chat_id: int, date_str: str) -> None:
    """Records the date (YYYY-MM-DD) of the last morning message sent to prevent duplicates on the same day."""
    with get_db() as db:
        user = db.query(User).filter(User.telegram_chat_id == chat_id).first()
        if user:
            user.last_morning_msg_date = date_str
            db.commit()

def set_user_morning_time(chat_id: int, time_str: str) -> bool:
    """Sets the preferred delivery time (HH:MM in 24h format) for the Good Morning message and activates it."""
    with get_db() as db:
        user = db.query(User).filter(User.telegram_chat_id == chat_id).first()
        if user:
            user.morning_time = time_str
            user.good_morning_enabled = True
            db.commit()
            logger.info(f"Updated morning_time={time_str} and enabled Good Morning for chat_id={chat_id}")
            return True
        return False




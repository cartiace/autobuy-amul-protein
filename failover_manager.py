"""
Failover Manager (Active-Standby High-Availability Supervisor).
Enables seamless automatic failover between Primary (CachyOS Laptop) and Backup (Windows PC)
using Neon PostgreSQL as a shared cloud lease lock.

Key Features:
- Zero Telegram 409 Conflicts: Enforces strict single-instance execution.
- Preemption Priority: Primary node (Laptop) automatically reclaims leadership upon boot.
- Automatic Takeover: Backup node (PC) activates when primary heartbeat goes silent (>35s).
- Cross-Platform: Works identically on Linux systemd and Windows Task Scheduler / batch.
"""

import os
import sys
import time
import signal
import logging
import asyncio
import platform
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

import database

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [FailoverManager] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("failover_manager.log", mode="a", encoding="utf-8")
    ]
)
logger = logging.getLogger("FailoverManager")

# Node Settings
IS_WINDOWS = platform.system() == "Windows"
DEFAULT_ROLE = "primary" if not IS_WINDOWS else "backup"
DEFAULT_NODE_ID = "laptop" if not IS_WINDOWS else "windows-pc"

NODE_ROLE = os.getenv("NODE_ROLE", DEFAULT_ROLE).strip().lower() # 'primary' or 'backup'
NODE_ID = os.getenv("NODE_ID", DEFAULT_NODE_ID).strip()
NODE_PRIORITY = 10 if NODE_ROLE == "primary" else 1

HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "10"))
FAILOVER_TIMEOUT = int(os.getenv("FAILOVER_TIMEOUT_SECONDS", "35"))
PREEMPTION_GRACE = int(os.getenv("PREEMPTION_GRACE_SECONDS", "12"))

class FailoverSupervisor:
    def __init__(self):
        self.bot_proc: Optional[subprocess.Popen] = None
        self.is_running = True
        self.state = "STANDBY" # 'ACTIVE' or 'STANDBY'

    def start_bot(self):
        """Starts the server.py application in a child subprocess."""
        if self.bot_proc and self.bot_proc.poll() is None:
            return # Already running

        cmd = [sys.executable, "server.py"]
        logger.info(f"🚀 [STARTING BOT] Launching server process: {' '.join(cmd)}")
        try:
            self.bot_proc = subprocess.Popen(
                cmd,
                cwd=os.path.dirname(os.path.abspath(__file__)),
                stdout=None, # Inherit stdout for systemd / console
                stderr=None,
            )
            self.state = "ACTIVE"
            logger.info(f"✅ Bot server process started with PID {self.bot_proc.pid}")
        except Exception as e:
            logger.error(f"❌ Failed to start bot server process: {e}", exc_info=True)

    def stop_bot(self):
        """Gracefully stops the server.py child process."""
        if not self.bot_proc or self.bot_proc.poll() is not None:
            self.bot_proc = None
            self.state = "STANDBY"
            return

        logger.info(f"🛑 [STOPPING BOT] Terminating server process PID {self.bot_proc.pid}...")
        try:
            if IS_WINDOWS:
                # On Windows, kill process tree to ensure Playwright & poller stop cleanly
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(self.bot_proc.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            else:
                self.bot_proc.terminate()
                try:
                    self.bot_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.bot_proc.kill()
                    self.bot_proc.wait(timeout=2)
            logger.info("✅ Bot server process stopped successfully.")
        except Exception as e:
            logger.warning(f"Warning while stopping bot process: {e}")
        finally:
            self.bot_proc = None
            self.state = "STANDBY"

    def shutdown(self, signum=None, frame=None):
        logger.info("Shutdown signal received. Cleaning up failover manager...")
        self.is_running = False
        self.stop_bot()

    def run(self):
        # Register signal handlers
        signal.signal(signal.SIGINT, self.shutdown)
        signal.signal(signal.SIGTERM, self.shutdown)

        print("=" * 65)
        print("          AMUL HIGH-AVAILABILITY FAILOVER SUPERVISOR            ")
        print("=" * 65)
        print(f"Node Identifier  : {NODE_ID}")
        print(f"Node Role        : {NODE_ROLE.upper()} (Priority: {NODE_PRIORITY})")
        print(f"Operating System : {platform.system()} ({platform.release()})")
        print(f"Heartbeat Rate   : Every {HEARTBEAT_INTERVAL}s")
        print(f"Failover Timeout : {FAILOVER_TIMEOUT}s of silence")
        print(f"Database         : {os.getenv('DATABASE_URL', 'Neon PostgreSQL')[:45]}...")
        print("=" * 65)

        # 1. Initialize DB schema if not already present
        database.init_db()

        # 2. If Primary Node: Assert leadership immediately (Preemption)
        if NODE_ROLE == "primary":
            logger.info("⚡ [PREEMPTION] Primary node online! Claiming leadership in Neon database...")
            database.record_cluster_heartbeat(node_id=NODE_ID, role=NODE_ROLE, priority=NODE_PRIORITY)
            logger.info(f"⏳ Waiting {PREEMPTION_GRACE}s grace window for any backup node to yield...")
            time.sleep(PREEMPTION_GRACE)
            self.start_bot()

        last_standby_log = 0

        # 3. Main Watchdog Loop
        while self.is_running:
            try:
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                leader = database.get_cluster_leader()

                # Determine if leader heartbeat is alive
                is_leader_alive = False
                leader_age_secs = 9999
                if leader and leader.last_heartbeat:
                    leader_age_secs = (now - leader.last_heartbeat).total_seconds()
                    is_leader_alive = leader_age_secs < FAILOVER_TIMEOUT

                # ─────────────────────────────────────────────────────────────
                # BRANCH A: This is the PRIMARY NODE (Laptop)
                # ─────────────────────────────────────────────────────────────
                if NODE_ROLE == "primary":
                    # Primary always owns the active role when running
                    database.record_cluster_heartbeat(node_id=NODE_ID, role=NODE_ROLE, priority=NODE_PRIORITY)
                    if not self.bot_proc or self.bot_proc.poll() is not None:
                        logger.warning("Primary bot process not running! Restarting...")
                        self.start_bot()

                # ─────────────────────────────────────────────────────────────
                # BRANCH B: This is the BACKUP NODE (Windows PC)
                # ─────────────────────────────────────────────────────────────
                    active_leader_id = getattr(leader, "active_node", getattr(leader, "node_id", "unknown"))
                    primary_is_active = (
                        is_leader_alive and leader.role == "primary" and active_leader_id != NODE_ID
                    )

                    if primary_is_active:
                        # Primary is alive -> Backup must yield and remain in standby
                        if self.bot_proc and self.bot_proc.poll() is None:
                            logger.info("⚡ [PREEMPTION DETECTED] Primary node is active! Stopping local bot to yield...")
                            self.stop_bot()
                        
                        if time.time() - last_standby_log > 60:
                            logger.info(f"💤 [STANDBY] Primary node '{active_leader_id}' is active (Heartbeat {int(leader_age_secs)}s ago). Standing by...")
                            last_standby_log = time.time()

                    else:
                        # Primary is DEAD or silent for > FAILOVER_TIMEOUT
                        if not self.bot_proc or self.bot_proc.poll() is not None:
                            logger.warning(
                                f"🚨 [FAILOVER TRIGGERED] Primary node silent for {int(leader_age_secs)}s! "
                                f"Windows backup node ({NODE_ID}) taking over active duty..."
                            )
                            database.record_cluster_heartbeat(node_id=NODE_ID, role=NODE_ROLE, priority=NODE_PRIORITY)
                            self.start_bot()
                        else:
                            # Already active backup: refresh heartbeat
                            database.record_cluster_heartbeat(node_id=NODE_ID, role=NODE_ROLE, priority=NODE_PRIORITY)

            except Exception as loop_err:
                logger.error(f"Error in failover supervisor loop: {loop_err}", exc_info=True)

            # Sleep until next check
            time.sleep(HEARTBEAT_INTERVAL)

        logger.info("Failover supervisor exited cleanly.")

if __name__ == "__main__":
    supervisor = FailoverSupervisor()
    supervisor.run()

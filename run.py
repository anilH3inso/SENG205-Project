# run.py
# Launches Care Portal desktop GUI + chatbot server (no health check)
from __future__ import annotations

import os, sys, subprocess, shutil
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CHATBOT_PORT = int(os.getenv("CARE_PORTAL_PORT", "8001"))   # default 8001
CHATBOT_HOST = os.getenv("CARE_PORTAL_HOST", "127.0.0.1")

PROJECT_ROOT = next(
    (p for p in [Path(__file__).resolve().parent, *Path(__file__).resolve().parents]
     if (p / "care_portal" / "__init__.py").exists()),
    Path(__file__).resolve().parent
)
DB_PATH = PROJECT_ROOT / "care_portal" / "care_portal.db"

def ensure_pythonpath():
    pr = str(PROJECT_ROOT)
    if pr not in sys.path:
        sys.path.insert(0, pr)
    os.environ["CARE_PORTAL_ROOT"] = pr

def run_command(argv: list[str]):
    subprocess.check_call(argv, cwd=str(PROJECT_ROOT))

def popen_command(argv: list[str]) -> subprocess.Popen:
    return subprocess.Popen(
        argv, cwd=str(PROJECT_ROOT),
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,  # quieter
        text=True, bufsize=1
    )

# ---------------------------------------------------------------------------
# Kill port (fast path)
# ---------------------------------------------------------------------------
def kill_port(port: int):
    if "--no-kill" in sys.argv:
        print(f"⏭️  Skipping port kill for :{port}")
        return
    if shutil.which("lsof"):
        try:
            out = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True, check=False).stdout
            pids = [p for p in out.split() if p.isdigit()]
            if pids:
                print(f"🔧 Killing PIDs on :{port}: {', '.join(pids)}")
                for pid in pids:
                    subprocess.run(["kill", "-9", pid], check=False)
            else:
                print(f"✅ No processes found on port {port}.")
            return
        except Exception:
            pass
    # Fallback
    try:
        out = subprocess.run(["bash", "-lc", f"fuser -n tcp {port} 2>/dev/null"], capture_output=True, text=True).stdout
        pids = [p for p in out.split() if p.isdigit()]
        if pids:
            print(f"🔧 Killing PIDs on :{port}: {', '.join(pids)}")
            for pid in pids:
                subprocess.run(["kill", "-9", pid], check=False)
        else:
            print(f"✅ No processes found on port {port}.")
    except Exception:
        print(f"✅ No processes found on port {port}.")

# ---------------------------------------------------------------------------
# Starters
# ---------------------------------------------------------------------------
def seed_database():
    # Skip if DB already exists unless forced
    if DB_PATH.exists() and "--force-seed" not in sys.argv and "--seed" not in sys.argv:
        print(f"⏭️  DB exists, skipping seed ({DB_PATH.name}). Use --force-seed to reseed.")
        return
    if "--no-seed" in sys.argv:
        print("⏭️  Skipping seed (flag).")
        return
    print("🌱 Seeding database...")
    run_command([sys.executable, "-m", "care_portal.seed"])

def start_chatbot():
    print(f"🤖 Starting Care Portal server on http://{CHATBOT_HOST}:{CHATBOT_PORT}")
    # Use the unified services entrypoint
    return popen_command([sys.executable, "-m", "care_portal.services.ai_server"])

def start_gui():
    print("🖥️ Launching Care Portal Desktop GUI...")
    run_command([sys.executable, "-m", "care_portal.app"])

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ensure_pythonpath()
    print(f"📁 Project root: {PROJECT_ROOT}")
    kill_port(CHATBOT_PORT)

    seed_database()
    bot_proc = start_chatbot()

    try:
        start_gui()
    finally:
        if bot_proc.poll() is None:
            print("🛑 Stopping chatbot...")
            bot_proc.terminate()
            try:
                bot_proc.wait(timeout=2)
            except Exception:
                bot_proc.kill()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 Exiting.")
        sys.exit(0)

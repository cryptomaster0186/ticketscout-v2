"""
script_runner.py — Subprocess-based Python script runner for the Signup Bot page.

Runs arbitrary .py scripts as subprocesses, passing:
  --url      <signup URL>
  --accounts <path to temp accounts file>  (email:password per line)
  --proxies  <path to temp proxies file>   (one proxy per line)

stdout/stderr from the script is streamed to log_queue for SSE delivery.
Only one script can run at a time.
"""

import logging
import os
import queue
import subprocess
import tempfile
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

# ── Module-level state ────────────────────────────────────────────────────────
log_queue: queue.Queue = queue.Queue()   # SSE-readable log entries
_proc: subprocess.Popen = None          # current subprocess
_proc_lock = threading.Lock()
_current_script: str = ""               # name of running script


# ── Public API ────────────────────────────────────────────────────────────────

def get_status() -> dict:
    with _proc_lock:
        running = _proc is not None and _proc.poll() is None
        return {
            "running": running,
            "script":  _current_script if running else "",
            "pid":     _proc.pid if running else None,
        }


def run_script(script_path: str, url: str,
               accounts_lines: list, proxy_lines: list) -> dict:
    """
    Start script_path as a subprocess with temp files for accounts and proxies.
    Raises RuntimeError if a script is already running.
    Returns {"started": True, "script": name}.
    """
    global _proc, _current_script

    with _proc_lock:
        if _proc is not None and _proc.poll() is None:
            raise RuntimeError("A script is already running. Stop it first.")

    # Write temp account file: email:password per line
    acc_tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
    acc_tmp.write("\n".join(accounts_lines))
    acc_tmp.close()

    # Write temp proxy file
    prx_tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
    prx_tmp.write("\n".join(proxy_lines))
    prx_tmp.close()

    import sys
    python = sys.executable
    cmd = [
        python, script_path,
        "--url",      url,
        "--accounts", acc_tmp.name,
        "--proxies",  prx_tmp.name,
    ]

    script_name = os.path.basename(script_path)
    _log(f"▶ Starting script: {script_name}", "info")
    _log(f"  URL: {url}", "info")
    _log(f"  Accounts: {len(accounts_lines)} | Proxies: {len(proxy_lines)}", "info")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except Exception as e:
        _log(f"❌ Failed to start script: {e}", "error")
        os.unlink(acc_tmp.name)
        os.unlink(prx_tmp.name)
        raise

    with _proc_lock:
        _proc = proc
        _current_script = script_name

    # Background reader thread
    def _read():
        global _proc, _current_script
        for line in proc.stdout:
            _log(line.rstrip(), "info")
        rc = proc.wait()
        _log(f"✅ Script finished — exit code {rc}", "info" if rc == 0 else "warning")
        # Clean up temp files
        for p in (acc_tmp.name, prx_tmp.name):
            try:
                os.unlink(p)
            except OSError:
                pass
        with _proc_lock:
            if _proc is proc:
                _proc = None
                _current_script = ""

    threading.Thread(target=_read, daemon=True).start()

    return {"started": True, "script": script_name}


def stop_script():
    """Terminate the running script if any."""
    global _proc
    with _proc_lock:
        p = _proc
    if p and p.poll() is None:
        p.terminate()
        _log("⏹ Script terminated by user.", "warning")


# ── Internal helpers ──────────────────────────────────────────────────────────

def _log(msg: str, level: str = "info"):
    log_queue.put({
        "msg":   msg,
        "level": level,
        "ts":    datetime.utcnow().strftime("%H:%M:%S"),
    })
    logger.debug(f"[script_runner] {msg}")

"""
signup_runner.py — HTTP signup bot engine for the Entradas UI.

Accepts dynamic inputs (URL, emails, proxies, threads, country, 2captcha key)
sent from the web UI. Streams logs back via a queue for SSE.

Usage (called by app.py):
    runner = SignupRunner(config_dict)
    runner.start()
    runner.stop()
    runner.get_status()
    runner.log_queue  ← read by SSE endpoint
"""

import logging
import queue
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ── Success / failure detection keywords ──────────────────────────────────────
SUCCESS_KEYWORDS = [
    "thank you", "thanks", "subscribed", "success", "confirmed",
    "you're in", "you are in", "welcome", "signed up", "check your email",
]
FAILURE_KEYWORDS = [
    "invalid email", "already subscribed", "already registered",
    "please try again", "something went wrong", "error occurred",
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]


# ══════════════════════════════════════════════════════════════════════════════
# SignupRunner
# ══════════════════════════════════════════════════════════════════════════════

class SignupRunner:
    """
    One instance per signup job. Create a new one for each run.
    """

    def __init__(self, cfg: dict):
        """
        cfg keys:
            url           (str)   — signup page URL
            emails        (list)  — list of email strings
            proxies       (list)  — list of "ip:port:user:pass" strings
            threads       (int)   — max concurrent threads (default 50)
            country       (str)   — country to select in dropdown
            twocaptcha_key(str)   — 2captcha API key (empty = skip captcha)
            discord_webhook(str)  — optional Discord webhook for per-signup alerts
        """
        self.url              = cfg.get("url", "").strip()
        self.emails           = [e.strip() for e in cfg.get("emails", []) if e.strip()]
        self.proxies          = [p.strip() for p in cfg.get("proxies", []) if p.strip()]
        self.threads          = int(cfg.get("threads", 50))
        self.country          = cfg.get("country", "").strip()
        self.twocaptcha_key   = cfg.get("twocaptcha_key", "").strip()
        self.discord_webhook  = cfg.get("discord_webhook", "").strip()

        # State
        self._stop_event = threading.Event()
        self._thread     = None
        self._running    = False

        # Stats (thread-safe)
        self._lock  = threading.Lock()
        self.stats  = {
            "total":    len(self.emails),
            "success":  0,
            "failed":   0,
            "error":    0,
            "running":  0,
            "done":     0,
        }

        # Log queue — read by SSE endpoint in app.py
        self.log_queue: queue.Queue = queue.Queue(maxsize=1000)

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(self):
        """Start signup run in a background thread."""
        if self._running:
            return
        self._running    = True
        self._stop_event = threading.Event()
        self._thread     = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._log("info", f"🚀 Signup bot started — {len(self.emails)} emails, {self.threads} threads")

    def stop(self):
        """Request stop. Running threads finish their current signup."""
        self._stop_event.set()
        self._running = False
        self._log("warning", "⏹ Stop requested — finishing current signups...")

    def is_running(self) -> bool:
        return self._running

    def get_status(self) -> dict:
        with self._lock:
            return {**self.stats, "running": self._running}

    # ── Internal ───────────────────────────────────────────────────────────────

    def _send_discord(self, email: str, success: bool, detail: str = ""):
        """Send per-signup Discord notification. Skips silently if no webhook set."""
        if not self.discord_webhook:
            return
        try:
            emoji  = "✅" if success else "❌"
            color  = 0x00FF88 if success else 0xFF4D6A
            fields = [
                {"name": "Email",   "value": f"`{email}`",       "inline": True},
                {"name": "Country", "value": self.country or "—","inline": True},
                {"name": "URL",     "value": self.url[:60],       "inline": False},
            ]
            if detail:
                fields.append({"name": "Detail", "value": detail[:200], "inline": False})

            payload = {
                "username": "Entradas Signup Bot",
                "embeds": [{
                    "title":       f"{emoji} Signup {'Success' if success else 'Failed'}",
                    "color":       color,
                    "fields":      fields,
                    "footer":      {"text": "Entradas Ticket Scout • Signup Bot"},
                    "timestamp":   time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }]
            }
            requests.post(self.discord_webhook, json=payload, timeout=8)
        except Exception as e:
            self._log("debug", f"Discord send error: {e}")

    def _send_discord_summary(self):
        """Send a summary embed when the full run finishes."""
        if not self.discord_webhook:
            return
        try:
            s = self.stats
            total   = s["total"]
            success = s["success"]
            failed  = s["failed"]
            errors  = s["error"]
            rate    = f"{round(success/total*100)}%" if total else "0%"

            payload = {
                "username": "Entradas Signup Bot",
                "embeds": [{
                    "title":       "📊 Signup Run Complete",
                    "color":       0x00FF88,
                    "description": f"Finished signing up **{total}** emails to:\n`{self.url}`",
                    "fields": [
                        {"name": "✅ Success",  "value": str(success), "inline": True},
                        {"name": "❌ Failed",   "value": str(failed),  "inline": True},
                        {"name": "⚠️ Errors",   "value": str(errors),  "inline": True},
                        {"name": "📈 Rate",     "value": rate,         "inline": True},
                        {"name": "🧵 Threads",  "value": str(self.threads), "inline": True},
                        {"name": "🌍 Country",  "value": self.country or "—", "inline": True},
                    ],
                    "footer": {"text": "Entradas Ticket Scout • Signup Bot"},
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }]
            }
            requests.post(self.discord_webhook, json=payload, timeout=8)
        except Exception as e:
            self._log("debug", f"Discord summary error: {e}")

    def _log(self, level: str, msg: str):
        """Push log entry to queue for SSE streaming."""
        try:
            self.log_queue.put_nowait({
                "level": level,
                "msg":   msg,
                "ts":    time.strftime("%H:%M:%S"),
            })
        except queue.Full:
            pass
        # Also send to Python logger
        getattr(logger, level if level in ("info","warning","error","debug") else "info")(msg)

    def _run(self):
        """Main executor loop."""
        try:
            with ThreadPoolExecutor(max_workers=self.threads) as executor:
                futures = {
                    executor.submit(self._signup_one, i, email): email
                    for i, email in enumerate(self.emails)
                    if not self._stop_event.is_set()
                }
                for future in as_completed(futures):
                    if self._stop_event.is_set():
                        for f in futures:
                            f.cancel()
                        break
                    try:
                        future.result()
                    except Exception as e:
                        self._log("error", f"Thread error: {e}")
        except Exception as e:
            self._log("error", f"Runner crashed: {e}")
        finally:
            self._running = False
            with self._lock:
                s = self.stats
            self._log(
                "info",
                f"✅ Done — {s['success']} success | {s['failed']} failed | {s['error']} errors"
            )
            self._send_discord_summary()

    def _signup_one(self, index: int, email: str):
        """Full HTTP signup flow for one email."""
        with self._lock:
            self.stats["running"] += 1

        try:
            session = self._make_session(index)

            # ── GET page ──────────────────────────────────────────
            try:
                resp = session.get(self.url, timeout=20, allow_redirects=True)
                resp.raise_for_status()
            except requests.RequestException as e:
                raise Exception(f"GET failed: {e}")

            # ── Parse form ────────────────────────────────────────
            action_url, form_data = self._extract_form(resp.text, email)
            if form_data is None:
                raise Exception("No signup form found on page")

            post_url = (
                action_url if action_url and action_url.startswith("http")
                else urljoin(self.url, action_url) if action_url
                else self.url
            )

            # ── Captcha check ─────────────────────────────────────
            captcha_type, sitekey = self._detect_captcha(resp.text)
            if captcha_type:
                token = self._solve_captcha(captcha_type, sitekey)
                if token:
                    field = "g-recaptcha-response" if captcha_type == "recaptcha" else "h-captcha-response"
                    form_data[field] = token
                    self._log("info", f"[{index:03d}] 🔐 Captcha solved for {email}")
                else:
                    self._log("warning", f"[{index:03d}] Captcha not solved — submitting anyway")
            else:
                self._log("debug", f"[{index:03d}] No captcha — free submit")

            # ── Human delay ───────────────────────────────────────
            time.sleep(random.uniform(0.5, 2.0))

            # ── POST ──────────────────────────────────────────────
            session.headers.update({
                "Referer":      self.url,
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin":       self.url.rstrip("/"),
            })
            try:
                post_resp = session.post(post_url, data=form_data, timeout=20, allow_redirects=True)
            except requests.RequestException as e:
                raise Exception(f"POST failed: {e}")

            # ── Detect success ────────────────────────────────────
            success = self._detect_success(post_resp.text, post_resp.status_code)

            with self._lock:
                if success:
                    self.stats["success"] += 1
                else:
                    self.stats["failed"] += 1
                self.stats["done"] += 1
                self.stats["running"] -= 1

            status_icon = "✅" if success else "❌"
            self._log(
                "info" if success else "warning",
                f"[{index:03d}] {status_icon} {email}"
            )
            self._send_discord(email, success)

        except Exception as e:
            with self._lock:
                self.stats["error"] += 1
                self.stats["done"]  += 1
                self.stats["running"] -= 1
            self._log("error", f"[{index:03d}] ❌ {email} — {e}")
            self._send_discord(email, False, str(e)[:200])

    # ── Session factory ────────────────────────────────────────────────────────

    def _make_session(self, index: int) -> requests.Session:
        session = requests.Session()
        if self.proxies:
            raw = self.proxies[index % len(self.proxies)]
            parts = raw.split(":")
            if len(parts) == 4:
                ip, port, user, pw = parts
                proxy_url = f"http://{user}:{pw}@{ip}:{port}"
            elif len(parts) == 2:
                proxy_url = f"http://{raw}"
            else:
                proxy_url = None
            if proxy_url:
                session.proxies.update({"http": proxy_url, "https": proxy_url})

        session.headers.update({
            "User-Agent":      random.choice(USER_AGENTS),
            "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection":      "keep-alive",
        })
        return session

    # ── Form parsing ───────────────────────────────────────────────────────────

    def _extract_form(self, html: str, email: str):
        soup = BeautifulSoup(html, "html.parser")

        # Find form with email input
        target_form = None
        for form in soup.find_all("form"):
            for inp in form.find_all("input"):
                t = (inp.get("type") or "").lower()
                n = (inp.get("name") or "").lower()
                p = (inp.get("placeholder") or "").lower()
                if t == "email" or "email" in n or "email" in p:
                    target_form = form
                    break
            if target_form:
                break
        if not target_form:
            forms = soup.find_all("form")
            target_form = forms[0] if forms else None
        if not target_form:
            return None, None

        form_data = {}

        for inp in target_form.find_all("input"):
            t    = (inp.get("type") or "text").lower()
            name = inp.get("name") or ""
            val  = inp.get("value") or ""
            if not name:
                continue

            if t == "hidden":
                form_data[name] = val
            elif t == "email":
                form_data[name] = email
            elif t == "text":
                form_data[name] = val
            elif t == "checkbox":
                cb_id  = (inp.get("id") or "").upper()
                cb_nm  = name.upper()
                combo  = f"{cb_id} {cb_nm} {val}".upper()
                # Skip Def Jam / Universal checkboxes
                if any(x in combo for x in ("DEF JAM", "UNIVERSAL", "ISLAND", "REPUBLIC")):
                    pass
                else:
                    form_data[name] = val or "on"
            elif t == "submit":
                form_data[name] = val or "Submit"

        # Country select
        select = target_form.find("select")
        if select and self.country:
            sname = select.get("name") or ""
            if sname:
                chosen = None
                for opt in select.find_all("option"):
                    txt = (opt.text or "").strip()
                    optval = opt.get("value") or txt
                    if (self.country.lower() in txt.lower() or
                            self.country.lower() == optval.lower()):
                        chosen = optval
                        break
                if chosen:
                    form_data[sname] = chosen

        action = target_form.get("action") or ""
        return action, form_data

    # ── Captcha detection ──────────────────────────────────────────────────────

    def _detect_captcha(self, html: str):
        soup = BeautifulSoup(html, "html.parser")
        rc = soup.find(class_="g-recaptcha")
        if rc:
            sk = rc.get("data-sitekey")
            if sk:
                return "recaptcha", sk
        hc = soup.find(class_="h-captcha")
        if hc:
            sk = hc.get("data-sitekey")
            if sk:
                return "hcaptcha", sk
        # Check script tags for embedded sitekey
        for script in soup.find_all("script", src=True):
            if "recaptcha" in script.get("src", ""):
                m = re.search(r'["\']sitekey["\']\s*:\s*["\']([^"\']+)["\']', html)
                if m:
                    return "recaptcha", m.group(1)
        return None, None

    # ── 2captcha solver ────────────────────────────────────────────────────────

    def _solve_captcha(self, captcha_type: str, sitekey: str):
        if not self.twocaptcha_key:
            self._log("warning", "Captcha detected but no 2captcha key set")
            return None

        self._log("info", f"🔐 Sending {captcha_type} to 2captcha...")

        # Submit
        method = "userrecaptcha" if captcha_type == "recaptcha" else "hcaptcha"
        key_field = "googlekey" if captcha_type == "recaptcha" else "sitekey"
        try:
            r = requests.post("http://2captcha.com/in.php", data={
                "key":      self.twocaptcha_key,
                "method":   method,
                key_field:  sitekey,
                "pageurl":  self.url,
                "json":     1,
            }, timeout=15)
            result = r.json()
            if result.get("status") != 1:
                self._log("error", f"2captcha error: {result.get('request')}")
                return None
            captcha_id = result["request"]
        except Exception as e:
            self._log("error", f"2captcha submit failed: {e}")
            return None

        # Poll
        for attempt in range(24):
            time.sleep(5)
            try:
                pr = requests.get("http://2captcha.com/res.php", params={
                    "key": self.twocaptcha_key, "action": "get",
                    "id": captcha_id, "json": 1,
                }, timeout=10)
                pr_json = pr.json()
                if pr_json.get("status") == 1:
                    self._log("info", f"✅ 2captcha solved (attempt {attempt+1})")
                    return pr_json["request"]
                elif pr_json.get("request") != "CAPCHA_NOT_READY":
                    self._log("error", f"2captcha error: {pr_json.get('request')}")
                    return None
            except Exception as e:
                self._log("error", f"2captcha poll error: {e}")
                return None

        self._log("error", "2captcha timed out")
        return None

    # ── Success detection ──────────────────────────────────────────────────────

    def _detect_success(self, html: str, status_code: int) -> bool:
        if status_code not in (200, 201, 302):
            return False
        hl = html.lower()
        for kw in SUCCESS_KEYWORDS:
            if kw in hl:
                return True
        for kw in FAILURE_KEYWORDS:
            if kw in hl:
                return False
        return status_code in (200, 201, 302)


# ── Module-level singleton ─────────────────────────────────────────────────────
_current_runner: SignupRunner = None
_runner_lock = threading.Lock()


def get_runner() -> SignupRunner:
    return _current_runner


def start_runner(cfg: dict) -> SignupRunner:
    global _current_runner
    with _runner_lock:
        if _current_runner and _current_runner.is_running():
            _current_runner.stop()
        _current_runner = SignupRunner(cfg)
        _current_runner.start()
        return _current_runner


def stop_runner():
    global _current_runner
    with _runner_lock:
        if _current_runner:
            _current_runner.stop()

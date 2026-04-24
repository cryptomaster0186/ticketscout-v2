"""
signup_playwright.py — Playwright-based signup runner for JS-heavy sites.

Architecture: ONE browser process shared across all signups.
Each signup gets its own browser context (isolated cookies/storage) running
in a thread. This is 5–10x faster than launching a new browser per email
because browser startup (~3s) only happens once.

Thread count = number of parallel tabs open simultaneously.

For simple HTML form sites → signup_runner.py (HTTP, 50–200 threads)
For JS-heavy sites like TAE/AE → this file (Playwright, 5–20 tabs)
"""

import logging
import queue
import threading
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

logger = logging.getLogger(__name__)

SUCCESS_KEYWORDS = [
    "thank you for signing up",
    "thank you for registering",
    "thanks for signing up",
    "you have been subscribed",
    "successfully subscribed",
    "check your email",
    "confirmation email",
    "you're in",
    "welcome",
    "signed up",
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]


class PlaywrightSignupRunner:
    """
    Single shared browser + parallel contexts per signup.
    Much faster than one browser per email.
    """

    def __init__(self, cfg: dict):
        self.url             = cfg.get("url", "").strip()
        self.emails          = [e.strip() for e in cfg.get("emails", []) if e.strip()]
        self.proxies         = [p.strip() for p in cfg.get("proxies", []) if p.strip()]
        self.threads         = min(int(cfg.get("threads", 5)), 20)
        self.country         = cfg.get("country", "Netherlands").strip()
        self.discord_webhook = cfg.get("discord_webhook", "").strip()

        self._stop_event = threading.Event()
        self._running    = False
        self._thread     = None
        self._lock       = threading.Lock()
        self._browser    = None   # shared browser instance

        self.stats = {
            "total":   len(self.emails),
            "success": 0,
            "failed":  0,
            "error":   0,
            "running": 0,
            "done":    0,
        }
        self.log_queue: queue.Queue = queue.Queue(maxsize=2000)

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(self):
        if self._running:
            return
        self._running    = True
        self._stop_event = threading.Event()
        self._thread     = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._log("info", f"🚀 Browser signup started — {len(self.emails)} emails, {self.threads} parallel tabs")
        if not self.proxies:
            self._log("warning", "⚠️  No proxies configured — Imperva may block headless browsers on protected sites")

    def stop(self):
        self._stop_event.set()
        self._running = False
        self._log("warning", "⏹ Stop requested — finishing current signups…")

    def is_running(self) -> bool:
        return self._running

    def get_status(self) -> dict:
        with self._lock:
            return {**self.stats, "running": self._running}

    # ── Logging ────────────────────────────────────────────────────────────────

    def _log(self, level: str, msg: str):
        try:
            self.log_queue.put_nowait({"level": level, "msg": msg, "ts": time.strftime("%H:%M:%S")})
        except queue.Full:
            pass
        getattr(logger, level if level in ("info", "warning", "error", "debug") else "info")(msg)

    # ── Main runner ────────────────────────────────────────────────────────────

    def _run(self):
        # Playwright sync API uses greenlets — browser objects CANNOT be shared
        # across threads. Each thread gets its own sync_playwright() + browser.
        # To amortize startup cost, each thread processes a slice of emails
        # sequentially (thread 0 → emails 0,3,6…  thread 1 → emails 1,4,7… etc.)

        # Split emails into per-thread batches
        batches = [[] for _ in range(self.threads)]
        for i, email in enumerate(self.emails):
            batches[i % self.threads].append((i, email))

        self._log("info", f"🌐 Starting {self.threads} browser(s) — {len(self.emails)} emails total")
        if not self.proxies:
            self._log("warning", "⚠️  No proxies — Imperva may block headless browsers on protected sites")

        try:
            with ThreadPoolExecutor(max_workers=self.threads) as executor:
                futures = [
                    executor.submit(self._worker, thread_idx, batch)
                    for thread_idx, batch in enumerate(batches)
                    if batch
                ]
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        self._log("error", f"Worker crashed: {e}")
        except Exception as e:
            self._log("error", f"Runner crashed: {e}")
        finally:
            self._running = False
            with self._lock:
                s = self.stats
            self._log("info", f"✅ Done — {s['success']} success | {s['failed']} failed | {s['error']} errors")
            self._send_discord_summary()

    def _worker(self, thread_idx: int, batch: list):
        """One thread = one browser instance, processes its email batch sequentially."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            proxy_cfg = self._get_proxy(thread_idx)
            launch_opts = {
                "headless": True,
                "args": [
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--disable-infobars",
                    "--window-size=1280,800",
                ],
            }
            if proxy_cfg:
                launch_opts["proxy"] = proxy_cfg

            browser = pw.chromium.launch(**launch_opts)
            self._log("info", f"[T{thread_idx:02d}] Browser launched ({len(batch)} emails)")
            try:
                for (index, email) in batch:
                    if self._stop_event.is_set():
                        break
                    self._signup_one(index, email, browser)
            finally:
                browser.close()
                self._log("info", f"[T{thread_idx:02d}] Browser closed")

    # ── Single signup — uses the thread's own browser, never crosses threads ──

    def _signup_one(self, index: int, email: str, browser):
        from playwright.sync_api import TimeoutError as PWTimeout

        with self._lock:
            self.stats["running"] += 1

        success = False
        try:
            # Each signup gets its own isolated context (fresh cookies/storage)
            ctx_opts = {
                "user_agent": random.choice(USER_AGENTS),
                "locale":     "en-US",
                "viewport":   {"width": 1280, "height": 800},
            }
            # Context-level proxy overrides browser-level proxy if set
            proxy_cfg = self._get_proxy(index)
            if proxy_cfg:
                ctx_opts["proxy"] = proxy_cfg

            context = browser.new_context(**ctx_opts)
            page    = context.new_page()

            # ── Intercept the TAE AJAX response to get the real success/fail ──
            ajax_responses = []

            def handle_response(response):
                try:
                    if "admin-ajax.php" in response.url or "ajax" in response.url.lower():
                        body = response.text()
                        ajax_responses.append({"url": response.url, "status": response.status, "body": body})
                except Exception:
                    pass

            page.on("response", handle_response)

            # Hide automation signals from JS
            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins',   {get: () => [1,2,3]});
                window.chrome = {runtime: {}};
            """)

            try:
                # ── Load page ─────────────────────────────────────────────────
                self._log("info", f"[{index:03d}] Loading {self.url}")
                page.goto(self.url, wait_until="domcontentloaded", timeout=45000)

                # Wait a moment for JS to fire
                time.sleep(2)

                # ── Dismiss cookie banners via JS (no Playwright clicks) ──────
                page.evaluate("""() => {
                    const texts = ['accept all','accept','i understand','agree','got it','close','ok'];
                    document.querySelectorAll('button, [role="button"]').forEach(btn => {
                        if (texts.some(t => btn.innerText.toLowerCase().trim().startsWith(t))) {
                            btn.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true}));
                        }
                    });
                }""")
                time.sleep(1)

                # ── Scroll to trigger lazy-loaded TAE form ────────────────────
                page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
                time.sleep(0.8)
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(0.8)
                page.evaluate("window.scrollTo(0, 0)")
                time.sleep(0.5)

                # ── Wait for form to appear (JS polling, no Playwright locators)
                form_ready = page.evaluate("""() => {
                    return !!document.querySelector(
                        '#ae-cform-input-reg-email-1, input[name="email"][type="email"], input[type="email"]'
                    );
                }""")

                if not form_ready:
                    # Give it a few more seconds then check again
                    time.sleep(4)
                    form_ready = page.evaluate("""() => {
                        return !!document.querySelector(
                            '#ae-cform-input-reg-email-1, input[name="email"][type="email"], input[type="email"]'
                        );
                    }""")

                if not form_ready:
                    snippet = page.content()[:600].replace("\n", " ")
                    raise Exception(f"Form not found after waiting. Page: {snippet}")

                self._log("debug", f"[{index:03d}] Form found — filling via JS")

                # ── Fill entire form via JS — zero Playwright pointer events ───
                # This completely bypasses the jQuery modal blocker overlay.
                result = page.evaluate(f"""() => {{
                    const country = {repr(self.country)};
                    const email   = {repr(email)};

                    // 1. Email
                    const emailInput = document.querySelector('#ae-cform-input-reg-email-1')
                                    || document.querySelector('input[name="email"][type="email"]')
                                    || document.querySelector('input[type="email"]');
                    if (!emailInput) return 'no_email_input';
                    emailInput.focus();
                    emailInput.value = email;
                    emailInput.dispatchEvent(new Event('input',  {{bubbles:true}}));
                    emailInput.dispatchEvent(new Event('change', {{bubbles:true}}));
                    emailInput.dispatchEvent(new Event('blur',   {{bubbles:true}}));

                    // 2. Country
                    const countryEl = document.querySelector('select[name="country"]')
                                   || document.querySelector('.ae-cform-input-country select')
                                   || document.querySelector('select');
                    if (countryEl) {{
                        let matched = false;
                        for (const opt of countryEl.options) {{
                            if (opt.text.toLowerCase().includes(country.toLowerCase())) {{
                                countryEl.value = opt.value;
                                matched = true;
                                break;
                            }}
                        }}
                        if (!matched) countryEl.value = 'NL';
                        countryEl.dispatchEvent(new Event('change', {{bubbles:true}}));
                    }}

                    // 3. Checkboxes — use native prototype setter so Parsley :checked passes
                    const nativeSetter = Object.getOwnPropertyDescriptor(
                        HTMLInputElement.prototype, 'checked'
                    ).set;
                    document.querySelectorAll('.ae-cform-optin-checkbox').forEach(cb => {{
                        const id = cb.getAttribute('data-ae-optin-id');
                        if (id === '2') {{
                            nativeSetter.call(cb, true);
                            cb.dispatchEvent(new Event('input',  {{bubbles:true}}));
                            cb.dispatchEvent(new Event('change', {{bubbles:true}}));
                        }} else {{
                            nativeSetter.call(cb, false);
                            cb.dispatchEvent(new Event('change', {{bubbles:true}}));
                        }}
                    }});

                    // 4. Trigger Parsley re-validation
                    try {{
                        const form = document.querySelector(
                            '#ae-cform-modal-container-1 form, #ae-cform-container-1 form, form[id^="ae-cform"]'
                        );
                        if (form && window.$ && $(form).parsley) $(form).parsley().validate();
                    }} catch(e) {{}}

                    return 'filled';
                }}""")
                self._log("debug", f"[{index:03d}] Form fill result: {result}")

                # ── Human-like pause ──────────────────────────────────────────
                time.sleep(random.uniform(0.8, 1.5))

                # ── Submit via JS dispatchEvent — no pointer events needed ─────
                submit_result = page.evaluate("""() => {
                    const submitBtn = document.querySelector('input[name="ae-cform-email-reg-submit"]')
                                   || document.querySelector('#ae-cform-modal-container-1 input[type="submit"]')
                                   || document.querySelector('#ae-cform-container-1 input[type="submit"]')
                                   || document.querySelector('input[type="submit"]')
                                   || document.querySelector('button[type="submit"]');
                    if (submitBtn) {
                        submitBtn.dispatchEvent(new MouseEvent('click', {bubbles:true, cancelable:true}));
                        return 'clicked:' + (submitBtn.id || submitBtn.name || submitBtn.type);
                    }
                    // Last resort: submit the form directly
                    const form = document.querySelector(
                        '#ae-cform-email-reg-1, form[id^="ae-cform"], form[data-parsley-validate]'
                    );
                    if (form) {
                        form.dispatchEvent(new Event('submit', {bubbles:true, cancelable:true}));
                        return 'form_submit';
                    }
                    return 'no_submit_found';
                }""")
                self._log("debug", f"[{index:03d}] Submit result: {submit_result}")

                # ── Wait for AJAX response ────────────────────────────────────
                time.sleep(4)

                # First check the intercepted AJAX response — most reliable
                detail = ""
                for resp in ajax_responses:
                    b = resp["body"].lower()
                    self._log("debug", f"[{index:03d}] AJAX {resp['url'][-40:]} → {resp['body'][:200]}")
                    if any(x in b for x in ['"success":true', '"status":"success"', '"success": true']):
                        success = True
                        break
                    elif '"fail_status":6' in b or '"fail_status": 6' in b:
                        detail = "WordPress user registration disabled on this site"
                        break
                    elif any(x in b for x in ['"success":false', '"success": false']):
                        import re
                        msg = re.search(r'"message"\s*:\s*"([^"]+)"', resp["body"])
                        detail = msg.group(1) if msg else "Server returned success:false"
                        break
                    elif "already" in b:
                        detail = "Email already registered"
                        success = True
                        break

                # Fall back to page text only if no AJAX response was captured
                if not ajax_responses:
                    page_text = page.content().lower()
                    self._log("debug", f"[{index:03d}] No AJAX captured — checking page text")
                    if any(kw in page_text for kw in SUCCESS_KEYWORDS):
                        success = True
                    elif "select one of the sign" in page_text or "parsley-error" in page_text:
                        detail = "Parsley validation failed (checkbox)"
                    elif "captcha" in page_text or "i am human" in page_text:
                        detail = "CAPTCHA challenge — proxy needed"
                    else:
                        detail = "Unknown failure (no AJAX response captured)"

                if success:
                    self._log("info",    f"[{index:03d}] ✅ {email}")
                else:
                    self._log("warning", f"[{index:03d}] ❌ {email} — {detail or 'no success signal'}")

            finally:
                context.close()

            with self._lock:
                if success:
                    self.stats["success"] += 1
                else:
                    self.stats["failed"] += 1
                self.stats["done"]    += 1
                self.stats["running"] -= 1

            self._send_discord(email, success)

        except Exception as e:
            with self._lock:
                self.stats["error"]   += 1
                self.stats["done"]    += 1
                self.stats["running"] -= 1
            self._log("error", f"[{index:03d}] ❌ {email} — {e}")
            self._send_discord(email, False, str(e)[:200])

    # ── Proxy config ───────────────────────────────────────────────────────────

    def _get_proxy(self, index: int) -> dict:
        if not self.proxies:
            return None
        raw = self.proxies[index % len(self.proxies)]
        parts = raw.split(":")
        if len(parts) == 4:
            ip, port, user, pw = parts
            return {"server": f"http://{ip}:{port}", "username": user, "password": pw}
        elif len(parts) == 2:
            return {"server": f"http://{raw}"}
        return None

    # ── Discord ────────────────────────────────────────────────────────────────

    def _send_discord(self, email: str, success: bool, detail: str = ""):
        if not self.discord_webhook:
            return
        try:
            emoji = "✅" if success else "❌"
            color = 0x00FF88 if success else 0xFF4D6A
            fields = [
                {"name": "Email",   "value": f"`{email}`",        "inline": True},
                {"name": "Country", "value": self.country or "—", "inline": True},
                {"name": "URL",     "value": self.url[:60],        "inline": False},
            ]
            if detail:
                fields.append({"name": "Detail", "value": detail[:200], "inline": False})
            payload = {
                "username": "Entradas Signup Bot",
                "embeds": [{
                    "title":     f"{emoji} Signup {'Success' if success else 'Failed'}",
                    "color":     color,
                    "fields":    fields,
                    "footer":    {"text": "Entradas Ticket Scout • Signup Bot"},
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }]
            }
            requests.post(self.discord_webhook, json=payload, timeout=8)
        except Exception as e:
            self._log("debug", f"Discord error: {e}")

    def _send_discord_summary(self):
        if not self.discord_webhook:
            return
        try:
            s     = self.stats
            total = s["total"]
            rate  = f"{round(s['success']/total*100)}%" if total else "0%"
            payload = {
                "username": "Entradas Signup Bot",
                "embeds": [{
                    "title":       "📊 Signup Run Complete",
                    "color":       0x00FF88,
                    "description": f"Finished **{total}** emails → `{self.url}`",
                    "fields": [
                        {"name": "✅ Success", "value": str(s["success"]), "inline": True},
                        {"name": "❌ Failed",  "value": str(s["failed"]),  "inline": True},
                        {"name": "⚠️ Errors",  "value": str(s["error"]),   "inline": True},
                        {"name": "📈 Rate",    "value": rate,              "inline": True},
                        {"name": "🌍 Country", "value": self.country,      "inline": True},
                    ],
                    "footer":    {"text": "Entradas Ticket Scout • Signup Bot"},
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }]
            }
            requests.post(self.discord_webhook, json=payload, timeout=8)
        except Exception as e:
            self._log("debug", f"Discord summary error: {e}")

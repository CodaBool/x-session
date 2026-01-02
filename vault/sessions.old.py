#!/usr/bin/env python3
"""
Usage:
  python3 create_session_browser.py <username> <password> [totp_seed] [--append sessions.jsonl] [--headless]
"""

import sys
import json
import asyncio
import pyotp
import nodriver as uc
import os
from datetime import datetime, timezone

def _ts():
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

def _tmp(name):
    return os.path.join(os.getenv("TMPDIR", "/tmp"), name)

def normalize_eval(obj):
    if isinstance(obj, dict):
        return obj

    if isinstance(obj, list):
        out = {}
        for item in obj:
            if not (isinstance(item, (list, tuple)) and len(item) == 2):
                continue
            k, v = item
            if isinstance(v, dict) and "value" in v:
                out[k] = v["value"]
            else:
                out[k] = v
        return out

    return {}



async def login_ui_state(tab):
    js = r"""
    (() => {
      const els = Array.from(document.querySelectorAll('input'));
      const vis = (el) => {
        const r = el.getBoundingClientRect();
        const s = getComputedStyle(el);
        return r.width > 0 && r.height > 0 &&
               s.visibility !== 'hidden' && s.display !== 'none';
      };

      const scriptFail = document.getElementById('ScriptLoadFailure');
      const script_fail_visible = !!(
        scriptFail &&
        scriptFail.getBoundingClientRect().width > 0 &&
        scriptFail.getBoundingClientRect().height > 0
      );

      const has_username_candidate = [
        'input[autocomplete="username"]',
        'input[name="text"]',
        'input[data-testid="ocfEnterTextTextInput"]',
        'input[type="text"]',
      ].some(s => {
        const el = document.querySelector(s);
        return el && vis(el);
      });

      const body_text_head =
        (document.body?.innerText || '').trim().slice(0, 200);

      return {
        total_inputs: els.length,
        visible_inputs: els.filter(vis).length,
        script_fail_visible,
        has_username_candidate,
        body_text_head
      };
    })()
    """
    try:
        raw = await asyncio.wait_for(tab.evaluate(js), timeout=4)
        return normalize_eval(raw)
    except Exception:
        return {
            "total_inputs": -1,
            "visible_inputs": -1,
            "script_fail_visible": False,
            "has_username_candidate": False,
            "body_text_head": ""
        }


async def wait_for_login_inputs_or_retry(tab, url, retries=3):
    for attempt in range(1, retries + 1):
        for tick in range(120):  # ~30s
            st = await login_ui_state(tab)

            if tick % 8 == 0:
                print(f"[*] UI state: {st}", file=sys.stderr)

            if st.get("has_username_candidate"):
                return True

            # Soft-block / error screen
            if looks_like_softblock(st):
                print("[!] X returned 'Something went wrong' screen (soft-block).", file=sys.stderr)
                break

            if st.get("script_fail_visible"):
                print("[!] ScriptLoadFailure visible", file=sys.stderr)
                break

            await asyncio.sleep(0.25)

        print(f"[!] Login UI not ready (attempt {attempt}/{retries}). Reloading…", file=sys.stderr)
        try:
            await asyncio.wait_for(tab.get(url), timeout=30)
            await asyncio.sleep(2)
        except Exception as e:
            print(f"[!] Reload navigation failed: {e}", file=sys.stderr)

    return False



async def click_button_by_text(tab, wanted, timeout=10):
    """
    Click a visible button whose text matches one of `wanted` (case-insensitive).
    Works for both <button> and div[role=button].
    """
    wanted_l = [w.strip().lower() for w in wanted]

    js = r"""
    (wanted) => {
      const isVisible = (el) => {
        const r = el.getBoundingClientRect();
        const s = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
      };

      const candidates = [
        ...document.querySelectorAll('button'),
        ...document.querySelectorAll('div[role="button"]'),
      ].filter(isVisible);

      for (const el of candidates) {
        const t = (el.innerText || el.textContent || '').trim().toLowerCase();
        if (!t) continue;
        for (const w of wanted) {
          if (t === w || t.includes(w)) {
            el.click();
            return {clicked: true, text: t};
          }
        }
      }
      return {clicked: false, text: null};
    }
    """

    end = asyncio.get_event_loop().time() + timeout
    last = None
    while asyncio.get_event_loop().time() < end:
        try:
            raw = await asyncio.wait_for(tab.evaluate(js, wanted_l), timeout=4)
            res = normalize_eval(raw)  # you already added this helper
            last = res
            if res.get("clicked"):
                return True, res
        except Exception:
            pass
        await asyncio.sleep(0.25)

    return False, last


async def wait_for_any_selector(tab, selectors, timeout=20):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        for sel in selectors:
            try:
                el = await asyncio.wait_for(tab.select(sel), timeout=2)
            except Exception:
                el = None
            if el:
                return sel
        await asyncio.sleep(0.25)
    return None

def looks_like_softblock(st):
    txt = (st.get("body_text_head") or "").lower()
    return "something went wrong" in txt and "retry" in txt


async def dump_inputs(tab, base):
    try:
        inputs = await asyncio.wait_for(tab.evaluate("""
        (() => {
          const els = Array.from(document.querySelectorAll('input'));
          const vis = (el) => {
            const r = el.getBoundingClientRect();
            const s = getComputedStyle(el);
            return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
          };
          return els.slice(0, 50).map(el => ({
            visible: vis(el),
            type: el.getAttribute('type'),
            name: el.getAttribute('name'),
            autocomplete: el.getAttribute('autocomplete'),
            testid: el.getAttribute('data-testid'),
            outer: el.outerHTML.slice(0, 300)
          }));
        })()
        """), timeout=5)
        with open(base + ".inputs.json", "w", encoding="utf-8") as f:
            json.dump(inputs, f, indent=2)
        print(f"[!] Wrote inputs snapshot: {base}.inputs.json", file=sys.stderr)
    except Exception as e:
        print(f"[!] Could not dump inputs snapshot: {e}", file=sys.stderr)

async def visible_input_count(tab):
    try:
        return await asyncio.wait_for(tab.evaluate("""
          (() => {
            const els = Array.from(document.querySelectorAll('input'));
            const vis = (el) => {
              const r = el.getBoundingClientRect();
              const s = getComputedStyle(el);
              return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
            };
            return els.filter(vis).length;
          })()
        """), timeout=3)
    except Exception:
        return -1


async def dump_debug(tab, label="debug", prefix=None):
    if prefix is not None:
        label = prefix
    stamp = _ts()
    base = _tmp(f"{label}-{stamp}")
    await dump_inputs(tab, base)


    # 1) URL + title are fast and often work even when DOM is weird
    try:
        url = await asyncio.wait_for(tab.evaluate("location.href"), timeout=3)
        title = await asyncio.wait_for(tab.evaluate("document.title"), timeout=3)
        with open(base + ".meta.txt", "w", encoding="utf-8") as f:
            f.write(f"url={url}\ntitle={title}\n")
        print(f"[!] Wrote debug meta: {base}.meta.txt", file=sys.stderr)
    except Exception as e:
        print(f"[!] Could not dump meta: {e}", file=sys.stderr)

    # 2) HTML dump with a hard timeout
    try:
        html = await asyncio.wait_for(tab.get_content(), timeout=8)
        with open(base + ".html", "w", encoding="utf-8") as f:
            f.write(html)
        print(f"[!] Wrote debug HTML: {base}.html", file=sys.stderr)
    except Exception as e:
        print(f"[!] Could not dump HTML: {e}", file=sys.stderr)


async def scripts_loaded(tab):
    try:
        return await asyncio.wait_for(
            tab.evaluate("Boolean(window.__SCRIPTS_LOADED__ && window.__SCRIPTS_LOADED__.main)"),
            timeout=3
        )
    except Exception:
        return False

async def wait_for_scripts_or_retry(tab, url, retries=3):
    for attempt in range(1, retries + 1):
        # wait up to ~15s for scripts
        for _ in range(60):
            if await scripts_loaded(tab):
                return True
            await asyncio.sleep(0.25)

        print(f"[!] Scripts not loaded (attempt {attempt}/{retries}). Reloading…", file=sys.stderr)
        try:
            # Hard reload via navigation (more reliable than location.reload in some cases)
            await asyncio.wait_for(tab.get(url), timeout=30)
        except Exception as e:
            print(f"[!] Reload navigation failed: {e}", file=sys.stderr)

    return False


async def find_with_hard_timeout(tab, selector, *, soft_timeout=10, hard_timeout=12):
    """
    tab.find() sometimes hangs; enforce a hard timeout at the asyncio level.
    """
    try:
        return await asyncio.wait_for(tab.find(selector, timeout=soft_timeout), timeout=hard_timeout)
    except asyncio.TimeoutError:
        return None

async def must_find_any(tab, selectors, *, timeout=20, label="element"):
    start = asyncio.get_event_loop().time()
    last_err = None

    while (asyncio.get_event_loop().time() - start) < timeout:
        for sel in selectors:
            try:
                el = await find_with_hard_timeout(tab, sel, soft_timeout=2, hard_timeout=4)
                if el is not None:
                    return el
            except Exception as e:
                last_err = e
        await asyncio.sleep(0.25)

    await dump_debug(tab, label=f"nodriver-{label}")
    raise RuntimeError(
        f"Could not find {label} after {timeout}s. Tried selectors: {selectors}. Last error: {last_err}"
    )



async def login_and_get_cookies(username, password, totp_seed=None, headless=False):
    chrome_bin = os.getenv("CHROME_BIN", "/usr/bin/chromium")
    chrome_args = [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--window-size=1280,720",
        "--disable-quic",
        "--disable-features=Translate,BackForwardCache",
        "--lang=en-US",
    ]

    login_url = "https://x.com/i/flow/login"

    last_error = None
    for attempt in range(1, 6):  # try up to 5 fresh sessions
        print(f"[*] Fresh browser attempt {attempt}/5", file=sys.stderr)
        browser = await uc.start(
            headless=headless,
            browser_executable_path=chrome_bin,
            args=chrome_args,
        )

        try:
            tab = await asyncio.wait_for(browser.get(login_url), timeout=30)
            await asyncio.sleep(3)

            try:
                url = await asyncio.wait_for(tab.evaluate("location.href"), timeout=5)
                print(f"[*] Landed on: {url}", file=sys.stderr)
            except Exception:
                pass

            ok = await wait_for_login_inputs_or_retry(tab, login_url, retries=2)
            if not ok:
                await dump_debug(tab, label="login-ui-not-ready")
                raise Exception("Login UI never rendered (soft-block or interstitial).")

            # ---- Username step (clean + deterministic) ----
            print("[*] Entering username...", file=sys.stderr)
            username_el = await must_find_any(
                tab,
                selectors=[
                    'input[autocomplete="username"]',
                    'input[name="text"]',
                    'input[data-testid="ocfEnterTextTextInput"]',
                    'input[type="text"]',
                ],
                timeout=20,
                label="username",
            )
            await username_el.send_keys(username)
            await asyncio.sleep(0.5)

            ok_click, info = await click_button_by_text(tab, ["next"])
            print(f"[*] Clicked Next? {ok_click} info={info}", file=sys.stderr)

            # Wait for password OR extra text challenge
            matched = await wait_for_any_selector(
                tab,
                selectors=[
                    'input[type="password"]',
                    'input[autocomplete="current-password"]',
                    'input[name="password"]',
                    'input[name="text"]',  # challenge step
                ],
                timeout=25
            )

            if matched == 'input[name="text"]':
                await dump_debug(tab, label="challenge-after-username")
                raise Exception("Got extra verification step after username (challenge).")

            if matched is None:
                await dump_debug(tab, label="no-password-after-next")
                raise Exception("Did not reach password step after clicking Next.")

            # ---- Password step ----
            print("[*] Entering password...", file=sys.stderr)
            pw_el = await must_find_any(
                tab,
                selectors=[
                    'input[autocomplete="current-password"]',
                    'input[name="password"]',
                    'input[type="password"]',
                ],
                timeout=25,
                label="password",
            )
            await pw_el.send_keys(password)
            await asyncio.sleep(0.5)

            ok_click, info = await click_button_by_text(tab, ["log in", "sign in"])
            print(f"[*] Clicked Log in? {ok_click} info={info}", file=sys.stderr)
            await asyncio.sleep(3)

            # ---- 2FA + cookies (your existing logic) ----
            page_content = await tab.get_content()
            if ("verification code" in page_content.lower()) or ("enter code" in page_content.lower()):
                if not totp_seed:
                    await dump_debug(tab, label="2fa-required")
                    raise Exception("2FA required but no TOTP seed provided")

                print("[*] 2FA detected, entering code...", file=sys.stderr)
                totp_code = pyotp.TOTP(totp_seed).now()
                code_el = await must_find_any(
                    tab,
                    selectors=['input[autocomplete="one-time-code"]', 'input[name="text"]', 'input[type="text"]'],
                    timeout=25,
                    label="2fa_code",
                )
                await code_el.send_keys(totp_code)
                await asyncio.sleep(4)

            print("[*] Retrieving cookies...", file=sys.stderr)
            for _ in range(30):
                cookies = await browser.cookies.get_all()
                cookies_dict = {c.name: c.value for c in cookies}
                if "auth_token" in cookies_dict and "ct0" in cookies_dict:
                    # same extraction logic you already have
                    user_id = None
                    twid = cookies_dict.get("twid")
                    if twid:
                        if "u%3D" in twid:
                            user_id = twid.split("u%3D")[1].split("&")[0].strip('"')
                        elif "u=" in twid:
                            user_id = twid.split("u=")[1].split("&")[0].strip('"')

                    cookies_dict["username"] = username
                    if user_id:
                        cookies_dict["id"] = user_id
                    return cookies_dict

                await asyncio.sleep(1)

            await dump_debug(tab, label="timeout-cookies")
            raise Exception("Timeout waiting for cookies")

        except Exception as e:
            last_error = e
            print(f"[!] Attempt {attempt} failed: {e}", file=sys.stderr)
            # backoff helps a lot with X soft-blocking
            await asyncio.sleep(2 * attempt)
        finally:
            try:
                browser.stop()
            except Exception:
                pass

    raise last_error or Exception("All attempts failed")


async def main():
    if len(sys.argv) < 3:
        print("Usage: python3 create_session_browser.py username password [totp_seed] [--append file.jsonl] [--headless]")
        sys.exit(1)

    username = sys.argv[1]
    password = sys.argv[2]
    totp_seed = None
    append_file = None
    headless = False

    # Parse optional arguments
    i = 3
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--append":
            if i + 1 < len(sys.argv):
                append_file = sys.argv[i + 1]
                i += 2
            else:
                print("[!] Error: --append requires a filename", file=sys.stderr)
                sys.exit(1)
        elif arg == "--headless":
            headless = True
            i += 1
        elif not arg.startswith("--"):
            if totp_seed is None:
                totp_seed = arg
            i += 1
        else:
            print(f"[!] Warning: Unknown argument: {arg}", file=sys.stderr)
            i += 1

    try:
        cookies = await login_and_get_cookies(username, password, totp_seed, headless)
        session = {
            "kind": "cookie",
            "username": cookies["username"],
            "id": cookies.get("id"),
            "auth_token": cookies["auth_token"],
            "ct0": cookies["ct0"],
        }
        output = json.dumps(session)

        if append_file:
            with open(append_file, "a", encoding="utf-8") as f:
                f.write(output + "\n")
            print(f"✓ Session appended to {append_file}", file=sys.stderr)
        else:
            print(output)

        return 0

    except Exception as error:
        print(f"[!] Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

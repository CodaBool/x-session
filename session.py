#!/usr/bin/env python3
"""
Requirements:
  pip install -r tools/requirements.txt

Usage:
  python3 tools/create_session_browser.py <username> <password> [totp_seed] [--append sessions.jsonl] [--headless] [--debug]

Examples:
  python3 tools/create_session_browser.py myusername mypassword TOTP_SECRET
  python3 tools/create_session_browser.py myusername mypassword TOTP_SECRET --append sessions.jsonl
  python3 tools/create_session_browser.py myusername mypassword TOTP_SECRET --headless
  python3 tools/create_session_browser.py myusername mypassword TOTP_SECRET --debug

Output:
  {"kind": "cookie", "username": "...", "id": "...", "auth_token": "...", "ct0": "..."}
"""

import asyncio
import json
import os
import sys

import nodriver as uc
import pyotp
from nodriver import cdp
from nodriver.cdp import network


LOGIN_URL = "https://x.com/i/flow/login"

# Use the real domains that set the cookies you need.
# For the x.com-style flow, these were the domains that worked.
COOKIE_URLS = [
    "https://x.com",
    "https://twitter.com",
]

DEFAULT_BROWSER_BIN = "/var/lib/flatpak/exports/bin/com.google.Chrome"


def patch_nodriver_cookie_parser():
    """
    Work around nodriver Cookie.from_json expecting Chrome's missing/removed
    `sameParty` field.

    Without this, nodriver can crash while parsing valid Chrome cookie JSON:
      KeyError: 'sameParty'
    """
    original_cookie_from_json = network.Cookie.from_json

    # Avoid double-patching if this function is called more than once.
    if getattr(network.Cookie.from_json, "_sameparty_patched", False):
        return

    def patched_cookie_from_json(cookie_json):
        cookie_json.setdefault("sameParty", False)
        return original_cookie_from_json(cookie_json)

    patched_cookie_from_json._sameparty_patched = True
    network.Cookie.from_json = patched_cookie_from_json


def extract_user_id_from_twid(twid):
    if not twid:
        return None

    if "u%3D" in twid:
        return twid.split("u%3D", 1)[1].split("&", 1)[0].strip('"')

    if "u=" in twid:
        return twid.split("u=", 1)[1].split("&", 1)[0].strip('"')

    return None


async def click_submit(tab, timeout=10):
    submit_button = await tab.find('button[type="submit"]', timeout=timeout)
    await submit_button.click()


async def get_required_cookies(tab, username, debug=False):
    print("[*] Retrieving cookies...", file=sys.stderr)

    patch_nodriver_cookie_parser()

    cookies = await asyncio.wait_for(
        tab.send(cdp.network.get_cookies(COOKIE_URLS)),
        timeout=10,
    )

    cookies_dict = {cookie.name: cookie.value for cookie in cookies}

    if debug:
        print(f"[*] Cookie names: {list(cookies_dict.keys())}", file=sys.stderr)

    missing = [name for name in ("auth_token", "ct0") if name not in cookies_dict]
    if missing:
        found = list(cookies_dict.keys())
        raise Exception(f"Required cookies not found. Missing: {missing}. Found: {found}")

    user_id = extract_user_id_from_twid(cookies_dict.get("twid"))

    cookies_dict["username"] = username
    if user_id:
        cookies_dict["id"] = user_id

    return cookies_dict


async def login_and_get_cookies(username, password, totp_seed=None, headless=False, debug=False):
    """Authenticate with pee.com and extract session cookies."""
    browser_bin = os.environ.get("BROWSER_BIN", DEFAULT_BROWSER_BIN)

    browser = await uc.start(
        headless=headless,
        browser_executable_path=browser_bin,
    )

    tab = await browser.get(LOGIN_URL)

    try:
        print(f"[*] Entering username {username}...", file=sys.stderr)

        username_success = False

        for attempt in range(1, 6):
            username_input = await tab.find(
                'input[autocomplete="username webauthn"]',
                timeout=10,
            )

            await username_input.click()
            await asyncio.sleep(0.5)
            await username_input.send_keys(username)
            await asyncio.sleep(0.2)

            await click_submit(tab)
            await asyncio.sleep(2)

            page_content = await tab.get_content()

            if "Could not log you in" not in page_content:
                username_success = True
                break

            wait = attempt * 10
            print(f"[!] Username step failed. Retrying in {wait} seconds...", file=sys.stderr)
            await asyncio.sleep(wait)

        if not username_success:
            raise Exception("Could not pass username step after 5 attempts")

        print("[*] Entering password...", file=sys.stderr)

        password_success = False

        for attempt in range(1, 6):
            password_input = await tab.find(
                'input[autocomplete="current-password"]',
                timeout=15,
            )

            await password_input.click()
            await asyncio.sleep(0.5)
            await password_input.send_keys(password)
            await asyncio.sleep(0.2)

            await click_submit(tab)
            await asyncio.sleep(2)

            page_content = await tab.get_content()

            if "Could not log you in" not in page_content:
                password_success = True
                break

            wait = attempt * 10
            print(f"[!] Password step failed. Retrying in {wait} seconds...", file=sys.stderr)
            await asyncio.sleep(wait)

        if not password_success:
            raise Exception("Could not pass password step after 5 attempts")

        page_content = await tab.get_content()

        if "verification code" in page_content or "Enter code" in page_content:
            if not totp_seed:
                raise Exception("2FA required but no TOTP seed provided")

            print("[*] 2FA detected, entering code...", file=sys.stderr)

            totp_code = pyotp.TOTP(totp_seed).now()
            code_input = await tab.select('input[type="text"]')

            await code_input.click()
            await asyncio.sleep(0.2)
            await code_input.send_keys(totp_code)
            await asyncio.sleep(0.2)

            await click_submit(tab)
            await asyncio.sleep(3)

        return await get_required_cookies(tab, username, debug=debug)

    finally:
        if debug:
            print("[*] Browser object:", browser, file=sys.stderr)
            print("[*] Browser config:", getattr(browser, "config", None), file=sys.stderr)
            print(
                "[*] Browser user_data_dir:",
                getattr(getattr(browser, "config", None), "user_data_dir", None),
                file=sys.stderr,
            )

        browser.stop()


def parse_args(argv):
    if len(argv) < 3:
        print(
            "Usage: python3 create_session_browser.py username password [totp_seed] [--append file.jsonl] [--headless] [--debug]",
            file=sys.stderr,
        )
        sys.exit(1)

    username = argv[1]
    password = argv[2]
    totp_seed = None
    append_file = None
    headless = False
    debug = False

    i = 3
    while i < len(argv):
        arg = argv[i]

        if arg == "--append":
            if i + 1 >= len(argv):
                print("[!] Error: --append requires a filename", file=sys.stderr)
                sys.exit(1)

            append_file = argv[i + 1]
            i += 2

        elif arg == "--headless":
            headless = True
            i += 1

        elif arg == "--debug":
            debug = True
            i += 1

        elif not arg.startswith("--"):
            if totp_seed is None:
                totp_seed = arg
            else:
                print(f"[!] Warning: Ignoring extra positional argument: {arg}", file=sys.stderr)
            i += 1

        else:
            print(f"[!] Warning: Unknown argument: {arg}", file=sys.stderr)
            i += 1

    return username, password, totp_seed, append_file, headless, debug


async def main():
    username, password, totp_seed, append_file, headless, debug = parse_args(sys.argv)

    try:
        cookies = await login_and_get_cookies(
            username=username,
            password=password,
            totp_seed=totp_seed,
            headless=headless,
            debug=debug,
        )

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

    except Exception as error:
        print(f"[!] Error: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

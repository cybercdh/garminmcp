# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "garminconnect>=0.2.25",
# ]
# ///
"""One-time interactive Garmin Connect login.

Prompts for your Garmin email and password (and an MFA code if your account
uses one), then saves OAuth tokens to ~/.garminconnect (override the location
with GARMINTOKENS). The MCP server only ever reads the cached tokens, which
last about a year. Your password is not stored anywhere.

Run:  uv run auth_setup.py   (from this directory)
"""

from __future__ import annotations

import contextlib
import os
import sys
from getpass import getpass

from garminconnect import Garmin

TOKEN_STORE = os.environ.get("GARMINTOKENS", os.path.expanduser("~/.garminconnect"))


def _prompt_mfa() -> str:
    return input("MFA code from your authenticator or email: ").strip()


def main() -> int:
    print(f"Garmin Connect login. Tokens will be saved to {TOKEN_STORE}")
    email = input("Garmin email: ").strip()
    password = getpass("Garmin password: ")

    # Passing the token store path to login() makes the library persist the
    # tokens itself after a successful credential login; prompt_mfa handles
    # any MFA challenge inline.
    garmin = Garmin(email=email, password=password, prompt_mfa=_prompt_mfa)
    garmin.login(TOKEN_STORE)
    with contextlib.suppress(Exception):  # belt and braces
        garmin.client.dump(TOKEN_STORE)

    # Prove the saved tokens work on their own before declaring success.
    check = Garmin()
    check.login(TOKEN_STORE)
    name = check.get_full_name()
    print(f"Logged in as {name}. Tokens saved to {TOKEN_STORE}.")
    print("The garmin MCP server is ready to use.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(1)
    except Exception as exc:
        if "429" in str(exc) or "TooManyRequests" in type(exc).__name__:
            print(
                "Garmin is rate limiting this IP after the earlier attempts. "
                "Wait five to ten minutes and run this script again."
            )
        else:
            print(f"Login failed: {exc}")
        sys.exit(1)

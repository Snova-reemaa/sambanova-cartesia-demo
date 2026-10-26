"""Getting a one-time code to the person who asked for it.

One interface, two backends. SMTP is written and off, because there is no
approved sender yet. The demo inbox puts the code on the caller's own
screen, which demonstrates the flow and proves nothing about identity —
anyone looking at that screen passes. It says so, in the panel and here.

If neither is configured, verification is unavailable and the caller is
helped without their history. It is never silently skipped: a check that
quietly does not happen is worse than one that is openly absent.
"""

import os
import smtplib
from email.message import EmailMessage

DEMO_INBOX = (os.getenv("DEMO_INBOX", "").lower() in ("1", "true", "yes", "on"))
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "")

SUBJECT = "Your VoxNova verification code"
BODY = ("Your code is {code}.\n\nIt expires in {minutes} minutes and can be "
        "used once.\n\nIf you did not ask for this, ignore it.\n")


def how():
    """"smtp", "demo" or None."""
    if SMTP_HOST and SMTP_FROM:
        return "smtp"
    if DEMO_INBOX:
        return "demo"
    return None


def available():
    return how() is not None


def send_code(email, code, minutes=10):
    """Returns (sent, shown_on_screen).

    `shown_on_screen` is the code when the demo inbox is in use and None
    otherwise — the only path on which the code ever comes back to the
    server's caller, and the reason that panel is labelled.
    """
    route = how()
    if route is None:
        return False, None
    if route == "demo":
        return True, code
    message = EmailMessage()
    message["Subject"] = SUBJECT
    message["From"] = SMTP_FROM
    message["To"] = email
    message.set_content(BODY.format(code=code, minutes=int(minutes)))
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.starttls()
            if SMTP_USER:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(message)
    except Exception as exc:                                  # noqa: BLE001
        # Never the code, never the password, never the address.
        print(f"  ! could not send the code: {type(exc).__name__}")
        return False, None
    return True, None

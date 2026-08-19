"""Emailing the attack plan to yourself over Gmail SMTP.

Credentials live in ``email_settings.json`` (gitignored) or, preferably, in the
environment. Gmail rejects account passwords for SMTP, so the password here is a
16-character *app password* generated at https://myaccount.google.com/apppasswords
(the Google account needs 2-Step Verification turned on).
"""

from __future__ import annotations

import json
import os
import smtplib
from email.message import EmailMessage
from html import escape
from pathlib import Path

import pandas as pd

EMAIL_SETTINGS_FILE = "email_settings.json"

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465

DEFAULT_EMAIL_SETTINGS = {
    "gmail_address": "",
    "app_password": "",
    "recipient": "",
}


def load_email_settings() -> dict[str, str]:
    """Settings from disk, with environment variables taking precedence."""
    settings = DEFAULT_EMAIL_SETTINGS.copy()

    path = Path(EMAIL_SETTINGS_FILE)
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            loaded = {}
        for key in settings:
            settings[key] = str(loaded.get(key, "")).strip()

    for key, env_var in (
        ("gmail_address", "SWGOH_GMAIL_ADDRESS"),
        ("app_password", "SWGOH_GMAIL_APP_PASSWORD"),
        ("recipient", "SWGOH_EMAIL_TO"),
    ):
        value = (os.environ.get(env_var) or "").strip()
        if value:
            settings[key] = value

    return settings


def save_email_settings(gmail_address: str, app_password: str, recipient: str) -> dict[str, str]:
    """Persist settings. A blank password keeps whatever is already stored."""
    current = DEFAULT_EMAIL_SETTINGS.copy()
    path = Path(EMAIL_SETTINGS_FILE)
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            loaded = {}
        for key in current:
            current[key] = str(loaded.get(key, "")).strip()

    password = (app_password or "").strip().replace(" ", "")
    settings = {
        "gmail_address": (gmail_address or "").strip(),
        "app_password": password or current["app_password"],
        "recipient": (recipient or "").strip(),
    }
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return settings


def is_configured(settings: dict[str, str] | None = None) -> bool:
    settings = settings or load_email_settings()
    return bool(settings["gmail_address"] and settings["app_password"])


def _units(value) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(unit) for unit in value)
    return str(value or "")


def render_plan(strategy_df: pd.DataFrame, gac_format: str) -> tuple[str, str, str]:
    """Return ``(subject, plain_text, html)`` for the current attack plan."""
    records = strategy_df.to_dict("records") if not strategy_df.empty else []
    subject = f"SWGOH GAC attack plan ({gac_format}) — {len(records)} matchups"

    text_lines = [f"SWGOH GAC attack plan — format {gac_format}", ""]
    html_rows = []
    for record in records:
        defense = f"{record.get('defense_leader', '')} [{_units(record.get('defense_units'))}]"
        counter_leader = record.get("chosen_counter_leader") or ""
        counter = (
            f"{counter_leader} [{_units(record.get('chosen_counter_units'))}]"
            if counter_leader
            else "No counter"
        )
        win = record.get("win_percent", "")
        seen = record.get("seen", "")
        status = record.get("status", "")

        text_lines.append(f"{defense}\n  -> {counter}  (win {win}, seen {seen}, {status})")
        html_rows.append(
            "<tr>"
            f"<td>{escape(str(defense))}</td>"
            f"<td>{escape(str(counter))}</td>"
            f"<td align=\"right\">{escape(str(win))}</td>"
            f"<td align=\"right\">{escape(str(seen))}</td>"
            f"<td>{escape(str(status))}</td>"
            "</tr>"
        )

    if not records:
        text_lines.append("No plan yet — run the pipeline first.")
        html_rows.append('<tr><td colspan="5">No plan yet — run the pipeline first.</td></tr>')

    html = f"""\
<html><body style="font-family:Segoe UI,Arial,sans-serif;font-size:14px">
<h2 style="margin-bottom:4px">SWGOH GAC attack plan</h2>
<p style="color:#666;margin-top:0">Format: <strong>{escape(gac_format)}</strong></p>
<table cellpadding="6" cellspacing="0" border="1" style="border-collapse:collapse">
<thead style="background:#f0f0f0">
<tr><th align="left">Enemy defense</th><th align="left">Assigned counter</th>
<th>Win%</th><th>Seen</th><th align="left">Status</th></tr>
</thead>
<tbody>{"".join(html_rows)}</tbody>
</table>
</body></html>"""

    return subject, "\n".join(text_lines), html


def send_plan_email(strategy_df: pd.DataFrame, gac_format: str) -> str:
    """Send the plan to the configured recipient. Returns a status message."""
    settings = load_email_settings()
    if not is_configured(settings):
        return "Email not configured — add your Gmail address and app password on the Setup tab."

    sender = settings["gmail_address"]
    recipient = settings["recipient"] or sender
    subject, text_body, html_body = render_plan(strategy_df, gac_format)

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.login(sender, settings["app_password"])
            smtp.send_message(message)
    except smtplib.SMTPAuthenticationError:
        return "Gmail rejected the login. Use a 16-character app password, not your account password."
    except (smtplib.SMTPException, OSError) as exc:
        return f"Could not send email: {exc}"

    return f"Attack plan emailed to {recipient}."

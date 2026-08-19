"""Tests for emailing the attack plan.

No SMTP connection is ever made: ``smtplib.SMTP_SSL`` is swapped for a recorder
that captures the message that would have been sent.
"""

import pandas as pd
import pytest

from swgoh.web import emailer


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    """Point the module at a throwaway settings file and clear env overrides."""
    monkeypatch.chdir(tmp_path)
    for env_var in ("SWGOH_GMAIL_ADDRESS", "SWGOH_GMAIL_APP_PASSWORD", "SWGOH_EMAIL_TO"):
        monkeypatch.delenv(env_var, raising=False)
    return tmp_path / emailer.EMAIL_SETTINGS_FILE


class FakeSMTP:
    """Stand-in for ``smtplib.SMTP_SSL`` that records the login and message."""

    instances: list["FakeSMTP"] = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.login_args = None
        self.message = None
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def login(self, user, password):
        self.login_args = (user, password)

    def send_message(self, message):
        self.message = message


@pytest.fixture
def fake_smtp(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(emailer.smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


@pytest.fixture
def plan_df():
    return pd.DataFrame(
        [
            {
                "defense_leader": "SLKR",
                "defense_units": ["SLKR", "KRU"],
                "chosen_counter_leader": "JMK",
                "chosen_counter_units": ["JMK", "GK"],
                "win_percent": 88.0,
                "seen": 12,
                "status": "assigned",
            },
            {
                "defense_leader": "GLREY",
                "defense_units": ["GLREY"],
                "chosen_counter_leader": "",
                "chosen_counter_units": [],
                "win_percent": "",
                "seen": 0,
                "status": "unmatched",
            },
        ]
    )


def test_app_password_spaces_are_stripped(settings_file, tmp_path):
    saved = emailer.save_email_settings("me@gmail.com", "abcd efgh ijkl mnop", "")
    assert saved["app_password"] == "abcdefghijklmnop"
    assert settings_file.exists()


def test_blank_password_keeps_the_stored_one(settings_file):
    emailer.save_email_settings("me@gmail.com", "abcdefghijklmnop", "")
    saved = emailer.save_email_settings("other@gmail.com", "", "")
    assert saved["app_password"] == "abcdefghijklmnop"
    assert saved["gmail_address"] == "other@gmail.com"


def test_environment_overrides_the_file(settings_file, monkeypatch):
    emailer.save_email_settings("file@gmail.com", "filepassword", "")
    monkeypatch.setenv("SWGOH_GMAIL_ADDRESS", "env@gmail.com")
    settings = emailer.load_email_settings()
    assert settings["gmail_address"] == "env@gmail.com"
    assert settings["app_password"] == "filepassword"


def test_unconfigured_send_reports_instead_of_connecting(settings_file, fake_smtp, plan_df):
    message = emailer.send_plan_email(plan_df, "5v5")
    assert "not configured" in message
    assert fake_smtp.instances == []


def test_send_defaults_to_mailing_yourself(settings_file, fake_smtp, plan_df):
    emailer.save_email_settings("me@gmail.com", "abcdefghijklmnop", "")

    message = emailer.send_plan_email(plan_df, "5v5")

    assert "me@gmail.com" in message
    sent = fake_smtp.instances[0]
    assert sent.login_args == ("me@gmail.com", "abcdefghijklmnop")
    assert sent.message["To"] == "me@gmail.com"
    assert sent.message["From"] == "me@gmail.com"


def test_send_honours_an_explicit_recipient(settings_file, fake_smtp, plan_df):
    emailer.save_email_settings("me@gmail.com", "abcdefghijklmnop", "guild@example.com")
    emailer.send_plan_email(plan_df, "3v3")
    assert fake_smtp.instances[0].message["To"] == "guild@example.com"


def test_body_lists_every_matchup(plan_df):
    subject, text, html = emailer.render_plan(plan_df, "5v5")

    assert "5v5" in subject and "2 matchups" in subject
    assert "SLKR" in text and "JMK" in text
    assert "No counter" in text
    assert "<table" in html and "GLREY" in html


def test_empty_plan_still_renders(plan_df):
    _, text, html = emailer.render_plan(pd.DataFrame(), "5v5")
    assert "No plan yet" in text
    assert "No plan yet" in html


def test_auth_failure_is_reported_not_raised(settings_file, fake_smtp, plan_df, monkeypatch):
    emailer.save_email_settings("me@gmail.com", "wrongpassword", "")

    def boom(self, user, password):
        raise emailer.smtplib.SMTPAuthenticationError(535, b"bad credentials")

    monkeypatch.setattr(FakeSMTP, "login", boom)

    message = emailer.send_plan_email(plan_df, "5v5")
    assert "app password" in message

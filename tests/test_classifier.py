"""Unit tests for the sensitive-email classifier."""

from mailgate.classifier import is_sensitive


def test_otp_subject():
    assert is_sensitive("Your verification code", "Hi", "noreply@example.com")


def test_2fa_in_body():
    assert is_sensitive("Welcome", "Your 2FA code is below", "noreply@example.com")


def test_standalone_code_wording():
    assert is_sensitive("Login", "Your code is 123456", "noreply@example.com")


def test_code_colon_wording():
    assert is_sensitive("Login", "Code: 482910", "noreply@example.com")


def test_password_reset():
    assert is_sensitive("Reset your password", "Click here", "noreply@example.com")


def test_sign_in_attempt():
    assert is_sensitive("New sign-in attempt", "Was this you?", "noreply@example.com")


def test_high_risk_domain_google():
    assert is_sensitive("Security alert", "Some activity", "Google <no-reply@accounts.google.com>")


def test_high_risk_domain_paypal():
    assert is_sensitive("Receipt", "You paid $5", "PayPal <service@paypal.com>")


def test_high_risk_domain_subdomain():
    assert is_sensitive("Alert", "x", "no-reply@login.appleid.apple.com")


def test_normal_email_not_sensitive():
    assert not is_sensitive("Meeting tomorrow", "Let's sync at 10am", "bob@example.com")


def test_github_pr_notification_not_sensitive():
    assert not is_sensitive(
        "[repo] PR #42 merged",
        "Your pull request was merged.",
        "GitHub <notifications@github.com>",
    )


def test_github_auth_alert_sensitive():
    assert is_sensitive(
        "New sign-in to your account",
        "A new device signed in.",
        "GitHub <noreply@github.com>",
    )


def test_case_insensitive():
    assert is_sensitive("Your OTP Code", "x", "noreply@example.com")


def test_bare_sender_address():
    assert is_sensitive("Alert", "x", "service@stripe.com")

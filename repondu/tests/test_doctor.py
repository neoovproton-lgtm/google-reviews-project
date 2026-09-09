from app.config import get_settings, reset_settings
from app.doctor import Check, check_imap, check_mailboxes, format_doctor, run_doctor


def test_missing_placeholders_reported(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "À_REMPLACER")
    reset_settings()
    checks = {
        "api_token": lambda s: Check("api_token", "missing", hint="API_TOKEN"),
        "proxy": lambda s: Check("proxy", "ok"),
        "chromium": lambda s: Check("chromium", "ok"),
        "anthropic": lambda s: Check("anthropic", "missing", hint="clé"),
        "boom": lambda s: 1 / 0,
    }
    r = run_doctor(get_settings(), checks)
    assert r["ok"] is False
    assert [c["name"] for c in r["todo_human"]] == ["api_token", "anthropic", "boom"]
    assert r["phases_ready"] == {"A": False, "B": False, "C": False, "D": False}
    text = format_doctor(r)
    assert "⬜ api_token" in text and "❌ boom" in text and "A ⛔" in text


def test_all_ok_and_alternatives(monkeypatch):
    reset_settings()
    ok = lambda name: lambda s: Check(name, "ok")  # noqa: E731
    checks = {
        n: ok(n)
        for n in (
            "api_token",
            "proxy",
            "chromium",
            "anthropic",
            "public_url",
            "service_from",
            "google_manager",
        )
    }
    checks["telegram"] = ok("telegram_bot")
    checks["resend"] = lambda s: Check("resend", "missing")
    checks["brevo"] = ok("brevo")  # une alternative suffit pour la phase C
    checks["mailboxes"] = lambda s: [Check("mailboxes", "ok"), Check("imap:a@x.fr", "ok")]
    checks["service_imap"] = ok("imap:service")
    checks["manager_imap"] = ok("imap:manager")
    r = run_doctor(get_settings(), checks)
    assert r["phases_ready"] == {"A": True, "B": True, "C": True, "D": True}
    assert r["ok"] is False and r["todo_human"][0]["name"] == "resend"  # signalé mais non bloquant
    assert "Phases prêtes : A ✅ · B ✅ · C ✅ · D ✅" in format_doctor(r)


def test_check_imap_and_mailboxes(tmp_path, monkeypatch):
    calls = []

    def login(host, port, user, password):
        calls.append(user)
        if user == "bad@x.fr":
            raise OSError("Authentication failed")

    assert check_imap("x", "h", 993, "good@x.fr", "p", "D", login).status == "ok"
    assert check_imap("x", "h", 993, "bad@x.fr", "p", "D", login).status == "error"
    assert check_imap("x", "", 993, "", "", "D", login).status == "missing"
    (tmp_path / "mb.json").write_text(
        '{"mailboxes":[{"address":"good@x.fr","provider":"resend","imap":{"host":"h","user":"good@x.fr","password":"p"}},'
        '{"address":"weird@x.fr","provider":"smtp"}]}'
    )
    monkeypatch.setenv("MAILBOXES_FILE", str(tmp_path / "mb.json"))
    reset_settings()
    out = check_mailboxes(get_settings(), login)
    assert [c.name for c in out] == [
        "mailboxes",
        "imap:good@x.fr",
        "imap:weird@x.fr",
        "provider:weird@x.fr",
    ]
    assert out[2].status == "missing" and out[3].status == "error"
    monkeypatch.setenv("MAILBOXES_FILE", str(tmp_path / "absent.json"))
    reset_settings()
    assert check_mailboxes(get_settings(), login)[0].status == "missing"


def test_doctor_api_and_telegram(db, monkeypatch):
    from fastapi.testclient import TestClient

    import app.doctor as doctor
    from app.main import app
    from app.telegram.bot import handle_update
    from tests.fakes import FakeTelegram

    monkeypatch.setattr(doctor, "DEFAULT_CHECKS", {"api_token": lambda s: Check("api_token", "ok")})
    with TestClient(app) as c:
        r = c.get("/doctor").json()
        assert r["checks"][0]["name"] == "api_token" and r["ok"] is True
    tg = FakeTelegram()
    with db() as s:
        handle_update({"message": {"chat": {"id": 7}, "text": "/doctor"}}, s, tg)
    assert tg.texts[-1].startswith("🩺 Répondu doctor")

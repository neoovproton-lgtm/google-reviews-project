from fastapi.testclient import TestClient

from app.config import reset_settings
from app.models import Prospect, ProspectStatus


def _client():
    from app.main import app

    return TestClient(app)


def test_health_and_empty_stats(db):
    with _client() as c:
        assert c.get("/health").json() == {"status": "ok"}
        stats = c.get("/stats").json()
        assert stats["prospects"]["total"] == 0
        assert stats["scrape_jobs"]["total"] == 0


def test_prospects_filter_and_order(db):
    with db() as s:
        s.add_all(
            [
                Prospect(place_id="a", name="A", city="Lyon", status=ProspectStatus.NEW),
                Prospect(
                    place_id="b", name="B", city="Lyon", status=ProspectStatus.QUALIFIED, score=12.0
                ),
                Prospect(
                    place_id="c",
                    name="C",
                    city="Paris",
                    status=ProspectStatus.QUALIFIED,
                    score=20.0,
                ),
            ]
        )
    with _client() as c:
        names = [p["name"] for p in c.get("/prospects?status=qualified").json()]
        assert names == ["C", "B"]
        assert [p["name"] for p in c.get("/prospects?city=Lyon").json()] == ["B", "A"]
        assert c.get("/prospects?status=bidon").status_code == 422
        assert c.get("/prospects/1").json()["name"] == "A"
        assert c.get("/prospects/999").status_code == 404
        assert c.get("/stats").json()["prospects"]["qualified"] == 2


def test_token_required_when_configured(db, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "secret")
    reset_settings()
    with _client() as c:
        assert c.get("/stats").status_code == 401
        assert c.get("/stats", headers={"Authorization": "Bearer secret"}).status_code == 200
        assert c.get("/health").status_code == 200


def test_scrape_job_endpoint_runs_in_background(db, monkeypatch):
    import app.main as main

    monkeypatch.setattr(main, "run_mode", lambda req: {"echo": req.mode})
    with _client() as c:
        r = c.post("/scrape", json={"mode": "score"})
        assert r.status_code == 202
        job_id = r.json()["id"]
        import time

        for _ in range(50):
            job = c.get(f"/jobs/{job_id}").json()
            if job["status"] != "running":
                break
            time.sleep(0.05)
        assert job["status"] == "done" and job["result"] == {"echo": "score"}
        assert c.get("/jobs/nope").status_code == 404

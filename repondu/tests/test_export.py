import csv
import io

from fastapi.testclient import TestClient

from app.export import COLUMNS, select_for_export, to_csv, write_export
from app.models import Prospect, ProspectStatus


def _seed(db):
    with db() as s:
        s.add_all(
            [
                Prospect(
                    place_id="a", name="A", city="Lyon", status=ProspectStatus.QUALIFIED, score=5
                ),
                Prospect(
                    place_id="b",
                    name="B",
                    city="Lyon",
                    status=ProspectStatus.ENRICHED,
                    score=9,
                    email="contact@b.fr",
                    canal_prioritaire="email",
                ),
                Prospect(
                    place_id="c",
                    name="C",
                    city="Paris",
                    status=ProspectStatus.DISQUALIFIED,
                    score=1,
                ),
                Prospect(place_id="d", name="D", city="Paris", status=ProspectStatus.NEW),
            ]
        )


def test_select_qualified_includes_enriched_sorted_by_score(db):
    _seed(db)
    with db() as s:
        assert [p.name for p in select_for_export(s, "qualified")] == ["B", "A"]
        assert [p.name for p in select_for_export(s, "qualified", limit=1)] == ["B"]
        assert [p.name for p in select_for_export(s, None)] == ["B", "A", "C", "D"]
        assert [p.name for p in select_for_export(s, None, city="Paris")] == ["C", "D"]


def test_csv_columns_and_content(db):
    _seed(db)
    with db() as s:
        text = to_csv(select_for_export(s, "qualified"))
    rows = list(csv.DictReader(io.StringIO(text)))
    assert list(rows[0].keys()) == COLUMNS
    assert rows[0]["name"] == "B" and rows[0]["email"] == "contact@b.fr"
    assert rows[1]["score"] == "5.0"


def test_write_export_file(db, tmp_path):
    _seed(db)
    with db() as s:
        path = write_export(s, tmp_path / "exports", "qualified", 300)
    assert path.exists() and path.name.startswith("prospects-qualified-")
    assert path.read_text().count("\n") == 3  # en-tête + 2 lignes


def test_export_endpoint(db):
    _seed(db)
    from app.main import app

    with TestClient(app) as c:
        r = c.get("/prospects/export.csv")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
        assert r.text.splitlines()[1].startswith("2,b,B,Lyon")
        assert c.get("/prospects/export.csv?status=nope").status_code == 422
        assert len(c.get("/prospects/export.csv?status=new").text.splitlines()) == 2

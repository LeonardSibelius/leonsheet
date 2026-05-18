from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_healthz_returns_ok(tmp_path: Path) -> None:
    client = TestClient(create_app(db_path=tmp_path / "test.db"))
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.text == "ok"

from fastapi.testclient import TestClient

from backend.main import app


def test_health_reports_writes_disabled():
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["awsWritesEnabled"] is False

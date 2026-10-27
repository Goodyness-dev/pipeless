from __future__ import annotations
from fastapi.testclient import TestClient
from app.api.main import app
import os

client = TestClient(app)


def test_health_endpoint():
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert "models_pinned" in data


def test_auth_rejection():
    # Attempting to access protected /models without token
    resp = client.get("/models")
    if os.getenv("PIPELESS_API_TOKEN"):
        assert resp.status_code == 401
    else:
        # If token unset in env during test, passes or returns empty list
        assert resp.status_code in (200, 401)


def test_ask_without_models_returns_400():
    headers = {}
    tok = os.getenv("PIPELESS_API_TOKEN")
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    resp = client.post("/ask", json={"question": "What is the revenue?"}, headers=headers)
    assert resp.status_code in (400, 401)

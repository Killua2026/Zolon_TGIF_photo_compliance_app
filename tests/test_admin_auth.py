import os

import pytest

from app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.secret_key = "test-secret-key"
    with app.test_client() as client:
        yield client


def test_root_redirects_to_admin(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin")


def test_admin_requires_login(client):
    response = client.get("/admin", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/login")


def test_admin_login_success(client, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "secret-pass")
    response = client.post("/admin/login", data={"password": "secret-pass"}, follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin")


def test_admin_api_requires_login(client):
    response = client.get("/api/admin/submissions", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/login")

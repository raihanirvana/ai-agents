from starlette.testclient import TestClient
from app.http.application import create_app
from app.http.security import Settings
from app.persistence import Database
from .conftest import CODE, ORIGIN


def test_new_api_and_database_instances_replay_committed_receipt(api, db_path):
    body = {"name": "Before restart"}
    first = api.cmd("/projects", body, "restart")
    cookie = api.client.cookies.get("ai_team_session")
    csrf = api.client.headers["X-CSRF-Token"]
    database = Database(db_path)
    app = create_app(db=database, store=api.store, login_code=CODE, settings=Settings())
    try:
        with TestClient(app, base_url="http://127.0.0.1:8000") as fresh:
            fresh.cookies.set("ai_team_session", cookie)
            result = fresh.post("/projects", json=body, headers={"Origin": ORIGIN,
                "X-CSRF-Token": csrf, "Idempotency-Key": "restart"})
            assert result.status_code == 200 and result.json() == first.json()
            assert len(fresh.get("/projects").json()["projects"]) == 1
    finally: database.dispose()

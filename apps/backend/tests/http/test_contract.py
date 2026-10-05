import json
from pathlib import Path
from app.http.application import create_app
from app.http.contract import typescript
from app.persistence import migrate
from tests.persistence import factories as f
from sqlalchemy import text


def test_exported_command_contract_matches_routes_and_security():
    app = create_app()
    exported = Path(__file__).resolve().parents[4] / "contracts" / "api" / "openapi.json"
    assert app.openapi() == json.loads(exported.read_text(encoding="utf-8"))
    assert typescript(app.openapi()) == exported.with_name("requests.ts").read_text(encoding="utf-8")
    spec = app.openapi()
    assert spec["components"]["schemas"]["Scope"]["additionalProperties"] is False
    assert spec["components"]["schemas"]["Criterion"]["properties"]["mode"]["default"] == "automated"
    assert spec["paths"]["/runtime/tools"]["post"]["security"] == [{"runtimeLease": []}]
    assert spec["paths"]["/projects/{project_id}/scope-approvals"]["post"]["security"] == [{"localSession": [], "csrf": []}]


def test_api_migration_preserves_data_and_guards_and_downgrades(db_path, db):
    with db.write() as s:
        p = f.project(s)
        t = f.ticket(s, p)
    db.dispose()
    migrate.downgrade(db_path, "0003")
    with db.read() as s:
        before = dict(s.execute(text("SELECT name, sql FROM sqlite_master WHERE type='trigger'")).all())
        assert s.execute(text("SELECT title FROM tickets WHERE id=:id"), {"id": t.id}).scalar()
        assert s.execute(text("SELECT name FROM sqlite_master WHERE name='api_commands'")).scalar() is None
    db.dispose()
    migrate.upgrade(db_path)
    with db.read() as s:
        after = dict(s.execute(text("SELECT name, sql FROM sqlite_master WHERE type='trigger'")).all())
        assert before == after and len(after) > 10
        assert s.execute(text("SELECT id FROM projects WHERE id=:id"), {"id": p.id}).scalar() == p.id
    assert migrate.schema_drift(db_path) == []

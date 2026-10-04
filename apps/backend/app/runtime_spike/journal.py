"""Scoped experiment journal, not the product job database (DEV-010).

Reservations survive transport failure and restart. An unfinished reservation
means unknown usage, never zero. SQLite transactions fence generations and input.
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class AdmissionError(RuntimeError):
    pass


class Journal:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.tx() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS scopes (
                    id TEXT PRIMARY KEY, identity TEXT NOT NULL, limits TEXT NOT NULL,
                    generation INTEGER NOT NULL, status TEXT NOT NULL,
                    model_calls INTEGER NOT NULL DEFAULT 0, tool_calls INTEGER NOT NULL DEFAULT 0,
                    active_s REAL NOT NULL DEFAULT 0, running_since REAL);
                CREATE TABLE IF NOT EXISTS reservations (
                    id TEXT PRIMARY KEY, scope TEXT NOT NULL, generation INTEGER NOT NULL,
                    kind TEXT NOT NULL, name TEXT NOT NULL, started REAL NOT NULL,
                    finished REAL, result TEXT);
                CREATE TABLE IF NOT EXISTS events (
                    cursor INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL,
                    generation INTEGER NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS inputs (
                    id TEXT PRIMARY KEY, scope TEXT NOT NULL, generation INTEGER NOT NULL,
                    question TEXT NOT NULL, checkpoint TEXT NOT NULL,
                    answer_id TEXT, answer TEXT, resumed_generation INTEGER);
                CREATE TABLE IF NOT EXISTS budget_changes (
                    authorization_id TEXT PRIMARY KEY, scope TEXT NOT NULL,
                    old_limits TEXT NOT NULL, new_limits TEXT NOT NULL, reason TEXT NOT NULL);
            """)

    @contextmanager
    def tx(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def create(self, scope: str, identity: dict, limits: dict):
        if set(limits) != {"model_calls", "tool_calls", "active_s", "output_tokens"}:
            raise ValueError("all finite limits are required")
        for k, v in limits.items():
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
                raise ValueError("limits must be finite positive numbers")
            if k != "active_s" and not isinstance(v, int):
                raise ValueError("call and token limits must be integers")
        with self.tx() as db:
            db.execute("INSERT INTO scopes(id,identity,limits,generation,status) VALUES (?,?,?,1,'idle')",
                       (scope, json.dumps(identity, sort_keys=True), json.dumps(limits)))

    @staticmethod
    def _row(db, scope):
        row = db.execute("SELECT * FROM scopes WHERE id=?", (scope,)).fetchone()
        if row is None:
            raise AdmissionError("unknown scope")
        return dict(row)

    @staticmethod
    def _elapsed(row):
        return row["active_s"] + (max(0, time.time() - row["running_since"]) if row["running_since"] else 0)

    @classmethod
    def _admit(cls, db, scope, generation):
        row = cls._row(db, scope)
        if row["generation"] != generation or row["status"] != "running":
            raise AdmissionError("inactive or stale generation")
        if cls._elapsed(row) >= json.loads(row["limits"])["active_s"]:
            raise AdmissionError("active duration exhausted")
        return row

    def start(self, scope, generation):
        with self.tx() as db:
            row = self._row(db, scope)
            if row["generation"] != generation or row["status"] not in ("idle", "resuming"):
                raise AdmissionError("start already admitted or scope inactive")
            if self._elapsed(row) >= json.loads(row["limits"])["active_s"]:
                raise AdmissionError("active duration exhausted")
            db.execute("UPDATE scopes SET status='running',running_since=? WHERE id=?", (time.time(), scope))

    def reserve(self, scope, generation, kind, name):
        if kind not in ("model", "tool"):
            raise ValueError("invalid reservation kind")
        with self.tx() as db:
            row = self._admit(db, scope, generation)
            column = kind + "_calls"
            if row[column] >= json.loads(row["limits"])[column]:
                raise AdmissionError(f"{kind} calls exhausted")
            rid = uuid.uuid4().hex
            db.execute(f"UPDATE scopes SET {column}={column}+1 WHERE id=?", (scope,))
            db.execute("INSERT INTO reservations VALUES (?,?,?,?,?,?,NULL,NULL)",
                       (rid, scope, generation, kind, name, time.time()))
            return rid

    def finish(self, rid, result):
        # A late usage receipt is accounting only: it cannot mutate runtime state.
        with self.tx() as db:
            db.execute("UPDATE reservations SET finished=?,result=? WHERE id=? AND finished IS NULL",
                       (time.time(), json.dumps(result), rid))

    def event(self, scope, generation, kind, payload):
        with self.tx() as db:
            row = self._row(db, scope)
            if row["generation"] != generation:
                raise AdmissionError("stale event")
            if row["status"] not in ("running", "waiting_input"):
                raise AdmissionError("inactive event")
            db.execute("INSERT INTO events(scope,generation,kind,payload) VALUES (?,?,?,?)",
                       (scope, generation, kind, json.dumps(payload)))

    def pause(self, scope, generation, status):
        if status not in ("stopped", "finished", "failed", "waiting_input"):
            raise ValueError("invalid state")
        with self.tx() as db:
            row = self._row(db, scope)
            if row["generation"] != generation:
                raise AdmissionError("stale state transition")
            if row["status"] in ("stopped", "finished", "failed"):
                return
            db.execute("UPDATE scopes SET status=?,active_s=?,running_since=NULL WHERE id=?",
                       (status, self._elapsed(row), scope))

    def request_input(self, scope, generation, question, checkpoint):
        with self.tx() as db:
            row = self._admit(db, scope, generation)
            iid = uuid.uuid4().hex
            db.execute("INSERT INTO inputs VALUES (?,?,?,?,?,NULL,NULL,NULL)",
                       (iid, scope, generation, question, json.dumps(checkpoint)))
            db.execute("UPDATE scopes SET status='waiting_input',active_s=?,running_since=NULL WHERE id=?",
                       (self._elapsed(row), scope))
            return iid

    def answer(self, scope, request_id, answer_id, text, identity):
        if not answer_id or not isinstance(text, str) or not text.strip() or len(text) > 8000:
            raise AdmissionError("invalid answer")
        with self.tx() as db:
            row = self._row(db, scope)
            if json.loads(row["identity"]) != identity:
                raise AdmissionError("scope/base identity changed; replan required")
            req = db.execute("SELECT * FROM inputs WHERE id=? AND scope=?", (request_id, scope)).fetchone()
            if req is None:
                raise AdmissionError("unknown input request")
            if req["answer_id"]:
                if req["answer_id"] == answer_id and req["answer"] == text:
                    return {"duplicate": True, "generation": req["resumed_generation"]}
                raise AdmissionError("input already answered")
            if row["status"] != "waiting_input" or row["generation"] != req["generation"]:
                raise AdmissionError("late input cannot resume this generation")
            new_gen = row["generation"] + 1
            db.execute("UPDATE inputs SET answer_id=?,answer=?,resumed_generation=? WHERE id=?",
                       (answer_id, text, new_gen, request_id))
            db.execute("UPDATE scopes SET status='resuming',generation=? WHERE id=?", (new_gen, scope))
            return {"duplicate": False, "generation": new_gen}

    def inspect(self, scope):
        with self.tx() as db:
            row = self._row(db, scope)
            row["active_s"] = self._elapsed(row)
            row["identity"] = json.loads(row["identity"])
            row["limits"] = json.loads(row["limits"])
            row["inputs"] = [dict(r) for r in db.execute("SELECT * FROM inputs WHERE scope=?", (scope,))]
            row["reservations"] = [dict(r) for r in db.execute("SELECT * FROM reservations WHERE scope=? ORDER BY started", (scope,))]
            for r in row["reservations"]:
                r["result"] = json.loads(r["result"]) if r["result"] else None
            return row

    def restart_failed(self, scope, identity):
        with self.tx() as db:
            row = self._row(db, scope)
            if row["status"] != "failed" or json.loads(row["identity"]) != identity:
                raise AdmissionError("recovery requires failed scope with unchanged identity")
            new_gen = row["generation"] + 1
            db.execute("UPDATE scopes SET status='resuming',generation=? WHERE id=?", (new_gen, scope))
            return new_gen

    def extend_budget(self, scope, authorization_id, *, model_calls, tool_calls, reason):
        if not authorization_id or not reason or any(type(v) is not int or v < 0 for v in (model_calls, tool_calls)):
            raise ValueError("explicit authorization, reason and nonnegative integer additions required")
        with self.tx() as db:
            existing = db.execute("SELECT * FROM budget_changes WHERE authorization_id=?", (authorization_id,)).fetchone()
            row = self._row(db, scope)
            old = json.loads(row["limits"])
            if existing:
                previous = json.loads(existing["old_limits"])
                proposed = {**previous, "model_calls": previous["model_calls"] + model_calls,
                            "tool_calls": previous["tool_calls"] + tool_calls}
                if existing["scope"] != scope or json.loads(existing["new_limits"]) != proposed or existing["reason"] != reason:
                    raise AdmissionError("authorization ID reused with different budget")
                return {"duplicate": True, "limits": old}
            if row["status"] not in ("failed", "waiting_input", "idle"):
                raise AdmissionError("pause runtime before changing its budget")
            new = {**old, "model_calls": old["model_calls"] + model_calls,
                   "tool_calls": old["tool_calls"] + tool_calls}
            db.execute("INSERT INTO budget_changes VALUES (?,?,?,?,?)",
                       (authorization_id, scope, json.dumps(old), json.dumps(new), reason))
            db.execute("UPDATE scopes SET limits=? WHERE id=?", (json.dumps(new), scope))
            return {"duplicate": False, "limits": new}

    def stream(self, scope, cursor=0):
        with self.tx() as db:
            return [{**dict(r), "payload": json.loads(r["payload"])} for r in db.execute(
                "SELECT * FROM events WHERE scope=? AND cursor>? ORDER BY cursor", (scope, cursor))]

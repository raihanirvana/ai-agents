"""Relay contract doubles. Real provider execution is recorded separately."""
import io
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from app.runtime_spike.journal import Journal
from app.runtime_spike.relay import Relay


class Response(io.BytesIO):
    status = 200
    headers = {"Content-Type": "text/event-stream"}


class Transport:
    def __init__(self, fail=False):
        self.requests = []
        self.fail = fail
        self.body = b'data: {"choices": [], "usage":{"total_tokens":7,"cost":0}}\n\ndata: [DONE]\n\n'

    def open(self, req, timeout):
        self.requests.append(req)
        if self.fail:
            raise urllib.error.HTTPError(req.full_url, 429, "quota", {}, io.BytesIO(b'{"error":"quota"}'))
        return Response(self.body)


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.j = Journal(Path(self.tmp.name) / "j.sqlite3")
        self.j.create("s", {}, {"model_calls": 2, "tool_calls": 1, "active_s": 60, "output_tokens": 100})
        self.j.start("s", 1)
        self.t = Transport()
        self.calls = []
        self.r = Relay(self.j, "s", 1, "configured", "test-provider-secret", {
            "read": lambda args: self.calls.append(args) or {"ok": True},
            "needs_path": lambda args: {"content": args["path"]}}, transport=self.t)
        self.r.__enter__()

    def tearDown(self):
        self.r.__exit__()
        self.tmp.cleanup()

    def post(self, route, body, **headers):
        req = urllib.request.Request(self.r.url + route, data=json.dumps(body).encode(), headers={
            "Authorization": "Bearer " + self.r.token, "Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=3) as res:
                return res.status, res.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_stream_usage_and_output_cap_before_forward(self):
        code, raw = self.post("/v1/chat/completions", {"model": "configured", "stream": True, "max_tokens": 999999})
        self.assertEqual(code, 200)
        self.assertIn(b"[DONE]", raw)
        sent = json.loads(self.t.requests[0].data)
        self.assertEqual(sent["max_tokens"], 100)
        self.assertEqual(sent["provider"], {"allow_fallbacks": False})
        state = self.j.inspect("s")
        self.assertEqual(state["model_calls"], 1)
        self.assertEqual(state["reservations"][0]["result"]["usage"]["total_tokens"], 7)

    def test_http_retry_reservations_count_and_exhaustion_blocks_forward(self):
        self.t.fail = True
        self.assertEqual(self.post("/v1/chat/completions", {"model": "configured"})[0], 429)
        self.assertEqual(self.post("/v1/chat/completions", {"model": "configured"})[0], 429)
        self.assertEqual(self.post("/v1/chat/completions", {"model": "configured"})[0], 409)
        self.assertEqual(len(self.t.requests), 2)
        for r in self.j.inspect("s")["reservations"]:
            self.assertIsNone(r["result"]["usage"])
            self.assertIsNone(r["result"]["cost"])

    def test_browser_credentials_routes_model_and_stale_generation_rejected(self):
        body = {"model": "configured"}
        for route, data, headers, code in [
            ("/v1/chat/completions", body, {"Authorization": "Bearer wrong"}, 401),
            ("/v1/chat/completions", body, {"Origin": "http://localhost"}, 403),
            ("/v1/chat/completions", {"model": "paid-alternate"}, {}, 403),
            ("/v1/responses", body, {}, 404)]:
            self.assertEqual(self.post(route, data, **headers)[0], code)
        self.j.pause("s", 1, "stopped")
        self.assertEqual(self.post("/tools", {"name": "read"})[0], 409)
        self.assertEqual(self.post("/v1/chat/completions", body)[0], 409)
        self.assertEqual(self.t.requests, [])
        self.assertEqual(self.calls, [])

    def test_tool_counter_reserved_before_execution(self):
        self.assertEqual(self.post("/tools", {"name": "unknown"})[0], 400)
        self.assertEqual(self.post("/tools", {"name": "read", "arguments": {"path": "one"}})[0], 200)
        self.assertEqual(self.post("/tools", {"name": "read"})[0], 409)
        self.assertEqual(self.calls, [{"path": "one"}])
        self.assertEqual(self.j.inspect("s")["tool_calls"], 1)

    def test_tool_failure_is_tool_error_not_admission_denial(self):
        # 409 makes the worker interrupt the run; a missing argument must not.
        code, raw = self.post("/tools", {"name": "needs_path", "arguments": {}})
        self.assertEqual(code, 400)
        self.assertEqual(json.loads(raw)["error"]["type"], "tool_error")
        self.assertEqual(self.j.inspect("s")["reservations"][0]["result"]["status"], "failed")

    def test_mid_stream_failure_does_not_append_second_response(self):
        self.t.body = b'data: {"choices": []}\n\ndata: {not json\n\n'
        code, raw = self.post("/v1/chat/completions", {"model": "configured", "stream": True})
        self.assertEqual(code, 200)
        self.assertNotIn(b"HTTP/1.1", raw)
        self.assertNotIn(b"admission_error", raw)
        self.assertEqual(self.j.inspect("s")["reservations"][0]["result"]["status"], "failed")


if __name__ == "__main__":
    unittest.main()

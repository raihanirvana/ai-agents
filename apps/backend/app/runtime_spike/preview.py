"""Operator preview of exactly the acceptance-passed immutable artifact."""
import argparse
import json
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit

from .acceptance import verify_artifact
from .experiment import Experiment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--scope", default="coffee-feature")
    parser.add_argument("--port", type=int, default=4186)
    args = parser.parse_args()
    exp = Experiment(args.root)
    result = json.loads((exp.path(args.scope).parent / "verification.json").read_text())["feature"]
    target, evidence = result["target"], result["acceptance"]
    if evidence["status"] != "passed" or evidence["target_id"] != target["target_id"]:
        raise ValueError("preview requires matching passed acceptance evidence")
    artifact = verify_artifact(exp.sup, exp.ref(args.scope), target)

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *params, **kwargs):
            super().__init__(*params, directory=str(artifact), **kwargs)

        def log_message(self, *params):
            pass

        def admitted(self):
            if self.headers.get("Host") != f"localhost:{args.port}":
                self.send_error(403)
                return False
            if self.headers.get("Authorization") or self.headers.get("Cookie"):
                self.send_error(403)
                return False
            if urlsplit(self.path).query:
                self.send_error(400)
                return False
            verify_artifact(exp.sup, exp.ref(args.scope), target)
            return True

        def do_GET(self):
            if self.admitted():
                super().do_GET()

        def do_HEAD(self):
            # HEAD must pass the same Host/credential gate as GET.
            if self.admitted():
                super().do_HEAD()

        def list_directory(self, path):
            self.send_error(404)

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'none'; frame-ancestors 'none'; base-uri 'none'")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-DEV006-Target", target["target_id"])
            super().end_headers()

    print(json.dumps({"url": f"http://localhost:{args.port}", "target_id": target["target_id"], "evidence_id": evidence["evidence_id"]}), flush=True)
    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()

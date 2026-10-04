"""Fetch integrity-pinned npm tarballs without giving target code a network.

Only HTTPS tarballs on the public npm registry are supported by this MVP.
The host downloads bytes; unpacking and npm execution happen inside Docker.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import urllib.error
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .errors import SandboxError
from .fsutil import read_file_beneath
from .runspec import ResourceLimits


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SandboxError("dependency redirects are not allowed")


def registry_tarballs(lock: dict) -> list[tuple[str, bytes]]:
    if not isinstance(lock, dict) or lock.get("lockfileVersion") not in (2, 3) or not isinstance(lock.get("packages"), dict):
        raise SandboxError("offline install requires a v2/v3 package-lock.json")
    packages = lock["packages"]
    if len(packages) > 5000:
        raise SandboxError("dependency count exceeds 5000")
    items: dict[str, bytes] = {}
    for name, package in packages.items():
        if name == "":
            continue
        if not isinstance(package, dict):
            raise SandboxError("invalid dependency entry")
        url = package.get("resolved", "")
        if not isinstance(url, str):
            raise SandboxError("invalid dependency URL")
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != "https" or parsed.netloc != "registry.npmjs.org"
                or parsed.query or parsed.fragment or not parsed.path.endswith(".tgz")):
            raise SandboxError("dependencies must be HTTPS tarballs from registry.npmjs.org")
        integrity = package.get("integrity", "")
        if not isinstance(integrity, str) or not integrity.startswith("sha512-"):
            raise SandboxError("every dependency requires sha512 integrity")
        try:
            digest = base64.b64decode(integrity.removeprefix("sha512-"), validate=True)
        except ValueError as exc:
            raise SandboxError("invalid dependency integrity") from exc
        if len(digest) != 64:
            raise SandboxError("invalid dependency integrity")
        if url in items and items[url] != digest:
            raise SandboxError("conflicting integrity for a dependency")
        items[url] = digest
    return list(items.items())


def fetch_tarballs(source: Path, dest: Path, limits: ResourceLimits, *, deadline: float,
                   is_cancelled) -> None:
    # Disable user-supplied proxy settings and redirects. TLS uses Python's trust store.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    lock = json.loads(read_file_beneath(source, "package-lock.json"))
    remaining = limits.max_snapshot_bytes
    for index, (url, expected) in enumerate(registry_tarballs(lock)):
        if is_cancelled() or time.monotonic() >= deadline:
            raise SandboxError("dependency acquisition cancelled or timed out")
        target = dest / f"{index:05d}.tgz"
        for attempt in range(3):
            if is_cancelled() or time.monotonic() >= deadline:
                raise SandboxError("dependency acquisition cancelled or timed out")
            digest = hashlib.sha512()
            try:
                with opener.open(url, timeout=min(15, max(0.1, deadline - time.monotonic()))) as response:
                    with open(target, "wb") as output:
                        while True:
                            if is_cancelled() or time.monotonic() >= deadline:
                                raise SandboxError("dependency acquisition cancelled or timed out")
                            chunk = response.read(min(64 * 1024, remaining + 1))
                            if not chunk:
                                break
                            remaining -= len(chunk)
                            if remaining < 0:
                                raise SandboxError("dependency download exceeds byte budget")
                            output.write(chunk)
                            digest.update(chunk)
                if not hmac.compare_digest(digest.digest(), expected):
                    raise SandboxError("dependency tarball integrity mismatch")
                break
            except (OSError, http.client.IncompleteRead) as exc:
                target.unlink(missing_ok=True)
                transient = not isinstance(exc, urllib.error.HTTPError) or exc.code in (408, 429) or exc.code >= 500
                if attempt == 2 or not transient:
                    raise
                # No budget reset: previously read bytes and elapsed time still count.

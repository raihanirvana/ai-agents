"""Loopback TCP -> unix socket forwarder owned by the preview supervisor.

The preview container has no network. The only way to reach it is this proxy, which listens on 127.0.0.1 and
validates each HTTP request before forwarding it to the container's unix socket.
"""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path


# Trusted transport only: no command or socket address comes from an HTTP request.
# Docker Desktop cannot forward a VM's Unix socket through a macOS bind mount.
SOCKET_RELAY = """
const s = require('node:net').connect(process.env.PREVIEW_SOCKET);
s.setTimeout(30000);
s.on('error', () => process.exit(1));
s.on('timeout', () => s.destroy());
s.on('close', () => process.stdin.destroy());
// EOF on docker stdin must not close the HTTP socket before the server has
// finished streaming its response. The guarded request asks for Connection: close.
process.stdin.pipe(s, {end: false});
s.pipe(process.stdout);
"""


def relay_command(docker: str, container: str) -> tuple[str, ...]:
    return (docker, 'exec', '-i', container, 'node', '-e', SOCKET_RELAY)


class ProxyError(RuntimeError):
    pass


class UnixProxy:
    def __init__(self, port: int, socket_path: Path, *, host: str = "127.0.0.1", max_connections: int = 64,
                 idle_s: float = 30.0, relay: tuple[str, ...] | None = None):
        if host != "127.0.0.1":
            raise ProxyError("preview proxy binds to loopback only")
        self.port, self.socket_path, self.host = port, str(socket_path), host
        self.max_connections, self.idle_s = max_connections, idle_s
        self.relay = relay
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: asyncio.AbstractServer | None = None
        self._thread: threading.Thread | None = None
        self._tasks: set[asyncio.Task] = set()
        self._gate: asyncio.Semaphore | None = None

    def start(self, timeout_s: float = 5.0) -> None:
        started, failure = threading.Event(), []

        def run():
            loop = asyncio.new_event_loop()
            self._loop = loop
            asyncio.set_event_loop(loop)
            self._gate = asyncio.Semaphore(self.max_connections)
            try:
                self._server = loop.run_until_complete(asyncio.start_server(self._client, self.host, self.port))
            except OSError as exc:
                failure.append(exc)
                started.set()
                loop.close()
                return
            started.set()
            try:
                loop.run_forever()
            finally:
                for task in list(self._tasks):
                    task.cancel()
                if self._tasks:
                    loop.run_until_complete(asyncio.gather(*self._tasks, return_exceptions=True))
                self._server.close()
                loop.run_until_complete(self._server.wait_closed())
                loop.close()

        self._thread = threading.Thread(target=run, name=f"preview-proxy-{self.port}", daemon=True)
        self._thread.start()
        if not started.wait(timeout_s):
            raise ProxyError("preview proxy did not start")
        if failure:
            raise ProxyError(f"cannot listen on {self.host}:{self.port}: {failure[0]}")

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def stop(self) -> None:
        loop, thread = self._loop, self._thread
        if loop is not None and thread is not None and thread.is_alive():
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=5)
        self._thread = None

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        self._tasks.add(task)
        try:
            if self._gate.locked():  # bounded: a hostile page cannot open unlimited connections
                return
            async with self._gate:
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), self.idle_s)
                if not self._allowed(head):
                    writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
                    await asyncio.wait_for(writer.drain(), self.idle_s)
                    return
                process = None
                if self.relay is None:
                    upstream_reader, upstream_writer = await asyncio.open_unix_connection(self.socket_path)
                else:
                    process = await asyncio.create_subprocess_exec(*self.relay, stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                    upstream_reader, upstream_writer = process.stdout, process.stdin
                try:
                    # One request per connection: pipelined requests cannot bypass the header guard.
                    lines = head.split(b"\r\n")
                    forwarded = [line for line in lines[:-2] if not line.lower().startswith(b"connection:")]
                    upstream_writer.write(b"\r\n".join(forwarded) + b"\r\nConnection: close\r\n\r\n")
                    await asyncio.wait_for(upstream_writer.drain(), self.idle_s)
                    await self._pipe(upstream_reader, writer)
                finally:
                    upstream_writer.close()
                    if process is not None:
                        # Bound the host CLI lifetime, including disconnect and proxy shutdown.
                        if process.returncode is None:
                            try:
                                process.kill()
                            except ProcessLookupError:
                                pass
                        await process.wait()
        except (OSError, asyncio.CancelledError, asyncio.TimeoutError, asyncio.IncompleteReadError,
                asyncio.LimitOverrunError):
            pass
        finally:
            writer.close()
            self._tasks.discard(task)

    def _allowed(self, head: bytes) -> bool:
        try:
            lines = head.decode("ascii").split("\r\n")
            method, path, version = lines[0].split(" ")
            if method not in ("GET", "HEAD") or not path.startswith("/") or path.startswith("//") \
                    or version not in ("HTTP/1.0", "HTTP/1.1"):
                return False
            headers = {}
            for line in lines[1:-2]:
                name, value = line.split(":", 1)
                if not name or name.strip() != name or name.lower() in headers:
                    return False
                headers[name.lower()] = value.strip()
            if headers.get("host") != f"localhost:{self.port}":
                return False
            if any(k in headers for k in ("cookie", "authorization", "proxy-authorization", "x-csrf-token")):
                return False
            if "origin" in headers and headers["origin"] != f"http://localhost:{self.port}":
                return False
            return "transfer-encoding" not in headers and headers.get("content-length", "0") == "0" \
                and headers.get("connection", "close").lower() in ("close", "keep-alive")
        except (UnicodeDecodeError, ValueError):
            return False

    async def _pipe(self, source: asyncio.StreamReader, sink: asyncio.StreamWriter) -> None:
        try:
            while True:
                data = await asyncio.wait_for(source.read(65536), self.idle_s)
                if not data:
                    break
                sink.write(data)
                await asyncio.wait_for(sink.drain(), self.idle_s)
        except (asyncio.TimeoutError, OSError, ConnectionError):
            pass
        finally:
            try:
                sink.write_eof()
            except (OSError, RuntimeError, NotImplementedError):
                pass


def fetch_via_socket(socket_path: Path, path: str = "/", timeout_s: float = 3.0) -> tuple[int, bytes]:
    """One HTTP/1.0 GET straight to the container socket (health/smoke check, no TCP involved)."""
    import socket
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout_s)
        sock.connect(str(socket_path))
        sock.sendall(f"GET {path} HTTP/1.0\r\nHost: preview\r\n\r\n".encode())
        chunks = []
        while len(b"".join(chunks)) < 9 * 1024 * 1024:
            data = sock.recv(65536)
            if not data:
                break
            chunks.append(data)
    raw = b"".join(chunks)
    head, _, body = raw.partition(b"\r\n\r\n")
    try:
        return int(head.split(b" ", 2)[1]), body
    except (IndexError, ValueError):
        return 0, b""

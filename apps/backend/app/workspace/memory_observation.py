"""Optional observational sampling for kernels without cgroup memory.peak.

Enabled only for benchmarking. Samples are a lower bound on peak usage, never
an OOM/gate/approval decision. Each probe ends normally; no resident container job.
"""
import threading


class MemoryObservation:
    def __init__(self, sandbox, name):
        self.sandbox, self.name = sandbox, name
        self.maximum = None
        self.samples = 0
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self.done.is_set():
            try:
                result = self.sandbox._docker('exec', self.name, 'sh', '-c',
                    'cat /sys/fs/cgroup/memory.current 2>/dev/null || cat /sys/fs/cgroup/memory/memory.usage_in_bytes',
                    timeout=2, check=False)
                value = int(result.stdout.strip())
                if result.returncode == 0 and value >= 0:
                    self.maximum = max(self.maximum or 0, value)
                    self.samples += 1
            except Exception:
                pass  # Observability never changes a command outcome.
            self.done.wait(0.5)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.done.set()
        self.thread.join(timeout=3)
        return self.maximum

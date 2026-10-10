"""Bound Docker CLI streams and import a quiescent, untrusted source archive."""
import os
import selectors
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

from . import fsutil
from .errors import SandboxError, LimitExceeded, PathViolation


def stream_command(argv, *, deadline, cancelled, cap, output=None):
    """Drain both pipes; keep bounded logs or abort an oversized archive."""
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    buffers = [bytearray(), bytearray()]
    sizes = [0, 0]
    timed_out = stopped = False
    try:
        with selectors.DefaultSelector() as poll:
            for index, pipe in enumerate((proc.stdout, proc.stderr)):
                os.set_blocking(pipe.fileno(), False)
                poll.register(pipe, selectors.EVENT_READ, index)
            while poll.get_map() or proc.poll() is None:
                stopped, timed_out = cancelled(), time.monotonic() >= deadline
                if stopped or timed_out:
                    break
                for key, _ in poll.select(timeout=0.1):
                    data = os.read(key.fd, 65536)
                    if not data:
                        poll.unregister(key.fileobj)
                        continue
                    index = key.data
                    sizes[index] += len(data)
                    if output is not None and index == 0:
                        if sizes[index] > cap:
                            raise LimitExceeded('sandbox archive exceeds transport byte bound')
                        output.write(data)
                    else:
                        log_cap = min(cap, 1024 * 1024) if output is not None else cap
                        buffers[index].extend(data[:max(0, log_cap-len(buffers[index]))])
            if stopped or timed_out:
                proc.kill()
            code = proc.wait(timeout=5)
        return code, bytes(buffers[0]), bytes(buffers[1]), timed_out, stopped, any(n > cap for n in sizes)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        proc.stdout.close()
        proc.stderr.close()


def import_work(docker, name, source, limits, *, deadline, cancelled):
    """Pause first. Import only validated files; never extract target tar paths directly."""
    source = Path(source)
    tree_limits = fsutil.TreeLimits(limits.max_snapshot_files, limits.max_snapshot_bytes)
    with tempfile.TemporaryDirectory(dir=source.parent, prefix='.work-export-') as temporary:
        root = Path(temporary)
        archive, stage = root / 'work.tar', root / 'tree'
        stage.mkdir(mode=0o755)
        stage.chmod(0o755)
        with archive.open('wb') as output:
            code, _, error, timed_out, stopped, _ = stream_command(
                [docker, 'cp', name + ':/work/.', '-'], deadline=deadline, cancelled=cancelled,
                cap=limits.max_snapshot_bytes + limits.max_snapshot_files * 4096 + 1024 * 1024, output=output)
        if code != 0 or timed_out or stopped:
            raise SandboxError('sandbox export failed or interrupted: ' + error.decode(errors='replace')[:300])
        seen, links, total = set(), [], 0
        try:
            tar = tarfile.open(archive, mode='r:')
        except tarfile.TarError as exc:
            raise PathViolation('sandbox export is not a valid tar archive') from exc
        with tar:
            for member in tar:
                if cancelled() or time.monotonic() >= deadline:
                    raise SandboxError('sandbox export validation interrupted')
                rel = member.name
                if rel in ('.', './') and member.isdir():
                    continue
                if rel.startswith('./'):
                    rel = rel[2:]
                if member.isdir():
                    rel = rel.rstrip('/')
                fsutil.validate_relpath(rel)
                if rel in seen or len(seen) >= tree_limits.max_files:
                    raise LimitExceeded('duplicate or too many sandbox archive entries')
                seen.add(rel)
                total += member.size
                if total > tree_limits.max_bytes or member.size > tree_limits.max_file_bytes or member.size < 0:
                    raise LimitExceeded('sandbox archive exceeds snapshot byte bound')
                if member.isdir():
                    (stage / rel).mkdir(parents=True, exist_ok=True)
                elif member.isfile() and not member.issparse():
                    with tar.extractfile(member) as content:
                        data = content.read(member.size + 1)
                    if len(data) != member.size:
                        raise PathViolation('sandbox archive file length mismatch')
                    fsutil.write_file_beneath(stage, rel, data, executable=bool(member.mode & 0o111))
                elif member.issym():
                    links.append((rel, member.linkname))
                else:
                    raise PathViolation('sandbox archive contains a special file or hardlink')
        # No symlink exists while regular files/directories are materialised.
        # Never create parents through a previously installed symlink either.
        for rel, target in links:
            fd = fsutil.open_beneath(stage, rel, os.O_WRONLY | os.O_CREAT | os.O_EXCL, create_dirs=True)
            os.close(fd)
        for rel, target in links:
            (stage / rel).unlink()
            os.symlink(target, stage / rel)
        entries = fsutil.scan_tree(stage, limits=tree_limits)
        for entry in entries:
            if entry.kind == 'dir':
                (stage / entry.rel).chmod(0o755)
        if cancelled() or time.monotonic() >= deadline:
            raise SandboxError('sandbox export publication interrupted')
        # Caller serializes source operations; old bytes survive failed validation.
        backup = root / 'previous'
        os.replace(source, backup)
        try:
            os.replace(stage, source)
        except BaseException:
            os.replace(backup, source)
            raise
        shutil.rmtree(backup)

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


# Dependency limits are separate from the editable source/output snapshot.
DEPENDENCY_LIMITS = fsutil.TreeLimits(100_000, 512 * 1024 * 1024)
DEPENDENCY_SCRATCH = ('.vite', '.vite-temp', '.cache')

# docker cp reads the container rootfs, not its live tmpfs mounts (Docker 24
# returns an empty /work). Freeze every target process before using the image's
# readonly tar binary. PID 1 is the trusted idle sleep; only this exporter can
# run afterward. Never add host capabilities or a writable host mount.
_EXPORT_WORK = r"""
const fs=require('node:fs'), cp=require('node:child_process');
function targets() { return fs.readdirSync('/proc').filter(n=>/^\d+$/.test(n)
  && Number(n)!==1 && Number(n)!==process.pid); }
let frozen=false;
for(let attempt=0;attempt<100;attempt++) {
  for(const pid of targets()) {
    try { process.kill(Number(pid),'SIGSTOP'); }
    catch(e) { if(e.code!=='ESRCH') throw e; }
  }
  frozen=targets().every(pid=> {
    try { return /^State:\s+[TtZX]/m.test(fs.readFileSync('/proc/'+pid+'/status','utf8')); }
    catch(e) { if(e.code==='ENOENT') return true; throw e; }
  });
  if(frozen) break;
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)),0,0,10);
}
if(!frozen) throw new Error('sandbox processes did not quiesce');
const result=cp.spawnSync('/bin/tar',['-C','/work','-cf','-','.'],
  {stdio:'inherit',env:{PATH:'/usr/local/bin:/usr/bin:/bin',LANG:'C'}});
if(result.error) throw result.error;
process.exit(result.status===null ? 1 : result.status);
"""


def prepare_dependency_scratch(tree):
    """Empty mountpoints only; target scratch lives in bounded container tmpfs."""
    import stat
    for name in DEPENDENCY_SCRATCH:
        path = Path(tree) / name
        path.mkdir(mode=0o755, exist_ok=True)
        if not stat.S_ISDIR(path.lstat().st_mode):
            raise PathViolation('dependency scratch mountpoint must be a real directory')

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


def import_work(docker, name, source, limits, *, deadline, cancelled, preserve_dependencies=False, install_phase=False):
    """Freeze, stream, pause, then validate; never extract target tar paths directly."""
    source = Path(source)
    tree_limits = fsutil.TreeLimits(limits.max_snapshot_files, limits.max_snapshot_bytes)
    with tempfile.TemporaryDirectory(dir=source.parent, prefix='.work-export-') as temporary:
        root = Path(temporary)
        archive, stage = root / 'work.tar', root / 'tree'
        stage.mkdir(mode=0o755)
        stage.chmod(0o755)
        export_files = tree_limits.max_files + (DEPENDENCY_LIMITS.max_files if install_phase else 0)
        export_bytes = tree_limits.max_bytes + (DEPENDENCY_LIMITS.max_bytes if install_phase else 0)
        with archive.open('wb') as output:
            code, _, error, timed_out, stopped, _ = stream_command(
                [docker, 'exec', '-e', 'NODE_OPTIONS=', '-e', 'NODE_PATH=',
                 '-e', 'PATH=/usr/local/bin:/usr/bin:/bin', name, 'node', '-e', _EXPORT_WORK],
                deadline=deadline, cancelled=cancelled,
                cap=export_bytes + export_files * 4096 + 1024 * 1024, output=output)
        if code != 0 or timed_out or stopped:
            raise SandboxError('sandbox export failed or interrupted: ' + error.decode(errors='replace')[:300])
        code, _, error, timed_out, stopped, _ = stream_command(
            [docker, 'pause', name], deadline=deadline, cancelled=cancelled, cap=limits.max_log_bytes)
        if code != 0 or timed_out or stopped:
            raise SandboxError('sandbox pause failed or interrupted: ' + error.decode(errors='replace')[:300])
        seen, links, total = set(), [], 0
        dependency_count, dependency_bytes, source_count = 0, 0, 0
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
                dependency = rel == 'node_modules' or rel.startswith('node_modules/')
                if preserve_dependencies and dependency:
                    if rel != 'node_modules' or not member.issym() or member.linkname != '/installed/node_modules':
                        raise PathViolation('readonly dependency mount reference was modified')
                    if rel in seen:
                        raise PathViolation('duplicate readonly dependency reference')
                    seen.add(rel)
                    continue
                if dependency:
                    dependency_count += 1
                    dependency_bytes += member.size
                    if not install_phase or dependency_count > DEPENDENCY_LIMITS.max_files or dependency_bytes > DEPENDENCY_LIMITS.max_bytes:
                        raise LimitExceeded('dependency export exceeds separate installation limits')
                else:
                    source_count += 1
                    total += member.size
                if rel in seen or source_count > tree_limits.max_files:
                    raise LimitExceeded('duplicate or too many sandbox archive entries')
                seen.add(rel)
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
        entries = fsutil.scan_tree(stage, exclude=('node_modules',), limits=tree_limits)
        if install_phase and ((stage / 'node_modules').exists() or (stage / 'node_modules').is_symlink()):
            fsutil.scan_tree(stage / 'node_modules', limits=DEPENDENCY_LIMITS)
            prepare_dependency_scratch(stage / 'node_modules')
            fsutil.scan_tree(stage / 'node_modules', limits=DEPENDENCY_LIMITS)
        for entry in entries:
            if entry.kind == 'dir':
                (stage / entry.rel).chmod(0o755)
        if cancelled() or time.monotonic() >= deadline:
            raise SandboxError('sandbox export publication interrupted')
        # Caller serializes source operations; old bytes survive failed validation.
        backup = root / 'previous'
        os.replace(source, backup)
        dependency_moved = False
        try:
            if preserve_dependencies:
                os.replace(backup / 'node_modules', stage / 'node_modules')
                dependency_moved = True
            os.replace(stage, source)
        except BaseException:
            if dependency_moved:
                os.replace(stage / 'node_modules', backup / 'node_modules')
            os.replace(backup, source)
            raise
        shutil.rmtree(backup)

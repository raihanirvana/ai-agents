"""A bounded, immutable input mount unique to one command.

Never bind the path which import_work replaces. Inventory is checked by the
trusted initializer before target code runs; dependencies stay on their own mount.
"""
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from . import fsutil


def prepare(source, limits):
    entries = fsutil.scan_tree(source, exclude=('node_modules',), limits=fsutil.TreeLimits(
        limits.max_snapshot_files, limits.max_snapshot_bytes, min(limits.max_snapshot_bytes, 50 * 1024 * 1024)))
    root = Path(tempfile.mkdtemp(prefix='.command-seed-', dir=Path(source).parent))
    try:
        root.chmod(0o755)
        tree = root / 'source'
        tree.mkdir(mode=0o755)
        fsutil.copy_entries(source, entries, tree, sandbox_visible=False)
        inventory = []
        for entry in entries:
            item = {'path': entry.rel, 'kind': entry.kind}
            if entry.kind == 'file':
                item['sha256'] = hashlib.sha256(fsutil.read_file_beneath(
                    tree, entry.rel, max_bytes=limits.max_snapshot_bytes)).hexdigest()
            elif entry.kind == 'symlink':
                item['target'] = entry.target
            inventory.append(item)
        manifest = root / 'inventory.json'
        manifest.write_text(json.dumps(inventory, separators=(',', ':')))
        manifest.chmod(0o644)
        return root, tree, manifest
    except BaseException:
        shutil.rmtree(root)
        raise

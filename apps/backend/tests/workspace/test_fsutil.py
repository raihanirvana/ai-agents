import os

import pytest

from app.workspace import LimitExceeded, PathViolation
from app.workspace import fsutil


@pytest.mark.parametrize("bad", [
    "../x", "/etc/passwd", "a/../b", "a//b", "./a", ".git", "a/.git/config", ".GIT/hooks", ".git /x",
    "a\\b", "a\x00b", "git~1/x", "x/" + "y" * 300,
])
def test_validate_relpath_rejects(bad):
    with pytest.raises(PathViolation):
        fsutil.validate_relpath(bad)


def test_validate_relpath_accepts_normal_and_gitignore():
    assert str(fsutil.validate_relpath("src/app/.gitignore")) == "src/app/.gitignore"


def test_scan_accepts_internal_relative_symlink(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "f.txt").write_text("x")
    os.symlink("a/f.txt", tmp_path / "link")
    kinds = {e.rel: e.kind for e in fsutil.scan_tree(tmp_path)}
    assert kinds == {"a": "dir", "a/f.txt": "file", "link": "symlink"}


def test_scan_rejects_absolute_symlink(tmp_path):
    os.symlink("/etc/passwd", tmp_path / "l")
    with pytest.raises(PathViolation):
        fsutil.scan_tree(tmp_path)


def test_scan_rejects_symlink_chain_that_lexically_looks_inside(tmp_path):
    # a/b -> .. is inside the tree, but s1 -> a/b/.. resolves to the parent of the tree.
    root = tmp_path / "tree"
    (root / "a").mkdir(parents=True)
    os.symlink("..", root / "a" / "b")
    os.symlink("a/b/..", root / "s1")
    with pytest.raises(PathViolation):
        fsutil.scan_tree(root)


def test_scan_rejects_fifo_and_nested_git(tmp_path):
    os.mkfifo(tmp_path / "pipe")
    with pytest.raises(PathViolation):
        fsutil.scan_tree(tmp_path)
    os.unlink(tmp_path / "pipe")
    (tmp_path / "sub" / ".git" / "hooks").mkdir(parents=True)
    with pytest.raises(PathViolation):
        fsutil.scan_tree(tmp_path)


def test_scan_excludes_listed_top_level_without_scanning_it(tmp_path):
    (tmp_path / "node_modules").mkdir()
    os.symlink("/etc/passwd", tmp_path / "node_modules" / "evil")
    (tmp_path / "ok.txt").write_text("1")
    assert [e.rel for e in fsutil.scan_tree(tmp_path, exclude=["node_modules"])] == ["ok.txt"]


def test_scan_enforces_bounds(tmp_path):
    for i in range(5):
        (tmp_path / f"f{i}").write_text("12345")
    with pytest.raises(LimitExceeded):
        fsutil.scan_tree(tmp_path, limits=fsutil.TreeLimits(max_files=3))
    with pytest.raises(LimitExceeded):
        fsutil.scan_tree(tmp_path, limits=fsutil.TreeLimits(max_bytes=10))


def test_tools_do_not_follow_symlinked_directories(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("host secret")
    root = tmp_path / "src"
    root.mkdir()
    os.symlink(outside, root / "d")
    with pytest.raises(OSError):
        fsutil.read_file_beneath(root, "d/secret.txt")
    with pytest.raises(OSError):
        fsutil.write_file_beneath(root, "d/new.txt", b"x")
    assert not (outside / "new.txt").exists()


def test_tools_do_not_follow_final_symlink(tmp_path):
    target = tmp_path / "host.txt"
    target.write_text("original")
    root = tmp_path / "src"
    root.mkdir()
    os.symlink(target, root / "f")
    with pytest.raises(OSError):
        fsutil.write_file_beneath(root, "f", b"overwritten")
    assert target.read_text() == "original"


def test_write_and_read_roundtrip_creates_dirs(tmp_path):
    fsutil.write_file_beneath(tmp_path, "a/b/c.txt", b"hello")
    assert fsutil.read_file_beneath(tmp_path, "a/b/c.txt") == b"hello"
    fsutil.remove_beneath(tmp_path, "a/b/c.txt")
    assert not (tmp_path / "a/b/c.txt").exists()


def test_sha256_tree_is_content_and_mode_sensitive(tmp_path):
    (tmp_path / "f").write_text("1")
    first = fsutil.sha256_tree(tmp_path, fsutil.scan_tree(tmp_path))
    (tmp_path / "f").write_text("2")
    second = fsutil.sha256_tree(tmp_path, fsutil.scan_tree(tmp_path))
    os.chmod(tmp_path / "f", 0o755)
    third = fsutil.sha256_tree(tmp_path, fsutil.scan_tree(tmp_path))
    assert len({first, second, third}) == 3

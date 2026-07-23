import subprocess
from pathlib import Path

from utils.git_utils import ensure_base_clone


def _make_origin_repo(path: Path) -> str:
    path.mkdir()
    subprocess.run(["git", "init", "--initial-branch=main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "README.md").write_text("hello")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)
    return str(path)


def test_ensure_base_clone_clones_when_absent(tmp_path: Path, monkeypatch):
    origin = _make_origin_repo(tmp_path / "origin")
    monkeypatch.setattr("utils.git_utils.REPOS_DIR", str(tmp_path / "cache"))

    bare_path = ensure_base_clone(origin)

    assert Path(bare_path).is_dir()
    subprocess.run(["git", "rev-parse", "--git-dir"], cwd=bare_path, check=True, capture_output=True)


def test_ensure_base_clone_fetches_when_already_valid(tmp_path: Path, monkeypatch):
    origin = _make_origin_repo(tmp_path / "origin")
    monkeypatch.setattr("utils.git_utils.REPOS_DIR", str(tmp_path / "cache"))

    first_bare_path = ensure_base_clone(origin)
    second_bare_path = ensure_base_clone(origin)

    assert first_bare_path == second_bare_path
    subprocess.run(["git", "rev-parse", "--git-dir"], cwd=second_bare_path, check=True, capture_output=True)


def test_ensure_base_clone_recovers_from_corrupted_cache(tmp_path: Path, monkeypatch):
    origin = _make_origin_repo(tmp_path / "origin")
    monkeypatch.setattr("utils.git_utils.REPOS_DIR", str(tmp_path / "cache"))

    bare_path = ensure_base_clone(origin)
    # Simulate an OS temp-directory cleanup sweeping HEAD out of an otherwise-intact
    # bare clone -- the directory still exists, but git no longer recognizes it.
    (Path(bare_path) / "HEAD").unlink()
    result = subprocess.run(["git", "rev-parse", "--git-dir"], cwd=bare_path, capture_output=True)
    assert result.returncode != 0

    recovered_path = ensure_base_clone(origin)

    assert recovered_path == bare_path
    subprocess.run(["git", "rev-parse", "--git-dir"], cwd=recovered_path, check=True, capture_output=True)

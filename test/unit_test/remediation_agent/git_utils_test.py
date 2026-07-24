import subprocess
from pathlib import Path
from unittest.mock import patch

from utils.git_utils import _ensure_gh_git_auth, ensure_base_clone, open_pull_request


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


def test_open_pull_request_sets_up_gh_auth_before_pushing():
    with patch("utils.git_utils.run", return_value="") as mock_run:
        open_pull_request("/repo", "branch", "commit msg", "title", "body")

    commands = [call.args[0] for call in mock_run.call_args_list]
    # gh auth setup-git must run before git push -- without it, a raw `git push` has no
    # way to authenticate even when GH_TOKEN is set (only `gh` itself reads that var
    # directly), and fails with "could not read Username for 'https://github.com'".
    assert commands[0] == ["gh", "auth", "setup-git"]
    assert ["git", "push", "-u", "origin", "branch"] in commands
    assert commands.index(["gh", "auth", "setup-git"]) < commands.index(["git", "push", "-u", "origin", "branch"])


def test_ensure_base_clone_sets_up_gh_auth_before_cloning(tmp_path: Path, monkeypatch):
    origin = _make_origin_repo(tmp_path / "origin")
    monkeypatch.setattr("utils.git_utils.REPOS_DIR", str(tmp_path / "cache"))

    with patch("utils.git_utils._ensure_gh_git_auth") as mock_auth:
        ensure_base_clone(origin)

    # Same gap as the push path: a private repo_url's `git clone`/`git fetch` would fail
    # the same way without this, and it must run before the clone actually happens.
    mock_auth.assert_called_once()


def test_ensure_gh_git_auth_is_best_effort_and_never_raises():
    # If gh isn't installed/authenticated (e.g. a local dev machine with no GH_TOKEN),
    # this must fail silently -- it should never block the actual git operation that
    # follows, which will surface its own clear error if credentials are truly needed.
    with patch("utils.git_utils.run", side_effect=RuntimeError("gh: not logged in")):
        _ensure_gh_git_auth("/some/path")  # must not raise

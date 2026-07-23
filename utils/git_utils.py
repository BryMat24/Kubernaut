import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import fcntl
from contextlib import contextmanager

CACHE_ROOT = os.path.join(tempfile.gettempdir(), "repos")
REPOS_DIR = os.path.join(CACHE_ROOT, "repos")
WORKTREES_DIR = os.path.join(CACHE_ROOT, "worktrees")


def run(cmd: list[str], cwd: str) -> str:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)}\n{result.stderr.strip()}")
    return result.stdout.strip()


def get_changed_files(repo: str) -> list[str]:
    run(["git", "add", "-A"], repo)
    output = run(["git", "diff", "--cached", "--name-only"], repo)
    return [f.strip() for f in output.splitlines() if f.strip()]


def get_diff_content(repo: str) -> str:
    run(["git", "add", "-A"], repo)
    return run(["git", "diff", "--cached"], repo)


def slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-")


def open_pull_request(
    repo: str,
    branch: str,
    commit_message: str,
    title: str,
    body: str,
    base: str = "main",
) -> str:
    run(["git", "add", "-A"], repo)
    run(["git", "commit", "-m", commit_message], repo)
    run(["git", "push", "-u", "origin", branch], repo)
    return run(["gh", "pr", "create", "--base", base, "--head", branch, "--title", title, "--body", body], repo)


def _repo_slug(repo_url: str) -> str:
    name = repo_url.rstrip("/").removesuffix(".git").rsplit("/", 1)[-1]
    digest = hashlib.sha1(repo_url.encode()).hexdigest()[:8]
    return f"{slugify(name)}-{digest}"


@contextmanager
def repo_lock(repo_url: str):
    os.makedirs(REPOS_DIR, exist_ok=True)
    lock_path = os.path.join(REPOS_DIR, f"{_repo_slug(repo_url)}.lock")
    with open(lock_path, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _is_valid_git_dir(path: str) -> bool:
    result = subprocess.run(["git", "rev-parse", "--git-dir"], cwd=path, capture_output=True, text=True)
    return result.returncode == 0


def ensure_base_clone(repo_url: str, base: str = "main") -> str:
    os.makedirs(REPOS_DIR, exist_ok=True)
    bare_path = os.path.join(REPOS_DIR, f"{_repo_slug(repo_url)}.git")
    # A cache directory can exist but be a corrupted/incomplete repo (e.g. a file like
    # HEAD swept by an OS temp-directory cleanup, or an interrupted clone) -- isdir()
    # alone can't tell the difference, so re-clone whenever the git-dir check fails.
    if os.path.isdir(bare_path) and not _is_valid_git_dir(bare_path):
        shutil.rmtree(bare_path)
    if not os.path.isdir(bare_path):
        run(["git", "clone", "--bare", repo_url, bare_path], REPOS_DIR)
    else:
        run(["git", "fetch", "origin", f"+refs/heads/{base}:refs/heads/{base}"], bare_path)
    return bare_path


def create_task_worktree(bare_path: str, branch: str, base: str = "main") -> str:
    os.makedirs(WORKTREES_DIR, exist_ok=True)
    worktree_path = os.path.join(WORKTREES_DIR, branch)
    run(["git", "worktree", "add", "-b", branch, worktree_path, base], bare_path)
    return worktree_path


def remove_task_worktree(bare_path: str, worktree_path: str) -> None:
    run(["git", "worktree", "remove", "--force", worktree_path], bare_path)
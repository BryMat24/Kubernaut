import hashlib
import os
import fcntl
import tempfile
from contextlib import contextmanager

from .git_utils import run, slugify

CACHE_ROOT = os.path.join(tempfile.gettempdir(), "repos")
REPOS_DIR = os.path.join(CACHE_ROOT, "repos")
WORKTREES_DIR = os.path.join(CACHE_ROOT, "worktrees")


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


def ensure_base_clone(repo_url: str, base: str = "main") -> str:
    os.makedirs(REPOS_DIR, exist_ok=True)
    bare_path = os.path.join(REPOS_DIR, f"{_repo_slug(repo_url)}.git")
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

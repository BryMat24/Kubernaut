import re
import subprocess


def run(cmd: list[str], cwd: str) -> str:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)}\n{result.stderr.strip()}")
    return result.stdout.strip()


def get_changed_files(repo: str) -> list[str]:
    output = run(["git", "diff", "--name-only"], repo)
    return [f.strip() for f in output.splitlines() if f.strip()]


def get_diff_content(repo: str) -> str:
    return run(["git", "diff"], repo)


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
    run(["git", "checkout", "-b", branch], repo)
    run(["git", "add", "-A"], repo)
    run(["git", "commit", "-m", commit_message], repo)
    run(["git", "push", "-u", "origin", branch], repo)
    return run(["gh", "pr", "create", "--base", base, "--head", branch, "--title", title, "--body", body], repo)
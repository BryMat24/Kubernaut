import subprocess

def get_changed_files(repo) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    return [f.strip() for f in result.stdout.splitlines() if f.strip()]

def get_diff_content(repo) -> str:
    result = subprocess.run(
        ["git", "diff"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    return result.stdout
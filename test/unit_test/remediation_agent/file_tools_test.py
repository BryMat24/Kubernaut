import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from tools.file_tools import (
    MAX_CHARS,
    edit_file,
    find,
    grep,
    list_files_in_directory,
    read_file_content,
    write_file,
)


def make_completed_process(stdout: str = "", stderr: str = "", returncode: int = 0):
    proc = MagicMock()
    proc.stdout = stdout
    proc.stderr = stderr
    proc.returncode = returncode
    return proc


# ------------------------------------------------------------------
# list_files_in_directory
# ------------------------------------------------------------------

def test_list_files_in_directory_success(tmp_path: Path):
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "subdir").mkdir()

    result = list_files_in_directory.func(str(tmp_path), ".")

    assert "a.txt: file_size=5 bytes, is_dir=False" in result
    assert "subdir: file_size=" in result and "is_dir=True" in result


def test_list_files_in_directory_empty_dir(tmp_path: Path):
    result = list_files_in_directory.func(str(tmp_path), ".")
    assert result == ""


def test_list_files_in_directory_nested_subdirectory(tmp_path: Path):
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "b.txt").write_text("data")

    result = list_files_in_directory.func(str(tmp_path), "nested")

    assert "b.txt: file_size=4 bytes, is_dir=False" in result


def test_list_files_in_directory_outside_working_directory(tmp_path: Path):
    result = list_files_in_directory.func(str(tmp_path), "../../etc")
    assert "outside the permitted working directory" in result


def test_list_files_in_directory_not_a_directory(tmp_path: Path):
    (tmp_path / "file.txt").write_text("x")
    result = list_files_in_directory.func(str(tmp_path), "file.txt")
    assert 'is not a directory' in result


# ------------------------------------------------------------------
# read_file_content
# ------------------------------------------------------------------

def test_read_file_content_success(tmp_path: Path):
    (tmp_path / "a.txt").write_text("hello world")
    result = read_file_content.func(str(tmp_path), "a.txt")
    assert result == "hello world"


def test_read_file_content_outside_working_directory(tmp_path: Path):
    result = read_file_content.func(str(tmp_path), "../../etc/passwd")
    assert "outside the permitted working directory" in result


def test_read_file_content_file_not_found(tmp_path: Path):
    result = read_file_content.func(str(tmp_path), "missing.txt")
    assert "File not found" in result


def test_read_file_content_truncates_large_files(tmp_path: Path):
    big_content = "x" * (MAX_CHARS + 500)
    (tmp_path / "big.txt").write_text(big_content)

    result = read_file_content.func(str(tmp_path), "big.txt")

    assert result.startswith("x" * MAX_CHARS)
    assert "truncated at 10000 characters" in result


def test_read_file_content_does_not_truncate_exact_max_chars(tmp_path: Path):
    content = "x" * MAX_CHARS
    (tmp_path / "exact.txt").write_text(content)

    result = read_file_content.func(str(tmp_path), "exact.txt")

    assert result == content
    assert "truncated" not in result


# ------------------------------------------------------------------
# grep
# ------------------------------------------------------------------

def _rg_match_line(file_path: str, line_number: int, text: str) -> str:
    return json.dumps({
        "type": "match",
        "data": {
            "path": {"text": file_path},
            "line_number": line_number,
            "lines": {"text": text + "\n"},
        },
    })


def test_grep_success_literal(tmp_path: Path):
    ndjson = "\n".join([
        _rg_match_line("./deployment.yaml", 12, "  mountPath: /data"),
        json.dumps({"type": "begin", "data": {}}),
    ])
    with patch("tools.file_tools.subprocess.run", return_value=make_completed_process(stdout=ndjson, returncode=0)) as mock_run:
        result = grep.func(str(tmp_path), "mountPath")

    cmd = mock_run.call_args.args[0]
    assert cmd == ["rg", "--json", "--fixed-strings", "--", "mountPath", "."]
    assert result == [{"file_path": "deployment.yaml", "line_number": 12, "line_content": "  mountPath: /data"}]


def test_grep_is_regex_omits_fixed_strings(tmp_path: Path):
    with patch("tools.file_tools.subprocess.run", return_value=make_completed_process(stdout="", returncode=0)) as mock_run:
        grep.func(str(tmp_path), "mount.*Path", is_regex=True)

    cmd = mock_run.call_args.args[0]
    assert "--fixed-strings" not in cmd
    assert cmd == ["rg", "--json", "--", "mount.*Path", "."]


def test_grep_with_file_glob(tmp_path: Path):
    with patch("tools.file_tools.subprocess.run", return_value=make_completed_process(stdout="", returncode=0)) as mock_run:
        grep.func(str(tmp_path), "pattern", file_glob="*.yaml")

    cmd = mock_run.call_args.args[0]
    assert "--glob" in cmd
    assert cmd[cmd.index("--glob") + 1] == "*.yaml"


def test_grep_no_matches(tmp_path: Path):
    with patch("tools.file_tools.subprocess.run", return_value=make_completed_process(stdout="", returncode=1)):
        result = grep.func(str(tmp_path), "nonexistent")
    assert result == []


def test_grep_ripgrep_not_installed(tmp_path: Path):
    with patch("tools.file_tools.subprocess.run", side_effect=FileNotFoundError()):
        result = grep.func(str(tmp_path), "pattern")
    assert "ripgrep" in result and "not installed" in result


def test_grep_ripgrep_failure(tmp_path: Path):
    with patch("tools.file_tools.subprocess.run", return_value=make_completed_process(stderr="some rg error", returncode=2)):
        result = grep.func(str(tmp_path), "pattern")
    assert "ripgrep failed" in result
    assert "some rg error" in result


def test_grep_working_directory_not_a_directory(tmp_path: Path):
    missing = tmp_path / "does-not-exist"
    with patch("tools.file_tools.subprocess.run") as mock_run:
        result = grep.func(str(missing), "pattern")
    mock_run.assert_not_called()
    assert "is not a directory" in result


# ------------------------------------------------------------------
# find
# ------------------------------------------------------------------

def test_find_success(tmp_path: Path):
    with patch("tools.file_tools.subprocess.run", return_value=make_completed_process(
        stdout="./apps/api/deployment.yaml\n./apps/frontend/deployment.yaml\n", returncode=0
    )) as mock_run:
        result = find.func(str(tmp_path), "deployment.yaml")

    cmd = mock_run.call_args.args[0]
    assert cmd == ["find", ".", "-type", "f", "-iname", "deployment.yaml"]
    assert result == [
        "apps/api/deployment.yaml",
        "apps/frontend/deployment.yaml",
    ]


def test_find_truncates_large_result_sets(tmp_path: Path):
    stdout = "\n".join(f"./apps/service-{i}/deployment.yaml" for i in range(75))
    with patch("tools.file_tools.subprocess.run", return_value=make_completed_process(stdout=stdout, returncode=0)):
        result = find.func(str(tmp_path), "*.yaml")

    assert len(result) == 51
    assert "75 files matched" in result[0]
    assert "showing first 50" in result[0]
    assert result[1] == "apps/service-0/deployment.yaml"
    assert result[-1] == "apps/service-49/deployment.yaml"


def test_find_strips_leading_glob_prefix(tmp_path: Path):
    with patch("tools.file_tools.subprocess.run", return_value=make_completed_process(stdout="", returncode=0)) as mock_run:
        find.func(str(tmp_path), "**/*.yaml")

    cmd = mock_run.call_args.args[0]
    assert cmd == ["find", ".", "-type", "f", "-iname", "*.yaml"]


def test_find_no_matches(tmp_path: Path):
    with patch("tools.file_tools.subprocess.run", return_value=make_completed_process(stdout="", returncode=0)):
        result = find.func(str(tmp_path), "*.nonexistent")
    assert result == []


def test_find_not_installed(tmp_path: Path):
    with patch("tools.file_tools.subprocess.run", side_effect=FileNotFoundError()):
        result = find.func(str(tmp_path), "*.py")
    assert "find" in result and "not installed" in result


def test_find_failure(tmp_path: Path):
    with patch("tools.file_tools.subprocess.run", return_value=make_completed_process(stderr="permission denied", returncode=1)):
        result = find.func(str(tmp_path), "*.py")
    assert "find failed" in result
    assert "permission denied" in result


def test_find_working_directory_not_a_directory(tmp_path: Path):
    missing = tmp_path / "does-not-exist"
    with patch("tools.file_tools.subprocess.run") as mock_run:
        result = find.func(str(missing), "*.py")
    mock_run.assert_not_called()
    assert "is not a directory" in result


# ------------------------------------------------------------------
# edit_file
# ------------------------------------------------------------------

def test_edit_file_success(tmp_path: Path):
    target = tmp_path / "deployment.yaml"
    target.write_text("replicas: 1\nport: 5678\n")

    result = edit_file.func(str(tmp_path), "deployment.yaml", "port: 5678", "port: 6000")

    assert "Successfully edited" in result
    assert target.read_text() == "replicas: 1\nport: 6000\n"


def test_edit_file_replaces_all_occurrences(tmp_path: Path):
    # content.replace() replaces every occurrence, not just the first -- this is
    # intentional (replace_all semantics), not a bug.
    target = tmp_path / "kustomization.yaml"
    target.write_text("demo\ndemo\ndemo\n")

    edit_file.func(str(tmp_path), "kustomization.yaml", "demo", "test2")

    assert target.read_text() == "test2\ntest2\ntest2\n"


def test_edit_file_no_op_when_old_and_new_content_are_identical(tmp_path: Path):
    target = tmp_path / "deployment.yaml"
    target.write_text("replicas: 1\nport: 5678\n")

    result = edit_file.func(str(tmp_path), "deployment.yaml", "port: 5678", "port: 5678")

    assert result == 'No changes: replacing that text in "deployment.yaml" would not change the file, edit skipped'
    assert target.read_text() == "replicas: 1\nport: 5678\n"


def test_edit_file_old_content_not_found(tmp_path: Path):
    target = tmp_path / "a.txt"
    target.write_text("original content")

    result = edit_file.func(str(tmp_path), "a.txt", "nonexistent text", "replacement")

    assert "old_content not found" in result
    assert target.read_text() == "original content"


def test_edit_file_outside_working_directory(tmp_path: Path):
    result = edit_file.func(str(tmp_path), "../../etc/passwd", "old", "new")
    assert "outside the permitted working directory" in result


def test_edit_file_not_found(tmp_path: Path):
    result = edit_file.func(str(tmp_path), "missing.txt", "old", "new")
    assert "File not found" in result


# ------------------------------------------------------------------
# write_file
# ------------------------------------------------------------------

def test_write_file_creates_new_file(tmp_path: Path):
    result = write_file.func(str(tmp_path), "new-manifest.yaml", "kind: ConfigMap\n")

    assert "Successfully wrote" in result
    assert (tmp_path / "new-manifest.yaml").read_text() == "kind: ConfigMap\n"


def test_write_file_creates_missing_parent_directories(tmp_path: Path):
    result = write_file.func(str(tmp_path), "apps/worker/values.yaml", "replicaCount: 3\n")

    assert "Successfully wrote" in result
    assert (tmp_path / "apps/worker/values.yaml").read_text() == "replicaCount: 3\n"


def test_write_file_overwrites_existing_file(tmp_path: Path):
    target = tmp_path / "existing.yaml"
    target.write_text("old content")

    result = write_file.func(str(tmp_path), "existing.yaml", "new content")

    assert "Successfully wrote" in result
    assert target.read_text() == "new content"


def test_write_file_no_op_when_content_already_matches(tmp_path: Path):
    target = tmp_path / "existing.yaml"
    target.write_text("same content")

    result = write_file.func(str(tmp_path), "existing.yaml", "same content")

    assert result == 'No changes: "existing.yaml" already matches this content, write skipped'
    assert target.read_text() == "same content"


def test_write_file_outside_working_directory(tmp_path: Path):
    result = write_file.func(str(tmp_path), "../../etc/passwd", "malicious")
    assert "outside the permitted working directory" in result


def test_write_file_target_is_a_directory(tmp_path: Path):
    (tmp_path / "adir").mkdir()
    result = write_file.func(str(tmp_path), "adir", "content")
    assert "is a directory, not a file" in result

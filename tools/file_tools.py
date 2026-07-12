from langchain_core.tools import tool
from typing import Annotated
import json
import os
import subprocess

MAX_CHARS = 10_000

@tool
def list_files_in_directory(
    working_directory: Annotated[str, "The absolute path to the base directory."],
    directory: Annotated[str, "The path to list, relative to working_directory."],
) -> str:
    """List the files or nested directories within a directory and their metadata."""
    abs_working_dir = os.path.abspath(working_directory)
    target_dir = os.path.abspath(os.path.join(working_directory, directory))
    if not target_dir.startswith(abs_working_dir):
        return f'Error: Cannot list "{directory}" as it is outside the permitted working directory'
    if not os.path.isdir(target_dir):
        return f'Error: "{directory}" is not a directory'
    try:
        files_info = []
        for filename in os.listdir(target_dir):
            filepath = os.path.join(target_dir, filename)
            file_size = 0
            is_dir = os.path.isdir(filepath)
            file_size = os.path.getsize(filepath)
            files_info.append(
                f"- {filename}: file_size={file_size} bytes, is_dir={is_dir}"
            )
        return "\n".join(files_info)
    except Exception as e:
        return f"Error listing files: {e}"

@tool
def read_file_content(
    working_directory: Annotated[str, "The absolute path to the base directory."],
    file_path: Annotated[str, "The path to the file to read, relative to working_directory."],
) -> str:
    """Read the content of a file."""
    abs_working_dir = os.path.abspath(working_directory)
    abs_file_path = os.path.abspath(os.path.join(working_directory, file_path))
    if not abs_file_path.startswith(abs_working_dir):
        return f'Error: Cannot read "{file_path}" as it is outside the permitted working directory'
    if not os.path.isfile(abs_file_path):
        return f'Error: File not found or is not a regular file: "{file_path}"'
    try:
        with open(abs_file_path, "r") as f:
            content = f.read(MAX_CHARS)
            if os.path.getsize(abs_file_path) > MAX_CHARS:
                content += (
                    f'[...File "{file_path}" truncated at {MAX_CHARS} characters]'
                )
        return content
    except Exception as e:
        return f"Error reading file: {e}"

@tool
def grep(
    working_directory: Annotated[str, "The absolute path to the base directory."],
    pattern: Annotated[str, "The string to search for. Treated as a regex if is_regex is True, otherwise matched literally."],
    file_glob: Annotated[str | None, 'Glob to restrict which files are searched, e.g. "*.py". Defaults to None (search all files).'] = None,
    is_regex: Annotated[bool, "Whether to treat pattern as a regex."] = False,
) -> list[dict] | str:
    """Search all files under working_directory for a pattern, returning matching lines with file paths and line numbers."""
    abs_working_dir = os.path.abspath(working_directory)
    if not os.path.isdir(abs_working_dir):
        return f'Error: "{working_directory}" is not a directory'

    cmd = ["rg", "--json"]
    if not is_regex:
        cmd.append("--fixed-strings")
    if file_glob:
        cmd.extend(["--glob", file_glob])
    cmd.extend(["--", pattern, "."])

    try:
        result = subprocess.run(cmd, cwd=abs_working_dir, capture_output=True, text=True)
    except FileNotFoundError:
        return 'Error: ripgrep ("rg") is not installed or not found on PATH'
    except Exception as e:
        return f"Error running ripgrep: {e}"

    if result.returncode not in (0, 1):
        return f"Error: ripgrep failed: {result.stderr.strip()}"

    matches = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        event = json.loads(line)
        if event.get("type") != "match":
            continue
        data = event["data"]
        file_path = data["path"]["text"]
        if file_path.startswith("./"):
            file_path = file_path[2:]
        matches.append(
            {
                "file_path": file_path,
                "line_number": data["line_number"],
                "line_content": data["lines"]["text"].rstrip("\n"),
            }
        )
    return matches

@tool
def find(
    working_directory: Annotated[str, "The absolute path to the base directory."],
    name_pattern: Annotated[str, 'Glob pattern, e.g. "**/*.py" or "test_*.py".'],
) -> list[str] | str:
    """Search for files by filename across the entire repo tree — use this when you know part of a file's name but not its directory."""
    abs_working_dir = os.path.abspath(working_directory)
    if not os.path.isdir(abs_working_dir):
        return f'Error: "{working_directory}" is not a directory'

    pattern = name_pattern[3:] if name_pattern.startswith("**/") else name_pattern
    cmd = ["find", ".", "-type", "f", "-iname", pattern]

    try:
        result = subprocess.run(cmd, cwd=abs_working_dir, capture_output=True, text=True)
    except FileNotFoundError:
        return 'Error: "find" is not installed or not found on PATH'
    except Exception as e:
        return f"Error running find: {e}"

    if result.returncode != 0:
        return f"Error: find failed: {result.stderr.strip()}"

    files = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        files.append(os.path.abspath(os.path.join(working_directory, line)))
    return files

@tool
def edit_file(
    working_directory: Annotated[str, "The absolute path to the base directory."],
    file_path: Annotated[str, "The path to the file to edit, relative to working_directory."],
    old_content: Annotated[str, "The exact existing text to replace. Must be unique in the file."],
    new_content: Annotated[str, "The text to replace it with."],
) -> str:
    """Replace a specific substring/block in a file (targeted patch, not full rewrite)."""
    abs_working_dir = os.path.abspath(working_directory)
    abs_file_path = os.path.abspath(os.path.join(working_directory, file_path))
    if not abs_file_path.startswith(abs_working_dir):
        return f'Error: Cannot read "{file_path}" as it is outside the permitted working directory'
    if not os.path.isfile(abs_file_path):
        return f'Error: File not found or is not a regular file: "{file_path}"'

    try:
        with open(abs_file_path, "r", encoding="utf-8") as file:
            content = file.read()

        if old_content not in content:
            return f'Error: old_content not found in "{file_path}"'

        content = content.replace(old_content, new_content)
        with open(abs_file_path, "w", encoding="utf-8") as file:
            file.write(content)
        return f'Successfully edited "{file_path}"'
    except Exception as e:
        return f"Error editing file: {e}"

@tool
def write_file(
    working_directory: Annotated[str, "The absolute path to the base directory."],
    file_path: Annotated[str, "The path to write to, relative to working_directory."],
    content: Annotated[str, "The full content to write."],
) -> str:
    """Create a new file or overwrite an existing one entirely. Use this to create new manifests."""
    abs_working_dir = os.path.abspath(working_directory)
    abs_file_path = os.path.abspath(os.path.join(abs_working_dir, file_path))
    if not abs_file_path.startswith(abs_working_dir):
        return f'Error: Cannot write "{file_path}" as it is outside the permitted working directory'
    if not os.path.exists(abs_file_path):
        try:
            os.makedirs(os.path.dirname(abs_file_path), exist_ok=True)
        except Exception as e:
            return f"Error: creating directory: {e}"
    if os.path.exists(abs_file_path) and os.path.isdir(abs_file_path):
        return f'Error: "{file_path}" is a directory, not a file'

    try:
        with open(abs_file_path, "w") as f:
            f.write(content)
        return (
            f'Successfully wrote to "{file_path}" ({len(content)} characters written)'
        )
    except Exception as e:
        return f"Error: writing to file: {e}"

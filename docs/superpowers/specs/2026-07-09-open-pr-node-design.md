# Open-PR Node Design

## Context

`agents/coding_agent.py` runs a LangGraph ReAct loop (`reasoning_node` ⇄ `tool_node`) that
edits manifests in the `Kubernaut-Gitops` repo, followed by an `evaluation_node` that
validates YAML syntax and runs an LLM-as-judge (`DiffEvaluator`) against the diff. Today,
when evaluation passes, the graph just ends — the fix sits as an uncommitted local diff in
`Kubernaut-Gitops` with nothing published. This adds a terminal `pr_node` that turns a
passing evaluation into an actual GitHub pull request.

Two pre-existing issues surfaced while scoping this and are fixed as part of this work,
since `pr_node` cannot function without them:

- **Stale file path**: prior work in this session assumed `coding_agent.py` lived at the
  repo root; the canonical file is `agents/coding_agent.py` (per commit "move coding_agent
  into agents directory"). This spec targets that path.
- **Broken `GITOPS_REPO_PATH`**: currently resolves to `Kubernaut/Kubernaut-Gitops`, which
  doesn't exist. The real repo is a sibling directory, `/Users/brymat24/repos/Kubernaut-Gitops`
  (confirmed: has remote `github.com/BryMat24/Kubernaut-Gitops`, default branch `main`, `gh`
  authenticated with `repo` scope). The path needs to be `../../Kubernaut-Gitops` relative to
  `agents/`.

## Goals

- After a passing evaluation, commit the working-tree diff in `Kubernaut-Gitops` to a new
  branch, push it, and open a GitHub PR via the `gh` CLI.
- Keep the node terminal: it runs once, and the graph ends after it regardless of outcome.
- Surface the PR URL (on success) or a clear error (on failure) as the agent's final message.

## Non-goals

- No retry loop or auto-recovery on push/PR failure — this is a one-shot terminal step.
- No CI/merge automation — this only opens the PR, it doesn't wait for or act on review.
- No GitHub API client library (PyGithub, httpx, etc.) — `gh` CLI is already installed and
  authenticated, so shelling out to it is sufficient.

## Architecture / data flow

```
evaluation_node --(eval_passed=True)--> pr_node --> END
                 --(eval_passed=False)--> reasoning_node
```

`_evaluation_routing`'s `{"reasoning_node": ..., "end": END}` mapping becomes
`{"reasoning_node": ..., "pr_node": "pr_node"}`, and a `pr_node` -> `END` edge is added.
`pr_node` reuses `get_changed_files` (already imported from `utils`) for the file list, and
reads a new `eval_reasoning` state field (set by `_evaluation_node` on a passing eval) so it
doesn't need to re-invoke the judge for PR body content.

## State changes (`CodingAgentState`)

- `eval_reasoning: str` — set by `_evaluation_node` when `result.correct` is `True`, holding
  `result.reasoning` from the judge. Consumed only by `pr_node`.
- `pr_url: str` — set by `pr_node` on success.

## Components

### `utils/pr_utils.py` (new file)

```python
def slugify(text: str, max_len: int = 40) -> str:
    """Lowercase, non-alphanumeric runs collapsed to single hyphens, truncated to max_len."""

def open_pull_request(
    repo: str,
    branch: str,
    commit_message: str,
    title: str,
    body: str,
    base: str = "main",
) -> str:
    """
    Runs, in `repo`, via subprocess:
      git checkout -b <branch>
      git add -A
      git commit -m <commit_message>
      git push -u origin <branch>
      gh pr create --base <base> --head <branch> --title <title> --body <body>
    Returns the PR URL (gh pr create prints it to stdout on success).
    Raises on any step's non-zero exit, with the failing command's stderr in the message.
    """
```

One function owns the whole publish sequence (branch → commit → push → `gh pr create`)
since these steps are never independently useful to this agent — this matches the existing
`utils/git_utils.py` style of small, focused subprocess wrappers per unit of work.
`utils/__init__.py` re-exports both names, matching the existing pattern for
`get_diff_content`/`get_changed_files`.

### `agents/coding_agent.py` changes

- `GITOPS_REPO_PATH` fixed to `../../Kubernaut-Gitops` relative to `agents/`.
- `CodingAgentState` gains `eval_reasoning: str` and `pr_url: str`.
- `_evaluation_node`: on the passing-eval path, also returns `"eval_reasoning": result.reasoning`.
- `_evaluation_routing`'s return type/mapping changes from `Literal["reasoning_node", "end"]`
  mapped to `{"reasoning_node": ..., "end": END}`, to `Literal["reasoning_node", "pr_node"]`
  mapped to `{"reasoning_node": ..., "pr_node": "pr_node"}`.
- New `_pr_node`:

```python
def _pr_node(self, state: CodingAgentState) -> dict[str, Any]:
    self.logger.info("\n=== opening PR ===")
    files = get_changed_files(GITOPS_REPO_PATH)
    branch = f"agent/{slugify(state['task'])}-{uuid.uuid4().hex[:6]}"
    try:
        pr_url = open_pull_request(
            GITOPS_REPO_PATH, branch,
            commit_message=f"fix: {state['task']}",
            title=state["task"][:72],
            body=self._build_pr_body(files, state.get("eval_reasoning", "")),
        )
        self.logger.info(f"  opened PR: {pr_url}")
        return {"pr_url": pr_url, "messages": [AIMessage(content=f"Opened PR: {pr_url}")]}
    except Exception as e:
        self.logger.info(f"  failed to open PR: {e}")
        return {"messages": [AIMessage(content=f"Failed to open PR: {e}")]}
```

- `_build_pr_body(files: list[str], reasoning: str) -> str`: small static helper formatting
  a bullet list of `files` plus a "Judge reasoning" section from `reasoning`.
- Graph wiring in `_build_graph`: `graph.add_node("pr_node", self._pr_node)` and
  `graph.add_edge("pr_node", END)`.

## Error handling

- `open_pull_request` raises (doesn't swallow) on any subprocess step failing — `_pr_node` is
  the single catch point, per the "log and end" decision. No partial-success state is treated
  specially (e.g. branch pushed but `gh pr create` failed still surfaces as a single caught
  error with whatever command failed in the message).
- No changes to `tool_node`/`reasoning_node` error handling — out of scope.

## Testing

- Manual: run `agents/coding_agent.py` end-to-end against a task that requires a real,
  correctable change, confirm a PR appears on `github.com/BryMat24/Kubernaut-Gitops` with the
  expected branch name, title, and body.
- Manual: run `_pr_node` directly (as already done for `_evaluation_node` in this session)
  against a state with `eval_passed=True` and a dirty `Kubernaut-Gitops` working tree, confirm
  the PR URL is logged and returned.

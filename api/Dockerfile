FROM python:3.11-slim

# git + gh are required at runtime: utils/git_utils.py shells out to both for
# the RemediationAgent's clone/worktree/commit/push/PR workflow. ripgrep (rg)
# is required by tools/file_tools.py's grep tool (used by PlannerAgent and
# RemediationAgent).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git ripgrep curl ca-certificates gnupg \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && apt-get purge -y --auto-remove curl gnupg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# api/main.py and everything it imports (graph/, agents/, models/,
# mcp_clients/, tools/, utils/) use repo-root-relative imports — there is no
# installed package (see CLAUDE.md). The build context for this image MUST
# be the repo root, not api/:
#   docker build -f api/Dockerfile -t kubernaut-api .
COPY api/ ./api/
COPY graph/ ./graph/
COPY agents/ ./agents/
COPY models/ ./models/
COPY mcp_clients/ ./mcp_clients/
COPY tools/ ./tools/
COPY utils/ ./utils/

# Required at runtime, not baked into the image:
#   OPENROUTER_API_KEY                          - OpenRouter API key, all LLM calls
#   DATABASE_URL                                 - Postgres connection string
#                                                   (LangGraph checkpoints + chat history)
#   GH_TOKEN                                     - GitHub token; `gh` uses it to
#                                                   authenticate pushes and PR creation
#                                                   against whatever GitOps repo a chat's
#                                                   repo_url points at
#   GIT_AUTHOR_NAME / GIT_AUTHOR_EMAIL           - `git commit` has no user.name/email
#   GIT_COMMITTER_NAME / GIT_COMMITTER_EMAIL       configured in this image; git reads
#                                                   these env vars automatically
#
# This process connects to the k8s/promql MCP tool servers over HTTP as
# separate deployments (see mcp_servers/*/Dockerfile) — they are not bundled
# here, matching the intentional process separation described in CLAUDE.md.
#
# utils/git_utils.py clones/worktrees repos under $TMPDIR/repos (container-
# local scratch space) — fine ephemeral, or mount a volume there to cache
# clones across restarts.

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]

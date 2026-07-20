# Chat UI design

Date: 2026-07-20
Status: approved, pending implementation

## Implementation note

The actual UI build (component styling/layout work in `/ui`) should be done using the
`frontend-design` skill, not ad hoc — invoke it for the frontend implementation tasks in the
plan. Backend changes (the `/api` section below) aren't in that skill's scope and follow the
normal implementation path.

## Problem

Kubernaut has a working FastAPI backend (`/api`) that runs the diagnosis → plan → human-approval →
remediation pipeline and persists chat history (`Chat`/`Message` rows), but no UI. There's no way
to interact with it except curl/Postman. This adds a chat-app frontend in `/ui` (Next.js +
Tailwind CSS) so a human can actually converse with the agent and approve/reject remediations.

## Goal

A minimal, working chat UI: start a new chat (with a title and a GitOps repo URL), send a query,
see the diagnosis/answer, review a proposed remediation plan inline and approve or reject it, and
see the outcome (PR link) — plus a sidebar to switch between past chats.

## Non-goals

- No streaming/live progress (backend doesn't support it yet — separate future work).
- No plan editing (`edited_plan`) — approve/reject only.
- No reconstructing an interactive approval card from chat history after a reload (see the
  explicit known limitation in Data Flow).
- No automated frontend tests in this pass (see Testing).

## Backend changes (in `/api`, required before the frontend can work as designed)

`repo_url` is required by every `/diagnose` call, but the backend's chat model doesn't store it
per-chat today — `/diagnose` currently accepts `repo_url` per-request and creates a `Chat`
implicitly if none is given. Decision: move chat creation into its own endpoint, store `repo_url`
on the `Chat` row, and stop auto-generating chat titles via LLM (title becomes user-supplied at
creation time).

- **New `POST /chats`** — body `{title: string, repo_url: string}`. Creates a `Chat` row, returns
  `{id, title, repo_url, created_at}`.
- **`api/orm/chat.py`**: `Chat` gains a `repo_url: Mapped[str]` column (`String`, `nullable=False`).
- **`api/schemas.py`**: `DiagnoseRequest` drops `repo_url`; `chat_id: UUID` becomes **required**
  (was `UUID | None = None`). Add a new `ChatCreateRequest{title: str, repo_url: str}`.
- **`api/main.py`, `POST /diagnose`**: drop the "create a chat if `chat_id` is `None`" branch —
  look up the `Chat` by the now-required `chat_id` (404 if missing), read `chat.repo_url` and use
  it when invoking the graph instead of `body.repo_url`.
- **`GET /chats`**: response gains `repo_url` per row (so a reloaded/deep-linked `/chat/{id}` page
  can recover it without a new endpoint).
- **Remove `api/service.py`** (`generate_title`) entirely, and its import/call site in
  `api/main.py`. No LLM call needed for titles anymore.
- **Add `CORSMiddleware`** to `api/main.py`, allowing the Next.js dev origin
  (`http://localhost:3000`) — the browser talks to the FastAPI backend directly, no proxy.

## Frontend architecture

- **Next.js (App Router, TypeScript) + Tailwind CSS**, scaffolded fresh in `/ui`.
- **Client Components + plain `fetch`**, no data-fetching library (React Query, SWR, etc.) —
  local `useState`/`useEffect` for chat list, message history, and in-flight request state. The
  4-endpoint (now 5) API surface doesn't justify a caching/mutation library yet; add one later if
  the app's data needs grow past what local state handles cleanly.
- **`NEXT_PUBLIC_API_URL`** env var for the backend base URL (e.g. `http://localhost:9000`), read
  through a single `lib/api.ts` wrapper (base URL + JSON parsing + error handling) rather than
  repeating fetch boilerplate in every component.
- **Routes**:
  - `/` — empty/landing state with the persistent sidebar and a prompt to start a new chat.
  - `/chat/[chatId]` — the active chat view (message history + composer).
- **Layout**: `app/layout.tsx` renders the sidebar once and wraps both routes, so switching chats
  doesn't re-fetch the chat list on every navigation.

## Components

- **`Sidebar`** — fetches `GET /chats` on mount, lists chats by title (most recent first, per the
  backend's existing ordering), highlights the active `chatId` (from the route param), "New chat"
  button.
- **`NewChatDialog`** — form (title + repo_url). Submits `POST /chats`, navigates to `/chat/{id}`.
- **`ChatView`** — for the active `chatId`: loads `GET /chats/{chatId}/messages` on mount to
  populate history, renders the message list, hosts `Composer` at the bottom.
- **`MessageBubble`** — renders one message; role-based styling (user right-aligned, assistant
  left-aligned), matching the backend's plain `{role, content}` shape.
- **`PlanCard`** — a **live-only** message type (see Data Flow's known limitation): plan summary,
  then each step (`file_path`, `description`, `new_content` in a monospace block), with
  **Approve**/**Reject** buttons wired to `POST /approve/{thread_id}`.
- **`Composer`** — text input + send button; disabled with a "thinking…" indicator while a
  `/diagnose` or `/approve` call is in-flight (these can take minutes — no streaming yet).

## Data flow

1. Load `/` → `Sidebar` fetches `GET /chats`.
2. Click "New chat" → `NewChatDialog` (title + repo_url) → `POST /chats` → navigate to
   `/chat/{id}`.
3. Entering a chat → `ChatView` fetches `GET /chats/{id}/messages`, renders history as plain
   `MessageBubble`s.
4. User sends a message → optimistically append a user bubble → `POST /diagnose {query, chat_id}`
   → composer shows "thinking…" while in-flight.
5. Response:
   - `status: "pending_approval"` → append a **live** `PlanCard` holding the returned `plan` +
     `thread_id` in local component state.
   - `status: "complete"` → append an assistant text bubble, reading just
     `result.diagnosis_result.summary` from the response (the backend's `result` field is the raw
     internal orchestrator state; the UI only reads that one field from it, not the whole blob).
6. Clicking Approve/Reject on a `PlanCard` → `POST /approve/{thread_id} {approved}` → buttons
   disable, then the outcome (PR link or rejection notice) is appended as a new assistant bubble.

**Known limitation, explicit by design**: a pending-approval message that's part of *history*
(fetched via `GET /chats/{id}/messages` after a reload) renders as **plain text only** — the
backend persists it as a flattened summary string, not structured plan JSON, so there's no live
Approve/Reject card to reconstruct after a refresh. Only the turn just returned in the current
session is interactive. Fixing this would mean persisting structured plan JSON on the `Message`
row, or re-adding a `GET /status/{thread_id}`-style endpoint — out of scope here.

## Error handling

- A failed `fetch` or non-2xx response (network error, 404, 500) during send/approve renders as a
  distinct, visually-flagged error bubble inline in the chat — not a toast, no extra UI system
  needed for an MVP.
- Navigating to `/chat/{id}` for a `chat_id` that 404s (stale link, typo, deleted) redirects to
  `/` with a brief inline notice.
- `PlanCard`'s Approve/Reject buttons re-enable on failure so the user can retry, rather than
  leaving the card permanently stuck mid-action.

## Testing

No automated frontend tests in this pass — matches this repo's existing pragmatic testing culture
(the backend only unit-tests pure logic like `_task_description`; nothing UI-related exists
anywhere in this repo yet). Verification is manual: run both servers and walk the golden path in
a real browser — new chat → ask a question → see a plan card → approve → see the outcome — plus
the informational-query path (no plan, straight to a text answer) and a couple of error cases
(bad `chat_id`, backend unreachable).

## Summary of files touched

- `api/orm/chat.py` — `Chat.repo_url` column
- `api/schemas.py` — `DiagnoseRequest` updated, new `ChatCreateRequest`
- `api/main.py` — new `POST /chats`, `/diagnose` updated, `GET /chats` updated, `CORSMiddleware`
  added, `api/service.py` import/call removed
- `api/service.py` — deleted
- `ui/` — new Next.js + Tailwind app: `app/layout.tsx`, `app/page.tsx`,
  `app/chat/[chatId]/page.tsx`, `components/Sidebar.tsx`, `components/NewChatDialog.tsx`,
  `components/ChatView.tsx`, `components/MessageBubble.tsx`, `components/PlanCard.tsx`,
  `components/Composer.tsx`, `lib/api.ts`

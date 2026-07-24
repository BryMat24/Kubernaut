// Relative path handled by app/api/proxy/[...path]/route.ts, which runs
// server-side in the ui pod and forwards to the real API_URL at request
// time -- keeps the browser bundle free of any backend URL.
const API_URL = "/api/proxy";

export interface ChatSummary {
  id: string;
  title: string;
  repo_url: string;
  created_at: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  thread_id: string | null;
  created_at: string;
}

export interface RemediationStep {
  step_number: number;
  file_path: string;
  description: string;
  new_content: string;
}

export interface RemediationPlan {
  summary: string;
  steps: RemediationStep[];
  planning_success: boolean;
}

export interface ProgressEvent {
  phase: "diagnosis" | "planner" | "remediation";
  status: "started" | "completed";
  message: string;
}

export interface DiagnoseResponse {
  status: "pending_approval" | "pending_info" | "complete";
  thread_id: string;
  chat_id: string;
  plan?: RemediationPlan;
  question?: string;
  message?: string;
  result?: { diagnosis_result?: { summary: string }; pr_url?: string };
}

export interface AnswerResponse {
  status: "pending_approval" | "pending_info" | "complete";
  thread_id: string;
  plan?: RemediationPlan;
  question?: string;
  message?: string;
  result?: { pr_url?: string; [key: string]: unknown };
}

export interface ApproveResponse {
  status: "pending_approval" | "complete";
  thread_id: string;
  payload?: { diagnosis: unknown; plan: RemediationPlan };
  message?: string;
  result?: { pr_url?: string; [key: string]: unknown };
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });

  if (!response.ok) {
    const body = await response.text();
    throw new ApiError(response.status, body || response.statusText);
  }

  return response.json() as Promise<T>;
}

type StreamEvent<T> =
  | ({ type: "progress" } & ProgressEvent)
  | ({ type: "final" } & T)
  | { type: "error"; message: string };

async function streamRequest<T>(
  path: string,
  body: unknown,
  onProgress: (event: ProgressEvent) => void
): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new ApiError(response.status, text || response.statusText);
  }
  if (!response.body) {
    throw new ApiError(response.status, "No response body");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalPayload: T | null = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let separatorIndex: number;
    while ((separatorIndex = buffer.indexOf("\n\n")) !== -1) {
      const rawEvent = buffer.slice(0, separatorIndex);
      buffer = buffer.slice(separatorIndex + 2);

      const line = rawEvent.startsWith("data: ") ? rawEvent.slice(6) : rawEvent;
      if (!line.trim()) continue;

      const event = JSON.parse(line) as StreamEvent<T>;

      if (event.type === "progress") {
        onProgress({ phase: event.phase, status: event.status, message: event.message });
      } else if (event.type === "error") {
        throw new ApiError(500, event.message);
      } else if (event.type === "final") {
        finalPayload = event;
      }
    }
  }

  if (finalPayload === null) {
    throw new ApiError(500, "Stream ended without a final result");
  }
  return finalPayload;
}

export function listChats(): Promise<ChatSummary[]> {
  return request<ChatSummary[]>("/chats");
}

export function createChat(title: string, repoUrl: string): Promise<ChatSummary> {
  return request<ChatSummary>("/chats", {
    method: "POST",
    body: JSON.stringify({ title, repo_url: repoUrl }),
  });
}

export function getChatMessages(chatId: string): Promise<ChatMessage[]> {
  return request<ChatMessage[]>(`/chats/${chatId}/messages`);
}

export function streamDiagnose(
  query: string,
  chatId: string,
  onProgress: (event: ProgressEvent) => void
): Promise<DiagnoseResponse> {
  return streamRequest<DiagnoseResponse>("/diagnose", { query, chat_id: chatId }, onProgress);
}

export function streamApprove(
  threadId: string,
  approved: boolean,
  onProgress: (event: ProgressEvent) => void
): Promise<ApproveResponse> {
  return streamRequest<ApproveResponse>(`/approve/${threadId}`, { approved }, onProgress);
}

export function streamAnswer(
  threadId: string,
  answer: string,
  onProgress: (event: ProgressEvent) => void
): Promise<AnswerResponse> {
  return streamRequest<AnswerResponse>(`/answer/${threadId}`, { answer }, onProgress);
}

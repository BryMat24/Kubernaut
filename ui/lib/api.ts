const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:9000";

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

export interface DiagnoseResponse {
  status: "pending_approval" | "complete";
  thread_id: string;
  chat_id: string;
  plan?: RemediationPlan;
  result?: { diagnosis_result?: { summary: string }; pr_url?: string };
}

export interface ApproveResponse {
  status: "pending_approval" | "complete";
  thread_id: string;
  payload?: { diagnosis: unknown; plan: RemediationPlan };
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

export function diagnose(query: string, chatId: string): Promise<DiagnoseResponse> {
  return request<DiagnoseResponse>("/diagnose", {
    method: "POST",
    body: JSON.stringify({ query, chat_id: chatId }),
  });
}

export function approve(threadId: string, approved: boolean): Promise<ApproveResponse> {
  return request<ApproveResponse>(`/approve/${threadId}`, {
    method: "POST",
    body: JSON.stringify({ approved }),
  });
}

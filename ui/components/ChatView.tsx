"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ChatMessage,
  ProgressEvent,
  RemediationPlan,
  streamDiagnose,
  getChatMessages,
  ApiError,
} from "@/lib/api";
import MessageBubble from "@/components/MessageBubble";
import Composer from "@/components/Composer";
import PlanCard from "@/components/PlanCard";
import MissingInfoCard, { MissingInfoOutcome } from "@/components/MissingInfoCard";
import ProgressTimeline from "@/components/ProgressTimeline";

export type TimelineItem =
  | { kind: "message"; id: string; message: ChatMessage }
  | { kind: "pending_plan"; id: string; threadId: string; plan: RemediationPlan }
  | { kind: "pending_info"; id: string; threadId: string; question: string };

interface ChatViewProps {
  chatId: string;
}

export default function ChatView({ chatId }: ChatViewProps) {
  const [items, setItems] = useState<TimelineItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [progressEvents, setProgressEvents] = useState<ProgressEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setHistoryLoading(true);
      try {
        const messages = await getChatMessages(chatId);
        if (cancelled) return;
        setItems(messages.map((m) => ({ kind: "message", id: m.id, message: m })));
        setError(null);
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          router.push("/");
          return;
        }
        setError(err instanceof ApiError ? err.message : "Failed to load chat");
      } finally {
        if (!cancelled) setHistoryLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [chatId, router]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [items, progressEvents]);

  async function handleSend(query: string) {
    const localId = `local-${Date.now()}`;
    setItems((prev) => [
      ...prev,
      {
        kind: "message",
        id: localId,
        message: {
          id: localId,
          role: "user",
          content: query,
          thread_id: null,
          created_at: new Date().toISOString(),
        },
      },
    ]);
    setSending(true);
    setProgressEvents([]);
    setError(null);
    try {
      const response = await streamDiagnose(query, chatId, (event) => {
        setProgressEvents((prev) => [...prev, event]);
      });
      if (response.status === "pending_approval" && response.plan) {
        setItems((prev) => [
          ...prev,
          {
            kind: "pending_plan",
            id: response.thread_id,
            threadId: response.thread_id,
            plan: response.plan!,
          },
        ]);
      } else if (response.status === "pending_info" && response.question) {
        setItems((prev) => [
          ...prev,
          {
            kind: "pending_info",
            id: response.thread_id,
            threadId: response.thread_id,
            question: response.question!,
          },
        ]);
      } else {
        const summary = response.message ?? response.result?.diagnosis_result?.summary ?? "(no diagnosis result)";
        setItems((prev) => [
          ...prev,
          {
            kind: "message",
            id: `assistant-${response.thread_id}`,
            message: {
              id: `assistant-${response.thread_id}`,
              role: "assistant",
              content: summary,
              thread_id: response.thread_id,
              created_at: new Date().toISOString(),
            },
          },
        ]);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to send message");
    } finally {
      setSending(false);
      setProgressEvents([]);
    }
  }

  function replaceItem(id: string, outcome: MissingInfoOutcome) {
    setItems((prev) =>
      prev.map((i): TimelineItem => {
        if (i.id !== id) return i;
        if (outcome.kind === "message") {
          return {
            kind: "message",
            id,
            message: {
              id,
              role: "assistant",
              content: outcome.content,
              thread_id: i.kind === "pending_info" || i.kind === "pending_plan" ? i.threadId : null,
              created_at: new Date().toISOString(),
            },
          };
        }
        if (outcome.kind === "pending_plan") {
          return { kind: "pending_plan", id, threadId: (i as { threadId: string }).threadId, plan: outcome.plan };
        }
        return { kind: "pending_info", id, threadId: (i as { threadId: string }).threadId, question: outcome.question };
      })
    );
  }

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <div className="flex items-center gap-2 border-b border-border bg-panel/60 px-4 py-2.5">
        <span
          aria-hidden="true"
          className={`h-1.5 w-1.5 rounded-full ${sending ? "bg-signal animate-pulse" : "bg-muted/40"}`}
        />
        <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-muted">
          link {sending ? "active" : "idle"}
        </span>
        <span className="truncate font-mono text-xs text-muted/50">{chatId}</span>
      </div>

      <div
        className="flex-1 overflow-y-auto px-4 py-5 space-y-5"
        aria-live="polite"
        aria-busy={sending}
      >
        {historyLoading && (
          <p className="font-mono text-xs text-muted animate-pulse">loading history…</p>
        )}

        {!historyLoading && items.length === 0 && !error && (
          <p className="text-sm text-muted">
            No messages yet. Ask a question about your cluster below to get started.
          </p>
        )}

        {items.map((item) =>
          item.kind === "message" ? (
            <MessageBubble key={item.id} message={item.message} />
          ) : item.kind === "pending_plan" ? (
            <PlanCard
              key={item.id}
              threadId={item.threadId}
              plan={item.plan}
              onResolved={(outcome) => replaceItem(item.id, { kind: "message", content: outcome })}
            />
          ) : (
            <MissingInfoCard
              key={`${item.id}-${item.question}`}
              threadId={item.threadId}
              question={item.question}
              onResolved={(outcome) => replaceItem(item.id, outcome)}
            />
          )
        )}

        {sending && <ProgressTimeline events={progressEvents} />}

        {error && (
          <p role="alert" className="text-sm text-critical">
            {error}
          </p>
        )}

        <div ref={bottomRef} />
      </div>

      <Composer onSend={handleSend} disabled={sending || historyLoading} />
    </div>
  );
}

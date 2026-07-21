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
import ProgressTimeline from "@/components/ProgressTimeline";

export type TimelineItem =
  | { kind: "message"; id: string; message: ChatMessage }
  | { kind: "pending_plan"; id: string; threadId: string; plan: RemediationPlan };

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
      } else {
        const summary = response.result?.diagnosis_result?.summary ?? "(no diagnosis result)";
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

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <div className="flex items-center gap-2 border-b border-border bg-panel/60 px-4 py-2.5">
        <span className="font-mono text-xs text-muted">session</span>
        <span className="truncate font-mono text-xs text-muted/70">{chatId}</span>
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
          ) : (
            <PlanCard
              key={item.id}
              threadId={item.threadId}
              plan={item.plan}
              onResolved={(outcome) => {
                setItems((prev) =>
                  prev.map((i) =>
                    i.id === item.id
                      ? {
                          kind: "message",
                          id: item.id,
                          message: {
                            id: item.id,
                            role: "assistant",
                            content: outcome,
                            thread_id: item.threadId,
                            created_at: new Date().toISOString(),
                          },
                        }
                      : i
                  )
                );
              }}
            />
          )
        )}

        {sending && <ProgressTimeline events={progressEvents} />}

        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}

        <div ref={bottomRef} />
      </div>

      <Composer onSend={handleSend} disabled={sending || historyLoading} />
    </div>
  );
}

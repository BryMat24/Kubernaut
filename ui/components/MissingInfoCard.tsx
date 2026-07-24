"use client";

import { useState } from "react";
import { ProgressEvent, RemediationPlan, streamAnswer, ApiError } from "@/lib/api";
import ProgressTimeline from "@/components/ProgressTimeline";

export type MissingInfoOutcome =
  | { kind: "message"; content: string }
  | { kind: "pending_plan"; plan: RemediationPlan }
  | { kind: "pending_info"; question: string };

interface MissingInfoCardProps {
  threadId: string;
  question: string;
  onResolved: (outcome: MissingInfoOutcome) => void;
}

export default function MissingInfoCard({ threadId, question, onResolved }: MissingInfoCardProps) {
  const [answer, setAnswer] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [progressEvents, setProgressEvents] = useState<ProgressEvent[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!answer.trim()) return;
    setSubmitting(true);
    setProgressEvents([]);
    setError(null);
    try {
      const response = await streamAnswer(threadId, answer, (event) => {
        setProgressEvents((prev) => [...prev, event]);
      });
      if (response.status === "pending_info" && response.question) {
        onResolved({ kind: "pending_info", question: response.question });
      } else if (response.status === "pending_approval" && response.plan) {
        onResolved({ kind: "pending_plan", plan: response.plan });
      } else {
        onResolved({ kind: "message", content: response.message ?? "Done." });
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to submit answer");
      setSubmitting(false);
      setProgressEvents([]);
    }
  }

  return (
    <section
      aria-label="Additional information requested"
      aria-busy={submitting}
      className="hud-frame rounded-md border border-caution/30 bg-caution/[0.06] px-4 py-3 text-sm text-foreground"
    >
      <div className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-[0.2em] text-caution">
        <span aria-hidden="true">»</span>
        <span>Input required</span>
      </div>

      <p className="mt-1.5 leading-relaxed text-foreground/90">{question}</p>

      {submitting && (
        <div className="mt-3 rounded-md border border-border bg-background px-3 py-2">
          <ProgressTimeline events={progressEvents} />
        </div>
      )}

      {error && (
        <p role="alert" className="mt-3 text-sm text-critical">
          {error}
        </p>
      )}

      <form onSubmit={handleSubmit} className="mt-3 flex gap-2">
        <input
          type="text"
          value={answer}
          onChange={(e) => setAnswer(e.target.value)}
          disabled={submitting}
          aria-label="Your answer"
          className="flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-signal/50"
          placeholder="Type your answer…"
        />
        <button
          type="submit"
          disabled={submitting || !answer.trim()}
          className="rounded-md bg-nominal px-3 py-2 text-sm font-medium text-nominal-foreground hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40 focus:outline-none focus:ring-2 focus:ring-signal/50"
        >
          {submitting ? "Submitting…" : "Submit"}
        </button>
      </form>
    </section>
  );
}

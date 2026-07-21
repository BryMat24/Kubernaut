"use client";

import { useState } from "react";
import { ProgressEvent, RemediationPlan, streamApprove, ApiError } from "@/lib/api";
import ProgressTimeline from "@/components/ProgressTimeline";

interface PlanCardProps {
  threadId: string;
  plan: RemediationPlan;
  onResolved: (outcome: string) => void;
}

type PendingAction = "approve" | "reject" | null;

export default function PlanCard({ threadId, plan, onResolved }: PlanCardProps) {
  const [pendingAction, setPendingAction] = useState<PendingAction>(null);
  const [progressEvents, setProgressEvents] = useState<ProgressEvent[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function handleDecision(approved: boolean) {
    setPendingAction(approved ? "approve" : "reject");
    setProgressEvents([]);
    setError(null);
    try {
      const response = await streamApprove(threadId, approved, (event) => {
        setProgressEvents((prev) => [...prev, event]);
      });
      if (response.status === "complete") {
        const prUrl = response.result?.pr_url ?? null;
        const fallback = approved
          ? prUrl
            ? `Opened PR: ${prUrl}`
            : "Approved, but no PR was opened."
          : "Remediation was not approved.";
        onResolved(response.message ?? fallback);
      } else {
        onResolved("Another approval is required.");
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to submit decision");
      setPendingAction(null);
      setProgressEvents([]);
    }
  }

  const submitting = pendingAction !== null;

  return (
    <section
      aria-label="Remediation plan pending approval"
      aria-busy={submitting}
      className="hud-frame rounded-md border border-caution/30 bg-caution/[0.06] px-4 py-3 text-sm text-foreground"
    >
      <div className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-[0.2em] text-caution">
        <span aria-hidden="true">»</span>
        <span>Authorization required</span>
      </div>

      <p className="mt-1.5 leading-relaxed text-foreground/90">{plan.summary}</p>

      {plan.steps.length === 0 ? (
        <p className="mt-3 font-mono text-xs text-muted">
          No file changes are included in this plan.
        </p>
      ) : (
        <ol className="mt-3 space-y-2">
          {plan.steps.map((step) => (
            <li
              key={step.step_number}
              className="overflow-hidden rounded-md border border-border bg-background"
            >
              <div className="flex items-center gap-2 border-b border-border bg-panel px-3 py-2">
                <span
                  aria-hidden="true"
                  className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-signal/40 font-mono text-[10px] text-signal"
                >
                  {step.step_number}
                </span>
                <span className="truncate font-mono text-xs text-foreground">{step.file_path}</span>
              </div>
              <p className="px-3 pt-2 text-xs leading-relaxed text-muted">{step.description}</p>
              <CodeBlock content={step.new_content} filePath={step.file_path} />
            </li>
          ))}
        </ol>
      )}

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

      <div className="mt-3 flex gap-2">
        <button
          type="button"
          onClick={() => handleDecision(true)}
          disabled={submitting}
          aria-label="Approve remediation plan"
          className="rounded-md bg-nominal px-3 py-2 text-sm font-medium text-nominal-foreground hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40 focus:outline-none focus:ring-2 focus:ring-signal/50"
        >
          {pendingAction === "approve" ? "Approving…" : "Approve"}
        </button>
        <button
          type="button"
          onClick={() => handleDecision(false)}
          disabled={submitting}
          aria-label="Reject remediation plan"
          className="rounded-md border border-border px-3 py-2 text-sm font-medium text-muted hover:border-critical/40 hover:text-critical disabled:cursor-not-allowed disabled:opacity-40 focus:outline-none focus:ring-2 focus:ring-critical/40"
        >
          {pendingAction === "reject" ? "Rejecting…" : "Reject"}
        </button>
      </div>
    </section>
  );
}

/**
 * Renders a step's proposed file contents as a line-numbered code pane
 * (editor-style, not a bare <pre>) so a human reviewer can actually read
 * the diff they're about to approve without it dominating the timeline.
 * `display: contents` lets each line's number/code pair drop directly into
 * the parent grid, which is what produces the two-column layout without a
 * syntax-highlighting dependency.
 */
function CodeBlock({ content, filePath }: { content: string; filePath: string }) {
  const lines = content.length > 0 ? content.split("\n") : [""];

  return (
    <pre
      aria-label={`Proposed contents of ${filePath}`}
      className="m-3 mt-2 max-h-72 overflow-y-auto rounded-md border border-border bg-background p-0"
    >
      <code className="grid grid-cols-[auto_1fr] gap-x-3 px-3 py-2 font-mono text-[11px] leading-5 text-foreground/90">
        {lines.map((line, i) => (
          <span key={i} className="contents">
            <span aria-hidden="true" className="select-none text-right text-muted/40">
              {i + 1}
            </span>
            <span className="whitespace-pre-wrap break-all">{line.length > 0 ? line : " "}</span>
          </span>
        ))}
      </code>
    </pre>
  );
}

"use client";

import { ProgressEvent } from "@/lib/api";

interface ProgressTimelineProps {
  events: ProgressEvent[];
}

const PHASE_LABELS: Record<ProgressEvent["phase"], string> = {
  diagnosis: "Diagnosis",
  planner: "Planning",
  remediation: "Remediation",
};

interface PhaseRow {
  phase: ProgressEvent["phase"];
  message: string;
  done: boolean;
}

function reduceToPhaseRows(events: ProgressEvent[]): PhaseRow[] {
  const order: ProgressEvent["phase"][] = [];
  const byPhase = new Map<ProgressEvent["phase"], PhaseRow>();

  for (const event of events) {
    if (!byPhase.has(event.phase)) {
      order.push(event.phase);
    }
    byPhase.set(event.phase, {
      phase: event.phase,
      message: event.message,
      done: event.status === "completed",
    });
  }

  return order.map((phase) => byPhase.get(phase)!);
}

export default function ProgressTimeline({ events }: ProgressTimelineProps) {
  const rows = reduceToPhaseRows(events);

  if (rows.length === 0) {
    return <p className="font-mono text-xs text-muted animate-pulse">kubernaut is thinking…</p>;
  }

  return (
    <ol aria-live="polite" className="space-y-1.5 font-mono text-xs">
      {rows.map((row) => (
        <li key={row.phase} className="flex items-start gap-2">
          <span aria-hidden="true" className={row.done ? "text-accent" : "text-accent animate-pulse"}>
            {row.done ? "✓" : "⋯"}
          </span>
          <span className="text-muted">
            <span className="text-foreground/70">{PHASE_LABELS[row.phase]}:</span> {row.message}
          </span>
        </li>
      ))}
    </ol>
  );
}

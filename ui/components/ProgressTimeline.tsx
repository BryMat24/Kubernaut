"use client";

import { useEffect, useState } from "react";
import { ProgressEvent } from "@/lib/api";

interface ProgressTimelineProps {
  events: ProgressEvent[];
}

const PHASE_LABELS: Record<ProgressEvent["phase"], string> = {
  diagnosis: "Diagnostic scan",
  planner: "Trajectory plan",
  remediation: "Course correction",
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

function formatElapsed(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `T+${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

export default function ProgressTimeline({ events }: ProgressTimelineProps) {
  const [startedAt] = useState(() => Date.now());
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const id = setInterval(() => setElapsed(Date.now() - startedAt), 1000);
    return () => clearInterval(id);
  }, [startedAt]);

  const rows = reduceToPhaseRows(events);

  if (rows.length === 0) {
    return <p className="font-mono text-xs text-muted animate-pulse">kubernaut is thinking…</p>;
  }

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between font-mono text-[10px] uppercase tracking-[0.2em] text-muted">
        <span>Telemetry</span>
        <span className="text-caution">{formatElapsed(elapsed)}</span>
      </div>
      <ol aria-live="polite" className="space-y-1">
        {rows.map((row) => (
          <li
            key={row.phase}
            className={`flex items-start gap-2 rounded px-1.5 py-1 font-mono text-xs ${
              row.done ? "" : "telemetry-active"
            }`}
          >
            <span aria-hidden="true" className={row.done ? "text-nominal" : "text-caution"}>
              {row.done ? "[✓]" : "[~]"}
            </span>
            <span className="text-muted">
              <span className="font-display uppercase tracking-wide text-foreground/80">
                {PHASE_LABELS[row.phase]}
              </span>
              {" — "}
              {row.message}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

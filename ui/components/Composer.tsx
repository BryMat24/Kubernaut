"use client";

import { useState } from "react";

interface ComposerProps {
  onSend: (query: string) => Promise<void>;
  disabled: boolean;
}

export default function Composer({ onSend, disabled }: ComposerProps) {
  const [value, setValue] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!value.trim() || disabled) return;
    const query = value;
    setValue("");
    await onSend(query);
  }

  return (
    <form onSubmit={handleSubmit} className="border-t border-border bg-panel px-4 py-3">
      <label htmlFor="composer-input" className="sr-only">
        Message the assistant
      </label>
      <div className="flex items-center gap-2 rounded-md border border-border bg-background px-3 py-2 focus-within:ring-2 focus-within:ring-signal/50">
        <span className="font-display text-sm text-signal" aria-hidden="true">
          ›
        </span>
        <input
          id="composer-input"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          disabled={disabled}
          placeholder={disabled ? "Thinking…" : "Ask about your cluster…"}
          className="flex-1 bg-transparent text-sm text-foreground placeholder:text-muted/60 focus:outline-none disabled:cursor-not-allowed"
        />
        <button
          type="submit"
          disabled={disabled || !value.trim()}
          className="rounded-md bg-nominal px-3 py-1.5 text-xs font-medium text-nominal-foreground hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40 focus:outline-none focus:ring-2 focus:ring-signal/50"
        >
          {disabled ? "Thinking…" : "Send"}
        </button>
      </div>
    </form>
  );
}

"use client";

import { useEffect, useState } from "react";
import { createChat, ChatSummary, ApiError } from "@/lib/api";

interface NewChatDialogProps {
  onClose: () => void;
  onCreated: (chat: ChatSummary) => void;
}

export default function NewChatDialog({ onClose, onCreated }: NewChatDialogProps) {
  const [title, setTitle] = useState("");
  const [repoUrl, setRepoUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const chat = await createChat(title.trim(), repoUrl.trim());
      onCreated(chat);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create chat");
      setSubmitting(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <form
        onSubmit={handleSubmit}
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-sm rounded-lg border border-border bg-panel p-6 shadow-2xl shadow-black/40 space-y-5"
      >
        <div>
          <h2 className="text-base font-semibold text-foreground">New chat</h2>
          <p className="mt-1 text-xs text-muted">Start an investigation against a GitOps repo.</p>
        </div>

        <div className="space-y-1.5">
          <label htmlFor="chat-title" className="block text-xs font-medium text-muted">
            Title
          </label>
          <input
            id="chat-title"
            required
            autoFocus
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted/60 focus:outline-none focus:ring-2 focus:ring-accent/50"
            placeholder="e.g. backend memory issue"
          />
        </div>

        <div className="space-y-1.5">
          <label htmlFor="chat-repo-url" className="block text-xs font-medium text-muted">
            GitOps repo URL
          </label>
          <input
            id="chat-repo-url"
            required
            value={repoUrl}
            onChange={(e) => setRepoUrl(e.target.value)}
            className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm font-mono text-foreground placeholder:text-muted/60 focus:outline-none focus:ring-2 focus:ring-accent/50"
            placeholder="https://github.com/org/repo.git"
          />
        </div>

        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}

        <div className="flex justify-end gap-2 pt-1">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md px-3 py-2 text-sm text-muted hover:text-foreground hover:bg-white/[0.04] focus:outline-none focus:ring-2 focus:ring-accent/50"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={submitting}
            className="rounded-md bg-accent px-3 py-2 text-sm font-medium text-accent-foreground hover:brightness-110 disabled:opacity-50 disabled:cursor-not-allowed focus:outline-none focus:ring-2 focus:ring-accent/50"
          >
            {submitting ? "Creating…" : "Create"}
          </button>
        </div>
      </form>
    </div>
  );
}

"use client";

import { useEffect, useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import { listChats, ChatSummary, ApiError } from "@/lib/api";
import NewChatDialog from "@/components/NewChatDialog";

export default function Sidebar() {
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    let cancelled = false;

    async function refresh() {
      try {
        const result = await listChats();
        if (cancelled) return;
        setChats(result);
        setError(null);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "Failed to load chats");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    refresh();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <aside className="w-72 shrink-0 border-r border-border bg-panel flex flex-col">
      <div className="flex items-center gap-2 px-4 py-4 border-b border-border">
        <span className="font-display text-sm text-signal" aria-hidden="true">
          ›_
        </span>
        <div className="flex flex-col leading-none">
          <span className="font-display text-sm font-bold tracking-tight text-foreground">
            KUBERNAUT
          </span>
          <span className="mt-1 font-mono text-[10px] uppercase tracking-[0.2em] text-muted">
            Mission log
          </span>
        </div>
      </div>

      <div className="px-3 pt-3 pb-2">
        <button
          onClick={() => setDialogOpen(true)}
          className="w-full flex items-center justify-center gap-1.5 rounded-md bg-nominal px-3 py-2 text-sm font-medium text-nominal-foreground hover:brightness-110 focus:outline-none focus:ring-2 focus:ring-signal/50"
        >
          <span aria-hidden="true">+</span> New chat
        </button>
      </div>

      <nav className="flex-1 overflow-y-auto px-2 pb-3 space-y-0.5" aria-label="Chats">
        {error && <p className="px-2 py-2 text-xs text-critical">{error}</p>}
        {!error && !loading && chats.length === 0 && (
          <p className="px-3 py-6 text-center text-xs leading-relaxed text-muted">
            No missions logged yet.
            <br />
            Start one to begin an investigation.
          </p>
        )}
        {chats.map((chat) => {
          const active = pathname === `/chat/${chat.id}`;
          return (
            <button
              key={chat.id}
              onClick={() => router.push(`/chat/${chat.id}`)}
              aria-current={active ? "page" : undefined}
              className={`group flex w-full flex-col gap-0.5 rounded-md border-l-2 px-3 py-2 text-left transition-colors focus:outline-none focus:ring-2 focus:ring-signal/50 ${
                active
                  ? "border-signal bg-white/[0.04] text-foreground"
                  : "border-transparent text-muted hover:bg-white/[0.03] hover:text-foreground"
              }`}
            >
              <span className="truncate text-sm">{chat.title}</span>
              <span className="truncate font-mono text-[11px] text-muted/70">{chat.repo_url}</span>
            </button>
          );
        })}
      </nav>

      {dialogOpen && (
        <NewChatDialog
          onClose={() => setDialogOpen(false)}
          onCreated={(chat) => {
            setDialogOpen(false);
            setChats((prev) => [chat, ...prev]);
            router.push(`/chat/${chat.id}`);
          }}
        />
      )}
    </aside>
  );
}

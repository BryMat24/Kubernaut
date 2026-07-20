import { ChatMessage } from "@/lib/api";

function formatTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export default function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  const label = isUser ? "you" : "kubernaut";

  return (
    <div>
      <div className="flex items-baseline gap-2 font-mono text-[11px]">
        <span className={isUser ? "text-accent" : "text-muted"} aria-hidden="true">
          {isUser ? ">" : "#"}
        </span>
        <span className={isUser ? "text-foreground" : "text-muted"}>{label}</span>
        {message.created_at && (
          <span className="text-muted/50">{formatTime(message.created_at)}</span>
        )}
      </div>
      <div
        className={`mt-1 whitespace-pre-wrap rounded-md border px-4 py-3 text-sm leading-relaxed text-foreground ${
          isUser ? "border-accent/25 bg-accent/[0.06]" : "border-border bg-panel"
        }`}
      >
        {message.content}
      </div>
    </div>
  );
}

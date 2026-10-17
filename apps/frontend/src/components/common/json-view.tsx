import { cn } from "@/lib/utils"

import { CopyButton } from "./copy-button"

export function JsonView({ value, className }: { value: unknown; className?: string }) {
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2)
  return (
    <div className={cn("relative rounded-md border bg-muted/40", className)}>
      <div className="absolute top-1 right-1">
        <CopyButton value={text} />
      </div>
      <pre className="max-h-96 overflow-auto p-3 pr-9 font-mono text-xs leading-relaxed whitespace-pre-wrap break-all">
        {text}
      </pre>
    </div>
  )
}

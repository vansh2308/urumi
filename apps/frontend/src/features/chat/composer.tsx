import { ArrowUpIcon, SquareIcon } from "lucide-react"
import { useState, type KeyboardEvent } from "react"

import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"

const MAX_CHARS = 8000

export function Composer({
  onSend,
  onCancel,
  running,
  disabled,
  placeholder = "Ask about products, orders, customers…",
}: {
  onSend: (text: string) => void
  onCancel: () => void
  running: boolean
  disabled?: boolean
  placeholder?: string
}) {
  const [text, setText] = useState("")
  const trimmed = text.trim()
  const tooLong = trimmed.length > MAX_CHARS

  const send = () => {
    if (!trimmed || running || disabled || tooLong) return
    onSend(trimmed)
    setText("")
  }
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      send()
    }
  }

  return (
    <div className="rounded-xl border bg-background p-2 shadow-xs focus-within:ring-2 focus-within:ring-ring/30">
      <Textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder={placeholder}
        disabled={disabled}
        rows={2}
        aria-label="Message"
        className="max-h-48 min-h-12 resize-none border-0 bg-transparent shadow-none focus-visible:ring-0 dark:bg-transparent"
      />
      <div className="flex items-center justify-between gap-2 px-1">
        <span className={tooLong ? "text-xs text-destructive" : "text-xs text-muted-foreground"}>
          {tooLong ? `${trimmed.length}/${MAX_CHARS} characters` : "Enter to send · Shift+Enter for a new line"}
        </span>
        {running ? (
          <Button size="icon-sm" variant="outline" onClick={onCancel} aria-label="Stop">
            <SquareIcon className="fill-current" />
          </Button>
        ) : (
          <Button size="icon-sm" onClick={send} disabled={!trimmed || disabled || tooLong} aria-label="Send">
            <ArrowUpIcon />
          </Button>
        )}
      </div>
    </div>
  )
}

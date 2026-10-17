import { CheckIcon, CopyIcon } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"

export function CopyButton({ value, label = "Copy" }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      toast.error("Could not copy to the clipboard.")
    }
  }
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button type="button" variant="ghost" size="icon-xs" onClick={copy} aria-label={label}>
          {copied ? <CheckIcon /> : <CopyIcon />}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{copied ? "Copied" : label}</TooltipContent>
    </Tooltip>
  )
}

/** A monospace identifier with a copy control, shortened for tables. */
export function CopyableId({ value, short = true }: { value: string; short?: boolean }) {
  return (
    <span className="inline-flex items-center gap-1 font-mono text-xs">
      <span title={value}>{short ? value.slice(0, 8) : value}</span>
      <CopyButton value={value} label="Copy id" />
    </span>
  )
}

import { useEffect, useState, type ComponentProps } from "react"

import { Input } from "@/components/ui/input"

/** An input that reports its value after the user pauses typing. */
export function DebouncedInput({
  value,
  onCommit,
  delay = 400,
  ...props
}: { value: string; onCommit: (value: string) => void; delay?: number } & Omit<
  ComponentProps<typeof Input>,
  "value" | "onChange"
>) {
  const [draft, setDraft] = useState(value)
  useEffect(() => setDraft(value), [value])
  useEffect(() => {
    if (draft === value) return
    const t = setTimeout(() => onCommit(draft.trim()), delay)
    return () => clearTimeout(t)
  }, [draft, value, delay, onCommit])
  return <Input {...props} value={draft} onChange={(e) => setDraft(e.target.value)} />
}

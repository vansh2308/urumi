import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

export const RANGES: Record<string, { label: string; days: number | null }> = {
  "1d": { label: "Last 24 hours", days: 1 },
  "7d": { label: "Last 7 days", days: 7 },
  "30d": { label: "Last 30 days", days: 30 },
  "90d": { label: "Last 90 days", days: 90 },
  all: { label: "All time", days: null },
}

/** since= as an ISO date rounded to the hour, so query keys stay stable while browsing. */
export function sinceFor(range: string): string | undefined {
  const days = RANGES[range]?.days
  if (!days) return undefined
  const d = new Date(Date.now() - days * 86_400_000)
  d.setMinutes(0, 0, 0)
  return d.toISOString()
}

export function RangeSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger size="sm" className="w-40" aria-label="Time range">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {Object.entries(RANGES).map(([k, r]) => (
          <SelectItem key={k} value={k}>
            {r.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

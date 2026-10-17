import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts"

import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/components/ui/chart"
import type { SummaryRow } from "@/lib/api/types"

const config = {
  ok: { label: "Succeeded", color: "var(--chart-2)" },
  errors: { label: "Failed or denied", color: "var(--destructive)" },
} satisfies ChartConfig

/** Daily tool calls (summary grouped by day), with empty days filled in. */
export function CallsChart({ rows, days }: { rows: SummaryRow[]; days: number }) {
  const byDay = new Map(rows.map((r) => [r.key, r]))
  const data = Array.from({ length: days }, (_, i) => {
    const d = new Date(Date.now() - (days - 1 - i) * 86_400_000)
    const key = d.toISOString().slice(0, 10)
    const row = byDay.get(key)
    return {
      day: d.toLocaleDateString(undefined, { month: "short", day: "numeric" }),
      ok: row ? row.calls - row.errors : 0,
      errors: row?.errors ?? 0,
    }
  })
  return (
    <ChartContainer config={config} className="aspect-auto h-64 w-full">
      <BarChart data={data} margin={{ left: -16, right: 8 }}>
        <CartesianGrid vertical={false} />
        <XAxis dataKey="day" tickLine={false} axisLine={false} tickMargin={8} minTickGap={16} />
        <YAxis allowDecimals={false} tickLine={false} axisLine={false} width={40} />
        <ChartTooltip content={<ChartTooltipContent />} />
        <ChartLegend content={<ChartLegendContent />} />
        <Bar dataKey="ok" stackId="calls" fill="var(--color-ok)" radius={[0, 0, 4, 4]} />
        <Bar dataKey="errors" stackId="calls" fill="var(--color-errors)" radius={[4, 4, 0, 0]} />
      </BarChart>
    </ChartContainer>
  )
}

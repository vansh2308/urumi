import type { ReactNode } from "react"

import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"

export function StatCard({
  label,
  value,
  hint,
  icon,
  isLoading,
}: {
  label: string
  value: ReactNode
  hint?: ReactNode
  icon?: ReactNode
  isLoading?: boolean
}) {
  return (
    <Card size="sm">
      <CardHeader>
        <CardDescription className="flex items-center justify-between gap-2">
          {label}
          <span className="text-muted-foreground [&_svg]:size-4">{icon}</span>
        </CardDescription>
        <CardTitle className="text-2xl font-semibold tabular-nums">
          {isLoading ? <Skeleton className="h-8 w-20" /> : value}
        </CardTitle>
        {hint && <div className="text-xs text-muted-foreground">{hint}</div>}
      </CardHeader>
    </Card>
  )
}

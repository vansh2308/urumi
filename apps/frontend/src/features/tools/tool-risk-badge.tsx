import { EyeIcon, PencilIcon, TriangleAlertIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"

import type { ToolRisk } from "./schema"

export function ToolRiskBadge({ risk }: { risk: ToolRisk }) {
  if (risk === "read")
    return (
      <Badge variant="outline" className="font-normal">
        <EyeIcon /> Read
      </Badge>
    )
  if (risk === "destructive")
    return (
      <Badge variant="destructive" className="font-normal">
        <TriangleAlertIcon /> Destructive
      </Badge>
    )
  return (
    <Badge variant="secondary" className="font-normal">
      <PencilIcon /> Write
    </Badge>
  )
}

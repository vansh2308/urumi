import type { ToolSet } from "@/lib/api/types"

export function describeAccess(set: ToolSet | null | undefined): string {
  if (!set) return "None"
  if (set.allow_all_tools) return "All tools"
  if (!set.tools.length) return "None"
  return `${set.tools.length} tool${set.tools.length === 1 ? "" : "s"}`
}

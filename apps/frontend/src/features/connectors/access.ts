import type { Connector, TeamConnector, ToolSet } from "@/lib/api/types"

export function describeAccess(set: ToolSet | null | undefined): string {
  if (!set) return "None"
  if (set.allow_all_tools) return "All tools"
  if (!set.tools.length) return "None"
  return `${set.tools.length} tool${set.tools.length === 1 ? "" : "s"}`
}

/** A provisioned store's MCP server: managed through the Stores page, not the registry. */
export function isStoreServer(r: {
  registry: Pick<Connector, "kind"> | null
  team: Pick<TeamConnector, "kind"> | null
}): boolean {
  return r.registry?.kind === "store" || r.team?.kind === "store"
}

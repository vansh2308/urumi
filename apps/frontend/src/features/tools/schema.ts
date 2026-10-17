import type { JsonSchema, McpTool } from "@/lib/api/types"

export interface ParamRow {
  name: string
  type: string
  required: boolean
  description: string
  detail: string
}

function typeOf(s: JsonSchema): string {
  if (s.enum) return "enum"
  if (Array.isArray(s.type)) return s.type.join(" | ")
  if (s.type === "array" && s.items) return `${typeOf(s.items)}[]`
  if (s.type) return s.type
  const any = (s.anyOf ?? s.oneOf) as JsonSchema[] | undefined
  if (any) return any.map(typeOf).join(" | ")
  return "any"
}

/** Top-level parameters of a tool's JSON input schema, for display. */
export function paramsOf(schema: JsonSchema | undefined): ParamRow[] {
  const props = schema?.properties ?? {}
  const required = new Set(schema?.required ?? [])
  return Object.entries(props).map(([name, s]) => {
    const detail: string[] = []
    if (s.enum) detail.push(`one of ${s.enum.map((v) => JSON.stringify(v)).join(", ")}`)
    if (s.default !== undefined) detail.push(`default ${JSON.stringify(s.default)}`)
    for (const k of ["minimum", "maximum", "minLength", "maxLength", "format", "pattern"] as const) {
      if (s[k] !== undefined) detail.push(`${k} ${String(s[k])}`)
    }
    return {
      name,
      type: typeOf(s),
      required: required.has(name),
      description: s.description ?? (s.title as string | undefined) ?? "",
      detail: detail.join(" · "),
    }
  })
}

function sample(s: JsonSchema): unknown {
  if (s.default !== undefined) return s.default
  if (s.enum?.length) return s.enum[0]
  const type = Array.isArray(s.type) ? s.type.find((t) => t !== "null") : s.type
  switch (type) {
    case "string":
      return ""
    case "integer":
    case "number":
      return typeof s.minimum === "number" ? s.minimum : 1
    case "boolean":
      return false
    case "array":
      return []
    case "object":
      return skeleton(s)
    default:
      return null
  }
}

/** A starting JSON body with the required arguments filled with placeholders. */
export function skeleton(schema: JsonSchema | undefined): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const name of schema?.required ?? []) {
    const s = schema?.properties?.[name]
    if (s) out[name] = sample(s)
  }
  return out
}

export type ToolRisk = "read" | "write" | "destructive"

/** From MCP tool annotations, falling back to the tool name. Display hint only. */
export function riskOf(tool: Pick<McpTool, "name" | "annotations">): ToolRisk {
  const a = tool.annotations
  if (a?.destructiveHint) return "destructive"
  if (a?.readOnlyHint) return "read"
  if (a?.readOnlyHint === false) return "write"
  return /^(list|get|search|read|find|count)_/.test(tool.name) ? "read" : "write"
}

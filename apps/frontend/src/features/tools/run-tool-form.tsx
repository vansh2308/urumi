import { useMutation } from "@tanstack/react-query"
import { PlayIcon } from "lucide-react"
import { useState } from "react"

import { ConfirmDialog } from "@/components/common/confirm-dialog"
import { JsonView } from "@/components/common/json-view"
import { ErrorAlert } from "@/components/common/query-state"
import { StatusBadge } from "@/components/common/status-badge"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Field, FieldDescription, FieldError, FieldLabel } from "@/components/ui/field"
import { Spinner } from "@/components/ui/spinner"
import { Textarea } from "@/components/ui/textarea"
import { ToolKeyStatus } from "@/features/tool-key/tool-key-status"
import { useToolKey } from "@/features/tool-key/use-tool-key"
import { gateway } from "@/lib/api/gateway"
import { ApiError } from "@/lib/api/http"
import type { McpTool } from "@/lib/api/types"
import { formatMs } from "@/lib/format"

import { riskOf, skeleton } from "./schema"

/** Invoke a tool through the gateway's REST endpoint with the tab's API key. */
export function RunToolForm({ teamId, slug, tool }: { teamId: string; slug: string; tool: McpTool }) {
  const { toolKey, clear } = useToolKey(teamId)
  const [text, setText] = useState(() => JSON.stringify(skeleton(tool.inputSchema), null, 2))
  const [parseError, setParseError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState<Record<string, unknown> | null>(null)
  const risk = riskOf(tool)

  const run = useMutation({
    mutationFn: async (args: Record<string, unknown>) => {
      const started = performance.now()
      const result = await gateway.connectors.callTool(toolKey!.key, teamId, slug, tool.name, args)
      return { result, ms: performance.now() - started }
    },
    onError: (e) => {
      if (e instanceof ApiError && e.status === 401) clear()
    },
  })

  const parse = (): Record<string, unknown> | null => {
    try {
      const value = JSON.parse(text || "{}")
      if (typeof value !== "object" || value === null || Array.isArray(value)) {
        setParseError("Arguments must be a JSON object.")
        return null
      }
      const missing = (tool.inputSchema?.required ?? []).filter((k) => !(k in value))
      if (missing.length) {
        setParseError(`Missing required: ${missing.join(", ")}`)
        return null
      }
      setParseError(null)
      return value
    } catch (e) {
      setParseError(`Invalid JSON: ${(e as Error).message}`)
      return null
    }
  }

  const submit = () => {
    const args = parse()
    if (!args) return
    if (risk === "read") run.mutate(args)
    else setConfirming(args)
  }

  if (!toolKey) {
    return (
      <Alert>
        <AlertDescription className="flex flex-wrap items-center justify-between gap-2">
          <span>Running a tool needs a platform API key. Connect one to try this tool.</span>
          <ToolKeyStatus teamId={teamId} />
        </AlertDescription>
      </Alert>
    )
  }

  const result = run.data?.result
  return (
    <div className="space-y-3">
      <Field data-invalid={parseError !== null}>
        <FieldLabel htmlFor="tool-args">Arguments (JSON)</FieldLabel>
        <Textarea
          id="tool-args"
          rows={6}
          className="font-mono text-xs"
          value={text}
          onChange={(e) => setText(e.target.value)}
          aria-invalid={parseError !== null}
        />
        {parseError ? (
          <FieldError>{parseError}</FieldError>
        ) : (
          <FieldDescription>Validated against the tool's schema by the server.</FieldDescription>
        )}
      </Field>
      <div className="flex items-center justify-between gap-2">
        <ToolKeyStatus teamId={teamId} />
        <Button onClick={submit} disabled={run.isPending}>
          {run.isPending ? <Spinner /> : <PlayIcon />}
          Run tool
        </Button>
      </div>

      {run.error && <ErrorAlert error={run.error} title="The gateway refused or failed the call" />}
      {result && (
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-sm">
            <StatusBadge
              status={result.isError ? "error" : "success"}
              label={result.isError ? "Tool reported an error" : "Succeeded"}
            />
            <span className="text-muted-foreground">{formatMs(run.data?.ms)}</span>
          </div>
          <JsonView value={result.structuredContent ?? result.content ?? result} />
        </div>
      )}

      <ConfirmDialog
        open={confirming !== null}
        onOpenChange={(o) => !o && setConfirming(null)}
        title={`Run ${tool.name}?`}
        description={
          <div className="space-y-2">
            <p>
              This tool can change data in the connected store
              {risk === "destructive" ? " and may not be reversible" : ""}. It runs with your
              permissions.
            </p>
            <JsonView value={confirming ?? {}} />
          </div>
        }
        confirmLabel="Run tool"
        destructive={risk === "destructive"}
        onConfirm={() => {
          if (confirming) run.mutate(confirming)
          setConfirming(null)
        }}
      />
    </div>
  )
}

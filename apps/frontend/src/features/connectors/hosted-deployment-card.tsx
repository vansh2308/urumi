import { RotateCwIcon, SlidersHorizontalIcon } from "lucide-react"
import { useState, type ReactNode } from "react"
import { toast } from "sonner"

import { CopyButton } from "@/components/common/copy-button"
import { StatusBadge } from "@/components/common/status-badge"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { HostedUpdateDialog } from "@/features/connectors/hosted-update-dialog"
import { useRestartHosted } from "@/hooks/queries/use-connectors"
import { errorMessage } from "@/lib/api/http"
import type { HostedServer } from "@/lib/api/types"
import { formatDateTime } from "@/lib/format"

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-3 gap-2 py-1.5 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="col-span-2 min-w-0 break-words">{children}</dd>
    </div>
  )
}

function Names({ names }: { names: string[] }) {
  if (!names.length) return <span className="text-muted-foreground">None</span>
  return (
    <div className="flex flex-wrap gap-1">
      {names.map((n) => (
        <Badge key={n} variant="outline" className="font-mono">
          {n}
        </Badge>
      ))}
    </div>
  )
}

/** Live deployment state and controls of a hosted MCP server (platform admin). */
export function HostedDeploymentCard({ server }: { server: HostedServer }) {
  const restart = useRestartHosted()
  const [updating, setUpdating] = useState(false)
  const { status } = server

  return (
    <Card>
      <CardHeader>
        <CardTitle>Deployment (hosted)</CardTitle>
        <CardDescription>
          Run by the platform in namespace {server.namespace}. Removing the server deletes these
          cluster objects.
        </CardDescription>
        <CardAction className="flex gap-2">
          <Button size="sm" variant="outline" onClick={() => setUpdating(true)}>
            <SlidersHorizontalIcon /> Update
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={restart.isPending}
            onClick={() =>
              restart.mutate(server.connector.id, {
                onSuccess: () => toast.success("Restarting the pod."),
                onError: (e) => toast.error(errorMessage(e)),
              })
            }
          >
            <RotateCwIcon /> Restart
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="space-y-3">
        {status.reason && (
          <p className="rounded-md border border-destructive/40 p-3 font-mono text-xs break-words">
            {status.reason}
          </p>
        )}
        <dl className="grid gap-x-8 sm:grid-cols-2">
          <Row label="Status">
            <span className="inline-flex items-center gap-2">
              <StatusBadge status={status.phase} />
              <span className="text-xs text-muted-foreground">
                {status.ready_replicas} ready · {status.restarts} restarts
              </span>
            </span>
          </Row>
          <Row label="Image">
            <span className="inline-flex items-center gap-1 font-mono text-xs break-all">
              {server.image}
              <CopyButton value={server.image} label="Copy image" />
            </span>
          </Row>
          <Row label="Workload">
            <span className="font-mono text-xs">deployment/{server.k8s_name}</span>
          </Row>
          <Row label="Port / path">
            <span className="font-mono text-xs">
              {server.port} {server.path}
            </span>
          </Row>
          <Row label="Limits">
            {server.cpu_millicores}m CPU · {server.memory_mib} MiB
          </Row>
          <Row label="Outbound">{server.egress === "internet" ? "Public internet (80/443)" : "None (DNS only)"}</Row>
          <Row label="Variables">
            <Names names={Object.keys(server.env)} />
          </Row>
          <Row label="Secret variables">
            <Names names={server.secret_env_keys} />
          </Row>
          <Row label="Arguments">
            {server.args.length ? (
              <code className="text-xs break-all">{server.args.join(" ")}</code>
            ) : (
              <span className="text-muted-foreground">Image default</span>
            )}
          </Row>
          <Row label="Updated">{formatDateTime(server.updated_at)}</Row>
        </dl>
      </CardContent>
      <HostedUpdateDialog server={server} open={updating} onOpenChange={setUpdating} />
    </Card>
  )
}

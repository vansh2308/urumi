import { ArrowLeftIcon, CheckCircle2Icon, CircleIcon, ExternalLinkIcon, RotateCcwIcon, Trash2Icon, XCircleIcon } from "lucide-react"
import { useState, type ReactNode } from "react"
import { Link, useNavigate, useParams } from "react-router"
import { toast } from "sonner"

import { PageHeader } from "@/components/common/page-header"
import { ErrorAlert } from "@/components/common/query-state"
import { StatusBadge, humanize } from "@/components/common/status-badge"
import { PageBody } from "@/components/layout/app-shell"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { DeleteStoreDialog } from "@/features/stores/delete-store-dialog"
import { STORE_STEPS } from "@/features/stores/steps"
import { StorePhaseBadge } from "@/features/stores/store-status"
import { useMe, useTeamId, useWorkspace } from "@/hooks/queries/use-session"
import { useRetryStore, useStore } from "@/hooks/queries/use-stores"
import { ApiError, errorMessage } from "@/lib/api/http"
import type { StoreDetail, StoreEvent } from "@/lib/api/types"
import { formatDateTime, formatRelative } from "@/lib/format"
import { NotFoundPage } from "@/pages/errors"

function Meta({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-3 gap-2 py-1.5 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="col-span-2 min-w-0 break-words">{children}</dd>
    </div>
  )
}

function ExternalLink({ href }: { href: string }) {
  return (
    <a href={href} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-mono text-xs hover:underline">
      {href}
      <ExternalLinkIcon className="size-3" />
    </a>
  )
}

function StepList({ store }: { store: StoreDetail }) {
  const done = store.step ? STORE_STEPS.findIndex((s) => s.id === store.step) : -1
  const running = store.status === "provisioning" || store.status === "requested"
  return (
    <ol className="space-y-2">
      {STORE_STEPS.map((step, i) => {
        const finished = store.status === "ready" || i <= done
        const current = running && i === done + 1
        const failedHere = store.status === "failed" && i === done + 1
        return (
          <li key={step.id} className="flex items-center gap-2 text-sm">
            {finished ? (
              <CheckCircle2Icon className="size-4 text-emerald-500" />
            ) : current ? (
              <Spinner className="size-4" />
            ) : failedHere ? (
              <XCircleIcon className="size-4 text-destructive" />
            ) : (
              <CircleIcon className="size-4 text-muted-foreground/50" />
            )}
            <span className={finished || current ? "" : "text-muted-foreground"}>{step.label}</span>
          </li>
        )
      })}
    </ol>
  )
}

function describeEvent(e: StoreEvent): string {
  const d = e.details as Record<string, string | number | undefined>
  switch (e.action) {
    case "store.step":
      return `Finished: ${STORE_STEPS.find((s) => s.id === d.step)?.label ?? d.step}`
    case "store.step_failed":
      return `Step ${d.step} failed (attempt ${d.attempt}): ${d.reason}`
    case "store.failed":
      return String(d.reason ?? "Failed")
    case "store.ready":
      return d.seconds ? `Ready after ${d.seconds}s` : "Ready"
    case "store.connector_registered":
      return `MCP server registered as ${d.connector}`
    default:
      return humanize(e.action.replace(/^store\./, ""))
  }
}

export default function StoreDetailPage() {
  const { storeId = "" } = useParams()
  const teamId = useTeamId()
  const { isTeamAdmin } = useWorkspace()
  const me = useMe()
  const navigate = useNavigate()
  const store = useStore(teamId, storeId)
  const retry = useRetryStore(teamId)
  const [deleting, setDeleting] = useState(false)

  if (store.isLoading) {
    return (
      <PageBody>
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-48 w-full" />
      </PageBody>
    )
  }
  if (store.error instanceof ApiError && store.error.status === 404) return <NotFoundPage />
  if (store.error || !store.data) {
    return (
      <PageBody>
        <ErrorAlert error={store.error} onRetry={() => void store.refetch()} />
      </PageBody>
    )
  }

  const s = store.data
  const canManage = isTeamAdmin || s.created_by_user_id === me.data?.user.id
  const ready = s.phase === "Ready"

  return (
    <PageBody>
      <Button variant="ghost" size="sm" className="w-fit" asChild>
        <Link to="/stores">
          <ArrowLeftIcon /> Stores
        </Link>
      </Button>
      <PageHeader
        title={s.name}
        description={<StorePhaseBadge store={s} />}
        actions={
          <>
            {ready && (
              <>
                <Button variant="outline" size="sm" asChild>
                  <a href={s.url} target="_blank" rel="noreferrer">
                    <ExternalLinkIcon /> Storefront
                  </a>
                </Button>
                <Button variant="outline" size="sm" asChild>
                  <a href={s.admin_url} target="_blank" rel="noreferrer">
                    <ExternalLinkIcon /> wp-admin
                  </a>
                </Button>
              </>
            )}
            {canManage && s.phase === "Failed" && (
              <Button
                size="sm"
                disabled={retry.isPending}
                onClick={() =>
                  retry.mutate(s.id, {
                    onSuccess: () => toast.success(`Provisioning ${s.name} again.`),
                    onError: (e) => toast.error(errorMessage(e)),
                  })
                }
              >
                {retry.isPending ? <Spinner /> : <RotateCcwIcon />} Retry
              </Button>
            )}
            {canManage && s.phase !== "Deleting" && (
              <Button variant="destructive" size="sm" onClick={() => setDeleting(true)}>
                <Trash2Icon /> Delete
              </Button>
            )}
          </>
        }
      />

      {s.status_reason && (
        <Alert variant={s.phase === "Failed" ? "destructive" : "default"}>
          <AlertTitle>{s.phase === "Failed" ? "Provisioning failed" : "In progress"}</AlertTitle>
          <AlertDescription>{s.status_reason}</AlertDescription>
        </Alert>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Store</CardTitle>
            <CardDescription>Its own namespace, WordPress + MariaDB, and MCP server.</CardDescription>
          </CardHeader>
          <CardContent>
            <dl>
              <Meta label="Storefront">{ready ? <ExternalLink href={s.url} /> : <span className="font-mono text-xs">{s.url}</span>}</Meta>
              <Meta label="wp-admin">
                {ready ? <ExternalLink href={s.admin_url} /> : <span className="font-mono text-xs">{s.admin_url}</span>}
                <div className="text-xs text-muted-foreground">User admin; the password was shown once when the store was created.</div>
              </Meta>
              <Meta label="Namespace"><span className="font-mono text-xs">{s.namespace}</span></Meta>
              <Meta label="Created">{formatDateTime(s.created_at)} by {s.created_by_name ?? s.created_by_email ?? "—"}</Meta>
              {s.ready_at && <Meta label="Ready">{formatDateTime(s.ready_at)}</Meta>}
              {s.phase === "Provisioning" && <Meta label="Deadline">{formatRelative(s.deadline_at)}</Meta>}
              {s.attempts > 0 && <Meta label="Retries">{s.attempts}</Meta>}
            </dl>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Provisioning</CardTitle>
          </CardHeader>
          <CardContent>
            <StepList store={s} />
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>MCP server</CardTitle>
          <CardDescription>Registered for this team automatically once the store is ready.</CardDescription>
        </CardHeader>
        <CardContent className="text-sm">
          {s.connector_slug ? (
            <p>
              <Link to={`/mcp-servers/${s.connector_slug}`} className="font-mono hover:underline">
                {s.connector_slug}
              </Link>{" "}
              — its tools are named <span className="font-mono">{s.connector_slug}__list_orders</span>,{" "}
              <span className="font-mono">{s.connector_slug}__get_order</span>, … in chat and on{" "}
              <span className="font-mono">/v1/mcp</span>.
            </p>
          ) : (
            <p className="text-muted-foreground">Not registered yet.</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Recent events</CardTitle>
        </CardHeader>
        <CardContent>
          {s.events.length === 0 ? (
            <p className="text-sm text-muted-foreground">No events yet.</p>
          ) : (
            <ul className="divide-y">
              {s.events.map((e, i) => (
                <li key={`${e.at}-${i}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2 text-sm">
                  <span className="w-40 shrink-0 text-xs text-muted-foreground" title={formatDateTime(e.at)}>
                    {formatRelative(e.at)}
                  </span>
                  <StatusBadge status={e.outcome} />
                  <span className="min-w-0 flex-1 break-words">{describeEvent(e)}</span>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <DeleteStoreDialog
        store={deleting ? s : null}
        teamId={teamId}
        onOpenChange={setDeleting}
        onDeleted={() => navigate("/stores")}
      />
    </PageBody>
  )
}

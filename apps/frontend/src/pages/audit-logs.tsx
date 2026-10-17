import { useState } from "react"

import { DebouncedInput } from "@/components/common/debounced-input"
import { JsonView } from "@/components/common/json-view"
import { PageHeader } from "@/components/common/page-header"
import { DataTable } from "@/components/data-table/data-table"
import { PageBody } from "@/components/layout/app-shell"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { auditColumns } from "@/features/audit/audit-columns"
import { RangeSelect, sinceFor } from "@/features/audit/range-select"
import { usePlatformAudit, useTeamAudit } from "@/hooks/queries/use-activity"
import { useMemberNames } from "@/hooks/queries/use-member-names"
import { useTeamId, useWorkspace } from "@/hooks/queries/use-session"
import { useMembers } from "@/hooks/queries/use-team"
import { useCursorPager } from "@/hooks/use-cursor-pager"
import { useSearchState } from "@/hooks/use-search-state"
import type { AuditQuery } from "@/lib/api/gateway"
import type { AuditEvent } from "@/lib/api/types"
import { formatDateTime } from "@/lib/format"

const PAGE_SIZE = 50

export default function AuditLogsPage() {
  const teamId = useTeamId()
  const { isTeamAdmin, isPlatformAdmin, membership } = useWorkspace()
  const { get, set } = useSearchState()
  const scope = isPlatformAdmin && get("scope") === "platform" ? "platform" : "team"
  const range = get("range") ?? "7d"
  const action = get("action") ?? ""
  const outcome = get("outcome") ?? "all"
  const actor = get("actor") ?? "all"
  const members = useMembers(teamId, isTeamAdmin)
  const nameOf = useMemberNames(teamId)
  const [details, setDetails] = useState<AuditEvent | null>(null)

  const filters = {
    action: action || undefined,
    outcome: outcome === "all" ? undefined : outcome,
    actor_user_id: actor === "all" ? undefined : actor,
    since: sinceFor(range),
  }
  const pager = useCursorPager(JSON.stringify({ scope, ...filters }))
  const q: AuditQuery = { ...filters, limit: PAGE_SIZE, cursor: pager.cursor }
  const team = useTeamAudit(teamId, q, scope === "team")
  const platform = usePlatformAudit(q, scope === "platform")
  const query = scope === "platform" ? platform : team

  return (
    <PageBody>
      <PageHeader
        title="Audit logs"
        description={
          scope === "platform"
            ? "Platform events outside any team: sign-ins, registrations and the connector registry."
            : isTeamAdmin
              ? `Every recorded action in ${membership?.team_name}.`
              : "Actions you performed in this team."
        }
      />
      {isPlatformAdmin && (
        <Tabs value={scope} onValueChange={(v) => set({ scope: v === "team" ? null : v, actor: null })}>
          <TabsList>
            <TabsTrigger value="team">Team</TabsTrigger>
            <TabsTrigger value="platform">Platform</TabsTrigger>
          </TabsList>
        </Tabs>
      )}
      <DataTable
        columns={auditColumns(nameOf, setDetails)}
        data={query.data?.items}
        isLoading={query.isLoading}
        error={query.error}
        onRetry={() => void query.refetch()}
        getRowId={(e) => e.id}
        searchPlaceholder="Search this page…"
        initialHidden={{ ip: false, request_id: false }}
        emptyTitle="No audit events"
        emptyDescription="Try a wider time range or fewer filters."
        cursor={{
          hasPrevious: pager.hasPrevious,
          hasNext: Boolean(query.data?.next_cursor),
          onPrevious: pager.previous,
          onNext: () => query.data?.next_cursor && pager.next(query.data.next_cursor),
          pageLabel: `Page ${pager.page}`,
        }}
        toolbar={
          <>
            <DebouncedInput
              className="h-7 w-48"
              placeholder="Action, e.g. api_key.create"
              aria-label="Action"
              value={action}
              onCommit={(v) => set({ action: v })}
            />
            <Select value={outcome} onValueChange={(v) => set({ outcome: v === "all" ? null : v })}>
              <SelectTrigger size="sm" className="w-36" aria-label="Outcome">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Any outcome</SelectItem>
                <SelectItem value="success">Success</SelectItem>
                <SelectItem value="denied">Denied</SelectItem>
                <SelectItem value="failure">Failure</SelectItem>
              </SelectContent>
            </Select>
            {isTeamAdmin && scope === "team" && (
              <Select value={actor} onValueChange={(v) => set({ actor: v === "all" ? null : v })}>
                <SelectTrigger size="sm" className="w-44" aria-label="Actor">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Any actor</SelectItem>
                  {members.data?.map((m) => (
                    <SelectItem key={m.user_id} value={m.user_id}>
                      {m.display_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            <RangeSelect value={range} onChange={(v) => set({ range: v === "7d" ? null : v })} />
          </>
        }
      />
      <Dialog open={details !== null} onOpenChange={(o) => !o && setDetails(null)}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle className="font-mono text-base">{details?.action}</DialogTitle>
            <DialogDescription>
              {formatDateTime(details?.created_at)} · {nameOf(details?.actor_user_id ?? null)} · {details?.outcome}
            </DialogDescription>
          </DialogHeader>
          <JsonView value={details ?? {}} />
        </DialogContent>
      </Dialog>
    </PageBody>
  )
}

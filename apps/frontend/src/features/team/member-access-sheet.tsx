import { useState } from "react"
import { toast } from "sonner"

import { ConfirmDialog } from "@/components/common/confirm-dialog"
import { ErrorAlert } from "@/components/common/query-state"
import { describeAccess } from "@/features/connectors/access"
import { ToolPicker } from "@/features/connectors/tool-picker"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import {
  useCatalog,
  useGrants,
  useRevokeGrant,
  useSetGrant,
  useTeamConnectors,
} from "@/hooks/queries/use-connectors"
import { errorMessage } from "@/lib/api/http"
import type { Grant, Member, TeamConnector, ToolSet } from "@/lib/api/types"

function GrantEditor({
  teamId,
  member,
  connector,
  grant,
}: {
  teamId: string
  member: Member
  connector: TeamConnector
  grant: Grant | undefined
}) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState<ToolSet>({ allow_all_tools: false, tools: [] })
  const [confirmRevoke, setConfirmRevoke] = useState(false)
  const catalog = useCatalog(teamId, connector.slug, editing)
  const setGrant = useSetGrant(teamId, member.user_id)
  const revoke = useRevokeGrant(teamId, member.user_id)
  const allowed = (catalog.data ?? []).filter((t) => t.allowed_for_team)

  const start = () => {
    setValue(grant ? { allow_all_tools: grant.allow_all_tools, tools: grant.tools } : { allow_all_tools: false, tools: [] })
    setEditing(true)
  }
  const save = () =>
    setGrant.mutate(
      { slug: connector.slug, body: value },
      {
        onSuccess: () => {
          toast.success(`Updated ${member.display_name}'s access to ${connector.display_name}.`)
          setEditing(false)
        },
        onError: (e) => toast.error(errorMessage(e)),
      }
    )

  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle>{connector.display_name}</CardTitle>
        <CardDescription>
          Team allows {describeAccess(connector.team_policy).toLowerCase()} · granted{" "}
          {grant ? describeAccess({ allow_all_tools: grant.allow_all_tools, tools: grant.tools }).toLowerCase() : "nothing"}
        </CardDescription>
        {!editing && (
          <CardAction className="flex gap-1">
            <Button size="sm" variant="outline" onClick={start}>
              {grant ? "Edit" : "Grant"}
            </Button>
            {grant && (
              <Button size="sm" variant="ghost" onClick={() => setConfirmRevoke(true)}>
                Revoke
              </Button>
            )}
          </CardAction>
        )}
      </CardHeader>
      <CardContent className="space-y-3">
        {!editing && grant && !grant.allow_all_tools && (
          <div className="flex flex-wrap gap-1">
            {grant.tools.map((t) => (
              <Badge key={t} variant="outline" className="font-mono font-normal">
                {t}
              </Badge>
            ))}
          </div>
        )}
        {editing &&
          (catalog.isLoading ? (
            <Skeleton className="h-40 w-full" />
          ) : (
            <>
              {catalog.error != null && <ErrorAlert error={catalog.error} title="Could not list tools" />}
              <ToolPicker
                value={value}
                onChange={setValue}
                tools={catalog.data ? allowed : null}
                allLabel="All tools the team allows"
              />
              <div className="flex justify-end gap-2">
                <Button size="sm" variant="outline" onClick={() => setEditing(false)}>
                  Cancel
                </Button>
                <Button
                  size="sm"
                  onClick={save}
                  disabled={setGrant.isPending || (!value.allow_all_tools && !value.tools.length)}
                >
                  {setGrant.isPending && <Spinner />}
                  Save grant
                </Button>
              </div>
            </>
          ))}
      </CardContent>
      <ConfirmDialog
        open={confirmRevoke}
        onOpenChange={setConfirmRevoke}
        title={`Revoke ${member.display_name}'s access to ${connector.display_name}?`}
        description="Their API keys and agent sessions lose these tools immediately."
        confirmLabel="Revoke access"
        destructive
        pending={revoke.isPending}
        onConfirm={() =>
          revoke.mutate(connector.slug, {
            onSuccess: () => {
              toast.success("Access revoked.")
              setConfirmRevoke(false)
            },
            onError: (e) => toast.error(errorMessage(e)),
          })
        }
      />
    </Card>
  )
}

/** A member's per-connector tool grants (admin). Admins implicitly have the team policy. */
export function MemberAccessSheet({
  teamId,
  member,
  onOpenChange,
}: {
  teamId: string
  member: Member | null
  onOpenChange: (open: boolean) => void
}) {
  const connectors = useTeamConnectors(teamId)
  const grants = useGrants(teamId, member?.user_id ?? null)
  return (
    <Sheet open={member !== null} onOpenChange={onOpenChange}>
      <SheetContent className="w-full gap-0 sm:max-w-lg">
        <SheetHeader className="border-b">
          <SheetTitle>Tool access · {member?.display_name}</SheetTitle>
          <SheetDescription>
            {member?.role === "admin"
              ? "Admins can use every tool the team policy allows; grants only apply to members."
              : "Members can only use tools granted here, within the team policy."}
          </SheetDescription>
        </SheetHeader>
        <ScrollArea className="min-h-0 flex-1">
          <div className="space-y-3 p-4">
            {(connectors.isLoading || grants.isLoading) && <Skeleton className="h-24 w-full" />}
            {connectors.error != null && <ErrorAlert error={connectors.error} />}
            {grants.error != null && <ErrorAlert error={grants.error} />}
            {member &&
              connectors.data?.map((c) => (
                <GrantEditor
                  key={c.slug}
                  teamId={teamId}
                  member={member}
                  connector={c}
                  grant={grants.data?.find((g) => g.connector === c.slug)}
                />
              ))}
            {connectors.data?.length === 0 && (
              <p className="text-sm text-muted-foreground">
                No MCP servers are enabled for the team. Enable one on the MCP servers page first.
              </p>
            )}
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  )
}

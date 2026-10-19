import { useState } from "react"
import { toast } from "sonner"

import { ConfirmDialog } from "@/components/common/confirm-dialog"
import { ErrorAlert } from "@/components/common/query-state"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { useOAuthConfig, useOAuthGrants, useRevokeOAuthGrant } from "@/hooks/queries/use-oauth"
import { errorMessage } from "@/lib/api/http"
import type { OAuthGrant } from "@/lib/api/types"
import { formatDateTime, formatRelative } from "@/lib/format"

/** MCP clients the user connected with OAuth, with one-click disconnect. */
export function ConnectedAppsCard() {
  const config = useOAuthConfig()
  const grants = useOAuthGrants()
  const revoke = useRevokeOAuthGrant()
  const [revoking, setRevoking] = useState<OAuthGrant | null>(null)
  if (config.data && !config.data.enabled) return null

  return (
    <Card>
      <CardHeader>
        <CardTitle>Connected apps</CardTitle>
        <CardDescription>
          MCP clients you signed in with OAuth. Each acts as you in one team with a one-hour key
          that it renews; disconnecting stops it at once.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {grants.isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : grants.error ? (
          <ErrorAlert error={grants.error} title="Could not load connected apps" />
        ) : !grants.data?.length ? (
          <p className="text-sm text-muted-foreground">No connected apps yet.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>App</TableHead>
                <TableHead>Team</TableHead>
                <TableHead>Last renewed</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {grants.data.map((g) => (
                <TableRow key={g.id}>
                  <TableCell>
                    <div className="font-medium">{g.client_name ?? "Unnamed client"}</div>
                    <div className="flex flex-wrap gap-1 pt-1">
                      {g.scopes.map((s) => (
                        <Badge key={s} variant="outline" className="font-mono font-normal">
                          {s}
                        </Badge>
                      ))}
                    </div>
                  </TableCell>
                  <TableCell>{g.team_name}</TableCell>
                  <TableCell title={formatDateTime(g.last_refreshed_at ?? g.created_at)}>
                    {formatRelative(g.last_refreshed_at ?? g.created_at)}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button size="sm" variant="ghost" onClick={() => setRevoking(g)}>
                      Disconnect
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
      <ConfirmDialog
        open={revoking !== null}
        onOpenChange={(o) => !o && setRevoking(null)}
        title={`Disconnect ${revoking?.client_name ?? "this app"}?`}
        description="Its access and refresh tokens stop working immediately. It has to ask for your approval again to reconnect."
        confirmLabel="Disconnect"
        destructive
        pending={revoke.isPending}
        onConfirm={() =>
          revoking &&
          revoke.mutate(revoking.id, {
            onSuccess: () => {
              toast.success(`${revoking.client_name ?? "App"} disconnected.`)
              setRevoking(null)
            },
            onError: (e) => toast.error(errorMessage(e)),
          })
        }
      />
    </Card>
  )
}

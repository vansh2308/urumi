import { PlugZapIcon, TriangleAlertIcon } from "lucide-react"
import { useState } from "react"
import { useSearchParams } from "react-router"

import { ErrorAlert } from "@/components/common/query-state"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { useConsentDecision, useConsentRequest } from "@/hooks/queries/use-oauth"
import { useWorkspace } from "@/hooks/queries/use-session"

const SCOPE_TEXT: Record<string, string> = {
  "tools:read": "See the MCP tools you are allowed to use",
  "tools:call": "Call those tools as you (every call is audited under your name)",
  "usage:read": "Read your own usage history",
}

/**
 * OAuth consent: an MCP client (Claude Code, Cursor, the MCP Inspector, ...) asked to act as the
 * signed-in user. Approving issues it a short-lived key for one team, with the user's own
 * permissions; the browser then returns to the client. The gateway enforces all of this.
 */
export default function OAuthConsentPage() {
  const [params] = useSearchParams()
  const requestId = params.get("request") ?? ""
  const { memberships, teamId: activeTeamId } = useWorkspace()
  const request = useConsentRequest(requestId)
  const decision = useConsentDecision(requestId)
  const [teamId, setTeamId] = useState<string | null>(null)
  const chosenTeam = teamId ?? activeTeamId ?? memberships[0]?.team_id ?? ""

  const decide = (approve: boolean) =>
    decision.mutate(
      { approve, team_id: approve ? chosenTeam : undefined },
      { onSuccess: ({ redirect_to }) => window.location.assign(redirect_to) }
    )

  const name = request.data?.client_name || "An MCP client"
  return (
    <div className="flex min-h-svh items-center justify-center bg-muted/30 p-4">
      <Card className="w-full max-w-md">
        <CardHeader>
          <div className="mb-2 flex size-10 items-center justify-center rounded-md border">
            <PlugZapIcon className="size-5" />
          </div>
          <CardTitle>{request.data ? `${name} wants to connect` : "Connect an app"}</CardTitle>
          <CardDescription>It will use your MCP tools through the Urumi gateway.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {!requestId ? (
            <ErrorAlert error={new Error("This link has no authorization request.")} title="Invalid link" />
          ) : request.isLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="h-4 w-2/3" />
              <Skeleton className="h-9 w-full" />
            </div>
          ) : request.error ? (
            <ErrorAlert error={request.error} title="This request cannot be completed" />
          ) : request.data ? (
            <>
              <div className="space-y-1 text-sm">
                <p className="font-medium">It will be able to:</p>
                <ul className="list-disc space-y-1 pl-5 text-muted-foreground">
                  {request.data.scopes.map((s) => (
                    <li key={s}>{SCOPE_TEXT[s] ?? s}</li>
                  ))}
                </ul>
              </div>
              <Field>
                <FieldLabel htmlFor="oauth-team">Team</FieldLabel>
                <Select value={chosenTeam} onValueChange={setTeamId}>
                  <SelectTrigger id="oauth-team" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {memberships.map((m) => (
                      <SelectItem key={m.team_id} value={m.team_id}>
                        {m.team_name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <FieldDescription>
                  The app gets exactly your access in this team. Revoke it any time under Profile →
                  Connected apps.
                </FieldDescription>
              </Field>
              <Alert>
                <TriangleAlertIcon />
                <AlertDescription>
                  Only approve if you just started this from your MCP client. Afterwards your browser
                  returns to <span className="font-mono">{request.data.redirect_host}</span>. The app's
                  name is chosen by the app itself.
                </AlertDescription>
              </Alert>
              {decision.error && <ErrorAlert error={decision.error} title="Could not complete" />}
            </>
          ) : null}
        </CardContent>
        {request.data && (
          <CardFooter className="justify-end gap-2">
            <Button variant="outline" disabled={decision.isPending} onClick={() => decide(false)}>
              Deny
            </Button>
            <Button disabled={decision.isPending || !chosenTeam} onClick={() => decide(true)}>
              {decision.isPending && <Spinner />}
              Approve
            </Button>
          </CardFooter>
        )}
      </Card>
    </div>
  )
}

import { useMutation } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "sonner"

import { ErrorAlert } from "@/components/common/query-state"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import { Spinner } from "@/components/ui/spinner"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useCreateApiKey } from "@/hooks/queries/use-api-keys"
import { useTeam } from "@/hooks/queries/use-team"
import { useWorkspace } from "@/hooks/queries/use-session"
import { gateway } from "@/lib/api/gateway"
import { errorMessage } from "@/lib/api/http"

import { useToolKey } from "./use-tool-key"

/**
 * The gateway only lets platform API keys (urk_) call tools, and the agent authenticates with
 * the caller's key, so chat and tool runs need one. It is kept in this tab's sessionStorage.
 */
export function ToolKeyDialog({
  open,
  onOpenChange,
  teamId,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  teamId: string
}) {
  const { isTeamAdmin } = useWorkspace()
  const team = useTeam(teamId)
  const { setToolKey } = useToolKey(teamId)
  const create = useCreateApiKey(teamId)
  const [pasted, setPasted] = useState("")
  const canCreate = isTeamAdmin || team.data?.members_can_create_keys

  const verify = useMutation({
    mutationFn: async (raw: string) => {
      const me = await gateway.auth.me(raw)
      if (me.credential !== "api_key" || !me.teams.some((t) => t.team_id === teamId)) {
        throw new Error("That key does not belong to this team.")
      }
      return me
    },
  })

  const createKey = () =>
    create.mutate(
      {
        name: `Dashboard ${new Date().toLocaleString()}`,
        scopes: ["tools:read", "tools:call", "usage:read"],
        expires_in_days: 1,
      },
      {
        onSuccess: (issued) => {
          setToolKey({
            key: issued.key,
            keyId: issued.id,
            prefix: issued.prefix,
            name: issued.name,
            expiresAt: issued.expires_at,
          })
          toast.success("Created a 1-day API key for this tab.")
          onOpenChange(false)
        },
        onError: (e) => toast.error(errorMessage(e)),
      }
    )

  const usePasted = () =>
    verify.mutate(pasted.trim(), {
      onSuccess: (me) => {
        setToolKey({
          key: pasted.trim(),
          keyId: me.api_key_id,
          prefix: pasted.trim().slice(0, 12),
          name: "Pasted key",
          expiresAt: null,
        })
        setPasted("")
        toast.success("API key connected.")
        onOpenChange(false)
      },
    })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Connect an API key</DialogTitle>
          <DialogDescription>
            Tool calls and the agent act with a platform API key, so they get exactly your
            permissions. The key stays in this browser tab only and is never shown again.
          </DialogDescription>
        </DialogHeader>
        <Tabs defaultValue={canCreate ? "create" : "paste"}>
          <TabsList className="w-full">
            <TabsTrigger value="create" disabled={!canCreate}>
              Create one
            </TabsTrigger>
            <TabsTrigger value="paste">Paste existing</TabsTrigger>
          </TabsList>
          <TabsContent value="create" className="space-y-3 pt-2">
            <p className="text-sm text-muted-foreground">
              Issues a key owned by you in this team with the tools:read, tools:call and
              usage:read scopes, expiring in 1 day. You can revoke it on the API keys page.
            </p>
            <Button className="w-full" onClick={createKey} disabled={create.isPending}>
              {create.isPending && <Spinner />}
              Create and connect
            </Button>
          </TabsContent>
          <TabsContent value="paste" className="space-y-3 pt-2">
            {verify.error && <ErrorAlert error={verify.error} title="Key not accepted" />}
            <Field>
              <FieldLabel htmlFor="tool-key">API key</FieldLabel>
              <Input
                id="tool-key"
                type="password"
                autoComplete="off"
                placeholder="urk_..."
                value={pasted}
                onChange={(e) => setPasted(e.target.value)}
              />
              <FieldDescription>Needs the tools:read and tools:call scopes.</FieldDescription>
            </Field>
            <DialogFooter>
              <Button onClick={usePasted} disabled={!pasted.trim().startsWith("urk_") || verify.isPending}>
                {verify.isPending && <Spinner />}
                Verify and connect
              </Button>
            </DialogFooter>
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}

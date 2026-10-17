import { LogOutIcon, PlusIcon } from "lucide-react"
import { useState } from "react"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import { AcceptInvitationForm } from "@/features/team/accept-invitation-form"
import { CreateTeamDialog } from "@/features/team/create-team-dialog"
import { useLogout, useMe } from "@/hooks/queries/use-session"

export function NoTeamPage() {
  const { data } = useMe()
  const logout = useLogout()
  const [creating, setCreating] = useState(false)
  return (
    <div className="flex min-h-svh items-center justify-center p-4">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>Join or create a team</CardTitle>
          <CardDescription>
            Signed in as {data?.user.email}. Everything in Urumi belongs to a team: create one, or
            accept an invitation from a team admin.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          <Button className="w-full" onClick={() => setCreating(true)}>
            <PlusIcon /> Create a team
          </Button>
          <Separator />
          <AcceptInvitationForm />
          <Separator />
          <Button variant="ghost" className="w-full" onClick={() => logout.mutate()}>
            <LogOutIcon /> Sign out
          </Button>
        </CardContent>
      </Card>
      <CreateTeamDialog open={creating} onOpenChange={setCreating} />
    </div>
  )
}

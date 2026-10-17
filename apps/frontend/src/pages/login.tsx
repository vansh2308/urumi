import { zodResolver } from "@hookform/resolvers/zod"
import { ServerIcon } from "lucide-react"
import { useForm } from "react-hook-form"
import { toast } from "sonner"
import { useSearchParams } from "react-router"
import { z } from "zod"

import { ErrorAlert } from "@/components/common/query-state"
import { TextField } from "@/components/common/form-fields"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { FieldGroup } from "@/components/ui/field"
import { Spinner } from "@/components/ui/spinner"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useLogin } from "@/hooks/queries/use-session"
import { gateway } from "@/lib/api/gateway"
import { useMutation } from "@tanstack/react-query"

const loginSchema = z.object({
  email: z.email("Enter a valid email address."),
  password: z.string().min(1, "Enter your password."),
})

const registerSchema = z.object({
  display_name: z.string().trim().min(1, "Enter your name.").max(100),
  email: z.email("Enter a valid email address."),
  password: z.string().min(1, "Choose a password.").max(256),
  invitation_token: z.string().trim().optional(),
})

function LoginForm() {
  const login = useLogin()
  const form = useForm<z.infer<typeof loginSchema>>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: "", password: "" },
  })
  return (
    <form onSubmit={form.handleSubmit((v) => login.mutate(v))} className="space-y-4">
      {login.error && <ErrorAlert error={login.error} title="Sign-in failed" />}
      <FieldGroup>
        <TextField control={form.control} name="email" label="Email" type="email" autoComplete="email" autoFocus />
        <TextField
          control={form.control}
          name="password"
          label="Password"
          type="password"
          autoComplete="current-password"
        />
      </FieldGroup>
      <Button type="submit" className="w-full" disabled={login.isPending}>
        {login.isPending && <Spinner />}
        Sign in
      </Button>
    </form>
  )
}

function RegisterForm({ onRegistered }: { onRegistered: () => void }) {
  const [params] = useSearchParams()
  const login = useLogin()
  const register = useMutation({
    mutationFn: async (v: z.infer<typeof registerSchema>) => {
      await gateway.auth.register({ ...v, invitation_token: v.invitation_token || undefined })
      return v
    },
  })
  const form = useForm<z.infer<typeof registerSchema>>({
    resolver: zodResolver(registerSchema),
    defaultValues: {
      display_name: "",
      email: "",
      password: "",
      invitation_token: params.get("invitation") ?? "",
    },
  })
  const submit = form.handleSubmit((v) =>
    register.mutate(v, {
      onSuccess: (values) => {
        toast.success("Account created.")
        onRegistered()
        login.mutate({ email: values.email, password: values.password })
      },
    })
  )
  return (
    <form onSubmit={submit} className="space-y-4">
      {register.error && <ErrorAlert error={register.error} title="Registration failed" />}
      <FieldGroup>
        <TextField control={form.control} name="display_name" label="Name" autoComplete="name" />
        <TextField control={form.control} name="email" label="Email" type="email" autoComplete="email" />
        <TextField
          control={form.control}
          name="password"
          label="Password"
          type="password"
          autoComplete="new-password"
          description="The gateway enforces its minimum length (12 characters by default)."
        />
        <TextField
          control={form.control}
          name="invitation_token"
          label="Invitation token (optional)"
          placeholder="uri_..."
          autoComplete="off"
          description="Required when self-signup is disabled. Joins the inviting team."
        />
      </FieldGroup>
      <Button type="submit" className="w-full" disabled={register.isPending || login.isPending}>
        {(register.isPending || login.isPending) && <Spinner />}
        Create account
      </Button>
    </form>
  )
}

export function LoginPage() {
  const [params, setParams] = useSearchParams()
  const tab = params.get("tab") === "register" ? "register" : "login"
  const setTab = (value: string) =>
    setParams(
      (p) => {
        const n = new URLSearchParams(p)
        if (value === "register") n.set("tab", "register")
        else n.delete("tab")
        return n
      },
      { replace: true }
    )
  return (
    <div className="flex min-h-svh items-center justify-center bg-muted/30 p-4">
      <div className="w-full max-w-sm space-y-6">
        <div className="flex items-center justify-center gap-2 text-lg font-semibold">
          <ServerIcon className="size-5" /> Urumi
        </div>
        <Card>
          <CardHeader>
            <CardTitle>{tab === "login" ? "Sign in" : "Create an account"}</CardTitle>
            <CardDescription>MCP gateway control plane</CardDescription>
          </CardHeader>
          <CardContent>
            <Tabs value={tab} onValueChange={setTab}>
              <TabsList className="mb-4 w-full">
                <TabsTrigger value="login">Sign in</TabsTrigger>
                <TabsTrigger value="register">Register</TabsTrigger>
              </TabsList>
              <TabsContent value="login">
                <LoginForm />
              </TabsContent>
              <TabsContent value="register">
                <RegisterForm onRegistered={() => setTab("login")} />
              </TabsContent>
            </Tabs>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

import { Outlet, useLocation } from "react-router"

import { Separator } from "@/components/ui/separator"
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar"

import { AppSidebar } from "./app-sidebar"
import { PAGE_TITLES } from "./nav"
import { ServiceStatus } from "./service-status"
import { ThemeToggle } from "./theme-toggle"

export function AppShell() {
  const { pathname } = useLocation()
  const section = `/${pathname.split("/")[1] ?? ""}`
  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset className="min-w-0">
        <header className="sticky top-0 z-10 flex h-12 shrink-0 items-center gap-2 border-b bg-background/95 px-4 backdrop-blur">
          <SidebarTrigger className="-ml-1" />
          <Separator orientation="vertical" className="mr-1 data-[orientation=vertical]:h-4" />
          <span className="text-sm font-medium">{PAGE_TITLES[section] ?? "Urumi"}</span>
          <div className="ml-auto flex items-center gap-1">
            <ServiceStatus />
            <ThemeToggle />
          </div>
        </header>
        <Outlet />
      </SidebarInset>
    </SidebarProvider>
  )
}

/** Standard page body; chat opts out to use the full height. */
export function PageBody({ children }: { children: React.ReactNode }) {
  return <main className="mx-auto w-full max-w-7xl flex-1 space-y-6 p-4 md:p-6">{children}</main>
}

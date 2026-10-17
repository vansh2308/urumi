import { MoreHorizontalIcon, PlusIcon, SearchIcon, TrashIcon } from "lucide-react"
import { useState } from "react"
import { Link } from "react-router"
import { toast } from "sonner"

import { ConfirmDialog } from "@/components/common/confirm-dialog"
import { ErrorAlert } from "@/components/common/query-state"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { useConversations, useDeleteConversation } from "@/hooks/queries/use-agent"
import { errorMessage } from "@/lib/api/http"
import type { Conversation } from "@/lib/api/types"
import type { ToolKey } from "@/lib/auth/store"
import { formatRelative } from "@/lib/format"
import { cn } from "@/lib/utils"

export function ConversationList({
  toolKey,
  clearKey,
  activeId,
  onDeleted,
}: {
  toolKey: ToolKey | null
  clearKey: () => void
  activeId: string | undefined
  onDeleted: (id: string) => void
}) {
  const conversations = useConversations(toolKey, clearKey)
  const remove = useDeleteConversation(toolKey)
  const [search, setSearch] = useState("")
  const [deleting, setDeleting] = useState<Conversation | null>(null)
  const items = (conversations.data?.pages.flat() ?? []).filter((c) =>
    c.title.toLowerCase().includes(search.toLowerCase())
  )

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="space-y-2 p-3">
        <Button asChild variant="outline" className="w-full justify-start" size="sm">
          <Link to="/chat">
            <PlusIcon /> New chat
          </Link>
        </Button>
        <InputGroup>
          <InputGroupAddon>
            <SearchIcon />
          </InputGroupAddon>
          <InputGroupInput
            placeholder="Search chats"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Search conversations"
          />
        </InputGroup>
      </div>
      <ScrollArea className="min-h-0 flex-1">
        <nav className="space-y-0.5 px-2 pb-3" aria-label="Conversations">
          {conversations.isLoading &&
            Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-9 w-full" />)}
          {conversations.error && <ErrorAlert error={conversations.error} title="Could not load chats" />}
          {items.map((c) => (
            <div
              key={c.id}
              className={cn(
                "group flex items-center rounded-md hover:bg-muted/60",
                c.id === activeId && "bg-muted"
              )}
            >
              <Link to={`/chat/${c.id}`} className="min-w-0 flex-1 px-2 py-1.5">
                <div className="truncate text-sm">{c.title || "Untitled"}</div>
                <div className="flex items-center gap-1 text-xs text-muted-foreground">
                  {c.busy && <Spinner className="size-3" />}
                  {formatRelative(c.updated_at)}
                </div>
              </Link>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    className="mr-1 opacity-0 group-hover:opacity-100 focus-visible:opacity-100 data-[state=open]:opacity-100"
                    aria-label={`Actions for ${c.title}`}
                  >
                    <MoreHorizontalIcon />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem variant="destructive" onClick={() => setDeleting(c)}>
                    <TrashIcon /> Delete
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          ))}
          {!conversations.isLoading && !conversations.error && items.length === 0 && (
            <p className="px-2 py-6 text-center text-sm text-muted-foreground">
              {search ? "No matching chats." : "No conversations yet."}
            </p>
          )}
          {conversations.hasNextPage && (
            <Button
              variant="ghost"
              size="sm"
              className="w-full"
              onClick={() => void conversations.fetchNextPage()}
              disabled={conversations.isFetchingNextPage}
            >
              {conversations.isFetchingNextPage && <Spinner />}
              Load more
            </Button>
          )}
        </nav>
      </ScrollArea>
      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(o) => !o && setDeleting(null)}
        title="Delete this conversation?"
        description={`“${deleting?.title}” and its messages, runs and tool call logs are deleted permanently.`}
        confirmLabel="Delete"
        destructive
        pending={remove.isPending}
        onConfirm={() =>
          deleting &&
          remove.mutate(deleting.id, {
            onSuccess: () => {
              toast.success("Conversation deleted.")
              onDeleted(deleting.id)
              setDeleting(null)
            },
            onError: (e) => toast.error(errorMessage(e)),
          })
        }
      />
    </div>
  )
}

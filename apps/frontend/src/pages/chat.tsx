import { KeyRoundIcon, MessageSquareIcon, PanelLeftIcon } from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useNavigate, useParams } from "react-router"

import { ErrorAlert, EmptyState } from "@/components/common/query-state"
import { Button } from "@/components/ui/button"
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { AssistantMessage } from "@/features/chat/assistant-message"
import { Composer } from "@/features/chat/composer"
import { ConversationList } from "@/features/chat/conversation-list"
import { itemsFromHistory, type ChatItem } from "@/features/chat/model"
import { useChatStore } from "@/features/chat/store"
import { ToolsPopover } from "@/features/chat/tools-popover"
import { UserMessage } from "@/features/chat/user-message"
import { ToolKeyDialog } from "@/features/tool-key/tool-key-dialog"
import { ToolKeyStatus } from "@/features/tool-key/tool-key-status"
import { useToolKey } from "@/features/tool-key/use-tool-key"
import { riskOf as toolRisk, type ToolRisk } from "@/features/tools/schema"
import { keyOf, useConversation, useRuns } from "@/hooks/queries/use-agent"
import { useAllTools } from "@/hooks/queries/use-connectors"
import { useTeamId } from "@/hooks/queries/use-session"
import { ApiError } from "@/lib/api/http"

const SUGGESTIONS = [
  "List the 5 most recent products",
  "Show orders that are still processing",
  "What is the status of order #1234?",
]

export default function ChatPage() {
  const { conversationId } = useParams()
  const navigate = useNavigate()
  const teamId = useTeamId()
  const { toolKey, clear, connecting } = useToolKey(teamId)
  const [keyDialog, setKeyDialog] = useState(false)
  const [listOpen, setListOpen] = useState(false)
  const conversation = useConversation(toolKey, conversationId)
  const runs = useRuns(toolKey, conversationId)
  const catalog = useAllTools(teamId)
  const live = useChatStore((s) => s.live)
  const send = useChatStore((s) => s.send)
  const cancel = useChatStore((s) => s.cancel)
  const dismiss = useChatStore((s) => s.dismiss)
  const scrollRef = useRef<HTMLDivElement>(null)
  const stickToBottom = useRef(true)

  // Risk badges come from the gateway's MCP tool annotations.
  const risks = useMemo(() => {
    const map = new Map<string, ToolRisk>()
    for (const t of catalog.tools) map.set(`${t.connector.slug}__${t.name}`, toolRisk(t))
    return map
  }, [catalog.tools])
  const riskOf = useCallback((name: string) => risks.get(name) ?? null, [risks])

  const liveHere =
    live &&
    live.teamId === teamId &&
    (live.conversationId === (conversationId ?? null) || (live.startedFromNew && !conversationId))
      ? live
      : null
  const running = live?.status === "starting" || live?.status === "streaming"

  // A new conversation gets its id from the first event: move the URL there.
  useEffect(() => {
    if (live && live.startedFromNew && live.conversationId && !conversationId && live.teamId === teamId) {
      navigate(`/chat/${live.conversationId}`, { replace: true })
    }
  }, [live, conversationId, navigate, teamId])

  const items: ChatItem[] = useMemo(() => {
    const messages = conversation.data?.messages ?? []
    // While a run is shown live, hide its persisted copy to avoid duplicates.
    const hidden = liveHere?.turn.runId
    return itemsFromHistory(hidden ? messages.filter((m) => m.run_id !== hidden) : messages)
  }, [conversation.data, liveHere?.turn.runId])
  const runsById = useMemo(() => new Map((runs.data ?? []).map((r) => [r.id, r])), [runs.data])

  useEffect(() => {
    const el = scrollRef.current
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight
  }, [items, liveHere])

  const onSend = (text: string) => {
    if (!toolKey) return setKeyDialog(true)
    stickToBottom.current = true
    void send({
      teamId,
      apiKey: toolKey.key,
      keyId: keyOf(toolKey),
      conversationId: conversationId ?? null,
      message: text,
      onUnauthorized: clear,
    })
  }

  const notFound =
    conversation.error instanceof ApiError && conversation.error.status === 404 && !liveHere
  const list = (
    <ConversationList
      toolKey={toolKey}
      clearKey={clear}
      activeId={conversationId}
      onDeleted={(id) => {
        if (id === conversationId) navigate("/chat")
        setListOpen(false)
      }}
    />
  )

  return (
    <div className="flex h-[calc(100svh-3rem)] min-h-0">
      <aside className="hidden w-64 shrink-0 border-r md:block">{list}</aside>
      <Sheet open={listOpen} onOpenChange={setListOpen}>
        <SheetContent side="left" className="w-72 p-0">
          <SheetTitle className="sr-only">Conversations</SheetTitle>
          {list}
        </SheetContent>
      </Sheet>

      <section className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2 border-b px-4 py-2">
          <Button variant="ghost" size="icon-sm" className="md:hidden" onClick={() => setListOpen(true)} aria-label="Conversations">
            <PanelLeftIcon />
          </Button>
          <h1 className="min-w-0 flex-1 truncate text-sm font-medium">
            {conversation.data?.conversation.title ?? (conversationId ? "" : "New chat")}
          </h1>
          <ToolsPopover toolKey={toolKey} />
          <ToolKeyStatus teamId={teamId} />
        </div>

        <div
          ref={scrollRef}
          className="min-h-0 flex-1 overflow-y-auto"
          onScroll={(e) => {
            const el = e.currentTarget
            stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
          }}
        >
          <div className="mx-auto w-full max-w-3xl space-y-6 px-4 py-6">
            {!toolKey && connecting ? (
              <EmptyState
                icon={<KeyRoundIcon />}
                title="Connecting…"
                description="Issuing this login's API key for the agent. Your conversations are kept."
              />
            ) : !toolKey ? (
              <EmptyState
                icon={<KeyRoundIcon />}
                title="Connect an API key to chat"
                description="The agent acts with a platform API key, so it can only use the tools you are allowed to use. The dashboard could not issue this login's key; try again or paste one."
                action={<Button onClick={() => setKeyDialog(true)}>Connect API key</Button>}
              />
            ) : notFound ? (
              <EmptyState title="Conversation not found" description="It may have been deleted, or it belongs to another user." />
            ) : conversation.isLoading ? (
              <div className="space-y-4">
                <Skeleton className="ml-auto h-10 w-1/2" />
                <Skeleton className="h-24 w-3/4" />
              </div>
            ) : conversation.error ? (
              <ErrorAlert error={conversation.error} title="Could not load the conversation" onRetry={() => void conversation.refetch()} />
            ) : (
              <>
                {items.length === 0 && !liveHere && (
                  <div className="space-y-4 py-10 text-center">
                    <MessageSquareIcon className="mx-auto size-8 text-muted-foreground" />
                    <div>
                      <h2 className="text-lg font-medium">What can I help with?</h2>
                      <p className="text-sm text-muted-foreground">
                        The agent calls your permitted MCP tools through the gateway.
                      </p>
                    </div>
                    <div className="flex flex-wrap justify-center gap-2">
                      {SUGGESTIONS.map((s) => (
                        <Button key={s} variant="outline" size="sm" onClick={() => onSend(s)} disabled={running}>
                          {s}
                        </Button>
                      ))}
                    </div>
                  </div>
                )}
                {items.map((item) =>
                  item.kind === "user" ? (
                    <UserMessage key={item.id} text={item.text} />
                  ) : (
                    <AssistantMessage
                      key={item.id}
                      turn={item.turn}
                      run={item.turn.runId ? runsById.get(item.turn.runId) : undefined}
                      riskOf={riskOf}
                    />
                  )
                )}
                {liveHere && (
                  <>
                    <UserMessage text={liveHere.userText} />
                    {liveHere.requestError ? (
                      <ErrorAlert error={liveHere.requestError} title="Message not sent" />
                    ) : (
                      <AssistantMessage
                        turn={liveHere.turn}
                        streaming={liveHere.status === "starting" || liveHere.status === "streaming"}
                        riskOf={riskOf}
                      />
                    )}
                    {liveHere.status === "cancelled" && (
                      <p className="text-center text-xs text-muted-foreground">
                        Stopped. Anything already done by a tool is not undone.{" "}
                        <button className="underline" onClick={dismiss}>
                          Dismiss
                        </button>
                      </p>
                    )}
                  </>
                )}
              </>
            )}
          </div>
        </div>

        <div className="mx-auto w-full max-w-3xl px-4 pb-4">
          <Composer
            onSend={onSend}
            onCancel={cancel}
            running={Boolean(running)}
            disabled={!toolKey || notFound || (conversation.data?.conversation.busy === true && !running)}
          />
          <p className="mt-1.5 text-center text-xs text-muted-foreground">
            Write tools change real store data. The agent only changes data when you ask for a specific change.
          </p>
        </div>
      </section>
      <ToolKeyDialog open={keyDialog} onOpenChange={setKeyDialog} teamId={teamId} />
    </div>
  )
}

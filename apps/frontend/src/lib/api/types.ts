// Response and request shapes of the gateway (/v1) and agent (/v1) REST APIs.
// Mirrors services/gateway/src/gateway/api/schemas.py and services/agent/src/agent/api/schemas.py.

export type TeamRole = "admin" | "member"
export type ApiKeyScope = "tools:read" | "tools:call" | "usage:read"
export const API_KEY_SCOPES: ApiKeyScope[] = ["tools:read", "tools:call", "usage:read"]

// --- auth ---------------------------------------------------------------------------------------

export interface User {
  id: string
  email: string
  display_name: string
  is_platform_admin: boolean
  created_at: string
}

export interface Session {
  access_token: string
  token_type: string
  expires_at: string
  user: User
}

export interface Membership {
  team_id: string
  team_name: string
  team_slug: string
  role: TeamRole
}

export interface Me {
  user: User
  credential: "session" | "api_key"
  api_key_id: string | null
  teams: Membership[]
}

export interface RegisterRequest {
  email: string
  password: string
  display_name: string
  invitation_token?: string
}

// --- teams --------------------------------------------------------------------------------------

export interface Team {
  id: string
  name: string
  slug: string
  members_can_create_keys: boolean
  max_key_ttl_days: number
  rate_limit_per_minute: number | null
  created_at: string
  role: TeamRole
}

export interface UpdateTeamRequest {
  name?: string
  members_can_create_keys?: boolean
  max_key_ttl_days?: number
  rate_limit_per_minute?: number
  reset_rate_limit?: boolean
}

export interface Member {
  user_id: string
  email: string
  display_name: string
  role: TeamRole
  joined_at: string
}

export type InvitationStatus = "pending" | "accepted" | "revoked" | "expired" | string

export interface Invitation {
  id: string
  team_id: string
  email: string
  role: TeamRole
  status: InvitationStatus
  created_at: string
  expires_at: string
  accepted_at: string | null
}

export interface IssuedInvitation extends Invitation {
  token: string
}

// --- API keys -----------------------------------------------------------------------------------

export interface ApiKey {
  id: string
  team_id: string
  owner_user_id: string
  name: string
  prefix: string
  scopes: ApiKeyScope[]
  tool_restrictions: string[] | null
  status: "active" | "revoked" | "expired" | string
  created_at: string
  expires_at: string
  last_used_at: string | null
  revoked_at: string | null
  rotated_from_id: string | null
  /** A dashboard key that ends with the login session. */
  session_bound?: boolean
  /** An OAuth access token of a connected app (short-lived, replaced on refresh). */
  oauth_issued?: boolean
}

// --- oauth ----------------------------------------------------------------------------------------

export interface OAuthConfig {
  enabled: boolean
  issuer?: string | null
  mcp_url?: string | null
}

export interface ConsentRequest {
  client_id: string
  /** Self-declared by the client; not verified. */
  client_name: string | null
  client_uri: string | null
  redirect_host: string
  scopes: string[]
  expires_at: string
}

export interface OAuthGrant {
  id: string
  client_name: string | null
  team_id: string
  team_name: string
  scopes: string[]
  created_at: string
  last_refreshed_at: string | null
  access_key_prefix: string | null
}

export interface IssuedApiKey extends ApiKey {
  key: string
}

export interface CreateApiKeyRequest {
  name: string
  scopes: ApiKeyScope[]
  expires_in_days?: number
  tool_restrictions?: string[]
}

// --- connectors ---------------------------------------------------------------------------------

export interface Connector {
  id: string
  slug: string
  display_name: string
  description: string
  enabled: boolean
  kind?: ConnectorKind
  /** A provisioned store's connector: edit and delete happen through the store. */
  managed?: boolean
  // Platform admins only:
  url?: string
  /** Header name of the stored credential; the value is never returned. */
  credential?: { header_name: string; configured: boolean } | null
  timeout_seconds?: number
  max_concurrency?: number
  created_at?: string
  updated_at?: string
}

export type ConnectorKind = "remote" | "hosted" | "store"

/** A static credential the gateway sends upstream. Write-only. */
export type CredentialInput =
  | { type: "bearer"; value: string }
  | { type: "header"; header_name: string; value: string }

export interface RegisterConnectorRequest {
  slug: string
  display_name: string
  description?: string
  url: string
  timeout_seconds?: number
  max_concurrency?: number
  credential?: CredentialInput
}

export type UpdateConnectorRequest = Partial<Omit<RegisterConnectorRequest, "slug">> & {
  enabled?: boolean
  clear_credential?: boolean
}

// --- hosted MCP servers --------------------------------------------------------------------------

export type HostedPhase = "deploying" | "ready" | "failed" | "stopped" | "missing" | "unknown"
export type HostedEgress = "internet" | "none"

export interface HostedSettings {
  enabled: boolean
  namespace: string
  allowed_images: string[]
  default_cpu_millicores: number
  max_cpu_millicores: number
  default_memory_mib: number
  max_memory_mib: number
  max_servers: number
}

export interface HostedServer {
  connector: Connector
  image: string
  port: number
  path: string
  args: string[]
  env: Record<string, string>
  secret_env_keys: string[]
  cpu_millicores: number
  memory_mib: number
  egress: HostedEgress
  namespace: string
  k8s_name: string
  status: { phase: HostedPhase; reason: string | null; ready_replicas: number; restarts: number }
  created_at: string
  updated_at: string
}

export interface DeployHostedServerRequest {
  slug: string
  display_name: string
  description?: string
  image: string
  port: number
  path?: string
  args?: string[]
  env?: Record<string, string>
  secret_env?: Record<string, string>
  credential?: CredentialInput
  cpu_millicores?: number
  memory_mib?: number
  egress?: HostedEgress
  timeout_seconds?: number
}

export interface UpdateHostedServerRequest {
  image?: string
  args?: string[]
  env?: Record<string, string>
  /** Values to set; null removes a variable. Others are kept. */
  secret_env?: Record<string, string | null>
  cpu_millicores?: number
  memory_mib?: number
  egress?: HostedEgress
}

export interface ConnectorStatus {
  slug: string
  display_name: string
  state: "connected" | "unreachable" | "disabled"
  error_code: string | null
  error_message: string | null
  latency_ms: number | null
  checked_at: string
  last_success_at: string | null
  last_failure: { at: string; error_code: string | null; error_message: string | null; duration_ms: number } | null
}

export interface ToolSummary {
  name: string
  title?: string | null
  description?: string | null
}

export interface Probe {
  server_name: string
  server_version: string
  protocol_version: string
  tools: ToolSummary[]
}

export interface ToolSet {
  allow_all_tools: boolean
  tools: string[]
}

export interface TeamConnector {
  slug: string
  display_name: string
  description: string
  kind?: ConnectorKind
  team_policy: ToolSet | null
  your_access: ToolSet
}

export interface CatalogEntry extends ToolSummary {
  allowed_for_team: boolean
}

export interface Grant {
  connector: string
  allow_all_tools: boolean
  tools: string[]
  updated_at: string
}

/** MCP tool definition as returned by the upstream server (camelCase per the MCP spec). */
export interface McpTool {
  name: string
  title?: string
  description?: string
  inputSchema?: JsonSchema
  outputSchema?: JsonSchema
  annotations?: {
    title?: string
    readOnlyHint?: boolean
    destructiveHint?: boolean
    idempotentHint?: boolean
    openWorldHint?: boolean
  }
}

export interface JsonSchema {
  type?: string | string[]
  properties?: Record<string, JsonSchema>
  required?: string[]
  description?: string
  enum?: unknown[]
  default?: unknown
  items?: JsonSchema
  [key: string]: unknown
}

export interface CallToolResult {
  content?: Array<{ type: string; text?: string; [key: string]: unknown }>
  structuredContent?: Record<string, unknown>
  isError?: boolean
}

// --- audit and usage ----------------------------------------------------------------------------

export type Outcome = "success" | "denied" | "failure"

export interface AuditEvent {
  id: string
  created_at: string
  team_id: string | null
  actor_user_id: string | null
  actor_api_key_id: string | null
  action: string
  target_type: string | null
  target_id: string | null
  outcome: Outcome | string
  request_id: string | null
  ip: string | null
  details: Record<string, unknown>
}

export interface Page<T> {
  items: T[]
  next_cursor: string | null
}

export interface Invocation {
  id: string
  created_at: string
  user_id: string
  api_key_id: string | null
  connector_slug: string
  tool_name: string
  transport: string
  outcome: string
  error_code: string | null
  duration_ms: number
  request_id: string | null
}

export type SummaryGroup = "tool" | "connector" | "user" | "outcome" | "day"

export interface SummaryRow {
  key: string
  calls: number
  errors: number
  denied: number
  avg_duration_ms: number
  p95_duration_ms: number
}

export interface UsageSummary {
  group_by: string
  since: string | null
  until: string | null
  rows: SummaryRow[]
}

export interface Health {
  status: string
  checks?: Record<string, string>
}

// --- agent --------------------------------------------------------------------------------------

export interface AgentMe {
  user_id: string
  email: string
  display_name: string
  team_id: string
  team_name: string
  role: string
  api_key_id: string
}

export interface AgentTool {
  name: string
  connector: string
  tool: string
  description: string
  input_schema: JsonSchema
}

export interface AgentTools {
  tools: AgentTool[]
  unavailable_connectors: string[]
}

export interface Conversation {
  id: string
  title: string
  created_at: string
  updated_at: string
  busy: boolean
}

export interface StoredToolCall {
  id: string
  name: string
  arguments: unknown
}

export interface ConversationMessage {
  id: string
  run_id: string | null
  role: "user" | "assistant" | "tool" | string
  content: string | null
  tool_calls: StoredToolCall[] | null
  tool_call_id: string | null
  tool_name: string | null
  tool_result: Record<string, unknown> | null
  created_at: string
}

export interface ConversationDetail {
  conversation: Conversation
  messages: ConversationMessage[]
}

export interface ToolCallLog {
  step: number
  call_id: string
  function_name: string
  connector_slug: string | null
  tool_name: string | null
  outcome: string
  error_code: string | null
  http_status: number | null
  latency_ms: number
  created_at: string
}

export interface Run {
  id: string
  request_id: string | null
  provider: string
  model: string
  status: string
  error_code: string | null
  tools_available: number
  steps: number
  tool_calls: number
  input_tokens: number
  output_tokens: number
  latency_ms: number | null
  first_token_ms: number | null
  llm_latency_ms: number
  tool_latency_ms: number
  created_at: string
  finished_at: string | null
  tool_call_log: ToolCallLog[]
}

export interface AgentUsageSummary {
  scope: string
  since: string
  runs: number
  runs_by_status: Record<string, number>
  avg_latency_ms: number | null
  p50_latency_ms: number | null
  p95_latency_ms: number | null
  avg_first_token_ms: number | null
  avg_llm_latency_ms: number | null
  avg_steps: number | null
  input_tokens: number
  output_tokens: number
  tool_calls: number
  tool_calls_by_outcome: Record<string, number>
  tools: Array<{
    connector: string | null
    tool: string | null
    calls: number
    failures: number
    avg_latency_ms: number | null
  }>
}

// --- provisioned stores --------------------------------------------------------------------------

export type StoreStatus = "requested" | "provisioning" | "ready" | "failed" | "deleting" | "deleted"
export type StorePhase = "Provisioning" | "Ready" | "Failed" | "Deleting" | "Deleted"

export interface Store {
  id: string
  team_id: string
  name: string
  slug: string
  status: StoreStatus
  phase: StorePhase
  /** The last provisioning step that finished. */
  step: string | null
  /** What it is waiting for while provisioning; why it failed when failed. */
  status_reason: string | null
  url: string
  admin_url: string
  namespace: string
  connector_id: string | null
  connector_slug: string | null
  attempts: number
  created_by_user_id: string | null
  created_by_name: string | null
  created_by_email: string | null
  created_at: string
  ready_at: string | null
  deadline_at: string
}

export interface CreatedStore extends Store {
  admin_username: string
  /** Returned once, by the request that created the store. */
  admin_password: string | null
  created: boolean
}

export interface StoreEvent {
  at: string
  action: string
  outcome: string
  details: Record<string, unknown>
}

export interface StoreDetail extends Store {
  events: StoreEvent[]
}

export interface StoreSettings {
  enabled: boolean
  max_per_user: number
  max_total: number
  provision_timeout_seconds: number
  base_domain: string
}

// Gateway REST API (services/gateway). Management calls use the user's session token (ugs_);
// tool invocation needs a platform API key (urk_) because the gateway only lets keys call tools.

import { GATEWAY_URL, request, type Query } from "./http"
import type {
  ApiKey,
  AuditEvent,
  CallToolResult,
  CatalogEntry,
  Connector,
  ConnectorStatus,
  ConsentRequest,
  CreateApiKeyRequest,
  DeployHostedServerRequest,
  Grant,
  Health,
  HostedServer,
  HostedSettings,
  Invitation,
  Invocation,
  IssuedApiKey,
  IssuedInvitation,
  McpTool,
  OAuthConfig,
  OAuthGrant,
  Me,
  Member,
  Page,
  Probe,
  RegisterConnectorRequest,
  RegisterRequest,
  Session,
  Store,
  StoreDetail,
  StoreSettings,
  CreatedStore,
  SummaryGroup,
  Team,
  TeamConnector,
  TeamRole,
  ToolSet,
  UpdateConnectorRequest,
  UpdateHostedServerRequest,
  UpdateTeamRequest,
  UsageSummary,
  User,
} from "./types"

const get = <T>(token: string | null, path: string, query?: Query) =>
  request<T>(GATEWAY_URL, path, { token, query })
const post = <T>(token: string | null, path: string, body?: unknown) =>
  request<T>(GATEWAY_URL, path, { method: "POST", token, body })
const put = <T>(token: string, path: string, body?: unknown) =>
  request<T>(GATEWAY_URL, path, { method: "PUT", token, body })
const patch = <T>(token: string, path: string, body?: unknown) =>
  request<T>(GATEWAY_URL, path, { method: "PATCH", token, body })
const del = (token: string, path: string) =>
  request<void>(GATEWAY_URL, path, { method: "DELETE", token })

const team = (teamId: string) => `/v1/teams/${teamId}`

export interface AuditQuery {
  actor_user_id?: string
  action?: string
  outcome?: string
  since?: string
  until?: string
  limit?: number
  cursor?: string
}

export interface InvocationQuery {
  user_id?: string
  connector?: string
  tool?: string
  outcome?: string
  since?: string
  until?: string
  limit?: number
  cursor?: string
}

export interface SummaryQuery {
  group_by: SummaryGroup
  user_id?: string
  connector?: string
  since?: string
  until?: string
}

export const gateway = {
  health: {
    live: () => get<Health>(null, "/health/live"),
    ready: () => get<Health>(null, "/health/ready"),
  },

  auth: {
    login: (email: string, password: string) =>
      post<Session>(null, "/v1/auth/login", { email, password }),
    register: (body: RegisterRequest) => post<User>(null, "/v1/auth/register", body),
    logout: (token: string) => post<void>(token, "/v1/auth/logout"),
    me: (token: string) => get<Me>(token, "/v1/me"),
  },

  teams: {
    list: (token: string) => get<Team[]>(token, "/v1/teams"),
    get: (token: string, teamId: string) => get<Team>(token, team(teamId)),
    create: (token: string, name: string) => post<Team>(token, "/v1/teams", { name }),
    update: (token: string, teamId: string, body: UpdateTeamRequest) =>
      patch<Team>(token, team(teamId), body),
    members: (token: string, teamId: string) => get<Member[]>(token, `${team(teamId)}/members`),
    changeRole: (token: string, teamId: string, userId: string, role: TeamRole) =>
      patch<void>(token, `${team(teamId)}/members/${userId}`, { role }),
    removeMember: (token: string, teamId: string, userId: string) =>
      del(token, `${team(teamId)}/members/${userId}`),
  },

  invitations: {
    list: (token: string, teamId: string, includeClosed: boolean) =>
      get<Invitation[]>(token, `${team(teamId)}/invitations`, { include_closed: includeClosed }),
    create: (token: string, teamId: string, email: string, role: TeamRole) =>
      post<IssuedInvitation>(token, `${team(teamId)}/invitations`, { email, role }),
    revoke: (token: string, teamId: string, invitationId: string) =>
      del(token, `${team(teamId)}/invitations/${invitationId}`),
    accept: (token: string, invitationToken: string) =>
      post<Invitation>(token, "/v1/invitations/accept", { token: invitationToken }),
  },

  apiKeys: {
    list: (token: string, teamId: string, includeInactive: boolean, ownerUserId?: string) =>
      get<ApiKey[]>(token, `${team(teamId)}/api-keys`, {
        include_inactive: includeInactive,
        owner_user_id: ownerUserId,
      }),
    get: (token: string, teamId: string, keyId: string) =>
      get<ApiKey>(token, `${team(teamId)}/api-keys/${keyId}`),
    create: (token: string, teamId: string, body: CreateApiKeyRequest) =>
      post<IssuedApiKey>(token, `${team(teamId)}/api-keys`, body),
    /** The dashboard's key for this login session; replaces the session's previous one. */
    session: (token: string, teamId: string) =>
      post<IssuedApiKey>(token, `${team(teamId)}/api-keys/session`),
    revoke: (token: string, teamId: string, keyId: string) =>
      post<ApiKey>(token, `${team(teamId)}/api-keys/${keyId}/revoke`),
    rotate: (token: string, teamId: string, keyId: string, graceHours: number) =>
      post<IssuedApiKey>(token, `${team(teamId)}/api-keys/${keyId}/rotate`, {
        grace_hours: graceHours,
      }),
  },

  oauth: {
    config: () => get<OAuthConfig>(null, "/v1/oauth/config"),
    request: (token: string, id: string) =>
      get<ConsentRequest>(token, `/v1/oauth/requests/${encodeURIComponent(id)}`),
    decide: (token: string, id: string, body: { approve: boolean; team_id?: string }) =>
      post<{ redirect_to: string }>(token, `/v1/oauth/requests/${encodeURIComponent(id)}/decision`, body),
    grants: (token: string) => get<OAuthGrant[]>(token, "/v1/oauth/grants"),
    revokeGrant: (token: string, id: string) => del(token, `/v1/oauth/grants/${id}`),
  },

  hosted: {
    settings: (token: string) => get<HostedSettings>(token, "/v1/hosted-servers/settings"),
    list: (token: string) => get<HostedServer[]>(token, "/v1/hosted-servers"),
    get: (token: string, id: string) => get<HostedServer>(token, `/v1/hosted-servers/${id}`),
    deploy: (token: string, body: DeployHostedServerRequest) =>
      post<HostedServer>(token, "/v1/hosted-servers", body),
    update: (token: string, id: string, body: UpdateHostedServerRequest) =>
      patch<HostedServer>(token, `/v1/hosted-servers/${id}`, body),
    restart: (token: string, id: string) =>
      post<HostedServer>(token, `/v1/hosted-servers/${id}/restart`),
  },

  stores: {
    settings: (token: string) => get<StoreSettings>(token, "/v1/stores/settings"),
    list: (token: string, teamId: string) => get<Store[]>(token, `${team(teamId)}/stores`),
    get: (token: string, teamId: string, id: string) =>
      get<StoreDetail>(token, `${team(teamId)}/stores/${id}`),
    /** The key makes a retried request (double click, flaky network) return the same store. */
    create: (token: string, teamId: string, name: string, idempotencyKey: string) =>
      request<CreatedStore>(GATEWAY_URL, `${team(teamId)}/stores`, {
        method: "POST",
        token,
        body: { name },
        headers: { "Idempotency-Key": idempotencyKey },
      }),
    remove: (token: string, teamId: string, id: string) =>
      request<Store>(GATEWAY_URL, `${team(teamId)}/stores/${id}`, { method: "DELETE", token }),
    retry: (token: string, teamId: string, id: string) =>
      post<Store>(token, `${team(teamId)}/stores/${id}/retry`),
  },

  connectors: {
    // Platform registry
    list: (token: string) => get<Connector[]>(token, "/v1/connectors"),
    get: (token: string, id: string) => get<Connector>(token, `/v1/connectors/${id}`),
    register: (token: string, body: RegisterConnectorRequest) =>
      post<Connector>(token, "/v1/connectors", body),
    update: (token: string, id: string, body: UpdateConnectorRequest) =>
      patch<Connector>(token, `/v1/connectors/${id}`, body),
    remove: (token: string, id: string) => del(token, `/v1/connectors/${id}`),
    registryStatus: (token: string) => get<ConnectorStatus[]>(token, "/v1/connectors/status"),
    teamStatus: (token: string, teamId: string) =>
      get<ConnectorStatus[]>(token, `${team(teamId)}/connectors/status`),
    probe: (token: string, id: string) => post<Probe>(token, `/v1/connectors/${id}/probe`),

    // Team policy, discovery and grants
    team: (token: string, teamId: string) =>
      get<TeamConnector[]>(token, `${team(teamId)}/connectors`),
    setTeamPolicy: (token: string, teamId: string, slug: string, body: ToolSet) =>
      put<TeamConnector>(token, `${team(teamId)}/connectors/${slug}`, body),
    removeTeamPolicy: (token: string, teamId: string, slug: string) =>
      del(token, `${team(teamId)}/connectors/${slug}`),
    catalog: (token: string, teamId: string, slug: string) =>
      get<CatalogEntry[]>(token, `${team(teamId)}/connectors/${slug}/catalog`),
    tools: (token: string, teamId: string, slug: string) =>
      get<McpTool[]>(token, `${team(teamId)}/connectors/${slug}/tools`),
    callTool: (apiKey: string, teamId: string, slug: string, tool: string, args: unknown) =>
      post<CallToolResult>(apiKey, `${team(teamId)}/connectors/${slug}/tools/${tool}/call`, {
        arguments: args,
      }),
  },

  grants: {
    list: (token: string, teamId: string, userId: string) =>
      get<Grant[]>(token, `${team(teamId)}/members/${userId}/grants`),
    set: (token: string, teamId: string, userId: string, slug: string, body: ToolSet) =>
      put<Grant>(token, `${team(teamId)}/members/${userId}/grants/${slug}`, body),
    revoke: (token: string, teamId: string, userId: string, slug: string) =>
      del(token, `${team(teamId)}/members/${userId}/grants/${slug}`),
  },

  activity: {
    teamAudit: (token: string, teamId: string, q: AuditQuery) =>
      get<Page<AuditEvent>>(token, `${team(teamId)}/audit-events`, { ...q }),
    platformAudit: (token: string, q: AuditQuery) =>
      get<Page<AuditEvent>>(token, "/v1/audit-events", { ...q }),
    invocations: (token: string, teamId: string, q: InvocationQuery) =>
      get<Page<Invocation>>(token, `${team(teamId)}/usage/invocations`, { ...q }),
    summary: (token: string, teamId: string, q: SummaryQuery) =>
      get<UsageSummary>(token, `${team(teamId)}/usage/summary`, { ...q }),
  },
}

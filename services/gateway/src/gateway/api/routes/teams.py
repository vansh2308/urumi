"""Teams, team settings, membership management and invitations.

Every route below ``/teams/{team_id}`` resolves the caller's membership first (404 if none);
admin-only operations are enforced in the services, not here.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from gateway.api.deps import (
    API_PREFIX,
    ContainerDep,
    SessionPrincipalDep,
    TeamDep,
    TeamSessionDep,
)
from gateway.api.schemas import (
    AcceptInvitationRequest,
    ChangeRoleRequest,
    CreateInvitationRequest,
    CreateTeamRequest,
    InvitationOut,
    IssuedInvitationOut,
    MemberOut,
    TeamOut,
    TeamWithRoleOut,
    UpdateTeamRequest,
)
from gateway.db.models import Invitation, TeamRole
from gateway.services.invitations import invitation_status
from gateway.services.teams import TeamSettingsChange

router = APIRouter(prefix=API_PREFIX, tags=["teams"])


def _team_with_role(team: object, role: TeamRole) -> TeamWithRoleOut:
    return TeamWithRoleOut.model_validate(
        {**TeamOut.model_validate(team).model_dump(), "role": role}
    )


def _invitation_out(inv: Invitation) -> InvitationOut:
    return InvitationOut(
        id=inv.id,
        team_id=inv.team_id,
        email=inv.email,
        role=TeamRole(inv.role),
        status=invitation_status(inv).value,
        created_at=inv.created_at,
        expires_at=inv.expires_at,
        accepted_at=inv.accepted_at,
    )


# --- teams --------------------------------------------------------------------------------------


@router.post("/teams", status_code=status.HTTP_201_CREATED, response_model=TeamWithRoleOut)
async def create_team(
    body: CreateTeamRequest, principal: SessionPrincipalDep, container: ContainerDep
) -> TeamWithRoleOut:
    """Create a team. The caller becomes its first admin."""
    team = await container.teams.create_team(principal, name=body.name)
    return _team_with_role(team, TeamRole.ADMIN)


@router.get("/teams", response_model=list[TeamWithRoleOut])
async def list_my_teams(
    principal: SessionPrincipalDep, container: ContainerDep
) -> list[TeamWithRoleOut]:
    """Teams the caller belongs to, with the caller's role in each."""
    return [
        _team_with_role(m.team, m.role)
        for m in await container.teams.list_for_user(principal.user_id)
    ]


@router.get("/teams/{team_id}", response_model=TeamWithRoleOut)
async def get_team(ctx: TeamDep) -> TeamWithRoleOut:
    """Team details and settings (any member)."""
    return _team_with_role(ctx.team, ctx.role)


@router.patch("/teams/{team_id}", response_model=TeamWithRoleOut)
async def update_team(
    body: UpdateTeamRequest, ctx: TeamSessionDep, container: ContainerDep
) -> TeamWithRoleOut:
    """Change team settings (admin)."""
    team = await container.teams.update_settings(
        ctx,
        TeamSettingsChange(
            name=body.name,
            members_can_create_keys=body.members_can_create_keys,
            max_key_ttl_days=body.max_key_ttl_days,
            rate_limit_per_minute=body.rate_limit_per_minute,
            clear_rate_limit=body.reset_rate_limit,
        ),
    )
    return _team_with_role(team, ctx.role)


# --- members ------------------------------------------------------------------------------------


@router.get("/teams/{team_id}/members", response_model=list[MemberOut])
async def list_members(ctx: TeamSessionDep, container: ContainerDep) -> list[MemberOut]:
    """Team members with their roles (admin)."""
    return [MemberOut.model_validate(m) for m in await container.teams.list_members(ctx)]


@router.patch("/teams/{team_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def change_member_role(
    user_id: uuid.UUID, body: ChangeRoleRequest, ctx: TeamSessionDep, container: ContainerDep
) -> None:
    """Assign the admin or member role (admin). The last admin cannot be demoted."""
    await container.teams.change_role(ctx, user_id, body.role)


@router.delete("/teams/{team_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(user_id: uuid.UUID, ctx: TeamSessionDep, container: ContainerDep) -> None:
    """Remove a member (admin). Their access grants and team API keys stop working at once."""
    await container.teams.remove_member(ctx, user_id)


# --- invitations --------------------------------------------------------------------------------


@router.post(
    "/teams/{team_id}/invitations",
    status_code=status.HTTP_201_CREATED,
    response_model=IssuedInvitationOut,
)
async def create_invitation(
    body: CreateInvitationRequest, ctx: TeamSessionDep, container: ContainerDep
) -> IssuedInvitationOut:
    """Invite an email address with a role (admin). The token is returned only here."""
    issued = await container.invitations.create(ctx, email=body.email, role=body.role)
    return IssuedInvitationOut(
        **_invitation_out(issued.invitation).model_dump(), token=issued.token
    )


@router.get("/teams/{team_id}/invitations", response_model=list[InvitationOut])
async def list_invitations(
    ctx: TeamSessionDep,
    container: ContainerDep,
    include_closed: bool = Query(False, description="Also list accepted/revoked/expired."),
) -> list[InvitationOut]:
    """Open invitations (admin)."""
    invitations = await container.invitations.list(ctx, include_closed=include_closed)
    return [_invitation_out(inv) for inv in invitations]


@router.delete(
    "/teams/{team_id}/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def revoke_invitation(
    invitation_id: uuid.UUID, ctx: TeamSessionDep, container: ContainerDep
) -> None:
    """Revoke an open invitation (admin)."""
    await container.invitations.revoke(ctx, invitation_id)


@router.post("/invitations/accept", response_model=InvitationOut)
async def accept_invitation(
    body: AcceptInvitationRequest, principal: SessionPrincipalDep, container: ContainerDep
) -> InvitationOut:
    """Join the team as the signed-in user. The account email must match the invitation."""
    return _invitation_out(await container.invitations.accept(principal, body.token))

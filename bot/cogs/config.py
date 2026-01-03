#######################################################
# Config Cog - Manage role-based permissions
#######################################################
import discord
from discord.ext import commands
from typing import Optional, List
from pathlib import Path
import json

try:
    # Preferred when running from project root (bot/ is on sys.path)
    from util import perms as perms_util
except Exception:
    # Fallback when package layout uses `bot` as top-level package
    from bot.util import perms as perms_util


class Config(commands.Cog):
    perms = discord.SlashCommandGroup(name="perms", description="Manage role->permission mappings")
    def __init__(self, bot):
        self.bot = bot

    # --- Helpers ----------------------------------------------------------------
    @staticmethod
    def _member_has_role_permission(self, member: discord.Member, permission_name: str) -> bool:
        """Return True if the member has any role mapped to permission_name.
        Comparison is done using stringified role IDs to match the perms util.
        """
        role_ids = perms_util.get_roles_for_permission(permission_name)
        if not role_ids:
            return False
        member_role_ids = {str(r.id) for r in member.roles}
        return bool(member_role_ids.intersection(set(role_ids)))

    async def _check_manage_permission(self, ctx: discord.ApplicationContext) -> bool:
        # Allow server admins / manage_guild
        gperms = getattr(ctx.author, "guild_permissions", None)
        if gperms and (gperms.manage_guild or gperms.administrator):
            return True
        # Allow members who have a role mapped to the special 'manage_economy' permission
        if self._member_has_role_permission(ctx.author, "manage_economy"):
            return True
        return False

    @staticmethod
    def _parse_role_token(self, guild: discord.Guild, token: str) -> Optional[int]:
        """Try to resolve a single role token (mention, numeric id, or name) to a role ID (int).
        Returns None if not resolvable.
        """
        token = token.strip()
        # role mention: <@&123>
        if token.startswith("<@&") and token.endswith(">"):
            inner = token[3:-1]
            if inner.isdigit():
                return int(inner)
        # plain numeric id
        if token.isdigit():
            return int(token)
        # try exact name match (case-sensitive then case-insensitive)
        role = discord.utils.get(guild.roles, name=token)
        if role:
            return role.id
        # case-insensitive fallback
        for r in guild.roles:
            if r.name.lower() == token.lower():
                return r.id
        return None

    @staticmethod
    def _format_role_list(self, guild: discord.Guild, role_id_strs: List[str]) -> str:
        parts = []
        for rid in role_id_strs:
            try:
                rid_int = int(rid)
            except Exception:
                parts.append(rid)
                continue
            role = guild.get_role(rid_int)
            if role:
                parts.append(f"{role.name} (<@&{rid}>)")
            else:
                parts.append(f"Unknown role ({rid})")
        return ", ".join(parts) if parts else "(none)"

    # --- Slash command group ---------------------------------------------------
    # Parent group is exposed via the `perms` attribute above. Define subcommands below.
    # Add a role to a permission
    @perms_util.has_permission("manage_perms")
    @perms.command(name="add", description="Add a role to a permission")
    async def perms_add(self, ctx: discord.ApplicationContext, permission: str, role: discord.Role):
        """Usage: /perms add <permission> <role>
        Adds the given role to the permission's role list.
        """
        # Permission check
        if not await self._check_manage_permission(ctx):
            await ctx.respond(":x: You do not have permission to manage role permissions.", ephemeral=True)
            return

        added = perms_util.add_role_to_permission(role.id, permission)
        if added:
            embed = discord.Embed(
                title="Permission Updated",
                description=f":white_check_mark: Role **{role.name}** ({role.id}) added to permission **{permission}**.",
                colour=discord.Color.green()
            )
            await ctx.respond(embed=embed, ephemeral=True)
        else:
            await ctx.respond(f":information_source: Role **{role.name}** is already assigned to **{permission}**.", ephemeral=True)

    # Remove a role from a permission
    @perms_util.has_permission("manage_perms")
    @perms.command(name="remove", description="Remove a role from a permission")
    async def perms_remove(self, ctx: discord.ApplicationContext, permission: str, role: discord.Role):
        """Usage: /perms remove <permission> <role>
        Removes the given role from the permission's role list.
        """
        if not await self._check_manage_permission(ctx):
            await ctx.respond(":x: You do not have permission to manage role permissions.", ephemeral=True)
            return

        removed = perms_util.remove_role_from_permission(role.id, permission)
        if removed:
            embed = discord.Embed(
                title="Permission Updated",
                description=f":white_check_mark: Role **{role.name}** ({role.id}) removed from permission **{permission}**.",
                colour=discord.Color.green()
            )
            await ctx.respond(embed=embed, ephemeral=True)
        else:
            await ctx.respond(f":information_source: Role **{role.name}** was not assigned to **{permission}**.", ephemeral=True)

    # Set (replace) roles for a permission. Accepts a comma-separated list of role mentions/ids/names
    @perms_util.has_permission("manage_perms")
    @perms.command(name="set", description="Replace the role list for a permission")
    async def perms_set(self, ctx: discord.ApplicationContext, permission: str, roles: str):
        """Usage: /perms set <permission> <roles>
        roles should be a comma-separated list of role mentions, IDs, or names.
        Example: /perms set manage_economy @Staff,123456789012345678,Helpers
        """
        if not await self._check_manage_permission(ctx):
            await ctx.respond(":x: You do not have permission to manage role permissions.", ephemeral=True)
            return

        # Parse the role tokens
        tokens = [t.strip() for t in roles.split(",") if t.strip()]
        if not tokens:
            await ctx.respond(":x: No roles provided.", ephemeral=True)
            return

        resolved_ids = []
        invalid = []
        for tok in tokens:
            rid = self._parse_role_token(ctx.guild, tok)
            if rid is None:
                invalid.append(tok)
            else:
                resolved_ids.append(str(rid))

        if invalid:
            await ctx.respond(f":x: Could not resolve role(s): {', '.join(invalid)}. Use mentions, IDs, or exact names.", ephemeral=True)
            return

        perms_util.set_roles_for_permission(permission, resolved_ids)
        formatted = self._format_role_list(ctx.guild, resolved_ids)
        embed = discord.Embed(
            title="Permission Roles Set",
            description=f":white_check_mark: Permission **{permission}** now has roles: {formatted}",
            colour=discord.Color.green()
        )
        await ctx.respond(embed=embed, ephemeral=True)

    # Check permissions or roles
    @perms_util.has_permission("manage_perms")
    @perms.command(name="check", description="Check permissions or roles")
    async def perms_check(self, ctx: discord.ApplicationContext, permission: Optional[str] = None, role: Optional[discord.Role] = None):
        """Usage examples:
        /perms check                      -> list known permission names
        /perms check permission:manage_economy -> list roles assigned to permission
        /perms check role:@Staff          -> list permissions assigned to role
        """
        # If neither provided, list known permissions
        if permission is None and role is None:
            names = perms_util.get_permissions()
            if not names:
                await ctx.respond("No permissions are currently defined.", ephemeral=False)
                return
            embed = discord.Embed(
                title="Known Permissions",
                description=("\n".join(f"- {n}" for n in names)),
                colour=discord.Color.blue()
            )
            await ctx.respond(embed=embed, ephemeral=False)
            return

        if permission is not None:
            role_ids = perms_util.get_roles_for_permission(permission)
            formatted = self._format_role_list(ctx.guild, role_ids)
            embed = discord.Embed(
                title=f"Roles for permission: {permission}",
                description=formatted,
                colour=discord.Color.blue()
            )
            await ctx.respond(embed=embed, ephemeral=False)
            return

        if role is not None:
            perms_for_role = perms_util.find_permissions_for_role(role.id)
            if not perms_for_role:
                await ctx.respond(f"Role **{role.name}** is not assigned to any permissions.", ephemeral=False)
                return
            embed = discord.Embed(
                title=f"Permissions for role: {role.name}",
                description=("\n".join(f"- {p}" for p in perms_for_role)),
                colour=discord.Color.blue()
            )
            await ctx.respond(embed=embed, ephemeral=False)
            return


# Setup function to add the cog to the bot
def setup(bot):
    bot.add_cog(Config(bot))

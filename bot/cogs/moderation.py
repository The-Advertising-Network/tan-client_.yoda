#######################################################
# Moderation Cog - Provides moderation-related commands for the bot
#######################################################
import discord
from discord.ext import commands
import asyncio
import datetime
import re

try:
    # Preferred when running from project root (bot/ is on sys.path)
    from util import mute_role as mute_role_util
    from util import perms as perms_util
    from core.database import ModerationDatabase
except Exception:
    # Fallback when package layout uses `bot` as top-level package
    from bot.util import mute_role as mute_role_util
    from bot.util import perms as perms_util
    from bot.core.database import ModerationDatabase

# Moderation cog
class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.muted_role_id = mute_role_util.get_mute_role()
        self.db = ModerationDatabase()
        # in-memory scheduled unmute tasks: keys are (guild_id, user_id)
        self._unmute_tasks: dict[tuple[int, int], asyncio.Task] = {}

    async def cog_load(self) -> None:
        """Called when the cog is loaded; reschedule pending mute timers from the database."""
        # reschedule pending mutes
        try:
            pending = self.db.get_pending_mutes()
            now = datetime.datetime.utcnow()
            for t in pending:
                try:
                    unmute_at = datetime.datetime.fromisoformat(t['unmute_at'])
                except Exception:
                    # Skip malformed entries
                    continue
                delay = (unmute_at - now).total_seconds()
                if delay <= 0:
                    # already due; attempt to unmute immediately
                    guild = self.bot.get_guild(t['guild_id'])
                    if guild:
                        member = guild.get_member(t['user_id'])
                        if member:
                            # schedule a near-immediate unmute to run in the event loop
                            asyncio.create_task(self._perform_unmute(member, guild, remove_db=True))
                    else:
                        # not in bot cache; remove stale timer
                        self.db.remove_mute_timer(t['user_id'], t['guild_id'])
                else:
                    # schedule with persistent entry
                    self._schedule_unmute_task(t['user_id'], t['guild_id'], delay, remove_db=True)
        except Exception:
            # don't let database errors stop cog loading
            pass

    @perms_util.has_permission('kick_members')
    @commands.slash_command(name='kick', help='Kick a member from the server.')
    async def kick(self, ctx: discord.ApplicationContext, member: discord.Member, *, reason=None):
        """Kicks a member from the server.
        Parameters:
            ctx (commands.Context): The context of the command.
            member (discord.Member): The member to kick.
            reason (str, optional): The reason for the kick.
        """
        try:
            await member.kick(reason=reason)
            embed = discord.Embed(
                title="Member Kicked",
                description=f"{member.mention} has been kicked from the server.",
                colour=discord.Color.orange()
            )
            await ctx.respond(embed=embed)
        except Exception as e:
            embed = discord.Embed(
                title="Kick Failed",
                description=f"Failed to kick {member.mention}. Error: {str(e)}",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed)

        try:
            embed = discord.Embed(
                title="You have been kicked",
                description=f"You have been kicked from {ctx.guild.name}.\nReason: {reason if reason else 'No reason provided.'}",
                colour=discord.Color.red()
            )
            await member.send(embed=embed)
        except Exception:
            pass  # Ignore if we can't send a DM

    @perms_util.has_permission('ban_members')
    @commands.slash_command(name='ban', help='Ban a member from the server.')
    async def ban(self, ctx: discord.ApplicationContext, member: discord.Member, *, reason=None):
        """Bans a member from the server.
        Parameters:
            ctx (commands.Context): The context of the command.
            member (discord.Member): The member to ban.
            reason (str, optional): The reason for the ban.
        """
        try:
            await member.ban(reason=reason)
            embed = discord.Embed(
                title="Member Banned",
                description=f"{member.mention} has been banned from the server.",
                colour=discord.Color.dark_red()
            )
            await ctx.respond(embed=embed)
        except Exception as e:
            embed = discord.Embed(
                title="Ban Failed",
                description=f"Failed to ban {member.mention}. Error: {str(e)}",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed)

        try:
            embed = discord.Embed(
                title="You have been banned",
                description=f"You have been banned from {ctx.guild.name}.\nReason: {reason if reason else 'No reason provided.'}",
                colour=discord.Color.red()
            )
            await member.send(embed=embed)
        except Exception:
            pass  # Ignore if we can't send a DM

    @perms_util.has_permission('ban_members')
    @commands.slash_command(name='unban', help='Unban a member from the server.')
    async def unban(self, ctx: discord.ApplicationContext, user_id: str):
        """Unbans a member from the server.
        Parameters:
            ctx (commands.Context): The context of the command.
            user_id (str): The ID of the user to unban.
        """
        try:
            user_id = int(user_id)
            user = await self.bot.fetch_user(user_id)
        except ValueError:
            embed = discord.Embed(
                title="Unban Failed",
                description="Invalid user ID provided.",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed)
            return
        except Exception as e:
            embed = discord.Embed(
                title="Unban Failed",
                description=f"Could not find user with ID {user_id}. Error: {str(e)}",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed)
            return
        try:
            await ctx.guild.unban(user)
            embed = discord.Embed(
                title="Member Unbanned",
                description=f"{user.mention} has been unbanned from the server.",
                colour=discord.Color.green()
            )
            await ctx.respond(embed=embed)
        except Exception as e:
            embed = discord.Embed(
                title="Unban Failed",
                description=f"Failed to unban {user.mention}. Error: {str(e)}",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed)

    @perms_util.has_permission('mute_members')
    @commands.slash_command(name='mute', help='Mute a member in the server.')
    async def mute(self, ctx: discord.ApplicationContext, member: discord.Member, duration: str = None, *, reason=None):
        """Mutes a member in the server by adding a 'Muted' role.
        Parameters:
            ctx (commands.Context): The context of the command.
            member (discord.Member): The member to mute.
            duration (str, optional): The duration of the mute (e.g., '1h30m').
            reason (str, optional): The reason for the mute.
        """
        muted_role = discord.utils.get(ctx.guild.roles, id=self.muted_role_id)
        if not muted_role:
            embed = discord.Embed(
                title="Mute Failed",
                description="No muted role has been set in this server.",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed)
            return

        # Determine duration: prefer explicit `duration` param; fallback to token in reason like "duration=1h30m"
        duration_str = duration
        if not duration_str and reason and isinstance(reason, str):
            m = re.search(r"duration\s*=\s*([0-9dhmsHDMS]+(?:[0-9dhmsDHMS]+)*)", reason)
            if m:
                duration_str = m.group(1)
                # remove token from reason for nicer messages
                reason = re.sub(r"\s*duration\s*=\s*%s" % re.escape(duration_str), "", reason).strip()

        try:
            await member.add_roles(muted_role, reason=reason)
            embed = discord.Embed(
                title="Member Muted",
                description=f"{member.mention} has been muted{(' for ' + reason) if reason else ''}.{(' Duration: ' + duration_str) if duration_str else ''}",
                colour=discord.Color.orange()
            )
            await ctx.respond(embed=embed)
        except Exception as e:
            embed = discord.Embed(
                title="Mute Failed",
                description=f"Failed to mute {member.mention}. Error: {str(e)}",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed)
            return

        # If a duration was provided, parse and schedule unmute
        if duration_str:
            delta = self._parse_duration(duration_str)
            if not delta:
                embed = discord.Embed(
                    title="Invalid Duration",
                    description=f"The duration '{duration_str}' could not be parsed. Use formats like '30s', '15m', '1h', '1d' or combinations like '1h30m'.",
                    colour=discord.Color.red()
                )
                await ctx.respond(embed=embed)
            else:
                unmute_at = datetime.datetime.utcnow() + delta
                # persist timer
                try:
                    self.db.add_mute_timer(member.id, ctx.guild.id, unmute_at.isoformat(), reason=reason, muted_by=ctx.author.id if hasattr(ctx, 'author') else None)
                except Exception:
                    # ignore DB failures but still schedule in-memory for immediate uptime
                    pass
                # schedule in-memory task
                self._schedule_unmute_task(member.id, ctx.guild.id, delta.total_seconds(), remove_db=True)

        try:
            embed = discord.Embed(
                title="You have been muted",
                description=f"You have been muted in {ctx.guild.name}.\nReason: {reason if reason else 'No reason provided.'}",
                colour=discord.Color.red()
            )
            await member.send(embed=embed)
        except Exception:
            pass  # Ignore if we can't send a DM

    @perms_util.has_permission('mute_members')
    @commands.slash_command(name='unmute', help='Unmute a member in the server.')
    async def unmute(self, ctx: discord.ApplicationContext, member: discord.Member):
        """Unmutes a member in the server by removing the 'Muted' role.
        Parameters:
            ctx (commands.Context): The context of the command.
            member (discord.Member): The member to unmute.
        """
        muted_role = discord.utils.get(ctx.guild.roles, id=self.muted_role_id)
        if not muted_role:
            embed = discord.Embed(
                title="Unmute Failed",
                description="No muted role has been set in this server.",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed)
            return

        try:
            await member.remove_roles(muted_role)
            # cleanup DB timer if present
            try:
                self.db.remove_mute_timer(member.id, ctx.guild.id)
            except Exception:
                pass
            # cancel in-memory task if scheduled
            task_key = (ctx.guild.id, member.id)
            task = self._unmute_tasks.pop(task_key, None)
            if task and not task.done():
                task.cancel()

            embed = discord.Embed(
                title="Member Unmuted",
                description=f"{member.mention} has been unmuted.",
                colour=discord.Color.green()
            )
            await ctx.respond(embed=embed)
        except Exception as e:
            embed = discord.Embed(
                title="Unmute Failed",
                description=f"Failed to unmute {member.mention}. Error: {str(e)}",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed)

        try:
            embed = discord.Embed(
                title="You have been unmuted",
                description=f"You have been unmuted in {ctx.guild.name}.",
                colour=discord.Color.green()
            )
            await member.send(embed=embed)
        except Exception:
            pass  # Ignore if we can't send a DM

    @perms_util.has_permission('purge_messages')
    @commands.slash_command(name='purge', help='Purge messages from a channel.')
    async def purge(self, ctx: discord.ApplicationContext, limit: int, channel=None):
        """Purges a number of messages from a channel.
        Parameters:
            ctx (commands.Context): The context of the command.
            limit (int): The number of messages to delete.
            channel (discord.TextChannel, optional): The channel to purge messages from. Defaults to the current channel.
        """
        target_channel = channel or ctx.channel
        try:
            deleted = await target_channel.purge(limit=limit)
            embed = discord.Embed(
                title="Messages Purged",
                description=f"Deleted {len(deleted)} messages from {target_channel.mention}.",
                colour=discord.Color.green()
            )
            await ctx.respond(embed=embed)
        except Exception as e:
            embed = discord.Embed(
                title="Purge Failed",
                description=f"Failed to purge messages from {target_channel.mention}. Error: {str(e)}",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed)

    @perms_util.has_permission('warn_members')
    @commands.slash_command(name='warn', help='Warn a member in the server.')
    async def warn(self, ctx: discord.ApplicationContext, member: discord.Member, *, reason=None):
        """Warns a member in the server.
        Parameters:
            ctx (commands.Context): The context of the command.
            member (discord.Member): The member to warn.
            reason (str, optional): The reason for the warning.
        """
        self.db.add_warning(member.id, reason if reason else "No reason provided.")
        embed = discord.Embed(
            title="Member Warned",
            description=f"{member.mention} has been warned.\nReason: {reason if reason else 'No reason provided.'}",
            colour=discord.Color.orange()
        )
        await ctx.respond(embed=embed)

    @perms_util.has_permission('warn_members')
    @commands.slash_command(name='unwarn', help='Remove a warning from a member.')
    async def unwarn(self, ctx: discord.ApplicationContext, member: discord.Member, log_id: int):
        """Removes a warning from a member in the server.
        Parameters:
            ctx (commands.Context): The context of the command.
            member (discord.Member): The member whose warning is to be removed.
            log_id (int): The ID of the warning log to remove.
        """
        self.db.remove_warning(member.id, log_id)
        embed = discord.Embed(
            title="Warning Removed",
            description=f"Warning ID {log_id} has been removed from {member.mention}.",
            colour=discord.Color.green()
        )
        await ctx.respond(embed=embed)

    @perms_util.has_permission('warn_members')
    @commands.slash_command(name='get_warnings', help='Get warnings for a member.')
    async def get_warnings(self, ctx: discord.ApplicationContext, member: discord.Member):
        """Gets warnings for a member in the server.
        Parameters:
            ctx (commands.Context): The context of the command.
            member (discord.Member): The member whose warnings are to be retrieved.
        """
        warnings = self.db.get_warnings(member.id)
        if warnings:
            lines = []
            for w in warnings:
                if isinstance(w, dict):
                    log_id = w.get('log_id') or w.get('id')
                    reason = w.get('reason', 'No reason provided.')
                    timestamp = w.get('timestamp')
                elif isinstance(w, (list, tuple)) and len(w) >= 3:
                    log_id, reason, timestamp = w[0], w[1], w[2]
                else:
                    # Fallback - attempt attribute access
                    log_id = getattr(w, 'log_id', None) or getattr(w, 'id', None)
                    reason = getattr(w, 'reason', 'No reason provided.')
                    timestamp = getattr(w, 'timestamp', None)
                lines.append(f"ID: {log_id} | Reason: {reason} | Timestamp: {timestamp}")

            description = "\n".join(lines)
            # Discord embed description has limits; truncate if necessary
            if len(description) > 4000:
                description = description[:3997] + "..."

            embed = discord.Embed(
                title=f"Warnings for {member.name}",
                description=description,
                colour=discord.Color.orange()
            )
        else:
            embed = discord.Embed(
                title=f"No Warnings for {member.name}",
                description="This member has no warnings.",
                colour=discord.Color.green()
            )
        await ctx.respond(embed=embed)

    @perms_util.has_permission('set_muted_role')
    @commands.slash_command(name='set_muted_role', help='Set the role to be used for muting members.')
    async def set_muted_role(self, ctx: discord.ApplicationContext, role: discord.Role):
        """Sets the role to be used for muting members.
        Parameters:
            ctx (commands.Context): The context of the command.
            role (discord.Role): The role to set as the muted role.
        """
        self.muted_role_id = role.id
        mute_role_util.set_mute_role(role.id)
        embed = discord.Embed(
            title="Muted Role Set",
            description=f"The muted role has been set to {role.mention}.",
            colour=discord.Color.green()
        )
        await ctx.respond(embed=embed)

    @commands.slash_command(name='get_muted_role', help='Get the currently set muted role.')
    async def get_muted_role(self, ctx: discord.ApplicationContext):
        """Gets the currently set muted role.
        Parameters:
            ctx (commands.Context): The context of the command.
        """
        if self.muted_role_id:
            muted_role = discord.utils.get(ctx.guild.roles, id=self.muted_role_id)
            if muted_role:
                embed = discord.Embed(
                    title="Current Muted Role",
                    description=f"The current muted role is {muted_role.mention}.",
                    colour=discord.Color.green()
                )
            else:
                embed = discord.Embed(
                    title="Muted Role Not Found",
                    description="The muted role ID is set, but the role does not exist in this server.",
                    colour=discord.Color.red()
                )
        else:
            embed = discord.Embed(
                title="No Muted Role Set",
                description="No muted role has been set in this server.",
                colour=discord.Color.red()
            )
        await ctx.respond(embed=embed)

    @perms_util.has_permission('strike_staff')
    @commands.slash_command(name='strike', help='Issue a strike to a staff member.')
    async def strike(self, ctx: discord.ApplicationContext, member: discord.Member, *, reason=None):
        """Issues a strike to a member in the server."""
        self.db.add_strike(member.id, reason if reason else "No reason provided.")
        embed = discord.Embed(
            title="Member Struck",
            description=f"{member.mention} has been issued a strike.\nReason: {reason if reason else 'No reason provided.'}",
            colour=discord.Color.orange()
        )
        await ctx.respond(embed=embed)

    @perms_util.has_permission('strike_staff')
    @commands.slash_command(name='get_strikes', help='Get strikes for a staff member.')
    async def get_strikes(self, ctx: discord.ApplicationContext, member: discord.Member):
        """Gets strikes for a member in the server."""
        strikes = self.db.get_strikes(member.id)
        if strikes:
            lines = []
            for s in strikes:
                if isinstance(s, dict):
                    log_id = s.get('log_id') or s.get('id')
                    reason = s.get('reason', 'No reason provided.')
                    timestamp = s.get('timestamp')
                elif isinstance(s, (list, tuple)) and len(s) >= 3:
                    log_id, reason, timestamp = s[0], s[1], s[2]
                else:
                    log_id = getattr(s, 'log_id', None) or getattr(s, 'id', None)
                    reason = getattr(s, 'reason', 'No reason provided.')
                    timestamp = getattr(s, 'timestamp', None)
                lines.append(f"ID: {log_id} | Reason: {reason} | Timestamp: {timestamp}")

            description = "\n".join(lines)
            if len(description) > 4000:
                description = description[:3997] + "..."

            embed = discord.Embed(
                title=f"Strikes for {member.name}",
                description=description,
                colour=discord.Color.orange()
            )
        else:
            embed = discord.Embed(
                title=f"No Strikes for {member.name}",
                description="This member has no strikes.",
                colour=discord.Color.green()
            )
        await ctx.respond(embed=embed)

    # --- Helpers for parsing durations and scheduling unmute tasks ---
    def _parse_duration(self, duration: str) -> datetime.timedelta | None:
        """Parse duration strings like '1h30m', '45m', '30s', '2d' into a timedelta. Returns None if invalid."""
        if not duration or not isinstance(duration, str):
            return None
        # normalize
        s = duration.strip().lower()
        # support formats like '1h30m', '90m', '1d2h'
        pattern = r'(?:(?P<days>\d+)d)?(?:(?P<hours>\d+)h)?(?:(?P<minutes>\d+)m)?(?:(?P<seconds>\d+)s)?$'
        m = re.match(pattern, s)
        if not m:
            return None
        parts = {k: int(v) if v else 0 for k, v in m.groupdict().items()}
        if all(v == 0 for v in parts.values()):
            return None
        return datetime.timedelta(days=parts['days'], hours=parts['hours'], minutes=parts['minutes'], seconds=parts['seconds'])

    def _schedule_unmute_task(self, user_id: int, guild_id: int, delay_seconds: float, remove_db: bool = True) -> None:
        """Create an asyncio.Task that waits for delay_seconds then unmute the user.
           remove_db: whether to remove the DB timer after unmuting (set True if the timer was persisted).
        """
        key = (guild_id, user_id)
        # cancel existing task if present
        existing = self._unmute_tasks.pop(key, None)
        if existing and not existing.done():
            existing.cancel()

        async def _task():
            try:
                await asyncio.sleep(delay_seconds)
                guild = self.bot.get_guild(guild_id)
                if not guild:
                    return
                member = guild.get_member(user_id)
                if not member:
                    return
                await self._perform_unmute(member, guild, remove_db=remove_db)
            except asyncio.CancelledError:
                return
            except Exception:
                return

        task = asyncio.create_task(_task())
        self._unmute_tasks[key] = task

    async def _perform_unmute(self, member: discord.Member, guild: discord.Guild, remove_db: bool = True) -> None:
        """Remove muted role from member and clean up DB entry if requested."""
        muted_role = discord.utils.get(guild.roles, id=self.muted_role_id)
        if not muted_role:
            return
        try:
            await member.remove_roles(muted_role)
        except Exception:
            pass
        if remove_db:
            try:
                self.db.remove_mute_timer(member.id, guild.id)
            except Exception:
                pass


def setup(bot):
    bot.add_cog(Moderation(bot))

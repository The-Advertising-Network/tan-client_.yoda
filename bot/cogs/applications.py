#######################################################
# Moderation Cog - Provides moderation-related commands for the bot
#######################################################
import discord
from discord.ext import commands

# Import database and permission utilities with robust fallbacks using dynamic import
try:
    # Preferred when running as a package
    from bot.util import perms as perms_util
    from bot.core.database import ApplicationsDatabase
except Exception:
    # Fallback when running from project root (bot/ is on sys.path)
    import importlib
    perms_util = importlib.import_module('util.perms')
    # import_module returns the module; perms_util points to the module, but earlier code expects functions/attributes under perms_util
    # ensure perms_util is the module object used similarly to previous imports
    ApplicationsDatabase = importlib.import_module('core.database').ApplicationsDatabase


# Applications cog
class Applications(commands.Cog):
    application_commands = discord.SlashCommandGroup("application", "Application Commands")
    appsmanage_commands = discord.SlashCommandGroup("appsmanage", "Application Management Commands")
    def __init__(self, bot):
        self.bot = bot
        # position structure: {'name': str, 'description': str, 'roles_given': list[int], 'questions': list[str], 'acceptance_message': str, 'rejection_message': str, 'open': bool}
        self.db = ApplicationsDatabase()

    # DM listener to handle app responses
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for DMs from users applying for positions and submit the next message as their application.

        Behavior:
        - Only handles direct messages (DMChannel) from non-bot users.
        - Checks for an in-progress application for the user (started via /apply).
        - The first message from the user in DMs after starting (and only that message) is submitted as their application answers.
        - The application must be submitted within 24 hours (enforced by the DB submit_application method).
        - The bot posts the submission into the configured applications channel for the server the bot is in.
        """
        # Ignore non-DMs and bot messages
        if not isinstance(message.channel, discord.DMChannel):
            return
        if message.author.bot:
            return

        # Check if the user has an in-progress application
        in_progress = self.db.get_in_progress_application(message.author.id)
        if not in_progress:
            return  # nothing to do

        # Only accept a single message: submit and stop
        answers = message.content or ""
        # Include attachments' URLs if present
        if message.attachments:
            attachments_text = "\n\nAttachments:\n" + "\n".join(a.url for a in message.attachments)
            answers = (answers + attachments_text).strip()

        submitted = self.db.submit_application(message.author.id, answers)
        if not submitted[0]:
            # Failure -- determine reason
            reason = submitted[1]
            if reason == 'no_in_progress':
                try:
                    await message.channel.send("You don't have an in-progress application. Start one with `/application apply <position_id>` in the server.")
                except discord.Forbidden:
                    pass
                return
            if reason == 'expired':
                try:
                    await message.channel.send("Your application has expired (more than 24 hours since you started). Please start again with `/application apply <position_id>`.")
                except discord.Forbidden:
                    pass
                return
            # Generic failure
            try:
                await message.channel.send("Failed to submit your application. Please contact staff.")
            except discord.Forbidden:
                pass
            return

        # On success, submitted is (True, application_id, position_id)
        _, application_id, position_id = submitted

        # Find the guild (this bot is intended for a single server)
        guild = None
        if self.bot.guilds:
            guild = self.bot.guilds[0]
        if not guild:
            try:
                await message.channel.send("Submission saved, but I couldn't find the server to post it to. Contact staff.")
            except discord.Forbidden:
                pass
            return

        # Get the configured applications channel for the guild
        channel_id = self.db.get_applications_channel(guild.id)
        if not channel_id:
            try:
                await message.channel.send("Submission saved, but no applications channel is configured. Please ping a management member.")
            except discord.Forbidden:
                pass
            return

        channel = guild.get_channel(channel_id)
        if not channel:
            try:
                await message.channel.send(f"Submission saved, but the configured applications channel (ID {channel_id}) could not be found in the server. Please ping a management member.")
            except discord.Forbidden:
                pass
            return

        # Build an embed for staff review
        position = self.db.get_position(position_id)
        position_name = position['name'] if position else f"ID {position_id}"
        embed = discord.Embed(title=f"New Application: {position_name}", colour=discord.Color.blue())
        embed.add_field(name="Applicant", value=f"{message.author} (ID: {message.author.id})", inline=False)
        embed.add_field(name="Application ID", value=str(application_id), inline=True)
        embed.add_field(name="Position ID", value=str(position_id), inline=True)
        truncated = (answers[:1900] + '...') if len(answers) > 1900 else answers or "(No content)"
        embed.add_field(name="Answers", value=truncated, inline=False)
        embed.set_footer(text="Use your normal review workflow to accept/reject and assign roles.")

        # If the user is flagged, prepare a mention string for staff roles and prepend it to the message
        mention_text = None
        try:
            flagged = self.db.is_user_flagged(message.author.id, guild_id=guild.id)
            if flagged:
                # Resolve roles that have the manage_applications permission
                role_ids = perms_util.get_roles_for_permission("manage_applications") or []
                # Convert and filter role ids to integers and ensure they exist in the guild
                present_role_ids = []
                for rid in role_ids:
                    try:
                        rid_int = int(rid)
                    except Exception:
                        continue
                    if any(r.id == rid_int for r in guild.roles):
                        present_role_ids.append(rid_int)
                if present_role_ids:
                    mention_text = ' '.join(f"<@&{r}>" for r in present_role_ids)
                else:
                    # Fallback text if no role IDs are configured or resolvable
                    mention_text = "@Staff"
        except Exception:
            # If flag check fails, continue without mention
            mention_text = None

        try:
            if mention_text:
                # Send mention first (so pings actually go through) then the embed
                await channel.send(content=mention_text)
            await channel.send(embed=embed)
            try:
                await message.channel.send("Your application has been submitted to staff for review. Thank you!")
            except discord.Forbidden:
                pass
        except discord.Forbidden:
            pass
        except Exception as e:
            try:
                await message.channel.send("An error occurred while submitting your application. Please contact staff.")
            except discord.Forbidden:
                pass


    @application_commands.command(name="list", description="List all application positions.")
    async def list_positions(self, ctx: discord.ApplicationContext, page: int = 1):
        """List all application positions with pagination."""
        positions = self.db.get_positions()
        if not positions:
            embed = discord.Embed(
                title="No Application Positions",
                description="There are currently no application positions defined.",
                colour=discord.Color.orange()
            )
            await ctx.respond(embed=embed)
            return

        # Pagination settings
        per_page = 6  # number of positions per page
        total = len(positions)
        total_pages = (total - 1) // per_page + 1

        # Validate requested page
        if page < 1 or page > total_pages:
            embed = discord.Embed(
                title="Page Not Found",
                description=f"Page {page} is out of range. There {'is' if total_pages==1 else 'are'} {total_pages} page{'s' if total_pages!=1 else ''} available.",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed)
            return

        # Slice the positions for the requested page
        start = (page - 1) * per_page
        end = start + per_page
        page_positions = positions[start:end]

        embed = discord.Embed(
            title="Application Positions",
            colour=discord.Color.blue()
        )
        for pos in page_positions:
            embed.add_field(
                name=f"ID {pos['position_id']}: {pos['name']}",
                value=f"Description: {pos.get('description', 'No description provided.')}",
                inline=False
            )

        embed.set_footer(text=f"Page {page}/{total_pages} — {total} position{'s' if total!=1 else ''}")
        await ctx.respond(embed=embed)

    @application_commands.command(name="apply", description="Apply for an application position.")
    async def apply(self, ctx: discord.ApplicationContext, position_id: int):
        """Apply for an application position."""
        if self.db.is_user_blacklisted(ctx.author.id):
            embed = discord.Embed(
                title="Application Denied",
                description="You are blacklisted from applying for positions.",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed, ephemeral=True)
            return

        position = self.db.get_position(position_id)
        if not position:
            embed = discord.Embed(
                title="Position Not Found",
                description=f"No application position found with ID {position_id}. Use `/application list` to see available positions.",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed, ephemeral=True)
            return

        if not position.get('open', False):
            embed = discord.Embed(
                title="Application Closed",
                description=f"The application position '{position['name']}' (ID: {position_id}) is currently closed for submissions.",
                colour=discord.Color.orange()
            )
            await ctx.respond(embed=embed, ephemeral=True)
            return

        # Start application process
        dm_embed = discord.Embed(
            title=f"Application for '{position['name']}'",
            description="You have initiated the application process. Please answer the following questions:",
            colour=discord.Color.blue()
        )
        questions = position.get('questions', [])
        if not questions:
            dm_embed.add_field(name="No Questions", value="There are no questions for this application. Please wait for further instructions from the staff.", inline=False)
        else:
            for idx, question in enumerate(questions, start=1):
                dm_embed.add_field(name=f"Question {idx}", value=question, inline=False)
        try:
            await ctx.author.send(embed=dm_embed)
        except discord.Forbidden:
            embed = discord.Embed(
                title="DM Failed",
                description="I was unable to send you a DM. Please ensure your privacy settings allow DMs from server members and try again.",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed, ephemeral=True)
            return

        # Start an in-progress application (stores timestamp). The next DM from the user is treated as the submission.
        self.db.start_application(user_id=ctx.author.id, position_id=position_id)
        embed = discord.Embed(
            title="Application Process Started",
            description=f"You have started the application process for '{position['name']}' (ID: {position_id}). Please check your DMs and reply with your answers — the next message you send will be submitted. You have 24 hours to submit.",
            colour=discord.Color.green()
        )
        await ctx.respond(embed=embed, ephemeral=True)

    @application_commands.command(name="withdraw", description="Withdraw your submitted application.")
    async def withdraw(self, ctx: discord.ApplicationContext, application_id: int = None):
        """Withdraw a submitted application. If application_id is omitted, withdraw the user's latest submitted application."""
        # Determine target application
        if application_id is not None:
            app = self.db.get_application(application_id)
            if not app:
                embed = discord.Embed(title="Application Not Found", description=f"No application found with ID {application_id}.", colour=discord.Color.red())
                await ctx.respond(embed=embed, ephemeral=True)
                return
        else:
            app = self.db.get_latest_submitted_application(ctx.author.id)
            if not app:
                embed = discord.Embed(title="No Submitted Application", description="You don't have any submitted applications to withdraw.", colour=discord.Color.orange())
                await ctx.respond(embed=embed, ephemeral=True)
                return

        # Ownership check
        if app['user_id'] != ctx.author.id:
            embed = discord.Embed(title="Permission Denied", description="You can only withdraw your own applications.", colour=discord.Color.red())
            await ctx.respond(embed=embed, ephemeral=True)
            return

        # Status checks - only 'submitted' (or maybe 'pending') can be withdrawn
        status = app.get('status', '')
        if status == 'withdrawn':
            embed = discord.Embed(title="Already Withdrawn", description=f"Application ID {app['application_id']} has already been withdrawn.", colour=discord.Color.orange())
            await ctx.respond(embed=embed, ephemeral=True)
            return
        if status in ('accepted', 'rejected'):
            embed = discord.Embed(title="Cannot Withdraw", description=f"Application ID {app['application_id']} has already been processed and cannot be withdrawn.", colour=discord.Color.red())
            await ctx.respond(embed=embed, ephemeral=True)
            return

        # Perform withdrawal
        success = self.db.withdraw_application(app['application_id'])
        if not success:
            embed = discord.Embed(title="Withdrawal Failed", description="Failed to withdraw the application. It may have already been withdrawn or does not exist.", colour=discord.Color.red())
            await ctx.respond(embed=embed, ephemeral=True)
            return

        embed = discord.Embed(title="Application Withdrawn", description=f"Your application (ID {app['application_id']}) has been withdrawn. Staff have been notified.", colour=discord.Color.green())
        await ctx.respond(embed=embed, ephemeral=True)

        # Optional: notify staff in the applications channel
        try:
            guild = None
            if self.bot.guilds:
                guild = self.bot.guilds[0]
            if guild:
                channel_id = self.db.get_applications_channel(guild.id)
                if channel_id:
                    channel = guild.get_channel(channel_id)
                    if channel:
                        notif = discord.Embed(title="Application Withdrawn", colour=discord.Color.orange())
                        notif.add_field(name="Applicant", value=f"{ctx.author} (ID: {ctx.author.id})", inline=False)
                        notif.add_field(name="Application ID", value=str(app['application_id']), inline=True)
                        notif.add_field(name="Position ID", value=str(app['position_id']), inline=True)
                        await channel.send(embed=notif)
        except Exception:
            # Don't let notification failures block the command response
            pass

    @application_commands.command(name="checkappstatus", description="Check the status of your submitted application.")
    async def check_app_status(self, ctx: discord.ApplicationContext, application_id: int = None):
        """Check the status of your submitted application. If application_id is omitted, checks the user's latest submitted application."""
        # Determine target application
        if application_id is not None:
            app = self.db.get_application(application_id)
            if not app:
                embed = discord.Embed(title="Application Not Found", description=f"No application found with ID {application_id}.", colour=discord.Color.red())
                await ctx.respond(embed=embed, ephemeral=True)
                return
        else:
            app = self.db.get_latest_submitted_application(ctx.author.id)
            if not app:
                embed = discord.Embed(title="No Submitted Application", description="You don't have any submitted applications to check.", colour=discord.Color.orange())
                await ctx.respond(embed=embed, ephemeral=True)
                return

        # Ownership check
        if app['user_id'] != ctx.author.id:
            embed = discord.Embed(title="Permission Denied", description="You can only check the status of your own applications.", colour=discord.Color.red())
            await ctx.respond(embed=embed, ephemeral=True)
            return

        # Build status embed
        embed = discord.Embed(title="Application Status", colour=discord.Color.blue())
        embed.add_field(name="Application ID", value=str(app['application_id']), inline=True)
        embed.add_field(name="Position ID", value=str(app['position_id']), inline=True)
        embed.add_field(name="Status", value=app.get('status', 'unknown').capitalize(), inline=False)

        await ctx.respond(embed=embed, ephemeral=True)


    # Application management commands

    @perms_util.has_permission("set_apps_channel")
    @appsmanage_commands.command(name="set_apps_channel", description="Set the channel for application submissions.")
    async def set_apps_channel(self, ctx: discord.ApplicationContext, channel: discord.TextChannel):
        """Set the channel for application submissions."""
        self.db.set_applications_channel(ctx.guild.id, channel.id)
        embed = discord.Embed(
            title="Application Channel Set",
            description=f"Application submissions channel set to {channel.mention}.",
            colour=discord.Color.green()
        )
        await ctx.respond(embed=embed)

    @perms_util.has_permission("set_apps_channel")
    @appsmanage_commands.command(name="get_apps_channel", description="List the current application submissions channel.")
    async def get_apps_channel(self, ctx: discord.ApplicationContext):
        """List the current application submissions channel."""
        channel_id = self.db.get_applications_channel(ctx.guild.id)
        if channel_id:
            channel = ctx.guild.get_channel(channel_id)
            if channel:
                embed = discord.Embed(
                    title="Current Application Channel",
                    description=f"The current application submissions channel is {channel.mention}.",
                    colour=discord.Color.green()
                )
            else:
                embed = discord.Embed(
                    title="Current Application Channel",
                    description=f"The application submissions channel is set to an invalid channel (ID: {channel_id}).",
                    colour=discord.Color.red()
                )
        else:
            embed = discord.Embed(
                title="Current Application Channel",
                description="No application submissions channel has been set.",
                colour=discord.Color.orange()
            )
        await ctx.respond(embed=embed)

    @perms_util.has_permission("manage_applications")
    @appsmanage_commands.command(name="create", description="Create a new application position.")
    async def create(self, ctx: discord.ApplicationContext, application_name: str):
        """Create a new application position.
        Allows identical names, but it's not recommended."""
        application_name = application_name.lower()
        position_id = self.db.add_position(application_name) # Add position to database and get its ID
        embed = discord.Embed(
            title="Application Created",
            description=f"Application position '{application_name}' created with ID {position_id}.",
            colour=discord.Color.green()
        )
        # Check if another position with this name already exists, and warn if so
        existing_positions = self.db.get_positions_by_name(application_name)
        if len(existing_positions) > 1:
            embed.add_field(
                name="Warning: Duplicate Names",
                value=f"There are multiple application positions with the name '{application_name}'. Consider using unique names to avoid confusion.",
                inline=False
            )
            embed.colour = discord.Color.orange()
        await ctx.respond(embed=embed)

    @perms_util.has_permission("manage_roles")
    @appsmanage_commands.command(name="delete", description="Delete an existing application position.")
    async def delete(self, ctx: discord.ApplicationContext, position_id: int):
        """Delete an existing application position."""
        position = self.db.get_position(position_id)
        if not position:
            embed = discord.Embed(
                title="Position Not Found",
                description=f"No application position found with ID {position_id}.",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed)
            return

        self.db.remove_position(position_id)
        embed = discord.Embed(
            title="Application Deleted",
            description=f"Application position '{position['name']}' (ID: {position_id}) has been deleted.",
            colour=discord.Color.green()
        )
        await ctx.respond(embed=embed)

    @perms_util.has_permission("manage_applications")
    @appsmanage_commands.command(name="open", description="Open an application position for submissions.")
    async def open_position(self, ctx: discord.ApplicationContext, position_id: int):
        """Open an application position for submissions."""
        position = self.db.get_position(position_id)
        if not position:
            embed = discord.Embed(
                title="Position Not Found",
                description=f"No application position found with ID {position_id}.",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed)
            return

        self.db.set_position_open(position_id, True)
        embed = discord.Embed(
            title="Application Opened",
            description=f"Application position '{position['name']}' (ID: {position_id}) is now open for submissions.",
            colour=discord.Color.green()
        )
        await ctx.respond(embed=embed)

    @perms_util.has_permission("manage_applications")
    @appsmanage_commands.command(name="close", description="Close an application position for submissions.")
    async def close_position(self, ctx: discord.ApplicationContext, position_id: int):
        """Close an application position for submissions."""
        position = self.db.get_position(position_id)
        if not position:
            embed = discord.Embed(
                title="Position Not Found",
                description=f"No application position found with ID {position_id}.",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed)
            return

        self.db.set_position_open(position_id, False)
        embed = discord.Embed(
            title="Application Closed",
            description=f"Application position '{position['name']}' (ID: {position_id}) is now closed for submissions.",
            colour=discord.Color.green()
        )
        await ctx.respond(embed=embed)

    @perms_util.has_permission("manage_applications")
    @appsmanage_commands.command(name="view", description="View details of an application position.")
    async def view_position(self, ctx: discord.ApplicationContext, position_id: int):
        """View details of an application position."""
        position = self.db.get_position(position_id)
        if not position:
            embed = discord.Embed(
                title="Position Not Found",
                description=f"No application position found with ID {position_id}.",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed)
            return

        embed = discord.Embed(
            title=f"Application Position: {position['name']}",
            colour=discord.Color.blue()
        )
        embed.add_field(name="ID", value=str(position['position_id']), inline=False)
        embed.add_field(name="Description", value=position.get('description', 'No description provided.'), inline=False)
        embed.add_field(name="Roles Given", value=", ".join([f"<@&{role_id}>" for role_id in position.get('roles_given', [])]) or "None", inline=False)
        embed.add_field(name="Questions", value="\n".join(position.get('questions', [])) or "None", inline=False)
        embed.add_field(name="Acceptance Message", value=position.get('acceptance_message', 'None'), inline=False)
        embed.add_field(name="Rejection Message", value=position.get('rejection_message', 'None'), inline=False)
        embed.add_field(name="Open for Submissions", value="Yes" if position.get('open', False) else "No", inline=False)

        await ctx.respond(embed=embed)

    @perms_util.has_permission("manage_applications")
    @appsmanage_commands.command(name="set_description", description="Set the description for an application position.")
    async def set_description(self, ctx: discord.ApplicationContext, position_id: int, *, description: str):
        """Set the description for an application position."""
        position = self.db.get_position(position_id)
        if not position:
            embed = discord.Embed(
                title="Position Not Found",
                description=f"No application position found with ID {position_id}.",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed)
            return

        self.db.modify(position_id, "description", description)
        embed = discord.Embed(
            title="Description Updated",
            description=f"Description for application position '{position['name']}' (ID: {position_id}) has been updated.",
            colour=discord.Color.green()
        )
        embed.add_field(name="Description (old)", value=position.get('description', 'No description provided.'), inline=False)
        embed.add_field(name="Description (new)", value=description, inline=False)
        await ctx.respond(embed=embed)

    @perms_util.has_permission("manage_applications")
    @appsmanage_commands.command(name="add_question", description="Add a question to an application position.")
    async def add_question(self, ctx: discord.ApplicationContext, position_id: int, *, question: str):
        """Add a question to an application position."""
        position = self.db.get_position(position_id)
        if not position:
            embed = discord.Embed(
                title="Position Not Found",
                description=f"No application position found with ID {position_id}.",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed)
            return

        questions = position.get('questions', [])
        questions.append(question)
        self.db.modify(position_id, "questions", questions)
        embed = discord.Embed(
            title="Question Added",
            description=f"Question added to application position '{position['name']}' (ID: {position_id}).",
            colour=discord.Color.green()
        )
        embed.add_field(name="New Question", value=question, inline=False)
        await ctx.respond(embed=embed)

    @perms_util.has_permission("manage_applications")
    @appsmanage_commands.command(name="remove_question", description="Remove a question from an application position.")
    async def remove_question(self, ctx: discord.ApplicationContext, position_id: int, question_index: int):
        """Remove a question from an application position by its index (1-based)."""
        position = self.db.get_position(position_id)
        if not position:
            embed = discord.Embed(
                title="Position Not Found",
                description=f"No application position found with ID {position_id}.",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed)
            return

        questions = position.get('questions', [])
        if question_index < 1 or question_index > len(questions):
            embed = discord.Embed(
                title="Invalid Question Index",
                description=f"Question index {question_index} is out of range. There are {len(questions)} question(s) available.",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed)
            return

        removed_question = questions.pop(question_index - 1)
        self.db.modify(position_id, "questions", questions)
        embed = discord.Embed(
            title="Question Removed",
            description=f"Question removed from application position '{position['name']}' (ID: {position_id}).",
            colour=discord.Color.green()
        )
        embed.add_field(name="Removed Question", value=removed_question, inline=False)
        await ctx.respond(embed=embed)

    @perms_util.has_permission("manage_applications")
    @appsmanage_commands.command(name="set_roles", description="Set the roles to be given upon acceptance for an application position.")
    async def set_roles(self, ctx: discord.ApplicationContext, position_id: int, *, roles: str = ""):
        """Set the roles (by mention, ID or name) to be given upon acceptance for an application position.

        Roles should be provided as a space- or comma-separated list. Example:
        `/appsmanage set_roles 1 @Role1 @Role2` or `/appsmanage set_roles 1 123456789012345678,987654321098765432`
        Passing no roles will clear the roles for the position.
        """
        position = self.db.get_position(position_id)
        if not position:
            embed = discord.Embed(
                title="Position Not Found",
                description=f"No application position found with ID {position_id}.",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed)
            return

        # If empty string, clear roles
        if not roles or not roles.strip():
            self.db.modify(position_id, "roles_given", [])
            embed = discord.Embed(
                title="Roles Cleared",
                description=f"All roles will be removed from application position '{position['name']}' (ID: {position_id}).",
                colour=discord.Color.green()
            )
            await ctx.respond(embed=embed)
            return

        # Parse roles: accept mentions like <@&id>, plain numeric IDs, or role names
        role_ids = []
        tokens = [t.strip() for part in roles.split(',') for t in part.split() if t.strip()]
        for token in tokens:
            # mention format
            if token.startswith('<@&') and token.endswith('>'):
                try:
                    rid = int(token[3:-1])
                except ValueError:
                    continue
                role = ctx.guild.get_role(rid)
                if role:
                    role_ids.append(role.id)
                continue

            # numeric id
            if token.isdigit():
                rid = int(token)
                role = ctx.guild.get_role(rid)
                if role:
                    role_ids.append(role.id)
                continue

            # try matching by name (case-insensitive)
            role = discord.utils.find(lambda r: r.name.lower() == token.lower(), ctx.guild.roles)
            if role:
                role_ids.append(role.id)

        if not role_ids:
            embed = discord.Embed(
                title="No Valid Roles Found",
                description=("I couldn't resolve any of the provided roles to existing guild roles. "
                             "Provide role mentions, IDs, or exact role names."),
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed)
            return

        # Remove duplicates while preserving order
        seen = set()
        unique_role_ids = []
        for rid in role_ids:
            if rid not in seen:
                seen.add(rid)
                unique_role_ids.append(rid)

        # Update DB
        self.db.modify(position_id, "roles_given", unique_role_ids)

        # Build success embed
        role_mentions = ", ".join([f"<@&{r}>" for r in unique_role_ids])
        embed = discord.Embed(
            title="Roles Updated",
            description=f"Roles to be given for application position '{position['name']}' (ID: {position_id}) have been updated.",
            colour=discord.Color.green()
        )
        embed.add_field(name="Roles Set", value=role_mentions, inline=False)
        await ctx.respond(embed=embed)

    @perms_util.has_permission("manage_applications")
    @appsmanage_commands.command(name="set_acceptance_message", description="Set the acceptance message for an application position.")
    async def set_acceptance_message(self, ctx: discord.ApplicationContext, position_id: int, *, message: str):
        """Set the acceptance message for an application position."""
        position = self.db.get_position(position_id)
        if not position:
            embed = discord.Embed(
                title="Position Not Found",
                description=f"No application position found with ID {position_id}.",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed)
            return

        self.db.modify(position_id, "acceptance_message", message)
        embed = discord.Embed(
            title="Acceptance Message Updated",
            description=f"Acceptance message for application position '{position['name']}' (ID: {position_id}) has been updated.",
            colour=discord.Color.green()
        )
        await ctx.respond(embed=embed)

    @perms_util.has_permission("manage_applications")
    @appsmanage_commands.command(name="set_rejection_message", description="Set the rejection message for an application position.")
    async def set_rejection_message(self, ctx: discord.ApplicationContext, position_id: int, *, message: str):
        """Set the rejection message for an application position."""
        position = self.db.get_position(position_id)
        if not position:
            embed = discord.Embed(
                title="Position Not Found",
                description=f"No application position found with ID {position_id}.",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed)
            return

        self.db.modify(position_id, "rejection_message", message)
        embed = discord.Embed(
            title="Rejection Message Updated",
            description=f"Rejection message for application position '{position['name']}' (ID: {position_id}) has been updated.",
            colour=discord.Color.green()
        )
        await ctx.respond(embed=embed)

    @perms_util.has_permission("manage_applications")
    @appsmanage_commands.command(name="approve", description="Approve an application, notify the applicant, and assign configured roles.")
    async def approve(self, ctx: discord.ApplicationContext, application_id: int):
        """Approve a submitted application by ID: set status to 'accepted', assign roles, DM the applicant, and log to the applications channel."""
        # Fetch the application
        app = self.db.get_application(application_id)
        if not app:
            embed = discord.Embed(title="Application Not Found", description=f"No application found with ID {application_id}.", colour=discord.Color.red())
            await ctx.respond(embed=embed, ephemeral=True)
            return

        # Only allow approving submitted applications
        status = app.get('status', '')
        if status in ('accepted', 'rejected', 'withdrawn'):
            embed = discord.Embed(title="Already Processed", description=f"Application ID {application_id} has status '{status}' and cannot be approved.", colour=discord.Color.orange())
            await ctx.respond(embed=embed, ephemeral=True)
            return

        # Update DB status first
        updated = self.db.set_application_status(application_id, 'accepted')
        if not updated:
            embed = discord.Embed(title="Failed to Update", description="Failed to mark the application as accepted. It may have been processed already.", colour=discord.Color.red())
            await ctx.respond(embed=embed, ephemeral=True)
            return

        # Gather position info and target user
        position = self.db.get_position(app['position_id'])
        position_name = position['name'] if position else f"ID {app['position_id']}"
        user_id = app['user_id']

        # Try to find the member in the guild
        member = ctx.guild.get_member(user_id) if ctx.guild else None
        if member is None:
            try:
                member = await ctx.guild.fetch_member(user_id)
            except Exception:
                member = None

        roles_assigned = []
        roles_failed = []

        # Assign roles if member is present and position defines roles_given
        roles_to_give = position.get('roles_given', []) if position else []
        if member and roles_to_give:
            # Resolve Role objects and filter out any that the bot cannot assign
            bot_member = ctx.guild.me
            assignable = []
            for rid in roles_to_give:
                role = ctx.guild.get_role(rid)
                if not role:
                    roles_failed.append((rid, 'role_not_found'))
                    continue
                # Check role hierarchy: bot must be higher than the role to assign it
                try:
                    if bot_member and role.position >= bot_member.top_role.position:
                        roles_failed.append((rid, 'role_above_bot'))
                        continue
                except Exception:
                    # If we cannot determine positions, attempt assignment and catch exceptions
                    pass
                assignable.append(role)

            if assignable:
                try:
                    await member.add_roles(*assignable, reason=f"Application approved (ID {application_id})")
                    roles_assigned = [r.id for r in assignable]
                except discord.Forbidden:
                    # Permission error assigning roles
                    for r in assignable:
                        roles_failed.append((r.id, 'forbidden'))
                except Exception:
                    for r in assignable:
                        roles_failed.append((r.id, 'failed'))

        # Prepare acceptance message
        acceptance_message = position.get('acceptance_message') if position else None
        dm_sent = False
        dm_failed = False
        dm_error = None
        # Build an embed for the DM or channel post
        acceptance_embed = discord.Embed(title="Application Approved", colour=discord.Color.green())
        acceptance_embed.add_field(name="Position", value=position_name, inline=False)
        acceptance_embed.add_field(name="Application ID", value=str(application_id), inline=True)
        acceptance_embed.add_field(name="Staff", value=f"{ctx.author}", inline=True)
        if acceptance_message:
            acceptance_embed.add_field(name="Message", value=acceptance_message, inline=False)

        # Try to DM the user
        try:
            if member:
                await member.send(embed=acceptance_embed)
                dm_sent = True
            else:
                # Try to DM by user id via user object
                user = await self.bot.fetch_user(user_id)
                if user:
                    await user.send(embed=acceptance_embed)
                    dm_sent = True
        except discord.Forbidden as e:
            dm_failed = True
            dm_error = 'forbidden'
        except Exception as e:
            dm_failed = True
            dm_error = 'failed'

        # If DM failed, attempt to post in the applications channel
        apps_channel_posted = False
        try:
            guild = ctx.guild if ctx.guild else (self.bot.guilds[0] if self.bot.guilds else None)
            if (not dm_sent) and guild:
                channel_id = self.db.get_applications_channel(guild.id)
                if channel_id:
                    channel = guild.get_channel(channel_id)
                    if channel:
                        # Build a public embed that mentions the user
                        public_embed = discord.Embed(title="Application Approved", colour=discord.Color.green())
                        public_embed.add_field(name="Applicant", value=f"<@{user_id}> (ID: {user_id})", inline=False)
                        public_embed.add_field(name="Position", value=position_name, inline=True)
                        public_embed.add_field(name="Application ID", value=str(application_id), inline=True)
                        public_embed.add_field(name="Staff", value=f"{ctx.author}", inline=True)
                        if acceptance_message:
                            public_embed.add_field(name="Message", value=acceptance_message, inline=False)
                        if roles_assigned:
                            public_embed.add_field(name="Roles Assigned", value=", ".join([f"<@&{r}>" for r in roles_assigned]), inline=False)
                        if roles_failed:
                            public_embed.add_field(name="Role Assignment Failures", value=", ".join([f"{t[0]} ({t[1]})" for t in roles_failed]), inline=False)
                        await channel.send(embed=public_embed)
                        apps_channel_posted = True
        except Exception:
            # Don't let logging failures block the command
            pass

        # Build response for the invoking staff
        summary = discord.Embed(title="Application Approved", colour=discord.Color.green())
        summary.add_field(name="Application ID", value=str(application_id), inline=True)
        summary.add_field(name="Applicant", value=f"<@{user_id}>", inline=True)
        summary.add_field(name="Position", value=position_name, inline=True)
        if roles_assigned:
            summary.add_field(name="Roles Assigned", value=", ".join([f"<@&{r}>" for r in roles_assigned]), inline=False)
        if roles_failed:
            summary.add_field(name="Role Assignment Failures", value=", ".join([f"{t[0]} ({t[1]})" for t in roles_failed]), inline=False)
        if dm_sent:
            summary.add_field(name="DM", value="Sent to applicant.", inline=True)
        elif dm_failed:
            summary.add_field(name="DM", value=f"Failed to send DM ({dm_error}).", inline=True)
        if apps_channel_posted:
            summary.add_field(name="Posted to Applications Channel", value="Yes", inline=True)

        await ctx.respond(embed=summary)

    @perms_util.has_permission("manage_applications")
    @appsmanage_commands.command(name="reject", description="Reject an application, notify the applicant, and log the rejection.")
    async def reject(self, ctx: discord.ApplicationContext, application_id: int, *, reason: str = None):
        """Reject a submitted application by ID: set status to 'rejected', DM the applicant with rejection_message or provided reason, and log to the applications channel."""
        # Fetch the application
        app = self.db.get_application(application_id)
        if not app:
            embed = discord.Embed(title="Application Not Found", description=f"No application found with ID {application_id}.", colour=discord.Color.red())
            await ctx.respond(embed=embed, ephemeral=True)
            return

        # Only allow rejecting submitted applications
        status = app.get('status', '')
        if status in ('accepted', 'rejected', 'withdrawn'):
            embed = discord.Embed(title="Already Processed", description=f"Application ID {application_id} has status '{status}' and cannot be rejected.", colour=discord.Color.orange())
            await ctx.respond(embed=embed, ephemeral=True)
            return

        # Update DB status to rejected
        updated = self.db.set_application_status(application_id, 'rejected')
        if not updated:
            embed = discord.Embed(title="Failed to Update", description="Failed to mark the application as rejected. It may have been processed already.", colour=discord.Color.red())
            await ctx.respond(embed=embed, ephemeral=True)
            return

        # Gather position info and target user
        position = self.db.get_position(app['position_id'])
        position_name = position['name'] if position else f"ID {app['position_id']}"
        user_id = app['user_id']

        # Try to find the member in the guild
        member = ctx.guild.get_member(user_id) if ctx.guild else None
        if member is None:
            try:
                member = await ctx.guild.fetch_member(user_id)
            except Exception:
                member = None

        # Prepare rejection message
        rejection_message = reason or (position.get('rejection_message') if position else None)
        dm_sent = False
        dm_failed = False
        dm_error = None
        rejection_embed = discord.Embed(title="Application Rejected", colour=discord.Color.red())
        rejection_embed.add_field(name="Position", value=position_name, inline=False)
        rejection_embed.add_field(name="Application ID", value=str(application_id), inline=True)
        rejection_embed.add_field(name="Staff", value=f"{ctx.author}", inline=True)
        if rejection_message:
            truncated = (rejection_message[:1900] + '...') if len(rejection_message) > 1900 else rejection_message
            rejection_embed.add_field(name="Reason", value=truncated, inline=False)

        # Try to DM the user
        try:
            if member:
                await member.send(embed=rejection_embed)
                dm_sent = True
            else:
                user = await self.bot.fetch_user(user_id)
                if user:
                    await user.send(embed=rejection_embed)
                    dm_sent = True
        except discord.Forbidden:
            dm_failed = True
            dm_error = 'forbidden'
        except Exception:
            dm_failed = True
            dm_error = 'failed'

        # If DM failed, attempt to post in the applications channel
        apps_channel_posted = False
        try:
            guild = ctx.guild if ctx.guild else (self.bot.guilds[0] if self.bot.guilds else None)
            if (not dm_sent) and guild:
                channel_id = self.db.get_applications_channel(guild.id)
                if channel_id:
                    channel = guild.get_channel(channel_id)
                    if channel:
                        public_embed = discord.Embed(title="Application Rejected", colour=discord.Color.red())
                        public_embed.add_field(name="Applicant", value=f"<@{user_id}> (ID: {user_id})", inline=False)
                        public_embed.add_field(name="Position", value=position_name, inline=True)
                        public_embed.add_field(name="Application ID", value=str(application_id), inline=True)
                        public_embed.add_field(name="Staff", value=f"{ctx.author}", inline=True)
                        if rejection_message:
                            public_embed.add_field(name="Reason", value=rejection_message, inline=False)
                        await channel.send(embed=public_embed)
                        apps_channel_posted = True
        except Exception:
            # Don't let logging failures block the command
            pass

        # Build response for the invoking staff
        summary = discord.Embed(title="Application Rejected", colour=discord.Color.red())
        summary.add_field(name="Application ID", value=str(application_id), inline=True)
        summary.add_field(name="Applicant", value=f"<@{user_id}>", inline=True)
        summary.add_field(name="Position", value=position_name, inline=True)
        if dm_sent:
            summary.add_field(name="DM", value="Sent to applicant.", inline=True)
        elif dm_failed:
            summary.add_field(name="DM", value=f"Failed to send DM ({dm_error}).", inline=True)
        if apps_channel_posted:
            summary.add_field(name="Posted to Applications Channel", value="Yes", inline=True)

        await ctx.respond(embed=summary)

    @perms_util.has_permission("manage_applications")
    @appsmanage_commands.command(name="appstatus", description="Change an application's status.")
    async def appstatus(self, ctx: discord.ApplicationContext, application_id: int, *, status: str):
        """Change an application's status. Accepts human-friendly status names and maps them to DB values.

        If status is 'On Hold', also posts: "Application <ID> has been placed on hold by <Staff>." to the apps channel (if configured).
        """
        # Normalize input and map to DB statuses (preserve existing 'rejected' value used elsewhere)
        mapping = {
            'pending': 'pending',
            'under review': 'under_review',
            'under_review': 'under_review',
            'accepted': 'accepted',
            'denied': 'rejected',
            'rejected': 'rejected',
            'withdrawn': 'withdrawn',
            'flagged': 'flagged',
            'on hold': 'on_hold',
            'on_hold': 'on_hold'
        }

        key = status.lower().strip()
        db_status = mapping.get(key)
        if not db_status:
            embed = discord.Embed(
                title="Invalid Status",
                description=("Status must be one of: Pending, Under Review, Accepted, Denied, Withdrawn, Flagged, On Hold."),
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed, ephemeral=True)
            return

        # Fetch application
        app = self.db.get_application(application_id)
        if not app:
            embed = discord.Embed(title="Application Not Found", description=f"No application found with ID {application_id}.", colour=discord.Color.red())
            await ctx.respond(embed=embed, ephemeral=True)
            return

        # If status already matches, inform the invoker
        current = app.get('status', '')
        if current == db_status:
            embed = discord.Embed(title="No Change", description=f"Application {application_id} already has status '{status}'.", colour=discord.Color.orange())
            await ctx.respond(embed=embed, ephemeral=True)
            return

        # Update DB
        updated = self.db.set_application_status(application_id, db_status)
        if not updated:
            # set_application_status returns False if row not found or status identical; we already checked identical, so treat as failure
            embed = discord.Embed(title="Update Failed", description="Failed to update the application's status. It may have been processed already.", colour=discord.Color.red())
            await ctx.respond(embed=embed, ephemeral=True)
            return

        # Special behavior for On Hold: post the short message to the apps channel
        if db_status == 'on_hold':
            try:
                guild = ctx.guild if ctx.guild else (self.bot.guilds[0] if self.bot.guilds else None)
                if guild:
                    channel_id = self.db.get_applications_channel(guild.id)
                    if channel_id:
                        channel = guild.get_channel(channel_id)
                        if channel:
                            # Exact message requested by user
                            msg = f"Application {application_id} has been placed on hold by {ctx.author.mention}."
                            await channel.send(msg)
            except Exception:
                # Don't let logging/posting failures block the command response
                pass

        # Respond to staff invoker with confirmation
        pretty = status.title()
        embed = discord.Embed(title="Status Updated", description=f"Application {application_id} status set to {pretty}.", colour=discord.Color.green())
        embed.add_field(name="New Status", value=pretty, inline=True)
        embed.add_field(name="Application ID", value=str(application_id), inline=True)
        await ctx.respond(embed=embed, ephemeral=True)

    @perms_util.has_permission("manage_applications")
    @appsmanage_commands.command(name="flag_app", description="Flag an application, preventing further action until unflagged.")
    async def flag_application(self, ctx: discord.ApplicationContext, application_id: int):
        """Flag an application as needing attention. This sets the status to 'flagged' and prevents acceptance/rejection until unflagged."""
        # Fetch the application
        app = self.db.get_application(application_id)
        if not app:
            embed = discord.Embed(title="Application Not Found", description=f"No application found with ID {application_id}.", colour=discord.Color.red())
            await ctx.respond(embed=embed, ephemeral=True)
            return

        # Only allow flagging submitted applications
        status = app.get('status', '')
        if status == 'flagged':
            embed = discord.Embed(title="Already Flagged", description=f"Application ID {application_id} is already flagged.", colour=discord.Color.orange())
            await ctx.respond(embed=embed, ephemeral=True)
            return

        # Update DB status to flagged
        updated = self.db.set_application_status(application_id, 'flagged')
        if not updated:
            embed = discord.Embed(title="Failed to Update", description="Failed to flag the application. It may have been processed already.", colour=discord.Color.red())
            await ctx.respond(embed=embed, ephemeral=True)
            return

        embed = discord.Embed(title="Application Flagged", description=f"Application ID {application_id} has been flagged. It cannot be processed further until unflagged.", colour=discord.Color.green())
        await ctx.respond(embed=embed, ephemeral=True)

    @perms_util.has_permission("manage_applications")
    @appsmanage_commands.command(name="unflag_app", description="Unflag a previously flagged application.")
    async def unflag_application(self, ctx: discord.ApplicationContext, application_id: int):
        """Unflag a previously flagged application, allowing normal processing."""
        # Fetch the application
        app = self.db.get_application(application_id)
        if not app:
            embed = discord.Embed(title="Application Not Found", description=f"No application found with ID {application_id}.", colour=discord.Color.red())
            await ctx.respond(embed=embed, ephemeral=True)
            return

        # Only allow unflagging flagged applications
        status = app.get('status', '')
        if status != 'flagged':
            embed = discord.Embed(title="Not Flagged", description=f"Application ID {application_id} is not flagged and cannot be unflagged.", colour=discord.Color.orange())
            await ctx.respond(embed=embed, ephemeral=True)
            return

        # Update DB status to submitted (or previous status)
        updated = self.db.set_application_status(application_id, 'submitted')
        if not updated:
            embed = discord.Embed(title="Failed to Update", description="Failed to unflag the application. It may have been processed already.", colour=discord.Color.red())
            await ctx.respond(embed=embed, ephemeral=True)
            return

        embed = discord.Embed(title="Application Unflagged", description=f"Application ID {application_id} has been unflagged and can be processed normally.", colour=discord.Color.green())
        await ctx.respond(embed=embed, ephemeral=True)

    # ----- New: user-level flagging commands -----
    @perms_util.has_permission("manage_applications")
    @appsmanage_commands.command(name="flag", description="Flag a user to auto-ping staff if they apply again on any application.")
    async def flag_user(self, ctx: discord.ApplicationContext, user: discord.User, *, reason: str = None):
        """Flag a user so staff will be pinged when they submit future applications."""
        try:
            self.db.flag_user(user.id, ctx.author.id, reason, guild_id=ctx.guild.id if ctx.guild else None)
            embed = discord.Embed(title="User Flagged", description=f"Flagged {user} (ID: {user.id}). Staff will be pinged if they re-apply.", colour=discord.Color.green())
            if reason:
                truncated = (reason[:1900] + '...') if len(reason) > 1900 else reason
                embed.add_field(name="Reason", value=truncated, inline=False)
            await ctx.respond(embed=embed, ephemeral=True)
        except Exception:
            embed = discord.Embed(title="Flag Failed", description="Failed to flag the user. Check logs.", colour=discord.Color.red())
            await ctx.respond(embed=embed, ephemeral=True)

    @perms_util.has_permission("manage_applications")
    @appsmanage_commands.command(name="unflag", description="Remove a user's application flag so staff won't be auto-pinged.")
    async def unflag_user(self, ctx: discord.ApplicationContext, user: discord.User):
        """Remove a user's application flag."""
        try:
            removed = self.db.unflag_user(user.id)
            if removed:
                embed = discord.Embed(title="User Unflagged", description=f"Removed flag for {user} (ID: {user.id}).", colour=discord.Color.green())
            else:
                embed = discord.Embed(title="Not Flagged", description=f"{user} (ID: {user.id}) was not flagged.", colour=discord.Color.orange())
            await ctx.respond(embed=embed, ephemeral=True)
        except Exception:
            embed = discord.Embed(title="Unflag Failed", description="Failed to remove the user's flag. Check logs.", colour=discord.Color.red())
            await ctx.respond(embed=embed, ephemeral=True)

    @perms_util.has_permission("manage_applications")
    @appsmanage_commands.command(name="history", description="Displays all past applications (paged).")
    async def history(self, ctx: discord.ApplicationContext, page: int = 1):
        """Display ALL past applications including all statuses, paginated."""
        try:
            total = self.db.get_applications_count()
        except Exception:
            embed = discord.Embed(title="Database Error", description="Failed to fetch applications. Check logs.", colour=discord.Color.red())
            await ctx.respond(embed=embed, ephemeral=True)
            return

        if total == 0:
            embed = discord.Embed(title="No Applications", description="There are no applications on record.", colour=discord.Color.orange())
            await ctx.respond(embed=embed, ephemeral=True)
            return

        per_page = 4
        total_pages = (total - 1) // per_page + 1

        if page < 1 or page > total_pages:
            embed = discord.Embed(
                title="Page Not Found",
                description=f"Page {page} is out of range. There {'is' if total_pages==1 else 'are'} {total_pages} page{'s' if total_pages!=1 else ''} available.",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed, ephemeral=True)
            return

        # Fetch page of applications
        offset = (page - 1) * per_page
        try:
            apps = self.db.get_applications(per_page, offset)
        except Exception:
            embed = discord.Embed(title="Database Error", description="Failed to fetch applications. Check logs.", colour=discord.Color.red())
            await ctx.respond(embed=embed, ephemeral=True)
            return

        embed = discord.Embed(title="Applications History", colour=discord.Color.blue())
        # Each field shows a compact summary for an application
        for app in apps:
            app_id = app.get('application_id')
            uid = app.get('user_id')
            pos_id = app.get('position_id')
            status = app.get('status', 'unknown')
            submitted = app.get('submission_date')
            answers = app.get('answers') or "(No content)"
            # Truncate answers to avoid embed limits
            if len(answers) > 800:
                answers = answers[:800] + '...'

            # Resolve position name if possible
            position = self.db.get_position(pos_id)
            position_name = position['name'] if position else f"ID {pos_id}"

            name = f"App #{app_id} — {status.capitalize()}"
            value = (f"Applicant: <@{uid}> (ID: {uid})\n"
                     f"Position: {position_name} (ID: {pos_id})\n"
                     f"Submitted: {submitted}\n\n"
                     f"Answers:\n{answers}")
            embed.add_field(name=name, value=value, inline=False)

        embed.set_footer(text=f"Page {page}/{total_pages} — {total} application{'s' if total != 1 else ''}")
        await ctx.respond(embed=embed, ephemeral=True)

    @perms_util.has_permission("manage_applications")
    @appsmanage_commands.command(name="blacklist", description="Blacklist a user from submitting applications.")
    async def blacklist_user(self, ctx: discord.ApplicationContext, user: discord.User, *, reason: str = None):
        """Blacklist a user from submitting applications."""
        try:
            self.db.blacklist_user(user.id, ctx.author.id, reason)
            embed = discord.Embed(title="User Blacklisted", description=f"Blacklisted {user} (ID: {user.id}). They cannot submit applications.", colour=discord.Color.green())
            if reason:
                truncated = (reason[:1900] + '...') if len(reason) > 1900 else reason
                embed.add_field(name="Reason", value=truncated, inline=False)
            await ctx.respond(embed=embed, ephemeral=True)
        except Exception:
            embed = discord.Embed(title="Blacklist Failed", description="Failed to blacklist the user. Check logs.", colour=discord.Color.red())
            await ctx.respond(embed=embed, ephemeral=True)

        dm_embed = discord.Embed(
            title="You Have Been Blacklisted",
            description="You have been blacklisted from submitting applications.",
            colour=discord.Color.red()
        )
        if reason:
            truncated = (reason[:1900] + '...') if len(reason) > 1900 else reason
            dm_embed.add_field(name="Reason", value=truncated, inline=False)
        try:
            await user.send(embed=dm_embed)
        except Exception:
            # Ignore DM failures
            pass

    @perms_util.has_permission("manage_applications")
    @appsmanage_commands.command(name="unblacklist", description="Remove a user's blacklist status.")
    async def unblacklist_user(self, ctx: discord.ApplicationContext, user: discord.User):
        """Remove a user's blacklist status."""
        try:
            removed = self.db.unblacklist_user(user.id)
            if removed:
                embed = discord.Embed(title="User Unblacklisted", description=f"Removed blacklist for {user} (ID: {user.id}).", colour=discord.Color.green())
            else:
                embed = discord.Embed(title="Not Blacklisted", description=f"{user} (ID: {user.id}) was not blacklisted.", colour=discord.Color.orange())
            await ctx.respond(embed=embed, ephemeral=True)
        except Exception:
            embed = discord.Embed(title="Unblacklist Failed", description="Failed to remove the user's blacklist. Check logs.", colour=discord.Color.red())
            await ctx.respond(embed=embed, ephemeral=True)


# Setup function to add the cog to the bot
def setup(bot):
    bot.add_cog(Applications(bot))

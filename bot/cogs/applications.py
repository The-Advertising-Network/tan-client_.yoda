#######################################################
# Applications Cog - Provides applications-related commands for the bot
#######################################################
import tempfile, os, time
import discord
import json
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
        """Listen for DMs from users applying for positions and handle per-question answers.

        Behavior:
        - Only handles direct messages (DMChannel) from non-bot users.
        - Checks for an in-progress application for the user (started via /apply).
        - Each message from the user in DMs is treated as the next answer. The bot
          will send the next question (if any) after receiving an answer, or submit
          when all questions are answered.
        - The application must be submitted within 24 hours (enforced by the DB methods).
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

        # Build answer text from message content and attachments
        answers = message.content or ""
        if message.attachments:
            attachments_text = "\n\nAttachments:\n" + "\n".join(a.url for a in message.attachments)
            answers = (answers + attachments_text).strip()

        # Append answer to in-progress application using new DB helper
        res = self.db.add_answer_to_in_progress(message.author.id, answers)
        if not res or not res[0]:
            # Failure -- determine reason
            reason = res[1] if isinstance(res, tuple) and len(res) > 1 else 'unknown'
            if reason == 'no_in_progress':
                try:
                    embed = discord.Embed(
                        title="No In-Progress Application",
                        description="You don't have an in-progress application. Start one with `/application apply <position_name>` in the server.",
                        colour=discord.Color.orange()
                    )
                    await message.channel.send(embed=embed)
                except discord.Forbidden:
                    pass
                return
            if reason == 'invalid_in_progress_state':
                try:
                    embed = discord.Embed(
                        title="Application Error",
                        description="Your in-progress application is in an unexpected state. Please contact staff.",
                        colour=discord.Color.red()
                    )
                    await message.channel.send(embed=embed)
                except discord.Forbidden:
                    pass
                return
            # Generic failure
            try:
                embed = discord.Embed(
                    title="Failed to Record Answer",
                    description="Failed to record your answer. Please contact staff.",
                    colour=discord.Color.red()
                )
                await message.channel.send(embed=embed)
            except discord.Forbidden:
                pass
            return

        # res is (True, completed, application_id, position_id, next_question, final_answers)
        _, completed, application_id, position_id, next_question, final_answers = res

        # If not completed, send the next question or a confirmation
        if not completed:
            if next_question:
                # Compute which question number we're on by inspecting the stored in-progress answers
                try:
                    in_prog = self.db.get_in_progress_application(message.author.id)
                    answered_count = 0
                    if in_prog and in_prog.get('answers'):
                        raw = in_prog.get('answers')
                        try:
                            parsed = json.loads(raw)
                            if isinstance(parsed, dict) and isinstance(parsed.get('answers'), list):
                                answered_count = len(parsed.get('answers'))
                        except Exception:
                            # If not JSON, fallback to treating as a single answered blob
                            answered_count = 1
                    question_num = answered_count + 1
                except Exception:
                    question_num = None

                # Send a single embed that includes the question number (if available) and the question text
                try:
                    q_title = f"Question {question_num}" if question_num else "Next Question"
                    q_embed = discord.Embed(
                        title=q_title,
                        description=next_question,
                        colour=discord.Color.blue()
                    )
                    await message.channel.send(embed=q_embed)
                except discord.Forbidden:
                    pass
                return
            else:
                # No next question found (shouldn't happen) - tell user to wait
                try:
                    embed = discord.Embed(
                        title="Answer Recorded",
                        description="Recorded your answer. Awaiting next question (if any).",
                        colour=discord.Color.blue()
                    )
                    await message.channel.send(embed=embed)
                except discord.Forbidden:
                    pass
                return

        # Completed submission - notify staff channel and user
        # Find the guild (this bot is intended for a single server)
        guild = None
        if self.bot.guilds:
            guild = self.bot.guilds[0]
        if not guild:
            try:
                embed = discord.Embed(
                    title="Submission Received",
                    description="Your application has been submitted, but I couldn't find the server to post it to. Please contact staff.",
                    colour=discord.Color.orange()
                )
                await message.channel.send(embed=embed)
            except discord.Forbidden:
                pass
            return

        # Get the configured applications channel for the guild
        channel_id = self.db.get_applications_channel(guild.id)
        if not channel_id:
            try:
                embed = discord.Embed(
                    title="Submission Received",
                    description="Your application has been submitted, but no applications channel is configured. Please contact staff.",
                    colour=discord.Color.orange()
                )
                await message.channel.send(embed=embed)
            except discord.Forbidden:
                pass
            return

        channel = guild.get_channel(channel_id)
        if not channel:
            try:
                embed = discord.Embed(
                    title="Submission Received",
                    description=f"Your application has been submitted, but the configured applications channel (ID {channel_id}) could not be found in the server. Please ping a management member.",
                    colour=discord.Color.orange()
                )
                await message.channel.send(embed=embed)
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
        truncated = (final_answers[:1900] + '...') if final_answers and len(final_answers) > 1900 else (final_answers or "(No content)")
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
                confirm_embed = discord.Embed(
                    title="Application Submitted",
                    description="Your application has been submitted to staff for review. Thank you!",
                    colour=discord.Color.green()
                )
                await message.channel.send(embed=confirm_embed)
            except discord.Forbidden:
                pass
        except discord.Forbidden:
            pass
        except Exception as e:
            try:
                embed = discord.Embed(
                    title="Submission Failed",
                    description="An error occurred while submitting your application. Please contact staff.",
                    colour=discord.Color.red()
                )
                await message.channel.send(embed=embed)
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
                name=f"{pos['name'].title()}",
                value=f"Description: {pos.get('description', 'No description provided.')}",
                inline=False
            )

        embed.set_footer(text=f"Page {page}/{total_pages} — {total} position{'s' if total!=1 else ''}")
        await ctx.respond(embed=embed)

    @application_commands.command(name="apply", description="Apply for an application position.")
    async def apply(self, ctx: discord.ApplicationContext, position_name: str):
        """Apply for an application position by name.

        This starts an in-progress application and sends the first question as a DM
        (rather than all questions at once). The user's subsequent DM messages will
        be treated as answers to each question in turn.
        """
        if self.db.is_user_blacklisted(ctx.author.id):
            embed = discord.Embed(
                title="Application Denied",
                description="You are blacklisted from applying for positions.",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed, ephemeral=True)
            return

        # Normalize and look up by name (positions are stored lowercased by create)
        lookup_name = position_name.lower()
        position = self.db.get_position(lookup_name)
        if not position:
            embed = discord.Embed(
                title="Position Not Found",
                description=f"No application position found with the name '{position_name}'. Use `/application list` to see available positions.",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed, ephemeral=True)
            return

        if not position.get('open', False):
            embed = discord.Embed(
                title="Application Closed",
                description=f"The application position '{position['name']}' (ID: {position['position_id']}) is currently closed for submissions.",
                colour=discord.Color.orange()
            )
            await ctx.respond(embed=embed, ephemeral=True)
            return

        # Start application process and send the first question only
        try:
            # Start the in-progress application using the resolved position_id
            app_id = self.db.start_application(user_id=ctx.author.id, position_id=position['position_id'])
            questions = position.get('questions', [])
            if not questions:
                # If there are no questions, inform the user and leave in-progress as empty; they can send a message to submit
                try:
                    await ctx.author.send(embed=discord.Embed(title=f"Application for '{position['name']}'", description="There are no questions for this application. Please send any additional information you want staff to see, or wait for staff to contact you.", colour=discord.Color.blue()))
                except discord.Forbidden:
                    embed = discord.Embed(
                        title="DM Failed",
                        description="I was unable to send you a DM. Please ensure your privacy settings allow DMs from server members and try again.",
                        colour=discord.Color.red()
                    )
                    await ctx.respond(embed=embed, ephemeral=True)
                    return
            else:
                # Send only the first question
                first_q = questions[0]
                dm_embed = discord.Embed(
                    title=f"Application for '{position['name']}'",
                    description="You have initiated the application process. Please answer the following question. Reply in this DM with your answer; the bot will send the next question.",
                    colour=discord.Color.blue()
                )
                dm_embed.add_field(name="Question 1", value=first_q, inline=False)
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

            embed = discord.Embed(
                title="Application Process Started",
                description=(f"You have started the application process for '{position['name'].title()}'. Please check your DMs and reply with your answer to Question 1 — the bot will send the next question. You have 24 hours to complete the application."),
                colour=discord.Color.green()
            )
            await ctx.respond(embed=embed, ephemeral=True)
        except discord.Forbidden:
            embed = discord.Embed(
                title="DM Failed",
                description="I was unable to send you a DM. Please ensure your privacy settings allow DMs from server members and try again.",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed, ephemeral=True)
            return

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

    @perms_util.has_permission("manage_applications")
    @appsmanage_commands.command(name="get_file", description="Provides a copy of the applications database file.")
    async def get_file(self, ctx: discord.ApplicationContext):
        """Provides a copy of the applications database file."""
        db_path = self.db.db_path
        try:
            await ctx.respond("Here is the applications database file:", file=discord.File(db_path))
        except Exception as e:
            embed = discord.Embed(
                title="Failed to Send Database",
                description=f"An error occurred while sending the database file: {e}",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed)

    @perms_util.has_permission("manage_applications")
    @appsmanage_commands.command(name="put_file", description="Replace the applications database with an uploaded file.")
    async def put_file(self, ctx: discord.ApplicationContext, file: discord.Attachment):
        """Replace the applications database with an uploaded file."""
        if not file.filename.endswith('.db'):
            embed = discord.Embed(
                title="Invalid File",
                description="The uploaded file must be a .db file.",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed)
            return

        try:
            # Download the file into a temporary path first
            file_bytes = await file.read()
            tmp_dir = tempfile.gettempdir()
            tmp_path = os.path.join(tmp_dir, f"uploaded_applications_{int(time.time())}.db")
            with open(tmp_path, 'wb') as f:
                f.write(file_bytes)

            # Validate schema before replacing the live database
            valid, reason = self.db.is_valid_database(tmp_path)
            if not valid:
                # Remove temp file and report
                try:
                    os.remove(tmp_path)
                except Exception as e:
                    print("Warning: failed to remove temporary uploaded database file.", e)
                embed = discord.Embed(
                    title="Invalid Database",
                    description=f"The uploaded database does not match the required schema: {reason}",
                    colour=discord.Color.red()
                )
                await ctx.respond(embed=embed)
                return

            # Replace the live database file. Try atomic replace first; if that
            # fails due to cross-device move (EXDEV on POSIX or WinError 17 on
            # Windows) fall back to copying the file.
            try:
                backup_path = self.db.db_path + '.bak'
                try:
                    if os.path.exists(self.db.db_path):
                        os.replace(self.db.db_path, backup_path)
                except Exception as e:
                    # best-effort; ignore backup failures
                    print("Warning: failed to backup temporary database file.", e)

                try:
                    # Attempt atomic replace
                    os.replace(tmp_path, self.db.db_path)
                except OSError as e_replace:
                    # Detect cross-device / different-filesystem error and fallback
                    import errno, shutil
                    is_exdev = False
                    if hasattr(e_replace, 'errno') and e_replace.errno == errno.EXDEV:
                        is_exdev = True
                    if hasattr(e_replace, 'winerror') and getattr(e_replace, 'winerror') == 17:
                        is_exdev = True

                    if is_exdev:
                        try:
                            shutil.copy2(tmp_path, self.db.db_path)
                            # remove the tmp file now it's copied
                            try:
                                os.remove(tmp_path)
                            except Exception as e_remove:
                                print("Warning: failed to remove temporary uploaded database file after copy.", e_remove)
                        except Exception as e_copy:
                            # Attempt to restore backup if copy failed
                            try:
                                if os.path.exists(backup_path):
                                    os.replace(backup_path, self.db.db_path)
                            except Exception as e_restore:
                                print("Warning: failed to restore database from backup after failed copy.", e_restore)
                            raise e_copy from e_replace
                    else:
                        # Not a cross-device error - re-raise to be handled below
                        raise
            except Exception as e:
                print("Error replacing database file:", e)
                embed = discord.Embed(
                    title="Failed to Replace Database",
                    description=f"An error occurred while replacing the database file: {e}",
                    colour=discord.Color.red()
                )
                await ctx.respond(embed=embed)
                return

            embed = discord.Embed(
                title="Database Replaced",
                description="The applications database has been successfully replaced with the uploaded file.",
                colour=discord.Color.green()
            )
            await ctx.respond(embed=embed)
        except Exception as e:
            print("Error processing uploaded database file:", e)
            embed = discord.Embed(
                title="Failed to Replace Database",
                description=f"An error occurred while replacing the database file: {e}",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed)

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
        # Enforce unique position names (case-insensitive).
        application_name = application_name.lower()
        existing_positions = self.db.get_position(application_name)
        if existing_positions:
            embed = discord.Embed(
                title="Creation Failed",
                description=f"An application position with the name '{application_name}' already exists. Choose a unique name.",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed, ephemeral=True)
            return

        # Add position to database and get its ID
        position_id = self.db.add_position(application_name)
        embed = discord.Embed(
            title="Application Created",
            description=f"Application position '{application_name}' created with ID {position_id}.",
            colour=discord.Color.green()
        )
        await ctx.respond(embed=embed)

    @perms_util.has_permission("manage_roles")
    @appsmanage_commands.command(name="delete", description="Delete an existing application position.")
    async def delete(self, ctx: discord.ApplicationContext, application_name: str):
        """Delete an existing application position by name. If multiple positions share the name, the command will ask you to disambiguate by ID."""
        lookup_name = application_name.lower()
        positions = self.db.get_position(lookup_name)
        if not positions:
            embed = discord.Embed(
                title="Position Not Found",
                description=f"No application position found with the name '{application_name}'.",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed)
            return

        if len(positions) > 1:
            # Ambiguous — ask the invoker to use the ID to delete
            duplicate_list = '\n'.join([f"ID {p['position_id']} — {p['name']}" for p in positions])
            embed = discord.Embed(
                title="Multiple Positions Found",
                description=(f"Multiple positions match the name '{application_name}'. Please re-run this command using the position's ID to delete the intended one.\n\n{duplicate_list}"),
                colour=discord.Color.orange()
            )
            await ctx.respond(embed=embed)
            return

        position = positions[0]
        position_id = position['position_id']

        self.db.remove_position(position_id)
        embed = discord.Embed(
            title="Application Deleted",
            description=f"Application position '{position['name']}' (ID: {position_id}) has been deleted.",
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

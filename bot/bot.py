#######################################################
# Bot module - Main entry point for the bot.
#######################################################
import os
import logging
import traceback
import sys

import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv

# Ensure the project root (parent of this file) is on sys.path so `import bot.*` works
# This makes running `python bot.py` behave similarly to `python -m bot.bot` for imports.
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Load environment variables from .env file
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Ensure an asyncio event loop is available (fixes "There is no current event loop in thread 'MainThread'" on newer Python)
try:
    # get_running_loop raises RuntimeError if there is no current loop
    asyncio.get_running_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

# Set up -> Bot client
intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)

# Load extensions (cogs)
def load_extensions():
    extensions = ['cogs.moderation', 'cogs.economy', 'cogs.config', 'cogs.applications']
    for ext in extensions:
        bot.load_extension(ext)
    logging.info("Extensions loaded:" + ", ".join(extensions))

# Event: on_ready - Called when the bot is online & ready
@bot.event
async def on_ready():
    logging.info(f'Logged in as {bot.user} (ID: {bot.user.id})')
    try:
        await bot.sync_commands()
        logging.info('Slash commands synced successfully.')
    except Exception as e:
        logging.error(f'Failed to sync slash commands: {e}')
    logging.info('------')

def _error_embed(title: str, description: str, colour: discord.Color = discord.Color.dark_red()) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, colour=colour)
    return embed

@bot.event
async def on_command_error(ctx: commands.Context, error: Exception) -> None:
    # Let command-level handlers run first
    if ctx.command and ctx.command.has_error_handler():
        return

    # Unwrap CommandInvokeError to get original exception
    if isinstance(error, commands.CommandInvokeError) and getattr(error, "original", None):
        error = error.original

    # Ignore unknown commands
    if isinstance(error, commands.CommandNotFound):
        return

    # Build contextual embed responses
    if isinstance(error, commands.MissingPermissions):
        perms = ", ".join(error.missing_permissions) if getattr(error, "missing_permissions", None) else "required permissions"
        embed = _error_embed("Missing Permissions", f":x: You lack permission(s): {perms}", discord.Color.orange())
    elif isinstance(error, commands.MissingRequiredArgument):
        embed = _error_embed("Missing Argument", f":x: Missing required argument `{error.param.name}`.")
    elif isinstance(error, commands.BadArgument):
        embed = _error_embed("Bad Argument", ":x: One or more arguments are invalid.")
    elif isinstance(error, commands.NotOwner):
        embed = _error_embed("Not Owner", ":x: Only the bot owner can run this command.")
    elif isinstance(error, commands.CheckFailure):
        # Friendly message for failed checks (permissions / custom checks).
        # If the CheckFailure carries a message, show it to the user; otherwise, show a generic message.
        message = str(error) if str(error) else ":x: You do not have permission to run this command."
        embed = _error_embed("Insufficient Permissions", message, discord.Color.orange())
    elif isinstance(error, discord.Forbidden):
        embed = _error_embed("Insufficient Bot Permissions", ":x: I do not have permission to perform that action.")
    else:
        # Log unexpected errors and avoid leaking internals to users
        tb = "".join(traceback.format_exception(type(error), error, getattr(error, "__traceback__", None)))
        logging.error("Unhandled command error: %s\n%s", error, tb)
        embed = _error_embed("Error", ":interrobang: An internal error occurred. Please view the console for more information.")

    # Try to send a reply. If this is an ApplicationContext (slash command) attempt to use respond so replies are visible to the user.
    try:
        if hasattr(ctx, "respond") and callable(getattr(ctx, "respond")):
            # Use ephemeral reply for permission-related errors so it's private to the user.
            ephemeral = isinstance(error, (commands.MissingPermissions, commands.CheckFailure))
            await ctx.respond(embed=embed, ephemeral=ephemeral)
        else:
            await ctx.send(embed=embed)
    except Exception as e:
        logging.exception(f"Failed to send error embed: {e}")


# Main entry point
if __name__ == '__main__':
    load_extensions()
    TOKEN = os.getenv('DISCORD_TOKEN')
    if not TOKEN:
        raise ValueError("DISCORD_TOKEN environment variable not set.")
    bot.run(TOKEN)

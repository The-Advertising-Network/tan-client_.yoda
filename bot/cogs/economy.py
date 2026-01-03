#######################################################
# Economy Cog - Provides economy-related commands for the bot
#######################################################
import discord
from discord.ext import commands

# Import database and permission utilities with robust fallbacks
try:
    # Preferred when running from project root (bot/ is on sys.path)
    from core.database import EconomyDatabase
except Exception:
    # Fallback when package layout uses `bot` as top-level package
    from bot.core.database import EconomyDatabase

try:
    from util import perms as perms_util
except Exception:
    from bot.util import perms as perms_util


# Economy cog
class Economy(commands.Cog):
    economy_admin = discord.SlashCommandGroup("economy_admin", "Economy Admin Commands")
    def __init__(self, bot):
        self.bot = bot
        self.db = EconomyDatabase()

    @commands.slash_command(name='balance', help='Check a user\'s balance.')
    async def balance(self, ctx: discord.ApplicationContext, member: discord.Member = None):
        """Check your current balance."""
        if not member:
            member = ctx.author
        user_id = member.id
        balance = self.db.get_balance(user_id)
        embed = discord.Embed(
            title=f"{member.name}'s Balance",
            description=f"{member.mention} has **{balance}** PTX.",
            colour=discord.Color.green()
        )
        await ctx.respond(embed=embed)

    @commands.slash_command(name='work', help='Earn some PTX by working.')
    async def work(self, ctx: discord.ApplicationContext):
        """Earn some PTX by working."""
        user_id = ctx.author.id
        worked, earned_amount = self.db.try_work(user_id)
        if worked:
            embed = discord.Embed(
                title="Work Successful",
                description=f"You worked hard and earned **{earned_amount}** PTX!",
                colour=discord.Color.green()
            )
        else:
            embed = discord.Embed(
                title="Work Already Claimed",
                description="You have already worked recently. Please try again later.",
                colour=discord.Color.red()
            )
        await ctx.respond(embed=embed)

    @commands.slash_command(name='daily', help='Claim your daily PTX.')
    async def daily(self, ctx: discord.ApplicationContext):
        """Claim your daily PTX."""
        user_id = ctx.author.id
        if self.db.try_daily(user_id):
            embed = discord.Embed(
                title="Daily Claimed",
                description=f"You have claimed your daily reward of **10** PTX!",
                colour=discord.Color.gold()
            )
        else:
            embed = discord.Embed(
                title="Daily Already Claimed",
                description="You have already claimed your daily reward today. Please try again tomorrow.",
                colour=discord.Color.red()
            )
        await ctx.respond(embed=embed)

    @commands.slash_command(name='leaderboard', help='Show the economy leaderboard.')
    async def leaderboard(self, ctx: discord.ApplicationContext, page: int = 1):
        """Show the economy leaderboard."""
        leaderboard_data = self.db.get_leaderboard(page)
        embed = discord.Embed(
            title="Economy Leaderboard",
            colour=discord.Color.blue()
        )
        description = ""
        for rank, (user_id, balance) in enumerate(leaderboard_data, start=(page - 1) * 10 + 1):
            user = self.bot.get_user(user_id)
            username = user.name if user else f"User ID {user_id}"
            description += f"**{rank}. {username}** - {balance} PTX\n"
        embed.description = description
        embed.set_footer(text=f"Page {page}")
        await ctx.respond(embed=embed)

    @commands.slash_command(name='pay', help='Pay a specified amount of PTX to another user.')
    async def pay(self, ctx: discord.ApplicationContext, member: discord.Member, amount: int):
        """Pay a specified amount of PTX to another user.
        Parameters:
            ctx (commands.Context): The context of the command.
            member (discord.Member): The member to whom PTX is to be paid.
            amount (int): The amount of PTX to pay.
        """
        payer_id = ctx.author.id
        payee_id = member.id
        if amount <= 0:
            embed = discord.Embed(
                title="Invalid Amount",
                description="The amount to pay must be greater than zero.",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed)
            return

        if self.db.get_balance(payer_id) < amount:
            embed = discord.Embed(
                title="Insufficient Funds",
                description="You do not have enough PTX to complete this transaction.",
                colour=discord.Color.red()
            )
            await ctx.respond(embed=embed)
            return

        self.db.update_balance(payer_id, -amount)
        self.db.update_balance(payee_id, amount)

        embed = discord.Embed(
            title="Payment Successful",
            description=f"You have paid **{amount}** PTX to {member.mention}.",
            colour=discord.Color.green()
        )
        await ctx.respond(embed=embed)

    # Admin commands - require the manage_economy permission (from data/roleperms.json)
    @perms_util.has_permission("manage_economy")
    @economy_admin.command(name='reset_balance', help='Resets a member\'s balance to zero.')
    async def reset_balance(self, ctx: discord.ApplicationContext, member: discord.Member):
        """Resets a member's balance to zero.
        Parameters:
            ctx (commands.Context): The context of the command.
            member (discord.Member): The member whose balance is to be reset.
        """
        user_id = member.id
        self.db.reset_balance(user_id)
        embed = discord.Embed(
            title="Balance Reset",
            description=f"{member.mention}'s balance has been reset to zero.",
            colour=discord.Color.red()
        )
        await ctx.respond(embed=embed)

    @perms_util.has_permission("manage_economy")
    @economy_admin.command(name='delete_user', help='Deletes a user from the economy database.')
    async def delete_user(self, ctx: discord.ApplicationContext, user_id: int):
        """Deletes a user from the database.
        Parameters:
            ctx (commands.Context): The context of the command.
            user_id (int): The user whose balance is to be deleted.
        """
        self.db.delete_user(user_id)
        embed = discord.Embed(
            title="User Deleted",
            description=f"User with ID {user_id} has been deleted from the economy database.",
            colour=discord.Color.red()
        )
        await ctx.respond(embed=embed)

    @perms_util.has_permission("manage_economy")
    @economy_admin.command(name='add_credits', help='Adds credits to a user\'s balance.')
    async def add_credits(self, ctx: discord.ApplicationContext, member: discord.Member, amount: int):
        """Adds credits to a user's balance.
        Parameters:
            ctx (commands.Context): The context of the command.
            member (discord.Member): The member to whom credits are to be added.
            amount (int): The amount of credits to add.
        """
        user_id = member.id
        self.db.update_balance(user_id, amount)
        embed = discord.Embed(
            title="Credits Added",
            description=f"Added **{amount}** PTX to {member.mention}'s balance.",
            colour=discord.Color.green()
        )
        await ctx.respond(embed=embed)

    @perms_util.has_permission("manage_economy")
    @economy_admin.command(name='remove_credits', help='Removes credits from a user\'s balance.')
    async def remove_credits(self, ctx: discord.ApplicationContext, member: discord.Member, amount: int):
        """Removes credits from a user's balance.
        Parameters:
            ctx (commands.Context): The context of the command.
            member (discord.Member): The member from whom credits are to be removed.
            amount (int): The amount of credits to remove.
        """
        user_id = member.id
        self.db.update_balance(user_id, -amount)
        embed = discord.Embed(
            title="Credits Removed",
            description=f"Removed **{amount}** PTX from {member.mention}'s balance.",
            colour=discord.Color.red()
        )
        await ctx.respond(embed=embed)


# Setup function to add the cog to the bot
def setup(bot):
    bot.add_cog(Economy(bot))

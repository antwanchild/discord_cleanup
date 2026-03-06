import discord
from discord import app_commands
from datetime import datetime

from config import BOT_VERSION, log
from stats import load_stats, reset_stats
from commands import cleanup_group


stats_group = app_commands.Group(name="stats", description="Cleanup statistics commands", parent=cleanup_group)


@stats_group.command(name="view", description="Show cleanup statistics")
@app_commands.checks.has_permissions(administrator=True)
async def stats_view(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    stats = load_stats()
    all_time = stats.get("all_time", {})
    rolling_30 = stats.get("rolling_30", {})
    monthly = stats.get("monthly", {})
    top_channels = sorted(
        all_time.get("channels", {}).items(),
        key=lambda x: x[1]["count"] if isinstance(x[1], dict) else x[1],
        reverse=True
    )[:5]

    embed = discord.Embed(
        title="📊 Cleanup Statistics",
        description=(
            f"🏠 Server: **{interaction.guild.name}**\n\n"
            f"**📅 Last 30 Days** (since {rolling_30.get('reset', 'N/A')})\n"
            f"\u3000🔁 Runs: **{rolling_30.get('runs', 0)}**\n"
            f"\u3000🗑️ Deleted: **{rolling_30.get('deleted', 0)}**\n\n"
            f"**🗓️ This Month** (since {monthly.get('reset', 'N/A')})\n"
            f"\u3000🔁 Runs: **{monthly.get('runs', 0)}**\n"
            f"\u3000🗑️ Deleted: **{monthly.get('deleted', 0)}**\n\n"
            f"**🏆 All Time**\n"
            f"\u3000🔁 Runs: **{all_time.get('runs', 0)}**\n"
            f"\u3000🗑️ Deleted: **{all_time.get('deleted', 0)}**"
        ),
        color=0x9B59B6,
        timestamp=datetime.now()
    )
    if top_channels:
        def ch_display(ch_id, ch_data):
            if isinstance(ch_data, dict):
                return f"`#{ch_data['name']}` — **{ch_data['count']}** deleted"
            return f"`#{ch_id}` — **{ch_data}** deleted"
        embed.add_field(
            name="🏆 Top 5 Channels (All Time)",
            value="\n".join([ch_display(ch_id, ch_data) for ch_id, ch_data in top_channels]),
            inline=False
        )
    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    await interaction.followup.send(embed=embed, ephemeral=True)


@stats_group.command(name="channel", description="Show cleanup stats for a specific channel")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(channel="The channel to show stats for")
async def stats_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    await interaction.response.defer(ephemeral=True)
    stats = load_stats()
    ch_id = str(channel.id)

    lines = []
    for bucket_key, bucket_label in [("all_time", "All Time"), ("monthly", "This Month"), ("rolling_30", "Last 30 Days")]:
        bucket = stats.get(bucket_key, {})
        ch_data = bucket.get("channels", {}).get(ch_id)
        if ch_data:
            count = ch_data["count"] if isinstance(ch_data, dict) else ch_data
        else:
            count = 0
        lines.append(f"**{bucket_label}:** {count} deleted")

    embed = discord.Embed(
        title=f"📊 Stats — #{channel.name}",
        description="\n".join(lines),
        color=0x9B59B6,
        timestamp=datetime.now()
    )
    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    await interaction.followup.send(embed=embed, ephemeral=True)


class StatsResetView(discord.ui.View):
    def __init__(self, scope: str, user: discord.User):
        super().__init__(timeout=30)
        self.scope = scope
        self.user = user

    @discord.ui.button(label="Confirm Reset", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("⛔ Only the person who triggered this reset can confirm it.", ephemeral=True)
            return
        success = reset_stats(self.scope)
        self.stop()
        embed = discord.Embed(
            title="🗑️ Stats Reset" if success else "🗑️ Stats Reset Failed",
            description=f"✅ **{self.scope.capitalize()}** stats have been reset." if success else "⛔ Invalid scope — no stats were changed.",
            color=0x2ECC71 if success else 0xFF0000,
            timestamp=datetime.now()
        )
        embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("⛔ Only the person who triggered this reset can cancel it.", ephemeral=True)
            return
        self.stop()
        log.info(f"Stats reset cancelled by {interaction.user} — scope: {self.scope}")
        embed = discord.Embed(
            title="🗑️ Stats Reset Cancelled",
            description="No stats were changed.",
            color=0x95A5A6,
            timestamp=datetime.now()
        )
        embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
        await interaction.response.edit_message(embed=embed, view=None)

    async def on_timeout(self):
        log.info(f"Stats reset timed out — scope: {self.scope}")


@stats_group.command(name="reset", description="Reset cleanup statistics")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(scope="Which stats to reset")
@app_commands.choices(scope=[
    app_commands.Choice(name="Rolling 30 Days", value="rolling"),
    app_commands.Choice(name="This Month", value="monthly"),
    app_commands.Choice(name="All Time", value="all"),
])
async def stats_reset(interaction: discord.Interaction, scope: app_commands.Choice[str]):
    log.info(f"Stats reset requested by {interaction.user} — scope: {scope.value}")
    embed = discord.Embed(
        title="⚠️ Confirm Stats Reset",
        description=f"Are you sure you want to reset **{scope.name}** stats?\n\nThis cannot be undone.",
        color=0xFFA500,
        timestamp=datetime.now()
    )
    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    await interaction.response.send_message(embed=embed, view=StatsResetView(scope=scope.value, user=interaction.user), ephemeral=True)

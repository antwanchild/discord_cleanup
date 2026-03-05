import discord
from discord import app_commands
from datetime import datetime
import os

import config as cfg
from config import (
    BOT_VERSION, CLEAN_TIMES, DEFAULT_RETENTION, LOG_LEVEL, LOG_MAX_FILES, LOG_DIR, WARN_UNCONFIGURED, log
)
from cleanup import build_channel_map, run_cleanup
from notifications import post_schedule_notification
from stats import load_stats, reset_stats
from utils import get_next_run_str, get_uptime_str, reload_channels, get_bot, update_schedule, update_retention, update_log_level, update_warn_unconfigured


cleanup_group = app_commands.Group(name="cleanup", description="Discord Cleanup Bot commands")
stats_group = app_commands.Group(name="stats", description="Cleanup statistics commands", parent=cleanup_group)


@cleanup_group.command(name="run", description="Trigger a full cleanup run on all configured channels")
@app_commands.checks.has_permissions(administrator=True)
async def cleanup_run(interaction: discord.Interaction):
    bot = get_bot()
    await interaction.response.send_message("🧹 Full cleanup started — report will be posted to the log channel when complete.", ephemeral=True)
    log.info(f"Manual full cleanup triggered by {interaction.user} in #{interaction.channel.name}")
    await run_cleanup(bot, interaction.guild)


@cleanup_group.command(name="channel", description="Trigger cleanup on a specific configured channel")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(channel="The channel to clean up")
async def cleanup_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    bot = get_bot()
    channel_map = build_channel_map(interaction.guild)
    if channel.id not in channel_map:
        await interaction.response.send_message(f"⚠️ `#{channel.name}` is not in your configured channels. Check `channels.yml`.", ephemeral=True)
        return
    await interaction.response.send_message(f"🧹 Cleanup started for `#{channel.name}` — report will be posted to the log channel when complete.", ephemeral=True)
    log.info(f"Manual channel cleanup triggered by {interaction.user} for #{channel.name}")
    await run_cleanup(bot, interaction.guild, single_channel_id=channel.id)


@cleanup_group.command(name="dryrun", description="Preview what would be deleted without actually deleting anything")
@app_commands.checks.has_permissions(administrator=True)
async def cleanup_dryrun(interaction: discord.Interaction):
    bot = get_bot()
    await interaction.response.send_message("🔍 Dry run started — preview report will be posted to the log channel when complete.", ephemeral=True)
    log.info(f"Dry run triggered by {interaction.user} in #{interaction.channel.name}")
    await run_cleanup(bot, interaction.guild, dry_run=True)


@cleanup_group.command(name="reload", description="Reload channels.yml without restarting the bot")
@app_commands.checks.has_permissions(administrator=True)
async def cleanup_reload(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    log.info(f"channels.yml reload triggered by {interaction.user}")
    success, message = reload_channels()
    embed = discord.Embed(
        title="🔄 channels.yml Reloaded" if success else "🔄 Reload Failed",
        description=f"{'✅' if success else '⛔'} {message}",
        color=0x2ECC71 if success else 0xFF0000,
        timestamp=datetime.now()
    )
    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    await interaction.followup.send(embed=embed, ephemeral=True)


@cleanup_group.command(name="version", description="Show bot version and uptime")
@app_commands.checks.has_permissions(administrator=True)
async def cleanup_version(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    embed = discord.Embed(
        title=f"ℹ️ Discord Cleanup Bot v{BOT_VERSION}",
        description=(
            f"⏱️ Uptime: **{get_uptime_str()}**\n"
            f"🐳 Image: `ghcr.io/antwanchild/discord_cleanup:{BOT_VERSION}`\n"
            f"🕐 Scheduled runs: **{', '.join(CLEAN_TIMES)}**"
        ),
        color=0x5865F2,
        timestamp=datetime.now()
    )
    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    await interaction.followup.send(embed=embed, ephemeral=True)


@cleanup_group.command(name="status", description="Show current bot configuration and next scheduled run")
@app_commands.checks.has_permissions(administrator=True)
async def cleanup_status(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    configured_count = 0
    excluded = []
    exclude_ids = {ch["id"] for ch in cfg.raw_channels if ch.get("exclude", False)}

    for ch in cfg.raw_channels:
        if ch.get("exclude", False):
            excluded.append(ch)
            continue
        if ch.get("type") == "category":
            category = interaction.guild.get_channel(ch["id"])
            if category:
                for sub in category.text_channels:
                    if sub.id not in exclude_ids and sub.permissions_for(interaction.guild.me).manage_messages:
                        configured_count += 1
        else:
            discord_channel = interaction.guild.get_channel(ch["id"])
            if discord_channel and discord_channel.category:
                if any(c["id"] == discord_channel.category.id for c in cfg.raw_channels if c.get("type") == "category"):
                    continue
            configured_count += 1

    channel_lines = []
    last_category_days = DEFAULT_RETENTION
    for ch in cfg.raw_channels:
        if ch.get("exclude", False):
            continue
        ch_name = ch.get("name", str(ch["id"]))
        deep = " 🧹" if ch.get("deep_clean") else ""
        if ch.get("type") == "category":
            days = ch.get("days", DEFAULT_RETENTION)
            last_category_days = days
            channel_lines.append(f"📁 **{ch_name}** ({days}d default{deep})")
        else:
            days = ch.get("days", DEFAULT_RETENTION)
            retention = f"{days}d ⚡override" if days != last_category_days else f"{days}d"
            channel_lines.append(f"\u3000`#{ch_name}` — {retention}{deep}")

    embed = discord.Embed(
        title="⚙️ Discord Cleanup Bot — Status",
        description=(
            f"🏠 Server: **{interaction.guild.name}**\n"
            f"📅 Default retention: **{cfg.DEFAULT_RETENTION} days**\n"
            f"🔍 Channels configured: **{configured_count}**\n"
            f"⛔ Channels excluded: **{len(excluded)}**\n"
            f"🕐 Scheduled runs: **{', '.join(cfg.CLEAN_TIMES)}**\n"
            f"⏭️ Next run: **{get_next_run_str()}**\n"
            f"📋 Log level: **{cfg.LOG_LEVEL}**\n"
            f"🗂️ Log retention: **{LOG_MAX_FILES} days**\n"
            f"⚠️ Warn unconfigured: **{'enabled' if cfg.WARN_UNCONFIGURED else 'disabled'}**\n"
            f"⏱️ Uptime: **{get_uptime_str()}**"
        ),
        color=0x5865F2,
        timestamp=datetime.now()
    )
    if channel_lines:
        embed.add_field(
            name="📋 Configured Channels (🧹 = deep clean enabled)",
            value="\n".join(channel_lines),
            inline=False
        )
    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    await interaction.followup.send(embed=embed, ephemeral=True)


# --- Stats Commands ---

@stats_group.command(name="view", description="Show cleanup statistics")
@app_commands.checks.has_permissions(administrator=True)
async def stats_view(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    stats = load_stats()
    all_time = stats.get("all_time", {})
    rolling_30 = stats.get("rolling_30", {})
    monthly = stats.get("monthly", {})
    top_channels = sorted(all_time.get("channels", {}).items(), key=lambda x: x[1], reverse=True)[:5]

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
        embed.add_field(
            name="🏆 Top 5 Channels (All Time)",
            value="\n".join([f"`#{ch}` — **{count}** deleted" for ch, count in top_channels]),
            inline=False
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


@cleanup_group.command(name="logs", description="Download today's log file")
@app_commands.checks.has_permissions(administrator=True)
async def cleanup_logs(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    today = datetime.now().strftime("%Y-%m-%d")
    log_path = os.path.join(LOG_DIR, f"cleanup-{today}.log")

    if not os.path.exists(log_path):
        await interaction.followup.send("⚠️ No log file found for today.", ephemeral=True)
        return

    file_size = os.path.getsize(log_path)
    if file_size > 8 * 1024 * 1024:
        await interaction.followup.send("⚠️ Log file exceeds Discord's 8MB limit — please retrieve it directly from `/config/logs`.", ephemeral=True)
        return

    log.info(f"Log file requested by {interaction.user}")
    await interaction.followup.send(
        content=f"📄 `cleanup-{today}.log`",
        file=discord.File(log_path, filename=f"cleanup-{today}.log"),
        ephemeral=True
    )


schedule_group = app_commands.Group(name="schedule", description="Manage cleanup schedule", parent=cleanup_group)


@cleanup_group.command(name="retention", description="Set the default message retention period in days")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(days="Number of days to retain messages (1-365)")
async def cleanup_retention(interaction: discord.Interaction, days: int):
    await interaction.response.defer(ephemeral=True)
    if not 1 <= days <= 365:
        await interaction.followup.send("⛔ Retention must be between 1 and 365 days.", ephemeral=True)
        return
    old = cfg.DEFAULT_RETENTION
    success, message = update_retention(days)
    embed = discord.Embed(
        title="✅ Retention Updated" if success else "⛔ Retention Update Failed",
        description=f"Default retention changed from **{old} days** to **{days} days**." if success else f"⛔ {message}",
        color=0x2ECC71 if success else 0xFF0000,
        timestamp=datetime.now()
    )
    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    log.info(f"Default retention set to {days} days by {interaction.user}")
    await interaction.followup.send(embed=embed, ephemeral=True)


@cleanup_group.command(name="loglevel", description="Set the log verbosity level")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(level="Log level")
@app_commands.choices(level=[
    app_commands.Choice(name="DEBUG", value="DEBUG"),
    app_commands.Choice(name="INFO", value="INFO"),
    app_commands.Choice(name="WARNING", value="WARNING"),
    app_commands.Choice(name="ERROR", value="ERROR"),
])
async def cleanup_loglevel(interaction: discord.Interaction, level: app_commands.Choice[str]):
    await interaction.response.defer(ephemeral=True)
    old = cfg.LOG_LEVEL
    success, message = update_log_level(level.value)
    embed = discord.Embed(
        title="✅ Log Level Updated" if success else "⛔ Log Level Update Failed",
        description=f"Log level changed from **{old}** to **{level.value}**." if success else f"⛔ {message}",
        color=0x2ECC71 if success else 0xFF0000,
        timestamp=datetime.now()
    )
    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    log.info(f"Log level set to {level.value} by {interaction.user}")
    await interaction.followup.send(embed=embed, ephemeral=True)


@cleanup_group.command(name="warnunconfigured", description="Toggle warnings for unconfigured channels")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(enabled="Enable or disable unconfigured channel warnings")
@app_commands.choices(enabled=[
    app_commands.Choice(name="Enable", value="true"),
    app_commands.Choice(name="Disable", value="false"),
])
async def cleanup_warnunconfigured(interaction: discord.Interaction, enabled: app_commands.Choice[str]):
    await interaction.response.defer(ephemeral=True)
    value = enabled.value == "true"
    old = cfg.WARN_UNCONFIGURED
    success, message = update_warn_unconfigured(value)
    embed = discord.Embed(
        title="✅ Setting Updated" if success else "⛔ Update Failed",
        description=f"Warn unconfigured changed from **{'enabled' if old else 'disabled'}** to **{'enabled' if value else 'disabled'}**." if success else f"⛔ {message}",
        color=0x2ECC71 if success else 0xFF0000,
        timestamp=datetime.now()
    )
    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    log.info(f"WARN_UNCONFIGURED set to {value} by {interaction.user}")
    await interaction.followup.send(embed=embed, ephemeral=True)


@schedule_group.command(name="list", description="Show current cleanup schedule")
@app_commands.checks.has_permissions(administrator=True)
async def schedule_list(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    embed = discord.Embed(
        title="🕐 Cleanup Schedule",
        description=(
            f"🕐 Scheduled runs: **{', '.join(cfg.CLEAN_TIMES)}**\n"
            f"⏭️ Next run: **{get_next_run_str()}**"
        ),
        color=0x5865F2,
        timestamp=datetime.now()
    )
    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    await interaction.followup.send(embed=embed, ephemeral=True)


@schedule_group.command(name="add", description="Add a new scheduled run time")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(time="Time in 24hr format e.g. 12:00")
async def schedule_add(interaction: discord.Interaction, time: str):
    await interaction.response.defer(ephemeral=True)
    current = list(cfg.CLEAN_TIMES)
    if time in current:
        await interaction.followup.send(f"⚠️ `{time}` is already in the schedule.", ephemeral=True)
        return
    old_times = list(current)
    current.append(time)
    current.sort()
    success, message = update_schedule(current)
    embed = discord.Embed(
        title="✅ Schedule Updated" if success else "⛔ Schedule Update Failed",
        description=f"Added `{time}` to schedule.\nNew schedule: **{message}**" if success else f"⛔ {message}",
        color=0x2ECC71 if success else 0xFF0000,
        timestamp=datetime.now()
    )
    if success:
        embed.add_field(name="⏭️ Next run", value=f"**{get_next_run_str()}**", inline=False)
        bot = get_bot()
        await post_schedule_notification(bot, interaction.guild, old_times, current, str(interaction.user))
    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    log.info(f"Schedule add '{time}' by {interaction.user} — {'success' if success else 'failed'}: {message}")
    await interaction.followup.send(embed=embed, ephemeral=True)


@schedule_group.command(name="remove", description="Remove a scheduled run time")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(time="Time to remove e.g. 12:00")
async def schedule_remove(interaction: discord.Interaction, time: str):
    await interaction.response.defer(ephemeral=True)
    current = list(cfg.CLEAN_TIMES)
    if time not in current:
        await interaction.followup.send(f"⚠️ `{time}` is not in the current schedule.", ephemeral=True)
        return
    if len(current) == 1:
        await interaction.followup.send("⛔ Cannot remove the last scheduled run time — at least one is required.", ephemeral=True)
        return
    old_times = list(current)
    current.remove(time)
    success, message = update_schedule(current)
    embed = discord.Embed(
        title="✅ Schedule Updated" if success else "⛔ Schedule Update Failed",
        description=f"Removed `{time}` from schedule.\nNew schedule: **{message}**" if success else f"⛔ {message}",
        color=0x2ECC71 if success else 0xFF0000,
        timestamp=datetime.now()
    )
    if success:
        embed.add_field(name="⏭️ Next run", value=f"**{get_next_run_str()}**", inline=False)
        bot = get_bot()
        await post_schedule_notification(bot, interaction.guild, old_times, current, str(interaction.user))
    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    log.info(f"Schedule remove '{time}' by {interaction.user} — {'success' if success else 'failed'}: {message}")
    await interaction.followup.send(embed=embed, ephemeral=True)


@cleanup_group.error
async def cleanup_group_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("⛔ You need Administrator permissions to use this command.", ephemeral=True)

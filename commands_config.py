import discord
from discord import app_commands
from datetime import datetime

import config as cfg
from config import BOT_VERSION, LOG_MAX_FILES, log
from notifications import post_schedule_notification, post_schedule_error_notification
from utils import get_next_run_str, get_bot, update_schedule, update_retention, update_log_level, update_warn_unconfigured, update_report_frequency
from commands import cleanup_group


config_group = app_commands.Group(name="config", description="Manage bot configuration", parent=cleanup_group)
schedule_group = app_commands.Group(name="schedule", description="Manage cleanup schedule", parent=cleanup_group)


@config_group.command(name="view", description="Show all current bot configuration values")
@app_commands.checks.has_permissions(administrator=True)
async def config_view(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    embed = discord.Embed(
        title="⚙️ Current Configuration",
        description=(
            f"📅 Default retention: **{cfg.DEFAULT_RETENTION} days**\n"
            f"🕐 Scheduled runs: **{', '.join(cfg.CLEAN_TIMES)}**\n"
            f"⏭️ Next run: **{get_next_run_str()}**\n"
            f"📋 Log level: **{cfg.LOG_LEVEL}**\n"
            f"🗂️ Log retention: **{cfg.LOG_MAX_FILES} days**\n"
            f"📊 Report frequency: **{cfg.REPORT_FREQUENCY}**\n"
            f"⚠️ Warn unconfigured: **{'enabled' if cfg.WARN_UNCONFIGURED else 'disabled'}**"
        ),
        color=0x5865F2,
        timestamp=datetime.now()
    )
    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    await interaction.followup.send(embed=embed, ephemeral=True)


@config_group.command(name="retention", description="Set the default message retention period in days")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(days="Number of days to retain messages (1-365)")
async def config_retention(interaction: discord.Interaction, days: int):
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


@config_group.command(name="loglevel", description="Set the log verbosity level")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(level="Log level")
@app_commands.choices(level=[
    app_commands.Choice(name="DEBUG", value="DEBUG"),
    app_commands.Choice(name="INFO", value="INFO"),
    app_commands.Choice(name="WARNING", value="WARNING"),
    app_commands.Choice(name="ERROR", value="ERROR"),
])
async def config_loglevel(interaction: discord.Interaction, level: app_commands.Choice[str]):
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


@config_group.command(name="warnunconfigured", description="Toggle warnings for unconfigured channels")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(enabled="Enable or disable unconfigured channel warnings")
@app_commands.choices(enabled=[
    app_commands.Choice(name="Enable", value="true"),
    app_commands.Choice(name="Disable", value="false"),
])
async def config_warnunconfigured(interaction: discord.Interaction, enabled: app_commands.Choice[str]):
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


@config_group.command(name="reportfrequency", description="Set how often the stats report is posted")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(frequency="Report frequency")
@app_commands.choices(frequency=[
    app_commands.Choice(name="Monthly (1st of month)", value="monthly"),
    app_commands.Choice(name="Weekly (every Monday)", value="weekly"),
    app_commands.Choice(name="Both", value="both"),
])
async def config_reportfrequency(interaction: discord.Interaction, frequency: app_commands.Choice[str]):
    await interaction.response.defer(ephemeral=True)
    old = cfg.REPORT_FREQUENCY
    success, message = update_report_frequency(frequency.value)
    embed = discord.Embed(
        title="✅ Report Frequency Updated" if success else "⛔ Update Failed",
        description=f"Report frequency changed from **{old}** to **{frequency.value}**." if success else f"⛔ {message}",
        color=0x2ECC71 if success else 0xFF0000,
        timestamp=datetime.now()
    )
    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    log.info(f"Report frequency set to {frequency.value} by {interaction.user}")
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
    success, message, reschedule_error = update_schedule(current)
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
        if reschedule_error:
            await post_schedule_error_notification(bot, interaction.guild, reschedule_error)
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
    success, message, reschedule_error = update_schedule(current)
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
        if reschedule_error:
            await post_schedule_error_notification(bot, interaction.guild, reschedule_error)
    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    log.info(f"Schedule remove '{time}' by {interaction.user} — {'success' if success else 'failed'}: {message}")
    await interaction.followup.send(embed=embed, ephemeral=True)

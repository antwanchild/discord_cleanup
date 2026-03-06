import discord
from discord import app_commands
from datetime import datetime
import os

import config as cfg
from config import (
    BOT_VERSION, DEFAULT_RETENTION, LOG_MAX_FILES, LOG_DIR, log
)
from cleanup import build_channel_map, run_cleanup
from stats import load_stats, reset_stats
from utils import get_next_run_str, get_uptime_str, reload_channels, get_bot


cleanup_group = app_commands.Group(name="cleanup", description="Discord Cleanup Bot commands")


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
            f"🕐 Scheduled runs: **{', '.join(cfg.CLEAN_TIMES)}**"
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

    # Split channel lines into pages of max 1000 chars
    pages = []
    current_page = []
    current_len = 0
    for line in channel_lines:
        if current_len + len(line) + 1 > 1000:
            pages.append("\n".join(current_page))
            current_page = [line]
            current_len = len(line)
        else:
            current_page.append(line)
            current_len += len(line) + 1
    if current_page:
        pages.append("\n".join(current_page))

    total_pages = max(len(pages), 1)

    main_embed = discord.Embed(
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
    if pages:
        main_embed.add_field(
            name=f"📋 Configured Channels (🧹 = deep clean enabled){f' — Page 1/{total_pages}' if total_pages > 1 else ''}",
            value=pages[0],
            inline=False
        )
    main_embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    await interaction.followup.send(embed=main_embed, ephemeral=True)

    for i, page in enumerate(pages[1:], start=2):
        continuation_embed = discord.Embed(
            color=0x5865F2,
            timestamp=datetime.now()
        )
        continuation_embed.add_field(
            name=f"📋 Configured Channels — Page {i}/{total_pages}",
            value=page,
            inline=False
        )
        continuation_embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
        await interaction.followup.send(embed=continuation_embed, ephemeral=True)


@cleanup_group.command(name="test", description="Post a test notification to the log channel")
@app_commands.checks.has_permissions(administrator=True)
async def cleanup_test(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    bot = get_bot()
    from config import LOG_CHANNEL_ID
    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if not log_channel:
        await interaction.followup.send("⛔ Could not find the log channel — check `LOG_CHANNEL_ID` in your env file.", ephemeral=True)
        return
    embed = discord.Embed(
        title="✅ Test Notification",
        description=(
            f"🏠 Server: **{interaction.guild.name}**\n"
            f"👤 Triggered by: **{interaction.user}**\n"
            f"📋 Log channel is working correctly."
        ),
        color=0x2ECC71,
        timestamp=datetime.now()
    )
    embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    await log_channel.send(embed=embed)
    log.info(f"Test notification posted by {interaction.user}")
    await interaction.followup.send(f"✅ Test notification posted to {log_channel.mention}.", ephemeral=True)


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


@cleanup_group.error
async def cleanup_group_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("⛔ You need Administrator permissions to use this command.", ephemeral=True)

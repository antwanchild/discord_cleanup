import asyncio
import os
import discord
from datetime import datetime, timezone, timedelta

import config as cfg
from config import (
    BOT_VERSION, CLEAN_TIMES, DEFAULT_RETENTION, LOG_CHANNEL_ID,
    RETRY_DELAY, WARN_UNCONFIGURED, log
)
from stats import update_stats, load_stats
from utils import get_next_run_str, setup_run_log, update_health


def build_channel_map(guild):
    """Builds a map of channel_id -> config dict."""
    override_map = {}
    exclude_set = set()
    category_map = {}

    for ch in cfg.raw_channels:
        ch_id = ch["id"]
        if ch.get("type") == "category":
            category_map[ch_id] = {
                "name": ch.get("name", str(ch_id)),
                "days": ch.get("days", DEFAULT_RETENTION),
                "deep_clean": ch.get("deep_clean", False)
            }
        elif ch.get("exclude", False):
            exclude_set.add(ch_id)
        else:
            override_map[ch_id] = {
                "days": ch.get("days", DEFAULT_RETENTION),
                "deep_clean": ch.get("deep_clean", False)
            }

    channel_map = {}

    for ch_config in cfg.raw_channels:
        if ch_config.get("type") != "category":
            continue
        cat_id = ch_config["id"]
        cat_days = ch_config.get("days", DEFAULT_RETENTION)
        cat_name = ch_config.get("name", str(cat_id))
        cat_deep_clean = ch_config.get("deep_clean", False)

        category = guild.get_channel(cat_id)
        if not category:
            log.warning(f"Category ID {cat_id} not found in guild")
            continue

        for sub in category.text_channels:
            if sub.id in exclude_set:
                log.info(f"#{sub.name} — excluded from cleanup (configured in channels.yml)")
                continue
            if sub.id in override_map:
                ov = override_map[sub.id]
                channel_map[sub.id] = {
                    "days": ov["days"],
                    "category_name": cat_name,
                    "category_default": cat_days,
                    "is_override": True,
                    "deep_clean": ov["deep_clean"] or cat_deep_clean
                }
            else:
                channel_map[sub.id] = {
                    "days": cat_days,
                    "category_name": cat_name,
                    "category_default": cat_days,
                    "is_override": False,
                    "deep_clean": cat_deep_clean
                }

    for ch_config in cfg.raw_channels:
        if ch_config.get("type") == "category":
            continue
        ch_id = ch_config["id"]
        if ch_id in channel_map or ch_id in exclude_set:
            continue

        days = ch_config.get("days", DEFAULT_RETENTION)
        deep_clean = ch_config.get("deep_clean", False)
        discord_channel = guild.get_channel(ch_id)
        cat_name = None
        cat_default = None

        if discord_channel and discord_channel.category:
            cat_name = discord_channel.category.name
            for cat_id, cat_data in category_map.items():
                if discord_channel.category.id == cat_id:
                    cat_default = cat_data["days"]
                    break

        channel_map[ch_id] = {
            "days": days,
            "category_name": cat_name,
            "category_default": cat_default,
            "is_override": days != DEFAULT_RETENTION,
            "deep_clean": deep_clean
        }

    return channel_map


def validate_channels(guild):
    """Validates all configured channels exist in the guild on startup."""
    log.info("Validating configured channels...")
    issues = 0
    total_channels = 0
    excluded_channels = 0

    for ch in cfg.raw_channels:
        if ch.get("exclude", False):
            excluded_channels += 1
            log.info(f"  ⛔ #{ch.get('name', ch['id'])} — excluded")
            continue
        ch_id = ch["id"]
        ch_name = ch.get("name", str(ch_id))
        channel = guild.get_channel(ch_id)
        if not channel:
            log.warning(f"  ❌ #{ch_name} (ID: {ch_id}) — not found in server")
            issues += 1
        elif ch.get("type") == "category":
            deep = " | deep_clean: enabled" if ch.get("deep_clean") else ""
            days = ch.get("days", DEFAULT_RETENTION)
            count = len(channel.text_channels)
            total_channels += count
            log.info(f"  📁 #{ch_name} — category | {count} channel(s) | {days}d retention{deep}")
        else:
            deep = " | deep_clean: enabled" if ch.get("deep_clean") else ""
            days = ch.get("days", DEFAULT_RETENTION)
            total_channels += 1
            log.info(f"  📄 #{channel.name} — {days}d retention{deep}")

    if issues == 0:
        log.info(f"Validation complete — {total_channels} channel(s) active | {excluded_channels} excluded | 0 issues")
    else:
        log.warning(f"Validation complete — {total_channels} channel(s) active | {excluded_channels} excluded | {issues} issue(s) found")

    if WARN_UNCONFIGURED:
        accounted_ids = {ch["id"] for ch in cfg.raw_channels}
        unaccounted = []

        for discord_channel in guild.text_channels:
            if discord_channel.id not in accounted_ids:
                if discord_channel.category and discord_channel.category.id in accounted_ids:
                    continue
                unaccounted.append(discord_channel)

        if unaccounted:
            for ch in unaccounted:
                cat = f" (in category: {ch.category.name})" if ch.category else ""
                log.warning(f"#{ch.name}{cat} — not configured in channels.yml")
            log.warning(f"{len(unaccounted)} unconfigured channel(s) found — add to channels.yml or exclude to silence this warning")
        else:
            log.info("All Discord channels are accounted for in channels.yml")


async def purge_channel(channel, days_old: int, bulk_cutoff: datetime, run_time: datetime, dry_run: bool = False, deep_clean: bool = False) -> dict:
    """Purges old messages from a single channel. Returns stats dict."""
    cutoff = run_time - timedelta(days=days_old)
    guild = channel.guild
    total_deleted = 0
    rate_limit_count = 0
    oldest_message_date = None

    if not channel.permissions_for(guild.me).read_message_history:
        log.warning(f"Skipping #{channel.name} — missing Read Message History permission")
        return {"count": -1, "rate_limits": 0, "oldest": None, "days": days_old, "deep_clean": deep_clean, "error": "Missing Read Message History permission"}

    if not channel.permissions_for(guild.me).manage_messages:
        log.warning(f"Skipping #{channel.name} — missing Manage Messages permission")
        return {"count": -1, "rate_limits": 0, "oldest": None, "days": days_old, "deep_clean": deep_clean, "error": "Missing Manage Messages permission"}

    log.info(f"{'[DRY RUN] ' if dry_run else ''}Starting purge on #{channel.name} | Days: {days_old} | Deep clean: {deep_clean}")

    # Bulk delete — messages between retention cutoff and 14 days old
    while True:
        try:
            messages_to_delete = []
            async for msg in channel.history(limit=100, before=cutoff):
                if msg.created_at > bulk_cutoff:
                    messages_to_delete.append(msg)
                    if oldest_message_date is None or msg.created_at < oldest_message_date:
                        oldest_message_date = msg.created_at

            if not messages_to_delete:
                log.info(f"#{channel.name} — bulk delete complete | Total: {total_deleted}")
                break

            if dry_run:
                total_deleted += len(messages_to_delete)
                log.info(f"[DRY RUN] #{channel.name} — would bulk delete {len(messages_to_delete)} | Running total: {total_deleted}")
                break
            else:
                if len(messages_to_delete) == 1:
                    await messages_to_delete[0].delete()
                else:
                    await channel.delete_messages(messages_to_delete)
                total_deleted += len(messages_to_delete)
                log.info(f"#{channel.name} — bulk deleted {len(messages_to_delete)} | Running total: {total_deleted}")
                await asyncio.sleep(1.5)

        except asyncio.CancelledError:
            log.info(f"#{channel.name} — task cancelled, stopping")
            break
        except discord.errors.HTTPException as e:
            if e.status == 429:
                rate_limit_count += 1
                retry_after = getattr(e, 'retry_after', RETRY_DELAY)
                log.warning(f"#{channel.name} — rate limited (#{rate_limit_count}), retrying in {retry_after:.1f}s")
                await asyncio.sleep(retry_after)
            else:
                log.error(f"#{channel.name} — HTTP error during bulk delete: {e}")
                break
        except discord.Forbidden:
            log.error(f"#{channel.name} — Forbidden. Check bot permissions.")
            return {"count": -1, "rate_limits": 0, "oldest": None, "days": days_old, "deep_clean": deep_clean, "error": "Forbidden — check bot permissions"}

    # Deep clean — individual deletion for messages older than 14 days
    if deep_clean:
        log.info(f"{'[DRY RUN] ' if dry_run else ''}#{channel.name} — starting deep clean")
        deep_deleted = 0

        while True:
            try:
                old_messages = []
                async for msg in channel.history(limit=50, before=bulk_cutoff):
                    if msg.created_at < cutoff:
                        old_messages.append(msg)
                        if oldest_message_date is None or msg.created_at < oldest_message_date:
                            oldest_message_date = msg.created_at

                if not old_messages:
                    log.info(f"#{channel.name} — deep clean complete | Individual deleted: {deep_deleted}")
                    break

                if dry_run:
                    deep_deleted += len(old_messages)
                    log.info(f"[DRY RUN] #{channel.name} — would individually delete {len(old_messages)} | Deep total: {deep_deleted}")
                    break
                else:
                    for msg in old_messages:
                        try:
                            await msg.delete()
                            deep_deleted += 1
                            await asyncio.sleep(1.0)
                        except asyncio.CancelledError:
                            log.info(f"#{channel.name} — task cancelled during deep clean")
                            break
                        except discord.errors.HTTPException as e:
                            if e.status == 429:
                                rate_limit_count += 1
                                retry_after = getattr(e, 'retry_after', RETRY_DELAY)
                                log.warning(f"#{channel.name} — rate limited during deep clean, retrying in {retry_after:.1f}s")
                                await asyncio.sleep(retry_after)
                            else:
                                log.error(f"#{channel.name} — HTTP error during deep clean: {e}")
                        except discord.Forbidden:
                            log.error(f"#{channel.name} — Forbidden during deep clean.")
                            break
                    log.info(f"#{channel.name} — deep clean batch | Deleted: {deep_deleted}")
                    await asyncio.sleep(2)

            except asyncio.CancelledError:
                log.info(f"#{channel.name} — task cancelled, stopping deep clean")
                break
            except discord.errors.HTTPException as e:
                if e.status == 429:
                    rate_limit_count += 1
                    retry_after = getattr(e, 'retry_after', RETRY_DELAY)
                    await asyncio.sleep(retry_after)
                else:
                    log.error(f"#{channel.name} — HTTP error fetching old messages: {e}")
                    break

        total_deleted += deep_deleted

    log.info(f"{'[DRY RUN] ' if dry_run else ''}#{channel.name} — complete | Total: {total_deleted} | Rate limits: {rate_limit_count}")
    return {"count": total_deleted, "rate_limits": rate_limit_count, "oldest": oldest_message_date, "days": days_old, "deep_clean": deep_clean, "error": None}


async def purge_all_channel(channel) -> dict:
    """Deletes ALL messages in a channel regardless of age. Returns dict with count and error."""
    guild = channel.guild
    total_deleted = 0
    rate_limit_count = 0

    if not channel.permissions_for(guild.me).read_message_history:
        return {"count": -1, "error": "Missing Read Message History permission"}
    if not channel.permissions_for(guild.me).manage_messages:
        return {"count": -1, "error": "Missing Manage Messages permission"}

    log.info(f"Starting full purge on #{channel.name}")
    bulk_cutoff = datetime.now(timezone.utc) - timedelta(days=14)

    # Bulk delete messages newer than 14 days
    while True:
        try:
            messages = []
            async for msg in channel.history(limit=100):
                if msg.created_at > bulk_cutoff:
                    messages.append(msg)
            if not messages:
                break
            if len(messages) == 1:
                await messages[0].delete()
            else:
                await channel.delete_messages(messages)
            total_deleted += len(messages)
            await asyncio.sleep(1)
        except discord.errors.RateLimited as e:
            rate_limit_count += 1
            log.warning(f"Rate limited on #{channel.name} — waiting {e.retry_after:.1f}s")
            await asyncio.sleep(e.retry_after)
        except Exception as e:
            log.error(f"Error during bulk purge on #{channel.name} — {e}")
            return {"count": total_deleted, "error": str(e)}

    # Individual delete for messages older than 14 days
    while True:
        try:
            messages = []
            async for msg in channel.history(limit=100):
                messages.append(msg)
            if not messages:
                break
            for msg in messages:
                try:
                    await msg.delete()
                    total_deleted += 1
                    await asyncio.sleep(0.5)
                except discord.errors.RateLimited as e:
                    rate_limit_count += 1
                    log.warning(f"Rate limited on #{channel.name} — waiting {e.retry_after:.1f}s")
                    await asyncio.sleep(e.retry_after)
                except Exception as e:
                    log.warning(f"Could not delete message in #{channel.name} — {e}")
        except Exception as e:
            log.error(f"Error during individual purge on #{channel.name} — {e}")
            return {"count": total_deleted, "error": str(e)}

    log.info(f"Full purge complete on #{channel.name} — deleted {total_deleted} messages")
    return {"count": total_deleted, "error": None}


async def run_cleanup(bot, guild, single_channel_id=None, dry_run: bool = False, triggered_by: str = "scheduler"):
    """Core cleanup logic used by scheduler, slash commands, and web UI."""
    update_health()

    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if not log_channel:
        log.error("Log channel not found. Check LOG_CHANNEL_ID in .env.discord_cleanup")
        return

    run_time = datetime.now(timezone.utc)
    bulk_cutoff = run_time - timedelta(days=14)
    local_run_time = datetime.now()

    channel_map = build_channel_map(guild)

    if single_channel_id:
        if single_channel_id in channel_map:
            channel_map = {single_channel_id: channel_map[single_channel_id]}
        else:
            log.warning(f"Channel ID {single_channel_id} not in configured channels")
            return

    setup_run_log(channel_count=len(channel_map))

    log.info(
        f"{'[DRY RUN] ' if dry_run else ''}Run cutoff: {local_run_time.strftime('%Y-%m-%d %H:%M:%S')} | "
        f"Bulk cutoff: {(local_run_time - timedelta(days=14)).strftime('%Y-%m-%d %H:%M:%S')} | "
        f"TZ: {os.getenv('TZ', 'UTC')}"
    )

    for ch in cfg.raw_channels:
        if ch.get("exclude", False):
            log.info(f"#{ch.get('name', ch['id'])} — excluded (skipping)")

    log.info(f"Starting {'dry run' if dry_run else 'cleanup run'} on {guild.name} across {len(channel_map)} channel(s) | triggered by: {triggered_by}")

    category_results = {}
    standalone_results = {}
    channel_results = {}
    error_lines = []
    grand_total = 0
    grand_rate_limits = 0
    has_warnings = False
    oldest_overall = None
    run_start = datetime.now()

    for channel_id, ch_config in channel_map.items():
        channel = guild.get_channel(channel_id)
        if not channel:
            ch_name = ch_config.get("name", str(channel_id))
            log.warning(f"Channel ID {channel_id} not found — skipping")
            error_lines.append(f"⚠️ `#{ch_name}` — channel not found (ID: `{channel_id}`)")
            has_warnings = True
            continue

        stats = await purge_channel(
            channel, ch_config["days"], bulk_cutoff, run_time,
            dry_run=dry_run, deep_clean=ch_config.get("deep_clean", False)
        )
        stats["is_override"] = ch_config["is_override"]

        if stats["count"] > 0:
            grand_total += stats["count"]
            channel_results[str(channel.id)] = {"name": channel.name, "count": stats["count"], "category": ch_config.get("category_name") or "Standalone"}
            log.info(f"  ✅ #{channel.name} — deleted {stats['count']} message(s)")
        elif stats["count"] == 0:
            log.info(f"  ℹ️ #{channel.name} — nothing to delete")
        if stats["count"] == -1:
            has_warnings = True
            if stats.get("error"):
                error_lines.append(f"🚫 `#{channel.name}` — {stats['error']}")
                log.warning(f"  ❌ #{channel.name} — {stats['error']}")

        grand_rate_limits += stats["rate_limits"]
        if stats["rate_limits"] > 0:
            error_lines.append(f"⚡ `#{channel.name}` — rate limited **{stats['rate_limits']}** time(s)")
            log.warning(f"  ⚡ #{channel.name} — rate limited {stats['rate_limits']} time(s)")

        if stats["oldest"] and (oldest_overall is None or stats["oldest"] < oldest_overall):
            oldest_overall = stats["oldest"]

        cat_name = ch_config["category_name"]
        if cat_name:
            if cat_name not in category_results:
                category_results[cat_name] = {"default_days": ch_config["category_default"], "channels": {}}
            category_results[cat_name]["channels"][channel.name] = stats
        else:
            standalone_results[channel.name] = stats

        await asyncio.sleep(2)

    run_end = datetime.now()
    elapsed = run_end - run_start
    minutes, seconds = divmod(int(elapsed.total_seconds()), 60)
    duration_str = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"

    if not dry_run and not single_channel_id:
        update_stats(channel_results)

        # Build category totals for last run summary
        cat_totals = {}
        for cat_name, cat_data in category_results.items():
            cat_total = sum(s["count"] for s in cat_data["channels"].values() if s["count"] > 0)
            if cat_total > 0:
                cat_totals[cat_name] = cat_total
        for ch_name, s in standalone_results.items():
            if s["count"] > 0:
                cat_totals[f"#{ch_name}"] = s["count"]

        # Determine status label for summary
        if has_warnings and grand_total == 0:
            run_status = "error"
        elif has_warnings:
            run_status = "warning"
        elif grand_total > 0:
            run_status = "success"
        else:
            run_status = "clean"

        save_last_run({
            "timestamp":        run_end.strftime("%Y-%m-%d %H:%M:%S"),
            "triggered_by":     triggered_by,
            "duration":         duration_str,
            "total_deleted":    grand_total,
            "channels_checked": len(channel_map),
            "rate_limits":      grand_rate_limits,
            "status":           run_status,
            "categories":       sorted(cat_totals.items(), key=lambda x: x[1], reverse=True)[:5],
        })
        stats_data = load_stats()
        log.info(
            f"Stats | All-time: {stats_data['all_time']['deleted']} deleted across {stats_data['all_time']['runs']} runs "
            f"| This month: {stats_data['monthly']['deleted']} deleted"
        )

    # Color logic
    if dry_run:
        color, status = 0x95A5A6, "🔍 Dry Run Complete"
    elif has_warnings and grand_total == 0:
        color, status = 0xFF0000, "⛔ Completed with Errors"
        log.error("Run completed with errors and nothing deleted")
    elif has_warnings:
        color, status = 0xFFA500, "⚠️ Completed with Warnings"
        log.warning("Run completed with warnings")
    elif grand_total > 0:
        color, status = 0x00C853, "✅ Cleanup Successful"
        log.info("Run completed successfully")
    else:
        color, status = 0x3498DB, "ℹ️ Nothing to Clean"
        log.info("Run completed — nothing to delete")

    # Build breakdown
    breakdown_lines = []
    for cat_name, cat_data in category_results.items():
        active_lines = []
        for ch_name, stats in cat_data["channels"].items():
            if stats["count"] == -1:
                active_lines.append(f"\u3000🚫 `#{ch_name}` — skipped (missing permissions)")
            elif stats["count"] > 0:
                label = "would delete" if dry_run else "deleted"
                deep_tag = " 🧹deep" if stats.get("deep_clean") else ""
                override_tag = f" ({stats['days']}d ⚡override)" if stats["is_override"] else ""
                active_lines.append(f"\u3000🗑️ `#{ch_name}` — **{stats['count']}** {label}{override_tag}{deep_tag}")
        if active_lines:
            breakdown_lines.append(f"📁 **{cat_name}** ({cat_data['default_days']}d default)")
            breakdown_lines.extend(active_lines)

    for ch_name, stats in standalone_results.items():
        if stats["count"] == -1:
            breakdown_lines.append(f"🚫 `#{ch_name}` — skipped (missing permissions)")
        elif stats["count"] > 0:
            label = "would delete" if dry_run else "deleted"
            deep_tag = " 🧹deep" if stats.get("deep_clean") else ""
            override_tag = f" ({stats['days']}d ⚡override)" if stats["is_override"] else ""
            breakdown_lines.append(f"🗑️ `#{ch_name}` — **{stats['count']}** {label}{override_tag}{deep_tag}")

    if not breakdown_lines:
        breakdown_lines.append("✅ No messages to clean")

    oldest_str = oldest_overall.strftime('%Y-%m-%d %I:%M %p') if oldest_overall else "N/A"
    title_prefix = "🔍 Dry Run Report" if dry_run else "🧹 Daily Cleanup Report"

    summary = (
        f"🏠 Server: **{guild.name}**\n"
        f"📅 Default retention: **{DEFAULT_RETENTION} days**\n"
        f"🔍 Channels checked: **{len(channel_map)}**\n"
        f"🗑️ {'Would delete' if dry_run else 'Total deleted'}: **{grand_total}**\n"
        + (f"📆 Oldest message: **{oldest_str}**\n" if grand_total > 0 else "")
        + (f"⚡ Rate limits hit: **{grand_rate_limits}**\n" if not dry_run else "")
        + f"⏱️ Duration: **{duration_str}**\n"
        + (f"⏭️ Next run: **{get_next_run_str()}**" if not dry_run else "")
    )

    # Split breakdown_lines into chunks that fit within Discord's 1024 char field limit
    chunks = []
    current_chunk = []
    current_len = 0
    for line in breakdown_lines:
        if current_len + len(line) + 1 > 1000 and current_chunk:
            chunks.append(current_chunk)
            current_chunk = [line]
            current_len = len(line)
        else:
            current_chunk.append(line)
            current_len += len(line) + 1
    if current_chunk:
        chunks.append(current_chunk)

    if not chunks:
        chunks = [["✅ No messages to clean"]]

    total_pages = len(chunks)

    # First embed — includes summary
    first_embed = discord.Embed(
        title=f"{title_prefix} — {status}",
        description=summary,
        color=color,
        timestamp=run_end
    )
    page_label = f"📋 Category Summary" if total_pages == 1 else f"📋 Category Summary (1/{total_pages})"
    first_embed.add_field(name=page_label, value="\n".join(chunks[0]), inline=False)
    if total_pages == 1:
        first_embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
    await log_channel.send(embed=first_embed)

    # Additional embeds if needed
    for i, chunk in enumerate(chunks[1:], start=2):
        page_embed = discord.Embed(color=color, timestamp=run_end)
        page_label = f"📋 Category Summary ({i}/{total_pages})"
        page_embed.add_field(name=page_label, value="\n".join(chunk), inline=False)
        if i == total_pages:
            page_embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
        await log_channel.send(embed=page_embed)

    _footer1 = f"  Run Complete | Deleted: {grand_total} | Duration: {duration_str}"
    _footer2 = f"  Warnings: {len(error_lines)} | Rate limits: {grand_rate_limits}"
    log.info("╔══════════════════════════════════════════════════════════╗")
    log.info(f"║{_footer1:<58}║")
    log.info(f"║{_footer2:<58}║")
    log.info("╚══════════════════════════════════════════════════════════╝")

    if error_lines:
        error_embed = discord.Embed(
            title=f"⚠️ Run Errors — {len(error_lines)} issue(s) found",
            description="\n".join(error_lines),
            color=0xFF0000,
            timestamp=run_end
        )
        error_embed.set_footer(text=f"Discord Cleanup Bot v{BOT_VERSION}")
        await log_channel.send(embed=error_embed)

    update_health()

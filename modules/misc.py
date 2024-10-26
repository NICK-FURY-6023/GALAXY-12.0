# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import datetime
import json
import os.path
import platform
import traceback
from copy import deepcopy
from itertools import cycle
from os import getpid
from random import shuffle
from typing import TYPE_CHECKING

import aiofiles
import disnake
import humanize
import psutil
from aiohttp import ClientSession
from disnake.ext import commands

from utils.db import DBModel, db_models
from utils.music.checks import check_requester_channel
from utils.music.converters import time_format, URL_REG
from utils.others import select_bot_pool, CustomContext, paginator

if TYPE_CHECKING:
    from utils.client import BotCore


def remove_blank_spaces(d):

    for k, v in list(d.items()):

        new_k = k.strip()
        if new_k != k:
            d[new_k] = d.pop(k)

        if isinstance(v, str):
            new_v = v.strip()
            if new_v != v:
                d[new_k] = new_v
        elif isinstance(v, dict):
            remove_blank_spaces(v)


class Misc(commands.Cog):

    emoji = "🔰"
    name = "Miscellaneous"
    desc_prefix = f"[{emoji} {name}] | "

    def __init__(self, bot: BotCore):
        self.bot = bot
        self.task = self.bot.loop.create_task(self.presences())
        self.extra_user_bots = []
        self.extra_user_bots_ids = [int(i) for i in bot.config['ADDITIONAL_BOT_IDS'].split() if i.isdigit()]

    def placeholders(self, text: str):

        if not text:
            return ""

        try:
            text = text.replace("{owner}", str(self.bot.owner))
        except AttributeError:
            pass

        if [i for i in ("{players_count}", "{players_user_count}","{players_count_allbotchannels}", "{players_count_allbotservers}") if i in text]:

            channels = set()
            guilds = set()
            users = set()
            player_count = 0

            for bot in self.bot.pool.bots:

                for player in bot.music.players.values():
                    if not player.auto_pause and not player.paused:
                        if bot == self.bot:
                            player_count += 1
                        try:
                            vc = player.guild.me.voice.channel
                        except AttributeError:
                            continue
                        channels.add(vc.id)
                        guilds.add(player.guild.id)
                        for u in vc.members:
                            if u.bot or u.voice.deaf or u.voice.self_deaf:
                                continue
                            users.add(u.id)

                if "{players_count}" in text:
                    if not player_count:
                        return
                    text = text.replace("{players_count}", str(player_count))

            if "{players_count_allbotchannels}" in text:

                if not channels:
                    return

                text = text.replace("{players_count_allbotchannels}", str(len(channels)))

            if "{players_count_allbotservers}" in text:

                if not guilds:
                    return

                text = text.replace("{players_count_allbotservers}", str(len(guilds)))

            if "{players_user_count}" in text:

                if not users:
                    return

                text = text.replace("{players_user_count}", str(len(users)))

        return text \
            .replace("{users}", f'{len([m for m in self.bot.users if not m.bot]):,}'.replace(",", ".")) \
            .replace("{playing}", f'{len(self.bot.music.players):,}'.replace(",", ".")) \
            .replace("{guilds}", f'{len(self.bot.guilds):,}'.replace(",", ".")) \
            .replace("{uptime}", time_format((disnake.utils.utcnow() - self.bot.uptime).total_seconds() * 1000,
                                             use_names=True))

    async def presences(self):

        try:

            activities = []

            for i in self.bot.config["LISTENING_PRESENCES"].split("||"):
                if i:
                    activities.append({"name":i, "type": "listening"})

            for i in self.bot.config["WATCHING_PRESENCES"].split("||"):
                if i:
                    activities.append({"name": i, "type": "watching"})

            for i in self.bot.config["PLAYING_PRESENCES"].split("||"):
                if i:
                    activities.append({"name": i, "type": "playing"})

            for i in self.bot.config["CUSTOM_STATUS_PRESENCES"].split("||"):
                if i:
                    activities.append({"name": i, "type": "custom_status"})

            for i in self.bot.config["STREAMING_PRESENCES"].split("|||"):
                if i:
                    try:
                        name, url = i.split("||")
                        activities.append({"name": name, "url": url.strip(" "), "type": "streaming"})
                    except Exception:
                        traceback.print_exc()

            if not activities:
                return

            shuffle(activities)

            activities = cycle(activities)

            ignore_sleep = False

            await asyncio.sleep(120)

            while True:

                try:
                    if self.bot._presence_loop_started and ignore_sleep is False:
                        await asyncio.sleep(self.bot.config["PRESENCE_INTERVAL"])
                except AttributeError:
                    self.bot._presence_loop_started = True

                await self.bot.wait_until_ready()

                activity_data = next(activities)

                activity_name = self.placeholders(activity_data["name"])

                if not activity_name:
                    await asyncio.sleep(15)
                    ignore_sleep = True
                    continue

                ignore_sleep = False

                if activity_data["type"] == "listening":
                    activity = disnake.Activity(
                        type=disnake.ActivityType.listening,
                        name=activity_name,
                    )

                elif activity_data["type"] == "watching":
                    activity = disnake.Activity(
                        type=disnake.ActivityType.watching,
                        name=activity_name,
                    )

                elif activity_data["type"] == "streaming":
                    activity = disnake.Activity(
                        type=disnake.ActivityType.streaming,
                        name=activity_name,
                        url=activity_data["url"]
                    )

                elif activity_data["type"] == "playing":
                    activity = disnake.Game(name=activity_name)

                else:
                    activity = disnake.Activity(
                        name="customstatus",
                        type=disnake.ActivityType.custom,
                        state=activity_name,
                    )

                await self.bot.change_presence(activity=activity)

                await asyncio.sleep(self.bot.config["PRESENCE_INTERVAL"])

        except Exception:
            traceback.print_exc()


    @commands.Cog.listener("on_guild_join")
    async def guild_add(self, guild: disnake.Guild):

        bots_in_guild = []
        bots_outside_guild = []

        for bot in self.bot.pool.bots:

            if bot == self.bot:
                continue

            if not bot.bot_ready:
                continue

            if bot.user in guild.members:
                bots_in_guild.append(bot)
            else:
                bots_outside_guild.append(bot)

        components = [disnake.ui.Button(custom_id="bot_invite", label="Need more music bots? Click here.")] if [b for b in self.bot.pool.bots if b.appinfo and b.appinfo.bot_public] else []

        if cmd:=self.bot.get_command("setup"):
            cmd_text = f"If you wish, use the command **/{cmd.name}** to create a dedicated channel " \
                        "for requesting music without commands and keep the music player fixed in a dedicated channel.\n\n"
        else:
            cmd_text = ""

        if self.bot.config["SUPPORT_SERVER"]:
            support_server = f"If you have any questions or want to stay updated with the latest news, you can join my [`support server`]({self.bot.config['SUPPORT_SERVER']})"
        else:
            support_server = ""

        if self.bot.default_prefix and not self.bot.config["INTERACTION_COMMAND_ONLY"]:
            guild_data = await self.bot.get_global_data(guild.id, db_name=DBModel.guilds)
            prefix = disnake.utils.escape_markdown(guild_data['prefix'], as_needed=True)
        else:
            prefix = ""

        image = "https://cdn.discordapp.com/attachments/554468640942981147/1082887587770937455/rainbow_bar2.gif"

        color = self.bot.get_color()

        send_video = ""

        try:
            channel = guild.system_channel if guild.system_channel.permissions_for(guild.me).send_messages else None
        except AttributeError:
            channel = None

        if not channel:

            if guild.me.guild_permissions.view_audit_log:

                async for entry in guild.audit_logs(action=disnake.AuditLogAction.integration_create, limit=50):

                    if entry.target.application_id == self.bot.user.id:

                        embeds = []

                        embeds.append(
                            disnake.Embed(
                                color=color,
                                description=f"Hello! Thank you very much for adding me to the server: **{guild.name}** :)"
                            ).set_image(url=image)
                        )

                        embeds.append(
                            disnake.Embed(
                                color=color,
                                description=f"To see all my commands, use slash command (**/**) in the server. " \
                                             f"**{guild.name}**"
                            ).set_image(url=image)
                        )

                        if prefix:
                            prefix_msg = f"My prefix in the server **{guild.name}** is: **{prefix}**"
                        else:
                            prefix = self.bot.default_prefix
                            prefix_msg = f"My default prefix is **{prefix}**"

                        embeds.append(
                            disnake.Embed(
                                color=color,
                                description=f"I also have prefix text commands. {prefix_msg} (my mention "
                                            f"also works as a prefix). To see all my text commands "
                                            f"use **{prefix}help** in a channel of the server **{guild.name}**. "
                                            f"If you want to change my prefix, use the command **{prefix}setprefix** "
                                            f"(you can have a personal prefix using the command "
                                            f"**{prefix}setmyprefix**)."
                            ).set_image(url=image)
                        )

                        if bots_in_guild:

                            msg = f"I noticed there are other bots in the server **{guild.name}** where I am compatible with " \
                                   f"the multi-voice system: {', '.join(b.user.mention for b in bots_in_guild)}\n\n" \
                                   f"When using music commands (e.g., play) without one of the bots connected in the channel, " \
                                    "one of the available bots in the server will be used."

                            if not self.bot.pool.config.get("MULTIVOICE_VIDEO_DEMO_URL"):
                                embeds.append(
                                    disnake.Embed(
                                        color=color,
                                        description=msg
                                    ).set_image(url=image)
                                )

                            else:
                                send_video = msg

                        elif bots_outside_guild and self.bot.config.get('MULTIVOICE_VIDEO_DEMO_URL'):
                            send_video = "**If there is demand in your server, you can also add more extra music bots.\n" \
                                         "All bots share the same prefix and slash command, eliminating the need " \
                                         f"to remember prefixes and slash commands for each individual bot.\n\n" \
                                         f"Check out the [video]({self.bot.config['MULTIVOICE_VIDEO_DEMO_URL']}) demonstrating the use of multi-bot in practice.**"

                        if support_server:
                            embeds.append(disnake.Embed(color=color, description=support_server).set_image(url=image))

                        try:
                            await entry.user.send(embeds=embeds, components=components)
                            if send_video:
                                await asyncio.sleep(1)
                                await entry.user.send(f"{send_video}\n\nCheck out the [**video**]({self.bot.config['MULTIVOICE_VIDEO_DEMO_URL']}) demonstrating this feature.")
                            return
                        except disnake.Forbidden:
                            pass
                        except Exception:
                            traceback.print_exc()
                        break

        if not channel:

            for c in (guild.public_updates_channel, guild.rules_channel):

                if c and c.permissions_for(guild.me).send_messages:
                    channel = c
                    break

            if not channel:
                return

        embeds = [
            disnake.Embed(
                color=color, description="Hello! To see all my commands, type slash command (**/**)\n"
                                         "`Note: If the commands are not appearing on your server, "
                                         "it might have reached the limit of bots with registered slash commands "
                                         "(if there are more than 50 integrations/apps on your server).`"
            ).set_image(url=image)
        ]

        if prefix:
            prefix_msg = f"My prefix in the server is: **{prefix}**"
        else:
            prefix = self.bot.default_prefix
            prefix_msg = f"My default prefix is **{prefix}**"

        embeds.append(
            disnake.Embed(
                color=color,
                description=f"I also have prefix text commands. {prefix_msg} (my mention "
                            f"also works as a prefix). To see all my text commands use "
                            f"**{prefix}help**. If you want to change my prefix, use the command **{prefix}setprefix** "
                            f"(you can have a personal prefix using the command **{prefix}setmyprefix**)."
            ).set_image(url=image)
        )

        if bots_in_guild:

            msg = f"I noticed there are other bots in the server **{guild.name}** where I am compatible with " \
                  f"the multi-voice system: {', '.join(b.user.mention for b in bots_in_guild)}\n\n" \
                  f"When using music commands (e.g., play) without one of the bots connected in the channel, one of " \
                  f"the available bots in the server will be used."

            if not self.bot.config.get('MULTIVOICE_VIDEO_DEMO_URL'):
                embeds.append(
                    disnake.Embed(
                        color=color,
                        description=msg
                    ).set_image(url=image)
                )
            else:
                send_video = msg

        elif bots_outside_guild and self.bot.config.get('MULTIVOICE_VIDEO_DEMO_URL'):
            send_video = "**If there is demand in your server, you can also add more extra music bots.\n" \
                          "All bots share the same prefix and slash command, eliminating the need " \
                          "to memorize prefixes and slash commands for each individual bot.\n\n" \
                        f"Check out the [video]({self.bot.config['MULTIVOICE_VIDEO_DEMO_URL']}) demonstrating the use of multi-bot in practice.**"

        embeds.append(disnake.Embed(color=color, description=cmd_text).set_image(url=image))

        if support_server:
            embeds.append(disnake.Embed(color=color, description=support_server).set_image(url=image))

        kwargs = {"delete_after": 60} if channel == guild.rules_channel else {"delete_after": 300}

        timestamp = int((disnake.utils.utcnow() + datetime.timedelta(seconds=kwargs["delete_after"])).timestamp())

        embeds[-1].description += f"\nThis message will be deleted automatically <t:{timestamp}:R>"

        try:
            await channel.send(embeds=embeds, components=components, **kwargs)
            if send_video:
                if "delete_after" in kwargs:
                    kwargs["delete_after"] = 600
                await asyncio.sleep(1)
                await channel.send(f"{send_video}\n\nCheck out the [**video**]({self.bot.config['MULTIVOICE_VIDEO_DEMO_URL']}) demonstrating this feature.", **kwargs)
        except:
            print(f"Failed to send new server message in channel: {channel}\n"
                f"Channel ID: {channel.id}\n"
                f"Channel Type: {type(channel)}\n"
                f"{traceback.format_exc()}")


    about_cd = commands.CooldownMapping.from_cooldown(1, 5, commands.BucketType.member)

    @commands.command(name="about", aliases=["info", "botinfo"], description="Display information about me.",
                    cooldown=about_cd)
    async def about_legacy(self, ctx: CustomContext):
        await self.about.callback(self=self, interaction=ctx)


    @commands.slash_command(
        description=f"{desc_prefix}Display information about me.", cooldown=about_cd, dm_permission=False,
        extras={"allow_private": True}
    )
    async def about(
            self,
            interaction: disnake.AppCmdInter
    ):

        inter, bot = await select_bot_pool(interaction, first=True)

        if not bot:
            return

        await inter.response.defer(ephemeral=True)

        try:
            lavalink_ram = psutil.Process(self.bot.pool.lavalink_instance.pid).memory_info().rss
        except:
            lavalink_ram = 0

        python_ram = psutil.Process(getpid()).memory_info().rss

        ram_msg = f"> 🖥️ **⠂RAM Usage (Python):** `{humanize.naturalsize(python_ram)}`\n"

        if lavalink_ram:
            ram_msg += f"> 🌋 **⠂RAM Usage (Lavalink):** `{humanize.naturalsize(lavalink_ram)}`\n" \
                        f"> 🖥️ **⠂RAM Usage (Total):** `{humanize.naturalsize(python_ram + lavalink_ram)}`\n"

        guild = bot.get_guild(inter.guild_id) or inter.guild

        try:
            color = bot.get_color(inter.guild.me if inter.guild else guild.me)
        except:
            color = bot.get_color()

        embed = disnake.Embed(description="", color=color)

        active_players_other_bots = 0
        inactive_players_other_bots = 0
        paused_players_other_bots = 0

        all_guilds_ids = set()

        allbots = self.bot.pool.get_all_bots()

        for b in allbots:
            for g in b.guilds:
                all_guilds_ids.add(g.id)

        guilds_size = len(all_guilds_ids)

        public_bot_count = 0
        private_bot_count = 0

        users = set()
        bots = set()
        listeners = set()

        user_count = 0
        bot_count = 0

        botpool_ids = [b.user.id for b in allbots]

        node_data = {}
        nodes_available = set()
        nodes_unavailable = set()

        for user in bot.users:
            if user.bot:
                bot_count += 1
            else:
                user_count += 1

        for b in allbots:

            for user in b.users:

                if user.id in botpool_ids:
                    continue

                if user.bot:
                    bots.add(user.id)
                else:
                    users.add(user.id)

            for n in b.music.nodes.values():

                if n.version == 0:
                    continue

                identifier = f"{n.identifier} (v{n.version})"

                if not identifier in node_data:
                    node_data[identifier] = {"total": 0, "available": 0, "website": n.website}

                node_data[identifier]["total"] += 1

                if n.is_available:
                    node_data[identifier]["available"] += 1

            for p in b.music.players.values():

                if p.auto_pause:
                    inactive_players_other_bots += 1

                elif p.paused:
                    try:
                        if any(m for m in p.guild.me.voice.channel.members if not m.bot):
                            paused_players_other_bots += 1
                            continue
                    except AttributeError:
                        pass
                    inactive_players_other_bots += 1

                else:
                    active_players_other_bots += 1
                    try:
                        vc = p.guild.me.voice.channel
                    except AttributeError:
                        continue
                    for u in vc.members:
                        if u.bot or u.voice.deaf or u.voice.self_deaf:
                            continue
                        listeners.add(u.id)

            if not b.appinfo or not b.appinfo.bot_public:
                private_bot_count += 1
            else:
                public_bot_count += 1

        for identifier, data in node_data.items():

            if data["available"] > 0:
                if data['website']:
                    nodes_available.add(
                        f"> [`✅⠂{identifier}`]({data['website']}) `[{data['available']}/{data['total']}]`")
                else:
                    nodes_available.add(f"> `✅⠂{identifier} [{data['available']}/{data['total']}]`")
            else:
                nodes_unavailable.add(f"> `❌⠂{identifier}`")

        node_txt_final = "\n".join(nodes_available)

        if node_txt_final:
            node_txt_final += "\n"
        node_txt_final += "\n".join(nodes_unavailable)

        if len(allbots) < 2:

            embed.description += "### Statistics (current bot):\n" \
                                 f"> 🏙️ **⠂Server{'s'[:(svcount:=len(bot.guilds))^1]}:** `{svcount:,}`\n" \
                                 f"> 👥 **⠂User{'s'[:user_count^1]}:** `{user_count:,}`\n"

            if bot_count:
                embed.description += f"> 🤖 **⠂Bot{'s'[:bot_count^1]}:** `{bot_count:,}`\n"

        else:

            embed.description += "### Statistics (totals across all bots):\n"

            if public_bot_count:
                embed.description += f"> 🤖 **⠂Bot{(s:='s'[:public_bot_count^1])} public{s}:** `{public_bot_count:,}`\n"

            if private_bot_count:
                embed.description += f"> 🤖 **⠂Bot{(s:='s'[:private_bot_count^1])} private{s}:** `{private_bot_count:,}`\n"

            embed.description += f"> 🏙️ **⠂Server{'s'[:guilds_size^1]}:** `{guilds_size:,}`\n"

            if users_amount := len(users):
                embed.description += f"> 👥 **⠂User{'s'[:users_amount^1]}:** `{users_amount:,}`\n"

            if bots_amount := len(bots):
                embed.description += f"> 🤖 **⠂Bot{'s'[:bots_amount^1]}:** `{bots_amount:,}`\n"

        embed.description += "### Other information:\n"

        if active_players_other_bots:
            embed.description += f"> ▶️ **Active{(s:='s'[:active_players_other_bots^1])} player{s}:** `{active_players_other_bots:,}`\n"

        if paused_players_other_bots:
            embed.description += f"> ⏸️ **⠂Player{'s'[:paused_players_other_bots^1]} paused:** `{paused_players_other_bots:,}`\n"

        if inactive_players_other_bots:
            embed.description += f"> 💤 **⠂Inactive{(s:='s'[:inactive_players_other_bots^1])} player{s}:** `{inactive_players_other_bots:,}`\n"

        if listeners:
            embed.description += f"> 🎧 **⠂Current{'s'[:(lcount:=len(listeners))^1]} listeners{'is'[:lcount^1] or 'l'}:** `{lcount:,}`\n"

        if bot.pool.commit:
            embed.description += f"> 📥 **⠂Current commit:** [`{bot.pool.commit[:7]}`]({bot.pool.remote_git_url}/commit/{bot.pool.commit})\n"

        embed.description += f"> 🐍 **⠂Python Version:** `{platform.python_version()}`\n" \
                            f"> 📦 **⠂Disnake Version:** `{disnake.__version__}`\n" \
                            f"> 📶 **⠂Latency:** `{round(bot.latency * 1000)}ms`\n" \
                            f"{ram_msg}" \
                            f"> ⏰ **⠂Uptime:** <t:{int(bot.uptime.timestamp())}:R>\n"

        if not bot.config["INTERACTION_COMMAND_ONLY"]:

            guild_data = await bot.get_global_data(inter.guild_id, db_name=DBModel.guilds)

            if guild_data["prefix"]:
                embed.description += f"> ⌨️ **⠂Server Prefix:** `{disnake.utils.escape_markdown(guild_data['prefix'], as_needed=True)}`\n"
            else:
                embed.description += f"> ⌨️ **⠂Default Prefix:** `{disnake.utils.escape_markdown(bot.default_prefix, as_needed=True)}`\n"

            user_data = await bot.get_global_data(inter.author.id, db_name=DBModel.users)

            if user_data["custom_prefix"]:
                embed.description += f"> ⌨️ **⠂Your User Prefix:** `{disnake.utils.escape_markdown(user_data['custom_prefix'], as_needed=True)}`\n"

        links = "[`[Source]`](https://github.com/NICK-FURY-6023/galaxy-new-patch-1.9.0.0)"

        if bot.config["SUPPORT_SERVER"]:
            links = f"[`[Support]`]({bot.config['SUPPORT_SERVER']})  **|** {links}"

        embed.description += f"> 🌐 **⠂**{links}\n"

        try:
            owner = bot.appinfo.team.owner
        except AttributeError:
            owner = bot.appinfo.owner

        if node_txt_final:

            embed.description += f"### Music Servers (Lavalink Servers):\n{node_txt_final}"

        try:
            avatar = owner.avatar.with_static_format("png").url
        except AttributeError:
            avatar = owner.default_avatar.with_static_format("png").url

        embed.set_footer(
            icon_url=avatar,
            text=f"Owner: {owner} [{owner.id}]"
        )

        components = [disnake.ui.Button(custom_id="bot_invite", label="Add me to your server")] if [b for b in self.bot.pool.bots if b.appinfo and (b.appinfo.bot_public or await b.is_owner(inter.author))] else None

        try:
            await inter.edit_original_message(embed=embed, components=components)
        except (AttributeError, disnake.InteractionNotEditable):
            try:
                await inter.response.edit_message(embed=embed, components=components)
            except:
                await inter.send(embed=embed, ephemeral=True, components=components)


    @commands.Cog.listener("on_button_click")
    async def invite_button(self, inter: disnake.MessageInteraction, is_command=False):

        if not is_command and inter.data.custom_id != "bot_invite":
            return

        bots_invites = []
        bots_in_guild = []

        guild = None

        if inter.guild_id:
            guild = inter.guild
        else:
            for bot in self.bot.pool.bots:
                if (guild:=bot.get_guild(inter.guild_id)):
                    break

        for bot in sorted(self.bot.pool.bots, key=lambda b: len(b.guilds)):

            try:
                if not bot.appinfo.bot_public and not await bot.is_owner(inter.author):
                    continue
            except:
                continue

            kwargs = {}

            invite = f"[`{disnake.utils.escape_markdown(str(bot.user.name))}`]({disnake.utils.oauth_url(bot.user.id, permissions=disnake.Permissions(bot.config['INVITE_PERMISSIONS']), scopes=('bot',), **kwargs)})"

            if bot.appinfo.flags.gateway_message_content_limited:
                invite += f" `[{len(bot.guilds)}/100]`"
            else:
                invite += f" `[{len(bot.guilds)}]`"

            if guild and inter.author.guild_permissions.manage_guild and bot.user in guild.members:
                bots_in_guild.append(invite)
            else:
                bots_invites.append(invite)

        txt = ""

        if bots_invites:
            txt += "## Music bots available:\n"
            for i in disnake.utils.as_chunks(bots_invites, 2):
                txt += " | ".join(i) + "\n"
            txt += "\n"

        if bots_in_guild:
            txt += "## Music bots that are already in the current server:\n"
            for i in disnake.utils.as_chunks(bots_in_guild, 2):
                txt += " | ".join(i) + "\n"

        if not txt:
            await inter.send(
                embed=disnake.Embed(
                    colour=self.bot.get_color(
                        inter.guild.me if inter.guild else guild.me if guild else None
                    ),
                    description="## There are no public bots available...",
                ), ephemeral=True
            )
            return

        color = self.bot.get_color(inter.guild.me if inter.guild else guild.me if guild else None)

        embeds = [
            disnake.Embed(
                colour=self.bot.get_color(inter.guild.me if inter.guild else guild.me if guild else None),
                description=p, color=color
            ) for p in paginator(txt)
        ]

        await inter.send(embeds=embeds, ephemeral=True)


    @commands.command(name="invite", aliases=["convidar"], description="Display my invite link for you to add me to your server.")
    async def invite_legacy(self, ctx):
        await self.invite.callback(self=self, inter=ctx)


    @commands.slash_command(
        description=f"{desc_prefix}Display my invite link for you to add me to your server.",
        dm_permission=False, extras={"allow_private": True}
    )
    async def invite(self, inter: disnake.AppCmdInter):

        await inter.response.defer(ephemeral=True)

        await self.invite_button(inter, is_command=True)

    @commands.user_command(name="Avatar", dm_permission=False)
    async def avatar(self, inter: disnake.UserCommandInteraction):

        embeds = []

        assets = {}

        if self.bot.intents.members:
            user = (await self.bot.fetch_user(inter.target.id) if not inter.target.bot else self.bot.get_user(inter.target.id))
        else:
            user = inter.target

        try:
            if inter.target.guild_avatar:
                assets["Avatar (Server)"] = inter.target.guild_avatar.with_static_format("png")
        except AttributeError:
            pass
        assets["Avatar (User)"] = user.display_avatar.with_static_format("png")
        if user.banner:
            assets["Banner"] = user.banner.with_static_format("png")

        for name, asset in assets.items():
            embed = disnake.Embed(description=f"{inter.target.mention} **[{name}]({asset.with_size(2048).url})**",
                                color=self.bot.get_color(inter.guild.me if inter.guild else None))
            embed.set_image(asset.with_size(256).url)
            embeds.append(embed)

        await inter.send(embeds=embeds, ephemeral=True)

    @commands.is_owner()
    @commands.max_concurrency(1, commands.BucketType.default)
    @commands.command(hidden=True, description="Temporary command to fix favorites with whitespace that cause errors in some situations.")
    async def fixfavs(self, ctx: CustomContext):

        if not os.path.isdir("./local_database/fixfavs_backup"):
            os.makedirs("./local_database/fixfavs_backup")

        async with ctx.typing():

            for bot in self.bot.pool.get_all_bots():

                db_data = await bot.pool.database.query_data(collection=str(bot.user.id), db_name=DBModel.guilds, limit=300)

                async with aiofiles.open(f"./local_database/fixfavs_backup/guild_favs_{bot.user.id}.json", "w") as f:
                    await f.write(json.dumps(db_data, indent=4))

                for data in db_data:
                    try:
                        remove_blank_spaces(data["player_controller"]["fav_links"])
                    except KeyError:
                        continue
                    await bot.update_data(id_=data["_id"], data=data, db_name=DBModel.guilds)

            db_data = await self.bot.pool.database.query_data(collection="global", db_name=DBModel.users, limit=500)

            async with aiofiles.open("./local_database/fixfavs_backup/user_favs.json", "w") as f:
                await f.write(json.dumps(db_data, indent=4))

            for data in db_data:
                remove_blank_spaces(data["fav_links"])
                await self.bot.update_global_data(id_=data["_id"], data=data, db_name=DBModel.users)

            await ctx.send("Favorites have been fixed successfully!")

    async def cog_check(self, ctx):
        return await check_requester_channel(ctx)

    def cog_unload(self):

        try:
            self.task.cancel()
        except:
            pass



class GuildLog(commands.Cog):

    def __init__(self, bot: BotCore):
        self.bot = bot
        self.hook_url: str = ""

        if bot.config["BOT_ADD_REMOVE_LOG"]:

            if URL_REG.match(bot.config["BOT_ADD_REMOVE_LOG"]):
                self.hook_url = bot.config["BOT_ADD_REMOVE_LOG"]
            else:
                print("Invalid webhook URL (for logging bot add/remove).")

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: disnake.Guild):

        print(f"Removed from the server: {guild.name} - [{guild.id}]")

        try:
            await self.bot.music.players[guild.id].destroy()
        except KeyError:
            pass
        except:
            traceback.print_exc()

        if not self.hook_url:
            return

        try:
            await self.send_hook(guild, title="I have been removed from a server", color=disnake.Color.red())
        except:
            traceback.print_exc()

        await self.bot.update_appinfo()

    @commands.Cog.listener()
    async def on_guild_join(self, guild: disnake.Guild):

        print(f"{self.bot.user.name} - Added to the server: {guild.name} - [{guild.id}]")

        try:
            guild_data = await self.bot.get_data(guild.id, db_name=DBModel.guilds)
            guild_data["player_controller"] = deepcopy(db_models[DBModel.guilds]["player_controller"])
            await self.bot.update_data(guild.id, guild_data, db_name=DBModel.guilds)
        except:
            traceback.print_exc()

        if not self.hook_url:
            return

        try:
            await self.send_hook(guild, title="I have been added to a new server", color=disnake.Color.green())
        except:
            traceback.print_exc()

        await self.bot.update_appinfo()

    async def send_hook(self, guild: disnake.Guild, title: str, color: disnake.Color):

        created_at = int(guild.created_at.timestamp())

        embed = disnake.Embed(
            description=f"__**{title}:**__\n"
                        f"```{guild.name}```\n"
                        f"**ID:** `{guild.id}`\n"
                        f"**Owner:** `{guild.owner} [{guild.owner.id}]`\n"
                        f"**Created at:** <t:{created_at}:f> - <t:{created_at}:R>\n"
                        f"**Verification level:** `{guild.verification_level or 'none'}`\n"
                        f"**Members:** `{len([m for m in guild.members if not m.bot])}`\n"
                        f"**Bots:** `{len([m for m in guild.members if m.bot])}`\n",
            color=color
        )

        try:
            embed.set_thumbnail(url=guild.icon.replace(static_format="png").url)
        except AttributeError:
            pass

        async with ClientSession() as session:
            webhook = disnake.Webhook.from_url(self.hook_url, session=session)
            await webhook.send(
                content=", ".join(f"<@{owner_id}>" for owner_id in self.bot.owner_ids) or self.bot.owner.mention,
                username=self.bot.user.name,
                avatar_url=self.bot.user.display_avatar.replace(size=256, static_format="png").url,
                embed=embed
            )


def setup(bot: BotCore):
    bot.add_cog(Misc(bot))
    bot.add_cog(GuildLog(bot))
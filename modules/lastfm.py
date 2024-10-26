from __future__ import annotations

import asyncio
import datetime
import traceback
from typing import TYPE_CHECKING, Optional
from urllib.parse import quote

import disnake
from aiohttp import ClientSession
from disnake.ext import commands

from utils.db import DBModel
from utils.music.errors import GenericError
from utils.music.lastfm_tools import LastFmException
from utils.music.models import LavalinkPlayer, LavalinkTrack
from utils.others import CustomContext, CommandArgparse

if TYPE_CHECKING:
    from utils.client import BotCore


class LastFMView(disnake.ui.View):

    def __init__(self, ctx, session_key: str):
        super().__init__(timeout=300)
        self.ctx = ctx
        self.interaction: Optional[disnake.MessageInteraction] = None
        self.session_key = ""
        self.username = ""
        self.token = ""
        self.last_timestamp = None
        self.auth_url = None
        self.skg = None
        self.network = None
        self.clear_session = False
        self.check_loop = None
        self.error = None
        self.cooldown = commands.CooldownMapping.from_cooldown(1, 15, commands.BucketType.user)


        if session_key:
            btn2 = disnake.ui.Button(label="Unlink last.fm account", style=disnake.ButtonStyle.red)
            btn2.callback = self.disconnect_account
            self.add_item(btn2)

        else:
            btn = disnake.ui.Button(label="Link last.fm account")
            btn.callback = self.send_authurl_callback
            self.add_item(btn)

    async def check_session_loop(self):

        count = 15

        while count > 0:
            try:
                await asyncio.sleep(20)
                data = await self.ctx.bot.last_fm.get_session_key(token=self.token)
                if data.get('error'):
                    count -= 1
                    continue
                self.session_key = data["session"]["key"]
                self.username = data["session"]["name"]
                self.stop()
                return
            except Exception as e:
                self.error = e
                self.auth_url = ""
                self.token = ""
                self.stop()
                return

        self.auth_url = ""
        self.token = ""

    async def interaction_check(self, interaction: disnake.MessageInteraction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.send("You cannot use that button", ephemeral=True)
            return False
        return True

    async def disconnect_account(self, interaction: disnake.MessageInteraction):
        self.clear_session = True
        self.session_key = ""
        self.interaction = interaction
        self.stop()

    async def send_authurl_callback(self, interaction: disnake.MessageInteraction):

        self.check_loop = self.ctx.bot.loop.create_task(self.check_session_loop())

        if not self.auth_url:
            self.token = await self.ctx.bot.last_fm.get_token()
            self.auth_url = f'http://www.last.fm/api/auth/?api_key={self.ctx.bot.last_fm.api_key}&token={self.token}'
            self.last_timestamp = int((disnake.utils.utcnow() + datetime.timedelta(minutes=5)).timestamp())

        await interaction.send(f"### [Click here](<{self.auth_url}>) to link your Last.fm account (on the page, click \"allow\")\n\n"
                               f"`The link expires in` <t:{self.last_timestamp}:R> `(If it has expired, click the button again).`\n\n"
                               f"`Attention: Do not share the \"click here\" link with anyone or post it in public places, "
                               f"as this link can grant access to your last.fm account`\n\n"
                               "`If you have already authorized the application, you must wait up to 20 seconds "
                               "for the above message to update and confirm the process.`",
                               ephemeral=True, delete_after=300)

class LastFmCog(commands.Cog):

    emoji = "🎧"
    name = "LastFM"
    desc_prefix = f"[{emoji} {name}] | "

    def __init__(self, bot: BotCore):
        self.bot = bot

    lastfm_cd = commands.CooldownMapping.from_cooldown(1, 45, commands.BucketType.member)
    lastfm_mc = commands.MaxConcurrency(1, per=commands.BucketType.user, wait=False)

    lastm_flags = CommandArgparse()
    lastm_flags.add_argument('-last_tracks', '-lasttracks', '-last', '-recents',
                             help="Number of recent songs to be displayed.\nEx: -last 5",
                             type=int, default=0)

    @commands.command(hidden=True, name="lastfm", aliases=["lastfmconnect", "lfm"],
                      description="Connect your last.fm account.", extras={"flags": lastm_flags},
                      cooldown=lastfm_cd, max_concurrency=lastfm_mc)
    async def lastfm_legacy(self, ctx: CustomContext, *flags):

        args, unknown = ctx.command.extras['flags'].parse_known_args(flags)

        if args.last_tracks > 7:
            args.last_tracks = 7

        await self.lastfm.callback(self=self, inter=ctx, last_tracks_amount=args.last_tracks)


    @commands.slash_command(hidden=True, name="lastfm",
                      description=f"{desc_prefix}Connect your last.fm account",
                      extras={"allow_private": True},
                      cooldown=lastfm_cd, max_concurrency=lastfm_mc)
    async def lastfm(
            self, inter: disnake.AppCmdInter,
            last_tracks_amount: int = commands.Param(
                name="recent_songs", description="Number of recent songs to be displayed.",
                default=0, min_value=0, max_value=7
            ),
    ):

        try:
            if not inter.permissions.embed_links:
                raise GenericError(f"**You do not have permission to send links/attachments in the channel <#{inter.channel_id}>**")
        except AttributeError:
            pass

        cog = self.bot.get_cog("Music")

        if cog:
            await inter.response.defer(ephemeral=await cog.is_request_channel(inter, ignore_thread=True))
        else:
            await inter.response.defer(ephemeral=True)

        data = await self.bot.get_global_data(inter.author.id, db_name=DBModel.users)

        lastfm_user = None

        if current_session_key:=data["lastfm"]["sessionkey"]:
            try:
                lastfm_user = await self.bot.last_fm.user_info(current_session_key)
            except LastFmException as e:
                if e.code == 9:
                    data["lastfm"]["sessionkey"] = ""
                    data["lastfm"]["username"] = ""
                    current_session_key = ""
                    await self.bot.update_global_data(inter.author.id, data, db_name=DBModel.users)
                else:
                    raise e

        else:
            data["lastfm"]["sessionkey"] = ""
            data["lastfm"]["username"] = ""
            current_session_key = ""
            await self.bot.update_global_data(inter.author.id, data, db_name=DBModel.users)

        embed_color = self.bot.get_color()

        if lastfm_user:

            name = lastfm_user['realname'] or lastfm_user['name']

            txt = f"> `👤` **⠂User:** [`{name}`](<{lastfm_user['url']}>)\n" \
                  f"> `⏰` **⠂Account created on:** <t:{lastfm_user['registered']['#text']}:f>\n"

            if lastfm_user['country']:
                txt += f"> `🌎` **⠂Country:** `{lastfm_user['country']}`\n"

            if playcount := lastfm_user['playcount']:
                txt += f"> `🔊` **⠂Total number of songs played:** [`{int(playcount):,}`](<https://www.last.fm/user/{lastfm_user['name']}/library>)\n"

            if playlists := lastfm_user['playlists'] != "0":
                txt += f"> `📄` **⠂Public playlists:** [`{int(playlists):,}`](<https://www.last.fm/user/{lastfm_user['name']}/playlists>)\n"

            try:
                slashcmd = f"</play:" + str(self.bot.get_global_command_named("play",
                                                                              cmd_type=disnake.ApplicationCommandType.chat_input).id) + ">"
            except AttributeError:
                slashcmd = "/play"

            txt += f"\n`Listen to your music in a voice channel using the` {slashcmd} `command to log them " \
                    f"to your last.fm account`\n"

            embeds = [disnake.Embed(
                description=txt, color=self.bot.get_color()
            ).set_thumbnail(url=lastfm_user['image'][-1]["#text"]).set_author(
                name="Last.fm: Linked account information",
                icon_url="https://www.last.fm/static/images/lastfm_avatar_twitter.52a5d69a85ac.png")]

            top_tracks = await self.bot.last_fm.user_top_tracks(data["lastfm"]["username"], limit=3)

            if top_tracks:

                embed = disnake.Embed(
                    description="\n".join(f"> ` {n+1}º ` [`{t['name']}`]({t['url']}) `De:` [`{t['artist']['name']}`]({t['artist']['url']}) `(x{int(t['playcount']):,})`" for n, t in enumerate(top_tracks)),
                    color=embed_color).set_author(name=f"Top 3: Songs you listened to the most (out of the total {int(lastfm_user['track_count']):,}):",
                    icon_url="https://i.ibb.co/Hhcwdf9/muse-heart-disc.jpg",
                    url=f"https://www.last.fm/user/{lastfm_user['name']}/library")

                if thumb:=top_tracks[0]['image'][-1]["#text"]:

                    if thumb.endswith("2a96cbd8b46e442fc41c2b86b821562f.png"):
                        t = top_tracks[0]
                        kw = {"track": t['name'], 'artist': t['artist']['name']}

                        try:
                            album = t['album']['#text']
                        except KeyError:
                            album = None

                        if album:
                            kw["album"] = album
                        r = await self.deezer_search(**kw)

                        for i in r:
                            if album and i['album']['title'] != album:
                                continue
                            thumb = i['album']['cover_big']
                            break

                    embed.set_thumbnail(url=thumb)

                embeds.append(embed)

            top_artists = await self.bot.last_fm.user_top_artists(data["lastfm"]["username"], limit=3)

            if top_artists:

                embed = disnake.Embed(
                    description="\n".join(f"> ` {n+1}º ` [`{t['name']}`]({t['url']}) `(x{int(t['playcount']):,})`" for n, t in enumerate(top_artists)),
                    color=embed_color).set_author(name=f"Top 3: Artists you listened to the most (out of the total {int(lastfm_user['artist_count']):,}):",
                    icon_url="https://i.ibb.co/8KQzkyy/muse-heart-artist-icon.jpg",
                    url=f"https://www.last.fm/user/{lastfm_user['name']}/library/artists")

                if thumb:=top_artists[0]['image'][-1]["#text"]:

                    if thumb.endswith("2a96cbd8b46e442fc41c2b86b821562f.png"):
                        t = top_artists[0]
                        r = await self.deezer_search(**{'artist': t['name']})

                        for i in r:
                            if i['artist']['name'].lower() == t['name'].lower():
                                thumb = i['artist']['picture_big']
                                break

                    embed.set_thumbnail(url=thumb)

                embeds.append(embed)

            top_albuns = await self.bot.last_fm.user_top_albums(data["lastfm"]["username"], limit=3)

            if top_albuns:

                embed = disnake.Embed(
                    description="\n".join(f"> ` {n+1}º ` [`{b['name']}`]({b['url']}) `de:` [`{b['artist']['name']}`]({b['artist']['url']}) `(x{int(b['playcount']):,})`" for n, b in enumerate(top_albuns)),
                    color=embed_color).set_author(name=f"Top 5: Albums you listened to the most (out of the total {int(lastfm_user['album_count']):,}):",
                    icon_url="https://i.ibb.co/s6TQK5D/muse-heart-disc-album.jpg",
                    url=f"https://www.last.fm/user/{lastfm_user['name']}/library/albums")

                if thumb:=top_albuns[0]['image'][-1]["#text"]:

                    if thumb.endswith("2a96cbd8b46e442fc41c2b86b821562f.png"):
                        t = top_albuns[0]
                        r = await self.deezer_search(**{"album": t['album']['#text'], 'artist': t['artist']['name']})

                        for i in r:
                            if t['album']['#text'].lower() == i['album']['title'].lower():
                                thumb = i['album']['cover_big']
                                break

                    embed.set_thumbnail(url=thumb)

                embeds.append(embed)

            if last_tracks_amount > 0:

                recenttracks = await self.bot.last_fm.user_recent_tracks(lastfm_user['name'])

                if recenttracks['track']:

                    for n, t in enumerate(recenttracks['track'][:last_tracks_amount]):
                        try:
                            txt = f"` {n+1}. ` [`{t['name']}`]({t['url']}) ( <t:{t['date']['uts']}:R> )\n"
                        except KeyError:
                            txt = f"`[▶️] Listening now:` [`{t['name']}`]({t['url']})\n"
                        artist_url = t['url'].split('/_/')[0]
                        t_embed = disnake.Embed(
                            color=embed_color,
                            description=f"{txt}`Artist:` [`{t['artist']['#text']}`]({artist_url})"
                        )

                        if n == 0:
                            t_embed.set_author(
                                icon_url="https://i.ibb.co/Qb3zjQ5/muse-heart-recently-played.jpg",
                                name=f"Recent music track{(s:='s'[:last_tracks_amount^1])} {s}you listened to:"
                            )

                        if t['album']['#text']:
                            t_embed.description += f" **-** `Album:` [`{t['album']['#text']}`]({artist_url}/{quote(t['album']['#text'])})"
                        t_embed.set_thumbnail(url=t['image'][0]['#text'] or 'https://i.ibb.co/pQPrKdw/lastfm-unknown-image.webp')
                        embeds.append(t_embed)

        else:
            embeds = [disnake.Embed(
                description="**Link (or create) an account on [last.fm](<https://www.last.fm/home>) "
                            "to log all the music you listen to here on your Last.fm profile. "
                            "This will allow you to get music/artist/album recommendations, have a general statistic of the music you listened to, "
                            "and gain access to an amazing community on the platform.**",
                color=embed_color
            ).set_thumbnail(url="https://www.last.fm/static/images/lastfm_avatar_twitter.52a5d69a85ac.png").
                      set_footer(text="Note: Music recognition will be ignored while you are listening to "
                                      "YouTube and SoundCloud")]

        view = LastFMView(inter, session_key=current_session_key)

        if isinstance(inter, CustomContext):
            msg = await inter.send(embeds=embeds, view=view)
            inter.store_message = msg
        else:
            msg = None
            await inter.edit_original_message(embeds=embeds, view=view)

        await view.wait()

        for c in view.children:
            c.disabled = True

        if not view.session_key and not view.clear_session:

            if view.error:
                raise view.error

            embeds[-1].set_footer(
                text="The time to interact with this message has expired.",
                icon_url="https://i.ibb.co/gb0cZQw/warning.png",
            )

            if msg:
                await msg.edit(embeds=embeds, view=view)
            else:
                await inter.edit_original_message(embeds=embeds, view=view)

            return

        newdata = {"scrobble": True, "sessionkey": view.session_key, "username": view.username}
        data["lastfm"].update(newdata)
        await self.bot.update_global_data(inter.author.id, data=data, db_name=DBModel.users)

        self.bot.pool.lastfm_sessions[inter.author.id] = newdata

        embeds[0].clear_fields()

        if view.interaction:
            func = view.interaction.response.edit_message
        elif msg:
            func = msg.edit
        else:
            func = inter.edit_original_message

        if view.session_key:
            embeds[0].description += f"\n### The account [{view.username}](<https://www.last.fm/user/{view.username}>) " \
                                 "has been successfully linked!\n\n`Now, when you listen to your music in the voice channel, " \
                                "it will be automatically logged to your last.fm account`"

            await func(embeds=embeds, view=view, content=None)

            for b in self.bot.pool.get_all_bots():
                for p in b.music.players.values():
                    try:
                        if inter.guild_id == p.guild_id and inter.author.id in p.last_channel.voice_states:
                            self.bot.loop.create_task(self.update_np(player=p))
                            return
                    except AttributeError:
                        continue

        else:
            embeds[-1].set_footer(
                text="Your account has been successfully unlinked!",
                icon_url="https://i.ibb.co/xLFhCd2/confirm.png",
            )
            await func(embeds=embeds, view=view, content=None)

    @commands.Cog.listener("on_voice_state_update")
    async def connect_vc_update(self, member: disnake.Member, before: disnake.VoiceState, after: disnake.VoiceState):

        if member.bot or not after.channel or before.channel == after.channel:
            return

        try:
            player: LavalinkPlayer = self.bot.music.players[member.guild.id]
        except KeyError:
            return

        if player.last_channel != after.channel:
            return

        try:
            if not player.current or member not in player.last_channel.members:
                return
        except AttributeError:
            return

        try:
            fm_user = player.lastfm_users[member.id]
        except KeyError:
            pass
        else:
            if fm_user["last_url"] == player.current.uri and fm_user["last_timestamp"] and datetime.datetime.utcnow() < fm_user["last_timestamp"]:
                return

        await self.startscrooble(player=player, track=player.last_track, users=[member])

    @commands.Cog.listener('on_wavelink_track_start')
    async def update_np(self, player: LavalinkPlayer):
        await self.startscrooble(player, track=player.last_track, update_np=True)

    @commands.Cog.listener('on_wavelink_track_end')
    async def startscrooble(self, player: LavalinkPlayer, track: LavalinkTrack, reason: str = None, update_np=False, users=None):

        if not track or track.is_stream or track.info["sourceName"] in ("local", "http"):
            return

        if not update_np:

            if reason != "FINISHED":
                return

            if track.duration < 20000:
                return

            if (disnake.utils.utcnow() - player.start_time).total_seconds() < ((track.duration * 0.75) / 1000):
                return

        counter = 3

        while counter > 0:
            if not player.guild.me.voice:
                await asyncio.sleep(2)
                continue
            break

        if not player.guild.me.voice:
            return

        if track.info["sourceName"] in ("youtube", "soundcloud"):

            if not track.album_name:
                return

            if track.ytid:
                if track.author.endswith(" - topic") and not track.author.endswith("Release - topic") and not track.title.startswith(track.author[:-8]):
                    name = track.title
                    artist = track.author[:-8]
                else:
                    try:
                        artist, name = track.title.split(" - ", maxsplit=1)
                    except ValueError:
                        name = track.title
                        artist = track.author
            else:
                name = track.single_title
                artist = track.author

            artist = artist.split(",")[0]

        else:
            artist = track.author.split(",")[0]
            name = track.single_title

        duration = int(track.duration / 1000)

        if not (album:=track.album_name) and not track.autoplay and track.info["sourceName"] in ("spotify", "deezer", "applemusic", "tidal"):
            album = track.single_title

        for user in users or player.last_channel.members:

            if user.bot:
                continue

            try:
                if user.voice.self_deaf or user.voice.deaf:
                    continue
            except AttributeError:
                continue

            try:
                fminfo = self.bot.pool.lastfm_sessions[user.id]
            except KeyError:
                user_data = await self.bot.get_global_data(user.id, db_name=DBModel.users)
                fminfo = user_data["lastfm"]
                self.bot.pool.lastfm_sessions[user.id] = fminfo

            if fminfo["scrobble"] is False or not fminfo["sessionkey"]:
                continue

            try:
                kwargs = {
                    "artist": artist, "track": name, "album": album, "duration": duration,
                    "session_key": fminfo["sessionkey"],
                }
                if update_np:
                    await self.bot.last_fm.update_nowplaying(**kwargs)
                else:
                    if track.requester != user.id:
                        kwargs["chosen_by_user"] = False
                    await self.bot.last_fm.track_scrobble(**kwargs)
            except Exception as e:
                if isinstance(e, LastFmException):
                    print(f"last.fm failed! user: {user.id} - code: {e.code} - message: {e.message}")
                    if e.code == 9:
                        user_data = await self.bot.get_global_data(user.id, db_name=DBModel.users)
                        user_data["lastfm"]["sessionkey"] = ""
                        await self.bot.update_global_data(user.id, user_data, db_name=DBModel.users)
                        try:
                            del self.bot.pool.lastfm_sessions[user.id]
                        except KeyError:
                            pass
                        try:
                            del player.lastfm_users[user.id]
                        except KeyError:
                            pass
                        continue
                traceback.print_exc()
                continue

            player.lastfm_users[user.id] = {
                "last_url": track.url,
                "last_timestamp": datetime.datetime.utcnow() + datetime.timedelta(seconds=duration)
            }

    async def deezer_search(self, **kwargs):

        query = " ".join(f'{k}:"{v}"' for k,v in kwargs.items())

        if not query:
            return

        base_url = 'https://api.deezer.com/search'
        params = {'q': query, 'strict': 'on'}

        async with ClientSession() as session:
            async with session.get(base_url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return data['data']
                else:
                    response.raise_for_status()


def setup(bot):
    if not bot.pool.config["LASTFM_KEY"] or not bot.pool.config["LASTFM_SECRET"]:
        print(("="*48) + "\n⚠️ - The Last.FM source will be disabled due to the lack of configuration of LASTFM_KEY and LASTFM_SECRET")
        return
    bot.add_cog(LastFmCog(bot))
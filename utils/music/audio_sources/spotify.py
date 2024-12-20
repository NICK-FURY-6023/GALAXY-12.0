# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import json
import os.path
import re
import time
from tempfile import gettempdir
from typing import Optional, TYPE_CHECKING, Union
from urllib.parse import quote

import aiofiles
from aiohttp import ClientSession

from utils.music.converters import fix_characters, URL_REG
from utils.music.errors import GenericError
from utils.music.models import PartialPlaylist, PartialTrack

if TYPE_CHECKING:
    from utils.client import BotCore

spotify_regex = re.compile("https://open.spotify.com?.+(album|playlist|artist|track)/([a-zA-Z0-9]+)")
spotify_link_regex = re.compile(r"(?i)https?:\/\/spotify\.link\/?(?P<id>[a-zA-Z0-9]+)")
spotify_regex_w_user = re.compile("https://open.spotify.com?.+(album|playlist|artist|track|user)/([a-zA-Z0-9]+)")

spotify_cache_file = os.path.join(gettempdir(), ".spotify_cache.json")


class SpotifyClient:

    def __init__(self, client_id: Optional[str] = None, client_secret: Optional[str] = None):

        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = "https://api.spotify.com/v1"
        self.spotify_cache = {}
        self.disabled = False
        self.type = "api" if client_id and client_secret else "visitor"

        try:
            with open(spotify_cache_file) as f:
                self.spotify_cache = json.load(f)
        except FileNotFoundError:
            pass

    async def request(self, path: str, params: dict = None):

        if self.disabled:
            return

        headers = {'Authorization': f'Bearer {await self.get_valid_access_token()}'}

        async with ClientSession() as session:
            async with session.get(f"{self.base_url}/{path}", headers=headers, params=params) as response:
                if response.status == 200:
                    return await response.json()
                elif response.status == 401:
                    await self.get_access_token()
                    return await self.request(path=path, params=params)
                elif response.status == 404:
                    raise GenericError("**There was no result for the provided link (please check if the link is correct or if its content is private or has been deleted).**\n\n"
                                       f"{str(response.url).replace('api.', 'open.').replace('/v1/', '/').replace('s/', '/')}")
                elif response.status == 429:
                    self.disabled = True
                    print(f"⚠️ - Spotify: Internal support disabled due to ratelimit (429).")
                    return
                else:
                    response.raise_for_status()

    async def get_track_info(self, track_id: str):
        return await self.request(path=f'tracks/{track_id}')

    async def get_album_info(self, album_id: str):
        return await self.request(path=f'albums/{album_id}')

    async def get_artist_top(self, artist_id: str):
        return await self.request(path=f'artists/{artist_id}/top-tracks')

    async def get_playlist_info(self, playlist_id: str):
        return await self.request(path=f"playlists/{playlist_id}")

    async def get_user_info(self, user_id: str):
        return await self.request(path=f"users/{user_id}")

    async def get_user_playlists(self, user_id: str):
        return await self.request(path=f"users/{user_id}/playlists")

    async def get_recommendations(self, seed_tracks: Union[list, str], limit=10):
        if isinstance(seed_tracks, str):
            track_ids = seed_tracks
        else:
            track_ids = ",".join(seed_tracks)

        return await self.request(path='recommendations', params={
            'seed_tracks': track_ids, 'limit': limit
        })

    async def track_search(self, query: str):
        return await self.request(path='search', params = {
        'q': query, 'type': 'track', 'limit': 10
        })

    async def get_access_token(self):

        if not self.client_id or not self.client_secret:
            access_token_url = "https://open.spotify.com/get_access_token?reason=transport&productType=embed"
            async with ClientSession() as session:
                async with session.get(access_token_url) as response:
                    data = await response.json()
                    self.spotify_cache = {
                        "access_token": data["accessToken"],
                        "expires_in": data["accessTokenExpirationTimestampMs"],
                        "expires_at": time.time() + data["accessTokenExpirationTimestampMs"],
                        "type": "visitor",
                    }
                    self.type = "visitor"
                    print("🎶 - Spotify access token successfully obtained for type: visitor.")

        else:
            token_url = 'https://accounts.spotify.com/api/token'

            headers = {
                'Authorization': 'Basic ' + base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
            }

            data = {
                'grant_type': 'client_credentials'
            }

            async with ClientSession() as session:
                async with session.post(token_url, headers=headers, data=data) as response:
                    data = await response.json()

                if data.get("error"):
                    print(f"⚠️ - Spotify: An error occurred while obtaining token: {data['error_description']}")
                    self.client_id = None
                    self.client_secret = None
                    await self.get_access_token()
                    return

                self.spotify_cache = data

                self.type = "api"

                self.spotify_cache["tyoe"] = "api"

                self.spotify_cache["expires_at"] = time.time() + self.spotify_cache["expires_in"]

                print("🎶 - Access token from Spotify successfully obtained via Official API.")

        async with aiofiles.open(spotify_cache_file, "w") as f:
            await f.write(json.dumps(self.spotify_cache))

    async def get_valid_access_token(self):
        if time.time() >= self.spotify_cache["expires_at"] or self.spotify_cache.get("type") != self.type:
            await self.get_access_token()
        return self.spotify_cache["access_token"]


    async def get_tracks(self, bot: BotCore, requester: int, query: str):

        if spotify_link_regex.match(query):
            async with bot.session.get(query, allow_redirects=False) as r:
                if 'location' not in r.headers:
                    raise GenericError("**Failed to retrieve result for the provided link...**")
                query = str(r.headers["location"])

        if not (matches := spotify_regex.match(query)) and not self.disabled:

            if URL_REG.match(query):
                return

            r = await self.track_search(query=query)

            tracks = []

            try:
                tracks_result = r['tracks']['items']
            except KeyError:
                pass
            else:
                for result in tracks_result:
                    t = PartialTrack(
                        uri=result["external_urls"]["spotify"],
                        author=result["artists"][0]["name"] or "Unknown Artist",
                        title=result["name"],
                        thumb=result["album"]["images"][0]["url"],
                        duration=result["duration_ms"],
                        source_name="spotify",
                        identifier=result["id"],
                        requester=requester
                    )

                    try:
                        t.info["isrc"] = result["external_ids"]["isrc"]
                    except KeyError:
                        pass

                    t.info["extra"]["authors"] = [fix_characters(i['name']) for i in result['artists'] if f"feat. {i['name'].lower()}"
                                                  not in result['name'].lower()]

                    t.info["extra"]["authors_md"] = ", ".join(f"[`{a['name']}`]({a['external_urls']['spotify']})" for a in result["artists"])

                    try:
                        if result["album"]["name"] != result["name"]:
                            t.info["extra"]["album"] = {
                                "name": result["album"]["name"],
                                "url": result["album"]["external_urls"]["spotify"]
                            }
                    except (AttributeError, KeyError):
                        pass

                    tracks.append(t)

                return tracks

            return

        if self.disabled:

            if [n for n in bot.music.nodes.values() if "spotify" in n.info.get("sourceManagers", [])]:
                return

            raise GenericError("**The support for Spotify links is temporarily disabled.**")

        url_type, url_id = matches.groups()

        if url_type == "track":

            result = await self.get_track_info(url_id)

            t = PartialTrack(
                uri=result["external_urls"]["spotify"],
                author=result["artists"][0]["name"] or "Unknown Artist",
                title=result["name"],
                thumb=result["album"]["images"][0]["url"],
                duration=result["duration_ms"],
                source_name="spotify",
                identifier=result["id"],
                requester=requester
            )

            try:
                t.info["isrc"] = result["external_ids"]["isrc"]
            except KeyError:
                pass

            t.info["extra"]["authors"] = [fix_characters(i['name']) for i in result['artists'] if f"feat. {i['name'].lower()}"
                                          not in result['name'].lower()]

            t.info["extra"]["authors_md"] = ", ".join(f"[`{a['name']}`]({a['external_urls']['spotify']})" for a in result["artists"])

            try:
                if result["album"]["name"] != result["name"]:
                    t.info["extra"]["album"] = {
                        "name": result["album"]["name"],
                        "url": result["album"]["external_urls"]["spotify"]
                    }
            except (AttributeError, KeyError):
                pass

            return [t]

        data = {
            'loadType': 'PLAYLIST_LOADED',
            'playlistInfo': {'name': ''},
            'sourceName': "spotify",
            'tracks_data': [],
            'is_album': False,
            "thumb": ""
        }

        if url_type == "album":

            result = await self.get_album_info(url_id)

            try:
                thumb = result["tracks"][0]["album"]["images"][0]["url"]
            except:
                thumb = ""

            if len(result["tracks"]) < 2:

                track = result["tracks"][0]

                t = PartialTrack(
                    uri=track["external_urls"]["spotify"],
                    author=track["artists"][0]["name"] or "Unknown Artist",
                    title=track["name"],
                    thumb=thumb,
                    duration=track["duration_ms"],
                    source_name="spotify",
                    identifier=track["id"],
                    requester=requester
                )

                try:
                    t.info["isrc"] = track["external_ids"]["isrc"]
                except KeyError:
                    pass

                t.info["extra"]["authors"] = [fix_characters(i['name']) for i in track['artists'] if
                                              f"feat. {i['name'].lower()}"
                                              not in track['name'].lower()]

                t.info["extra"]["authors_md"] = ", ".join(
                    f"[`{a['name']}`]({a['external_urls']['spotify']})" for a in track["artists"])

                try:
                    t.info["extra"]["album"] = {
                        "name": result["name"],
                        "url": result["external_urls"]["spotify"]
                    }
                except (AttributeError, KeyError):
                    pass

                return [t]

            data["playlistInfo"]["name"] = result["name"]
            data["playlistInfo"]["is_album"] = True

            for t in result["tracks"]["items"]:
                t["album"] = result

            tracks_data = result["tracks"]["items"]

        elif url_type == "artist":

            result = await self.get_artist_top(url_id)

            try:
                data["playlistInfo"]["name"] = "The most played songs: " + \
                                               [a["name"] for a in result["tracks"][0]["artists"] if a["id"] == url_id][0]
            except IndexError:
                data["playlistInfo"]["name"] = "The most played songs: " + result["tracks"][0]["artists"][0]["name"]
            tracks_data = result["tracks"]

        elif url_type == "playlist":
            result = await bot.spotify.get_playlist_info(url_id)
            data["playlistInfo"]["name"] = result["name"]
            data["playlistInfo"]["thumb"] = result["images"][0]["url"]
            tracks_data = [t["track"] for t in result["tracks"]["items"]]

        else:
            raise GenericError(f"**The Spotify link is not recognized/supported:**\n{query}")

        if not tracks_data:
            raise GenericError("**There were no results found in the provided Spotify link...**")

        data["playlistInfo"]["selectedTrack"] = -1
        data["playlistInfo"]["type"] = url_type

        playlist = PartialPlaylist(data, url=query)

        playlist_info = playlist if url_type != "album" else None

        for t in tracks_data:

            if not t:
                continue

            try:
                thumb = t["album"]["images"][0]["url"]
            except (IndexError, KeyError):
                thumb = ""

            track = PartialTrack(
                uri=t["external_urls"].get("spotify", f"https://www.youtube.com/results?search_query={quote(t['name'])}"),
                author=t["artists"][0]["name"] or "Unknown Artist",
                title=t["name"],
                thumb=thumb,
                duration=t["duration_ms"],
                source_name="spotify",
                identifier=t["id"],
                playlist=playlist_info,
                requester=requester
            )

            try:
                track.info["isrc"] = t["external_ids"]["isrc"]
            except KeyError:
                pass

            try:
                track.info["extra"]["album"] = {
                    "name": t["album"]["name"],
                    "url": t["album"]["external_urls"]["spotify"]
                }
            except (AttributeError, KeyError):
                pass

            if t["artists"][0]["name"]:
                track.info["extra"]["authors"] = [fix_characters(i['name']) for i in t['artists'] if f"feat. {i['name'].lower()}" not in t['name'].lower()]
                track.info["extra"]["authors_md"] = ", ".join(f"[`{fix_characters(a['name'])}`](<" + a['external_urls'].get('spotify', f'https://www.youtube.com/results?search_query={quote(t["name"])}') + ">)" for a in t['artists'])
            else:
                track.info["extra"]["authors"] = ["Unknown Artist"]
                track.info["extra"]["authors_md"] = "`Unknown Artist`"

            playlist.tracks.append(track)

        return playlist
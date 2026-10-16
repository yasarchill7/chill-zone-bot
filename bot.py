
import os
import asyncio
import discord
from discord.ext import commands
import yt_dlp

FFMPEG_PATH = "ffmpeg"

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="?", intents=intents)

YDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": False,
    "quiet": True,
    "default_search": "ytsearch",
    "ignoreerrors": True,
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

queues = {}
now_playing = {}
loop_song = {}
volumes = {}
autoplay = {}

def get_queue(guild_id):
    return queues.setdefault(guild_id, [])

async def extract_tracks(query):
    loop = asyncio.get_running_loop()

    def fetch():
        with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
            info = ydl.extract_info(query, download=False)

            if not info:
                return []

            entries = info.get("entries")
            if entries is not None:
                tracks = []
                for entry in entries:
                    if entry and entry.get("webpage_url"):
                        tracks.append({
                            "title": entry.get("title", "Unknown title"),
                            "url": entry["webpage_url"],
                        })
                return tracks

            return [{
                "title": info.get("title", "Unknown title"),
                "url": info.get("webpage_url", query),
            }]

    return await loop.run_in_executor(None, fetch)

async def start_next(ctx):
    guild_id = ctx.guild.id
    voice = ctx.guild.voice_client

    if not voice or not voice.is_connected():
        return

    if voice.is_playing() or voice.is_paused():
        return

    queue = get_queue(guild_id)

    if loop_song.get(guild_id) and guild_id in now_playing:
        track = now_playing[guild_id]
    elif queue:
        track = queue.pop(0)
        now_playing[guild_id] = track
    else:
        if autoplay.get(guild_id):
            await ctx.send(
                "Queue is empty. Add another song with `?play song name` 🎵"
            )
        return

    try:
        tracks = await extract_tracks(track["url"])
        if not tracks:
            await ctx.send(f"Couldn't load **{track['title']}**. Skipping it.")
            now_playing.pop(guild_id, None)
            await start_next(ctx)
            return

        # Re-extract the selected track's direct audio URL.
        loop = asyncio.get_running_loop()

        def fetch_audio():
            with yt_dlp.YoutubeDL({
                **YDL_OPTIONS,
                "noplaylist": True,
            }) as ydl:
                info = ydl.extract_info(track["url"], download=False)
                if info and "entries" in info:
                    info = next(
                        (entry for entry in info["entries"] if entry), None
                    )
                if not info or not info.get("url"):
                    raise ValueError("No audio stream found")
                return info["url"]

        audio_url = await loop.run_in_executor(None, fetch_audio)

        source = discord.PCMVolumeTransformer(
            discord.FFmpegPCMAudio(
                audio_url,
                executable=FFMPEG_PATH,
                **FFMPEG_OPTIONS,
            ),
            volume=volumes.get(guild_id, 0.5),
        )

        def after_play(error):
            if error:
                print(f"Playback error: {error}")

            future = asyncio.run_coroutine_threadsafe(
                after_track(ctx), bot.loop
            )

        voice.play(source, after=after_play)
        await ctx.send(f"🎶 Now playing: **{track['title']}**")

    except Exception as exc:
        print(f"Playback error: {type(exc).__name__}: {exc}")
        await ctx.send("Couldn't play that song. Trying the next one...")
        now_playing.pop(guild_id, None)
        await start_next(ctx)

async def after_track(ctx):
    guild_id = ctx.guild.id

    if loop_song.get(guild_id):
        # Repeat the same track after it ends.
        loop_song[guild_id] = True
    else:
        now_playing.pop(guild_id, None)

    await start_next(ctx)

@bot.event
async def on_ready():
    print(f"CHILL ZONE BOT online as {bot.user}")

@bot.command()
async def ping(ctx):
    await ctx.send("CHILL ZONE BOT is alive! 🔥")

@bot.command()
async def play(ctx, *, query: str):
    if not ctx.author.voice:
        await ctx.send("Join a voice channel first, bro! 🎧")
        return

    channel = ctx.author.voice.channel
    voice = ctx.guild.voice_client

    if voice is None:
        voice = await channel.connect()
    elif voice.channel != channel:
        await voice.move_to(channel)

    await ctx.send("Finding your music... 🎵")

    try:
        tracks = await extract_tracks(query)

        if not tracks:
            await ctx.send("No playable songs found.")
            return

        queue = get_queue(ctx.guild.id)
        voice = ctx.guild.voice_client

        if voice and (voice.is_playing() or voice.is_paused()):
            queue.extend(tracks)
            await ctx.send(f"Added **{len(tracks)}** song(s) to the queue! 📃")
        else:
            queue.extend(tracks)
            await start_next(ctx)

            if len(tracks) > 1:
                await ctx.send(
                    f"Added playlist with **{len(tracks)}** songs! 🔥"
                )

    except Exception as exc:
        print(f"Search error: {type(exc).__name__}: {exc}")
        await ctx.send("Couldn't find that music. Try another song or URL.")

@bot.command()
async def skip(ctx):
    voice = ctx.guild.voice_client
    if voice and (voice.is_playing() or voice.is_paused()):
        loop_song[ctx.guild.id] = False
        voice.stop()
        await ctx.send("Skipped! ⏭️")
    else:
        await ctx.send("Nothing is playing right now.")

@bot.command()
async def stop(ctx):
    voice = ctx.guild.voice_client
    if voice:
        loop_song[ctx.guild.id] = False
        get_queue(ctx.guild.id).clear()
        now_playing.pop(ctx.guild.id, None)
        voice.stop()
        await ctx.send("Playback stopped and queue cleared. 🛑")
    else:
        await ctx.send("I'm not in a voice channel.")

@bot.command()
async def leave(ctx):
    voice = ctx.guild.voice_client
    if voice:
        get_queue(ctx.guild.id).clear()
        now_playing.pop(ctx.guild.id, None)
        loop_song[ctx.guild.id] = False
        await voice.disconnect()
        await ctx.send("Left the voice channel. 👋")
    else:
        await ctx.send("I'm not in a voice channel.")

@bot.command()
async def pause(ctx):
    voice = ctx.guild.voice_client
    if voice and voice.is_playing():
        voice.pause()
        await ctx.send("Paused. ⏸️")
    else:
        await ctx.send("Nothing is playing.")

@bot.command()
async def resume(ctx):
    voice = ctx.guild.voice_client
    if voice and voice.is_paused():
        voice.resume()
        await ctx.send("Resumed! ▶️")
    else:
        await ctx.send("Music isn't paused.")

@bot.command()
async def queue(ctx):
    items = get_queue(ctx.guild.id)
    current = now_playing.get(ctx.guild.id)

    lines = []
    if current:
        lines.append(f"🎶 Playing: **{current['title']}**")

    lines.extend(
        f"{i}. {item['title']}" for i, item in enumerate(items, start=1)
    )

    if not lines:
        await ctx.send("The music queue is empty.")
    else:
        await ctx.send("📃 **Music Queue**\n" + "\n".join(lines[:20]))

@bot.command()
async def nowplaying(ctx):
    track = now_playing.get(ctx.guild.id)
    if track:
        await ctx.send(f"🎶 Now playing: **{track['title']}**")
    else:
        await ctx.send("Nothing is playing right now.")

@bot.command()
async def volume(ctx, amount: int):
    if not 0 <= amount <= 100:
        await ctx.send("Choose a volume from 0 to 100.")
        return

    guild_id = ctx.guild.id
    volumes[guild_id] = amount / 100
    voice = ctx.guild.voice_client

    if voice and voice.source:
        if isinstance(voice.source, discord.PCMVolumeTransformer):
            voice.source.volume = amount / 100

    await ctx.send(f"Volume set to **{amount}%** 🔊")

@bot.command()
async def clear(ctx):
    get_queue(ctx.guild.id).clear()
    await ctx.send("Upcoming songs cleared. 🗑️")

@bot.command()
async def remove(ctx, position: int):
    items = get_queue(ctx.guild.id)

    if position < 1 or position > len(items):
        await ctx.send("That queue position doesn't exist.")
        return

    removed = items.pop(position - 1)
    await ctx.send(f"Removed **{removed['title']}** from the queue.")

@bot.command()
async def loop(ctx):
    guild_id = ctx.guild.id
    loop_song[guild_id] = not loop_song.get(guild_id, False)

    if loop_song[guild_id]:
        await ctx.send("Current song loop enabled. 🔁")
    else:
        await ctx.send("Current song loop disabled.")

@bot.command()
async def autoplay(ctx):
    guild_id = ctx.guild.id
    autoplay[guild_id] = not autoplay.get(guild_id, False)

    if autoplay[guild_id]:
        await ctx.send(
            "Autoplay enabled. Add songs with `?play`; "
            "I'll continue through the queue. 🎵"
        )
    else:
        await ctx.send("Autoplay disabled.")

@bot.command()
async def commands_list(ctx):
    await ctx.send(
        "**🎵 CHILL ZONE MUSIC COMMANDS**\n"
        "`?play song or playlist URL` — Play or queue music\n"
        "`?skip` — Skip song\n"
        "`?pause` / `?resume` — Control playback\n"
        "`?queue` — Show queue\n"
        "`?nowplaying` — Current song\n"
        "`?volume 50` — Set volume\n"
        "`?remove 2` — Remove queue item\n"
        "`?clear` — Clear upcoming songs\n"
        "`?loop` — Toggle current-song loop\n"
        "`?autoplay` — Toggle queue continuation\n"
        "`?stop` — Stop and clear queue\n"
        "`?leave` — Leave voice chat\n"
        "`?ping` — Test bot\n"
        "`?commands_list` — Show commands"
    )

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Missing an argument. Try `?commands_list`.")
        return
    if isinstance(error, commands.BadArgument):
        await ctx.send("Invalid argument. Check the command and try again.")
        return
    print(f"Command error: {type(error).__name__}: {error}")


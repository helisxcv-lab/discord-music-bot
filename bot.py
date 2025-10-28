import os, re
from urllib.parse import urlparse, parse_qs
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import lavalink

load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
LL_HOST = os.getenv("LAVALINK_HOST", "127.0.0.1")
LL_PORT = int(os.getenv("LAVALINK_PORT", "2333"))
LL_PASSWORD = os.getenv("LAVALINK_PASSWORD", "youshallnotpass")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
AUTOJOIN_ON_JOIN = os.getenv("AUTOJOIN_ON_JOIN", "1") == "1"
PREFERRED_VC = os.getenv("AUTOJOIN_CHANNEL", "").strip()

def drive_to_media_url(s: str) -> str | None:
    if not s: return None
    def to_api(fid: str) -> str: return f"https://www.googleapis.com/drive/v3/files/{fid}?alt=media&key={GOOGLE_API_KEY}"
    def to_uc(fid: str) -> str:  return f"https://drive.usercontent.google.com/uc?id={fid}&export=download"
    use_api = bool(GOOGLE_API_KEY)
    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", s): return to_api(s) if use_api else to_uc(s)
    try: u = urlparse(s)
    except Exception: return None
    host = (u.netloc or "")
    if "drive.google.com" in host or "drive.usercontent.google.com" in host:
        m = re.search(r"/file/d/([^/]+)/", u.path or "")
        if m: return to_api(m.group(1)) if use_api else to_uc(m.group(1))
        qs = parse_qs(u.query or "")
        if "id" in qs and qs["id"]: return to_api(qs["id"][0]) if use_api else to_uc(qs["id"][0])
    return None

intents = discord.Intents.none()
intents.guilds = True
intents.voice_states = True
bot = commands.Bot(command_prefix=None, intents=intents)

class LavalinkVoiceClient(discord.VoiceClient):
    async def on_voice_server_update(self, data):
        await bot.lavalink.voice_update_handler(data)
    async def on_voice_state_update(self, data):
        if data.user_id == bot.user.id:
            await bot.lavalink.voice_update_handler(data)
    async def connect(self, *, timeout: float, reconnect: bool, self_deaf: bool = False, self_mute: bool = False):
        await self.channel.guild.change_voice_state(
            channel=self.channel,
            self_deaf=self_deaf if hasattr(self, "self_deaf") else self_deaf,
            self_mute=self_mute if hasattr(self, "self_mute") else self_mute
        )
    async def disconnect(self, *, force: bool):
        await self.channel.guild.change_voice_state(channel=None)

def get_player(gid: int):
    return bot.lavalink.player_manager.create(gid)

def can_join(ch: discord.VoiceChannel) -> bool:
    perms = ch.permissions_for(ch.guild.me)
    return perms.connect and perms.speak

async def pick_voice_channel(guild: discord.Guild) -> discord.VoiceChannel | None:
    if PREFERRED_VC:
        for ch in guild.voice_channels:
            if ch.name == PREFERRED_VC and can_join(ch):
                return ch
    best, cnt = None, -1
    for ch in guild.voice_channels:
        c = len(getattr(ch, "members", []))
        if c > cnt and can_join(ch):
            best, cnt = ch, c
    return best

@bot.event
async def on_ready():
    if not hasattr(bot, "lavalink"):
        bot.lavalink = lavalink.Client(bot.user.id)
        bot.lavalink.add_node(host=LL_HOST, port=LL_PORT, password=LL_PASSWORD, region="asia")
        bot.add_listener(bot.lavalink.voice_update_handler, "on_socket_response")
    try:
        await bot.tree.sync()
    except Exception:
        pass
    print(f"Logged in as {bot.user}")
    if AUTOJOIN_ON_JOIN:
        for g in bot.guilds:
            if g.voice_client:
                continue
            ch = await pick_voice_channel(g)
            if ch and len(getattr(ch, "members", [])) > 0:
                try:
                    await ch.connect(cls=LavalinkVoiceClient)
                    bot.lavalink.player_manager.create(g.id)
                    print(f"[AUTOJOIN] Joined {g.name} / {ch.name}")
                except Exception as e:
                    print(f"[AUTOJOIN] Failed on {g.name}: {e}")

@bot.event
async def on_guild_join(guild: discord.Guild):
    if not AUTOJOIN_ON_JOIN:
        return
    ch = await pick_voice_channel(guild)
    if ch and len(getattr(ch, "members", [])) > 0:
        try:
            await ch.connect(cls=LavalinkVoiceClient)
            bot.lavalink.player_manager.create(guild.id)
            print(f"[AUTOJOIN] Joined {guild.name} / {ch.name}")
        except Exception as e:
            print(f"[AUTOJOIN] Failed on {guild.name}: {e}")

@bot.tree.command(description="현재 음성 채널로 봇을 초대합니다.")
async def join(interaction: discord.Interaction):
    if not interaction.user.voice or not interaction.user.voice.channel:
        return await interaction.response.send_message("먼저 음성 채널에 들어가줘!", ephemeral=True)
    ch = interaction.user.voice.channel
    await interaction.response.defer(ephemeral=True)
    await ch.connect(cls=LavalinkVoiceClient)
    bot.lavalink.player_manager.create(interaction.guild_id)
    await interaction.followup.send(f"입장: {ch.name}")

@bot.tree.command(description="지정 음성 채널로 바로 소환")
@app_commands.describe(channel="봇을 보낼 음성 채널")
async def summon(interaction: discord.Interaction, channel: discord.VoiceChannel):
    await interaction.response.defer(ephemeral=True)
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.disconnect(force=True)
    await channel.connect(cls=LavalinkVoiceClient)
    bot.lavalink.player_manager.create(interaction.guild_id)
    await interaction.followup.send(f"입장: {channel.name}")

@bot.tree.command(description="노래 재생 (Drive 파일ID/URL 또는 직접 오디오 URL)")
@app_commands.describe(query="Drive 파일ID/URL 또는 직접 오디오 URL")
async def play(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    if not interaction.guild.voice_client:
        if interaction.user.voice and interaction.user.voice.channel:
            await interaction.user.voice.channel.connect(cls=LavalinkVoiceClient)
        bot.lavalink.player_manager.create(interaction.guild_id)
    url = drive_to_media_url(query) or (query if query.startswith("http") else None)
    if not url:
        return await interaction.followup.send("드라이브 파일ID/URL 또는 직접 오디오 URL을 넣어줘.")
    results = await bot.lavalink.get_tracks(url)
    if not results or not results.get("tracks"):
        return await interaction.followup.send("트랙을 찾지 못했어. 파일 공개 상태와 링크 형식을 확인해줘.")
    track = results["tracks"][0]
    p = get_player(interaction.guild_id)
    p.add(requester=interaction.user.id, track=track)
    if not p.is_playing:
        await p.play()
    title = track["info"].get("title", "Unknown")
    await interaction.followup.send(f"▶️ 재생: {title}")

@bot.tree.command(description="지금 곡")
async def np(interaction: discord.Interaction):
    p = get_player(interaction.guild_id)
    if not p.current:
        return await interaction.response.send_message("재생 중인 곡이 없어.")
    await interaction.response.send_message(f"🎶 Now Playing: {p.current['info'].get('title','Unknown')}")

@bot.tree.command(description="대기열")
async def queue(interaction: discord.Interaction):
    p = get_player(interaction.guild_id)
    if not p.queue:
        return await interaction.response.send_message("대기열이 비었어.")
    lines = [f"{i}. {t['info'].get('title','Unknown')}" for i, t in enumerate(p.queue, start=1)]
    await interaction.response.send_message("**대기열**\n" + "\n".join(lines))

@bot.tree.command(description="컨트롤")
@app_commands.describe(action="pause/resume/skip/stop/leave")
async def control(interaction: discord.Interaction, action: str):
    p = get_player(interaction.guild_id); a = action.lower()
    if a == "pause":
        await p.set_pause(True); msg="⏸️ 일시정지"
    elif a == "resume":
        await p.set_pause(False); msg="▶️ 재개"
    elif a == "skip":
        await p.skip(); msg="⏭️ 스킵"
    elif a == "stop":
        await p.stop(); p.queue.clear(); msg="⏹️ 정지 & 큐 비움"
    elif a == "leave":
        if interaction.guild.voice_client:
            await interaction.guild.voice_client.disconnect(force=True)
        msg="👋 나갈게!"
    else:
        msg="pause/resume/skip/stop/leave 중 하나"
    await interaction.response.send_message(msg)

bot.run(DISCORD_TOKEN)

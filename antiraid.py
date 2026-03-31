import discord
from discord import app_commands, ui
from discord.ext import commands
import os, asyncio, sqlite3, aiohttp
from datetime import datetime, UTC
from dotenv import load_dotenv

# ────────────────────────────────────────────────
# 1. INITIALIZATION
# ────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, 'key.env')
DB_PATH = os.path.join(BASE_DIR, 'security.db')

load_dotenv(ENV_PATH)
TOKEN = os.getenv('TOKEN')

def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

with get_db_connection() as conn:
    conn.execute("CREATE TABLE IF NOT EXISTS backups (guild_id INTEGER, name TEXT, type TEXT, position INTEGER, label TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS backup_roles (guild_id INTEGER, name TEXT, color INTEGER, permissions INTEGER, position INTEGER, label TEXT)")
    conn.commit()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="$", intents=intents, help_command=None)

# ────────────────────────────────────────────────
# 2. BACKUP & RESTORE
# ────────────────────────────────────────────────
def security_embed(title, description, color=0x000000):
    e = discord.Embed(title=f"🛡️ {title}", description=description, color=color)
    e.timestamp = datetime.now(UTC)
    return e

@bot.command(name="backup")
@commands.has_permissions(administrator=True)
async def backup_server(ctx, label: str):
    with get_db_connection() as conn:
        conn.execute("DELETE FROM backups WHERE label = ?", (label,))
        conn.execute("DELETE FROM backup_roles WHERE label = ?", (label,))
        for c in ctx.guild.channels:
            conn.execute("INSERT INTO backups (guild_id, name, type, position, label) VALUES (?, ?, ?, ?, ?)",
                         (ctx.guild.id, c.name, str(c.type), c.position, label))
        for r in ctx.guild.roles:
            if not r.is_default() and not r.managed:
                conn.execute("INSERT INTO backup_roles (guild_id, name, color, permissions, position, label) VALUES (?, ?, ?, ?, ?, ?)",
                             (ctx.guild.id, r.name, r.color.value, r.permissions.value, r.position, label))
        conn.commit()
    await ctx.send(f"✅ **Global Backup '{label}' Saved.**")

@bot.command(name="viewrestore")
@commands.has_permissions(administrator=True)
async def view_restore(ctx):
    with get_db_connection() as conn:
        data = conn.execute("SELECT label, COUNT(*) as count FROM backups GROUP BY label").fetchall()
    if not data:
        return await ctx.send("❌ No backups found.")
    embed = security_embed("Global Backups", "Available snapshots for restore:")
    for row in data:
        embed.add_field(name=f"📦 {row['label']}", value=f"Contains `{row['count']}` items.", inline=True)
    await ctx.send(embed=embed)

@bot.command(name="restore")
@commands.has_permissions(administrator=True)
async def restore_server(ctx, label: str):
    with get_db_connection() as conn:
        chan_data = conn.execute("SELECT * FROM backups WHERE label = ? ORDER BY position ASC", (label,)).fetchall()
        role_data = conn.execute("SELECT * FROM backup_roles WHERE label = ? ORDER BY position ASC", (label,)).fetchall()
    if not chan_data and not role_data:
        return await ctx.send(f"❌ Backup `{label}` not found.")
    await ctx.send(f"🔄 **Cloning '{label}' into this server...**")
    for r in role_data:
        try:
            await ctx.guild.create_role(name=r['name'], color=discord.Color(r['color']), permissions=discord.Permissions(r['permissions']))
        except:
            pass
    for c in chan_data:
        try:
            if 'text' in c['type'].lower(): await ctx.guild.create_text_channel(c['name'])
            elif 'voice' in c['type'].lower(): await ctx.guild.create_voice_channel(c['name'])
            elif 'category' in c['type'].lower(): await ctx.guild.create_category(c['name'])
        except:
            pass
    await ctx.send(f"✅ **Restore of '{label}' complete.**")

# ────────────────────────────────────────────────
# 3. LEAK COMMAND - FIXED
# ────────────────────────────────────────────────
async def search_songs(query: str, limit: int = 25):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://juicewrldapi.com/juicewrld/songs/",
                params={"search": query, "page": 1}
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
        results = data.get("results", []) if isinstance(data, dict) else []
        return results[:limit]
    except:
        return []

def get_download_url(song):
    path = song.get("path") or song.get("file_path") or song.get("audio_path")
    if path:
        # URL-encode basic safety
        safe_path = path.replace(" ", "%20")
        return f"https://juicewrldapi.com/juicewrld/files/download/?path={safe_path}"
    return song.get("download_url") or song.get("audio_url")

class SongSelect(ui.Select):
    def __init__(self, songs: list):
        options = []
        for song in songs:
            title = song.get("title") or song.get("name") or "Unknown"
            # Safe short description (max 97 chars)
            era = song.get("era")
            if isinstance(era, dict):
                era_name = era.get("name", "N/A")
            else:
                era_name = str(era) if era else "N/A"
            desc = f"{song.get('category', 'Unreleased')} • {era_name}"
            if len(desc) > 97:
                desc = desc[:94] + "..."
            
            options.append(discord.SelectOption(
                label=(title[:90] + "...") if len(title) > 90 else title,
                value=str(song.get("id") or song.get("public_id") or ""),
                description=desc
            ))
        
        super().__init__(placeholder="Select a song to view details...", min_values=1, max_values=1, options=options)
        self.songs = songs

    async def callback(self, interaction: discord.Interaction):
        selected_id = self.values[0]
        song = next((s for s in self.songs if str(s.get("id") or s.get("public_id")) == selected_id), None)
        if not song:
            return await interaction.response.send_message("Song not found.", ephemeral=True)

        await self.show_song_details(interaction, song)

    async def show_song_details(self, interaction: discord.Interaction, song):
        title = song.get("title") or song.get("name", "Unknown Song")
        embed = discord.Embed(title=title, color=0x1e1e1e)

        era = song.get("era")
        era_name = era.get("name") if isinstance(era, dict) else str(era) if era else "N/A"
        embed.description = f"**Category:** {song.get('category', 'Unreleased')} • **Era:** {era_name}"

        embed.add_field(name="Artists", value=song.get("credited_artists") or song.get("artists", "Juice WRLD"), inline=True)
        embed.add_field(name="Producers", value=song.get("producers", "N/A"), inline=True)
        embed.add_field(name="Length", value=song.get("length", "N/A"), inline=True)
        embed.add_field(name="Recorded", value=song.get("record_dates") or song.get("recorded_date", "N/A"), inline=True)

        aka = song.get("also_known_as") or ", ".join(song.get("track_titles", [])[:3])
        if aka and aka != title:
            embed.add_field(name="Also Known As", value=str(aka)[:500], inline=False)

        image_url = song.get("image_url") or song.get("image")
        if image_url:
            if not image_url.startswith("http"):
                image_url = f"https://juicewrldapi.com{image_url}"
            embed.set_image(url=image_url)

        embed.set_footer(text="Powered by juicewrldapi.com")

        view = SongView(song)
        await interaction.response.edit_message(embed=embed, view=view)

class DownloadButton(ui.Button):
    def __init__(self, song):
        self.song = song
        self.download_url = get_download_url(song)
        super().__init__(
            label="⬇️ Download",
            style=discord.ButtonStyle.primary,
            disabled=self.download_url is None
        )

    async def callback(self, interaction: discord.Interaction):
        if self.download_url:
            await interaction.response.send_message(
                f"**{self.song.get('title') or self.song.get('name')}**\n{self.download_url}",
                ephemeral=True
            )
        else:
            await interaction.response.send_message("❌ No download link available for this song.", ephemeral=True)

class SongView(ui.View):
    def __init__(self, song):
        super().__init__(timeout=180)
        self.add_item(DownloadButton(song))

# Prefix Command
@bot.command(name="leak")
async def leak_prefix(ctx, *, song_name: str):
    await ctx.send(f"🔍 Searching for **{song_name}**...")

    songs = await search_songs(song_name)

    if not songs:
        return await ctx.send(f"❌ No results found for **{song_name}**.")

    if len(songs) == 1:
        song = songs[0]
        embed = discord.Embed(title=song.get("title") or song.get("name", "Unknown Song"), color=0x1e1e1e)
        # (You can copy the full embed from show_song_details if you want)
        view = SongView(song)
        await ctx.send(embed=embed, view=view)   # Simplified for now - it will show basic info
    else:
        view = ui.View(timeout=90)
        select = SongSelect(songs)
        view.add_item(select)
        await ctx.send(f"**Found {len(songs)} matches for `{song_name}`.**\nSelect one below:", view=view)

# Slash Command (same logic)
@bot.tree.command(name="leak", description="Search Juice WRLD songs")
@app_commands.describe(song_name="Song name or keyword")
async def leak_slash(interaction: discord.Interaction, song_name: str):
    await interaction.response.defer()
    songs = await search_songs(song_name)

    if not songs:
        return await interaction.followup.send(f"❌ No results found for **{song_name}**.")

    if len(songs) == 1:
        await SongSelect([songs[0]]).show_song_details(interaction, songs[0])  # Re-use the function
    else:
        view = ui.View(timeout=90)
        view.add_item(SongSelect(songs))
        await interaction.followup.send(f"**Found {len(songs)} matches for `{song_name}`.** Select one:", view=view)

# ────────────────────────────────────────────────
# EVENTS
# ────────────────────────────────────────────────
@bot.event
async def on_ready():
    try:
        await bot.tree.sync()
        print(f"✅ Bot is online: {bot.user} | Commands synced")
    except Exception as e:
        print(f"Sync error: {e}")

bot.run(TOKEN)

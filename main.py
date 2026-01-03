import nextcord
from nextcord.ext import commands
from nextcord import Interaction, SlashOption
import json
import os
import asyncio
import chat_exporter
from threading import Thread
from flask import Flask

# ================= CONFIGURATION =================
# ใช้ Environment Variables จาก Render เป็นหลัก ถ้าไม่มีให้ใช้ค่า Default หรืออ่านจากไฟล์
# คุณต้องไปตั้งค่า Environment Variables ในหน้า Dashboard ของ Render

TOKEN = os.environ.get("TOKEN")
LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID", 0))
TICKET_CATEGORY_ID = int(os.environ.get("TICKET_CATEGORY_ID", 0))

# ส่วน Ticket Count ยังต้องใช้ไฟล์ชั่วคราว (ข้อควรระวัง: เลขจะรีเซ็ตเมื่อ Deploy ใหม่)
CONFIG_FILE = "ticket_count.json"
TRANSCRIPTS_DIR = "transcripts"

def get_ticket_count():
    if not os.path.exists(CONFIG_FILE):
        return 0
    with open(CONFIG_FILE, "r") as f:
        data = json.load(f)
        return data.get("count", 0)

def save_ticket_count(count):
    with open(CONFIG_FILE, "w") as f:
        json.dump({"count": count}, f)

def is_ticket_channel(channel):
    return channel.name.startswith("ticket-")

intents = nextcord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(intents=intents)

# ================= FLASK SERVER (KEEP ALIVE) =================
app = Flask('')

@app.route('/')
def home():
    return "Bot is running!"

def run():
  app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# ================= TRANSCRIPT =================
async def create_transcript_file(channel: nextcord.TextChannel):
    guild_id = str(channel.guild.id)
    os.makedirs(os.path.join(TRANSCRIPTS_DIR, guild_id), exist_ok=True)

    file_name = f"{channel.name}-{channel.id}.html"
    html_path = os.path.join(TRANSCRIPTS_DIR, guild_id, file_name)

    transcript = await chat_exporter.export(
        channel,
        limit=None,
        tz_info="Asia/Bangkok",
        bot=channel.guild.me
    )

    if transcript is None:
        return None

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(transcript)

    return html_path

# ================= VIEW =================
class CloseTicket(nextcord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.closed = False

    @nextcord.ui.button(label="", emoji="🔒", custom_id="close") # เปลี่ยน Emoji เป็น Default เพื่อป้องกัน error ถ้าบอทไม่อยู่ในเซิฟที่เก็บ Emoji
    async def close(self, button: nextcord.ui.Button, interaction: Interaction):
        if self.closed:
            await interaction.response.send_message("Ticket is already being closed", ephemeral=True)
            return

        self.closed = True
        button.disabled = True
        await interaction.message.edit(view=self)

        await interaction.response.send_message("Closing ticket, generating transcript...")

        html_file_path = await create_transcript_file(interaction.channel)

        if LOG_CHANNEL_ID:
            log_channel = bot.get_channel(LOG_CHANNEL_ID)
            if log_channel and html_file_path:
                file_to_send = nextcord.File(html_file_path, filename=f"transcript-{interaction.channel.name}.html")
                await log_channel.send(
                    content=f"📝 **Transcript Log**\nTicket: {interaction.channel.name}\nClosed by: {interaction.user.mention}",
                    file=file_to_send
                )

        await asyncio.sleep(3)
        await interaction.channel.delete()

class OpenTicketView(nextcord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @nextcord.ui.button(label="Open Ticket", emoji="📩", custom_id="open")
    async def open_ticket(self, button: nextcord.ui.Button, interaction: Interaction):
        
        current_count = get_ticket_count() + 1
        save_ticket_count(current_count)
        
        ticket_number = current_count

        category = interaction.guild.get_channel(TICKET_CATEGORY_ID)
        
        # ตรวจสอบว่ามี Category หรือไม่
        if not category:
             await interaction.response.send_message("Error: Ticket Category ID not found configuration.", ephemeral=True)
             return

        overwrites = {
            interaction.guild.default_role: nextcord.PermissionOverwrite(view_channel=False),
            interaction.user: nextcord.PermissionOverwrite(view_channel=True, send_messages=True)
        }

        channel = await interaction.guild.create_text_channel(
            name=f"ticket-{ticket_number:04d}", # จัดรูปแบบเป็น 0001, 0002
            category=category,
            overwrites=overwrites
        )

        embed = nextcord.Embed(title="Ticket Chat", description="Support will be with you shortly.", color=0x2f3136)
        message = await channel.send(content=f"{interaction.user.mention}", embed=embed, view=CloseTicket())
        await message.pin()

        await interaction.response.send_message(f"Ticket created: {channel.mention}", ephemeral=True)

# ================= COMMANDS =================
@bot.slash_command(name="panel", description="Create ticket panel")
async def ticketpanel(interaction: Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("No permission", ephemeral=True)

    embed = nextcord.Embed(title="Ticket System", description="Click to open a ticket", color=0x2f3136)
    await interaction.channel.send(embed=embed, view=OpenTicketView())
    await interaction.response.send_message("Panel created", ephemeral=True)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    bot.add_view(OpenTicketView())
    bot.add_view(CloseTicket())

# เริ่ม Web Server ก่อนเริ่มบอท
keep_alive()
bot.run(TOKEN)

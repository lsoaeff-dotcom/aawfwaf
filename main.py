import nextcord
from nextcord.ext import commands
from nextcord import Interaction, SlashOption
import json
import os
import asyncio
import chat_exporter
from threading import Thread
from flask import Flask, send_from_directory

# ================= CONFIGURATION =================
TOKEN = os.environ.get("TOKEN")
LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID", 0))
TICKET_CATEGORY_ID = int(os.environ.get("TICKET_CATEGORY_ID", 0))

# ใส่ URL เว็บของคุณที่ได้จาก Render (ไม่ต้องมี / ปิดท้าย)
# เช่น https://my-ticket-bot.onrender.com
# แนะนำให้ไปตั้งใน Environment Variable ชื่อ APP_URL จะดีที่สุด
APP_URL = os.environ.get("APP_URL", "https://aawfwaf.onrender.com/")

CONFIG_FILE = "ticket_count.json"
TRANSCRIPTS_DIR = "transcripts" # โฟลเดอร์เก็บไฟล์

# ตรวจสอบและสร้างโฟลเดอร์เก็บไฟล์
if not os.path.exists(TRANSCRIPTS_DIR):
    os.makedirs(TRANSCRIPTS_DIR)

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

# ================= FLASK SERVER (WEB & TRANSCRIPT HOST) =================
app = Flask('')

@app.route('/')
def home():
    return "Bot is running!"

# เพิ่ม Route สำหรับเปิดดูไฟล์ Transcript ผ่านเว็บ
@app.route('/transcripts/<path:filename>')
def serve_transcript(filename):
    return send_from_directory(os.path.abspath(TRANSCRIPTS_DIR), filename)

def run():
  app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# ================= TRANSCRIPT GEN =================
async def create_transcript_url(channel: nextcord.TextChannel):
    # ตั้งชื่อไฟล์ให้ไม่ซ้ำ (ใช้ ID)
    file_name = f"{channel.name}-{channel.id}.html"
    html_path = os.path.join(TRANSCRIPTS_DIR, file_name)

    # สร้าง HTML
    transcript = await chat_exporter.export(
        channel,
        limit=None,
        tz_info="Asia/Bangkok",
        bot=channel.guild.me
    )

    if transcript is None:
        return None

    # บันทึกไฟล์ลงเครื่อง (ชั่วคราวบน Render)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(transcript)

    # สร้างลิงก์ URL
    full_url = f"{APP_URL}/transcripts/{file_name}"
    return full_url

# ================= VIEW =================
class CloseTicket(nextcord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.closed = False

    @nextcord.ui.button(label="", emoji="<:approve:1431941755439153332>", custom_id="close")
    async def close(self, button: nextcord.ui.Button, interaction: Interaction):
        if self.closed:
            await interaction.response.send_message("Ticket is already being closed", ephemeral=True)
            return

        self.closed = True
        button.disabled = True
        await interaction.message.edit(view=self)

        await interaction.response.send_message("Closing ticket, generating link...")

        # สร้างลิงก์
        transcript_url = await create_transcript_url(interaction.channel)

        if LOG_CHANNEL_ID:
            log_channel = bot.get_channel(LOG_CHANNEL_ID)
            if log_channel and transcript_url:
                # ส่งเป็น Embed พร้อมลิงก์กดได้เลย
                embed = nextcord.Embed(
                    title="Ticket Transcript",
                    description=f"**Ticket:** {interaction.channel.name}\n**Closed by:** {interaction.user.mention}\n\n[Click here to view Transcript]({transcript_url})",
                    color=0x00ff00
                )
                embed.set_footer(text="Link will expire if bot restarts (Render Free Tier)")
                
                await log_channel.send(embed=embed)

        await asyncio.sleep(3)
        await interaction.channel.delete()

class OpenTicketView(nextcord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @nextcord.ui.button(label="", emoji="<:idk:1431941766893932626>", custom_id="open")
    async def open_ticket(self, button: nextcord.ui.Button, interaction: Interaction):
        
        current_count = get_ticket_count() + 1
        save_ticket_count(current_count)
        ticket_number = current_count

        category = interaction.guild.get_channel(TICKET_CATEGORY_ID)
        if not category:
             await interaction.response.send_message("Error: Category ID not set.", ephemeral=True)
             return

        overwrites = {
            interaction.guild.default_role: nextcord.PermissionOverwrite(view_channel=False),
            interaction.user: nextcord.PermissionOverwrite(view_channel=True, send_messages=True)
        }

        channel = await interaction.guild.create_text_channel(
            name=f"ticket-{ticket_number:04d}",
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

keep_alive()
bot.run(TOKEN)

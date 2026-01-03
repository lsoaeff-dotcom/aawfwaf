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
# ดึงค่าจาก Render Environment Variables
TOKEN = os.environ.get("TOKEN")
LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID", 0))
TICKET_CATEGORY_ID = int(os.environ.get("TICKET_CATEGORY_ID", 0))

# URL เว็บของคุณ (Render จะให้มา)
# ถ้าไม่ตั้งใน Env Var จะใช้ค่า Default นี้ (อย่าลืมไปแก้ใน Render)
APP_URL = os.environ.get("APP_URL", "https://your-app.onrender.com")

# เลขเริ่มต้นของ Ticket (ตั้งใน Render Key: START_TICKET)
DEFAULT_START_TICKET = int(os.environ.get("START_TICKET", 1221))

CONFIG_FILE = "ticket_count.json"
TRANSCRIPTS_DIR = "transcripts"

# สร้างโฟลเดอร์เก็บไฟล์
if not os.path.exists(TRANSCRIPTS_DIR):
    os.makedirs(TRANSCRIPTS_DIR)

# ฟังก์ชันจัดการเลข Ticket
def get_ticket_count():
    # 1. ถ้ามีไฟล์เซฟอยู่แล้ว (บอทยังไม่ดับ) ให้ใช้ค่าล่าสุด
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
                return data.get("count", DEFAULT_START_TICKET)
        except:
            pass
    
    # 2. ถ้าไม่มีไฟล์ (เพิ่ง Deploy ใหม่) ให้ใช้ค่าจาก START_TICKET
    return DEFAULT_START_TICKET

def save_ticket_count(count):
    with open(CONFIG_FILE, "w") as f:
        json.dump({"count": count}, f)

def is_ticket_channel(channel):
    return channel.name.startswith("ticket-")

# ตั้งค่า Bot
intents = nextcord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(intents=intents)

# ================= FLASK SERVER (เว็บเซิร์ฟเวอร์) =================
app = Flask('')

@app.route('/')
def home():
    return "Bot is running & serving transcripts!"

# ลิงก์สำหรับเปิดดูไฟล์ HTML
@app.route('/transcripts/<path:filename>')
def serve_transcript(filename):
    return send_from_directory(os.path.abspath(TRANSCRIPTS_DIR), filename)

def run():
  app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# ================= TRANSCRIPT SYSTEM =================
async def create_transcript_url(channel: nextcord.TextChannel):
    # ตั้งชื่อไฟล์
    file_name = f"{channel.name}-{channel.id}.html"
    html_path = os.path.join(TRANSCRIPTS_DIR, file_name)

    # สร้างข้อมูล Transcript
    transcript = await chat_exporter.export(
        channel,
        limit=None,
        tz_info="Asia/Bangkok",
        bot=channel.guild.me
    )

    if transcript is None:
        return None

    # บันทึกไฟล์
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(transcript)

    # สร้าง URL ส่งกลับไป
    # ตัด / ที่ท้าย URL ออก (ถ้ามี) เพื่อไม่ให้ซ้ำซ้อน
    clean_app_url = APP_URL.rstrip('/')
    full_url = f"{clean_app_url}/transcripts/{file_name}"
    return full_url

# ================= UI VIEWS =================
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

        await interaction.response.send_message("Closing ticket, saving transcript...")

        # สร้างลิงก์ Transcript
        transcript_url = await create_transcript_url(interaction.channel)

        # ส่งเข้าห้อง Log
        if LOG_CHANNEL_ID:
            log_channel = bot.get_channel(LOG_CHANNEL_ID)
            if log_channel and transcript_url:
                embed = nextcord.Embed(
                    title="Ticket Transcript Log",
                    description=f"**Ticket:** {interaction.channel.name}\n**Closed by:** {interaction.user.mention}\n\n[Click here to view Transcript]({transcript_url})",
                    color=0x00ff00
                )
                embed.set_footer(text="Link available while bot is running")
                await log_channel.send(embed=embed)

        await asyncio.sleep(3)
        await interaction.channel.delete()

class OpenTicketView(nextcord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @nextcord.ui.button(label="", emoji="<:idk:1431941766893932626>", custom_id="open")
    async def open_ticket(self, button: nextcord.ui.Button, interaction: Interaction):
        
        # นับเลข Ticket ถัดไป
        current_count = get_ticket_count() + 1
        save_ticket_count(current_count)
        ticket_number = current_count

        category = interaction.guild.get_channel(TICKET_CATEGORY_ID)
        if not category:
             await interaction.response.send_message("Error: Category ID not set in Environment Variables.", ephemeral=True)
             return

        overwrites = {
            interaction.guild.default_role: nextcord.PermissionOverwrite(view_channel=False),
            interaction.user: nextcord.PermissionOverwrite(view_channel=True, send_messages=True)
        }

        # สร้างชื่อห้อง ticket-XXXX
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

    embed = nextcord.Embed(title="Ticket System", description="Click button below to open a ticket", color=0x2f3136)
    await interaction.channel.send(embed=embed, view=OpenTicketView())
    await interaction.response.send_message("Panel created", ephemeral=True)

@bot.slash_command(name="setticket", description="Set the current ticket number (Admin only)")
async def setticket(
    interaction: Interaction, 
    number: int = SlashOption(description="The number of the LAST ticket (Next one will be +1)")
):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("No permission", ephemeral=True)
    
    save_ticket_count(number)
    await interaction.response.send_message(f"Ticket count manually set to **{number}**. Next ticket will be **ticket-{number+1:04d}**.", ephemeral=True)

@bot.slash_command(name="add", description="Add user to ticket")
async def add(interaction: Interaction, member: nextcord.Member):
    if not is_ticket_channel(interaction.channel):
        return await interaction.response.send_message("This command can only be used in a ticket channel.", ephemeral=True)
    await interaction.channel.set_permissions(member, view_channel=True, send_messages=True)
    await interaction.response.send_message(f"Added {member.mention}", ephemeral=True)

@bot.slash_command(name="remove", description="Remove user from ticket")
async def remove(interaction: Interaction, member: nextcord.Member):
    if not is_ticket_channel(interaction.channel):
        return await interaction.response.send_message("This command can only be used in a ticket channel.", ephemeral=True)
    await interaction.channel.set_permissions(member, overwrite=None)
    await interaction.response.send_message(f"Removed {member.mention}", ephemeral=True)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    print(f"Current Start Ticket: {get_ticket_count()}")
    bot.add_view(OpenTicketView())
    bot.add_view(CloseTicket())

# เริ่ม Web Server และ Bot
keep_alive()
bot.run(TOKEN)

import os
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from collections import defaultdict
from telegram import Update, ChatMemberUpdated
from telegram.ext import (
    Application,
    CommandHandler,
    ChatMemberHandler,
    ContextTypes,
)

# --- CONFIGURATION FROM RENDER ENVIRONMENT VARIABLES ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
OWNER_ID = int(os.environ.get("OWNER_ID", "0").strip())

# --- ANTI-NUKE RULE: More than 4 bans in 60 seconds (1 minute) ---
MAX_BANS = 4          # Allowed up to 4 bans; the 5th ban triggers demotion
TIME_WINDOW = 60      # 60 seconds (1 minute) rolling window

ban_tracker = defaultdict(list)


# --- KEEP-ALIVE WEB SERVER FOR FREE RENDER HOSTING ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Guardian Bot is running and protecting the group.")

    def log_message(self, format, *args):
        pass


def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()


# --- BOT COMMANDS (FOR GROUP OWNER ONLY) ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🛡️ **Guardian Anti-Nuke Bot is Active**\n\n"
        "**Rule:** If any bot-promoted admin bans/kicks more than 4 users under 1 minute, "
        "their admin permissions are stripped immediately.\n\n"
        "**Owner Commands:**\n"
        "• Reply `/promote` to a user's message to make them a protected admin.\n"
        "• Reply `/demote` to a user's message to remove their admin rights.",
        parse_mode="Markdown",
    )


async def promote_mod(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Promotes a user through the bot so the bot has hierarchy power to demote them."""
    if update.effective_user.id != OWNER_ID:
        return

    if not update.message.reply_to_message:
        await update.message.reply_text("⚠️ Reply to a user's message with `/promote`.", parse_mode="Markdown")
        return

    target = update.message.reply_to_message.from_user
    chat_id = update.effective_chat.id

    try:
        await context.bot.promote_chat_member(
            chat_id=chat_id,
            user_id=target.id,
            can_manage_chat=True,
            can_delete_messages=True,
            can_restrict_members=True,
            can_pin_messages=True,
            can_invite_users=True,
            can_manage_video_chats=True,
            can_promote_members=False,  # Prevent mods from adding rogue admins
        )
        await update.message.reply_text(
            f"✅ Promoted **{target.full_name}**.\n"
            f"They are now monitored (Max {MAX_BANS} bans per {TIME_WINDOW}s).",
            parse_mode="Markdown",
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to promote: {e}")


async def demote_mod(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manually strips admin rights from a bot-promoted moderator."""
    if update.effective_user.id != OWNER_ID:
        return

    if not update.message.reply_to_message:
        await update.message.reply_text("⚠️ Reply to a moderator's message with `/demote`.", parse_mode="Markdown")
        return

    target = update.message.reply_to_message.from_user
    chat_id = update.effective_chat.id

    try:
        await context

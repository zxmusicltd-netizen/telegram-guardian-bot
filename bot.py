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
        await context.bot.promote_chat_member(
            chat_id=chat_id,
            user_id=target.id,
            can_manage_chat=False,
            can_delete_messages=False,
            can_restrict_members=False,
            can_pin_messages=False,
            can_invite_users=False,
            can_change_info=False,
            can_manage_video_chats=False,
            can_promote_members=False,
        )
        await update.message.reply_text(f"⬇️ Stripped admin permissions from **{target.full_name}**.", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to demote: {e}")


# --- REAL-TIME BAN & KICK MONITOR ---
async def monitor_bans(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Watches every ban/kick and strips permissions if > 4 happen within 60 seconds."""
    result: ChatMemberUpdated = update.chat_member
    if not result:
        return

    old_status = result.old_chat_member.status
    new_status = result.new_chat_member.status
    actor = result.from_user  # The admin who executed the ban/kick

    # Check if the action was a ban ("kicked") or a kick (member/restricted -> "left" triggered by someone else)
    is_ban_or_kick = (
        new_status == "kicked"
        or (old_status in ("member", "restricted") and new_status == "left" and actor.id != result.new_chat_member.user.id)
    )

    if not is_ban_or_kick:
        return

    # Ignore bans done by the Group Owner or the Bot itself
    if actor.id == OWNER_ID or actor.id == context.bot.id:
        return

    now = time.time()
    # Filter list to only keep bans by this admin within the last 60 seconds
    recent_bans = [t for t in ban_tracker[actor.id] if now - t < TIME_WINDOW]
    recent_bans.append(now)
    ban_tracker[actor.id] = recent_bans

    # Trigger if they exceed 4 bans (i.e. on the 5th ban within 60 seconds)
    if len(recent_bans) > MAX_BANS:
        chat_id = result.chat.id
        try:
            await context.bot.promote_chat_member(
                chat_id=chat_id,
                user_id=actor.id,
                can_manage_chat=False,
                can_delete_messages=False,
                can_restrict_members=False,
                can_pin_messages=False,
                can_invite_users=False,
                can_change_info=False,
                can_manage_video_chats=False,
                can_promote_members=False,
            )
            ban_tracker[actor.id].clear()
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"🚨 **ADMIN ABUSE PREVENTED** 🚨\n\n"
                    f"Admin **{actor.full_name}** (`{actor.id}`) banned/kicked more than "
                    f"{MAX_BANS} members under 1 minute.\n"
                    f"✅ **All admin permissions have been stripped automatically.**"
                ),
                parse_mode="Markdown",
            )
        except Exception as e:
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"⚠️ **Alert:** Admin **{actor.full_name}** exceeded the ban limit, "
                    f"but I couldn't demote them (`{e}`).\n"
                    f"*Make sure they were promoted using `/promote` through me!*"
                ),
                parse_mode="Markdown",
            )


def main() -> None:
    if not BOT_TOKEN or not OWNER_ID:
        raise ValueError("Missing BOT_TOKEN or OWNER_ID in Environment Variables!")

    # Start web server in background for Render free hosting
    threading.Thread(target=run_health_server, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("promote", promote_mod))
    app.add_handler(CommandHandler("demote", demote_mod))
    app.add_handler(ChatMemberHandler(monitor_bans, ChatMemberHandler.CHAT_MEMBER))

    print("Guardian Bot is running...")
    app.run_polling(allowed_updates=["message", "chat_member"], drop_pending_updates=True)


if __name__ == "__main__":
    main()

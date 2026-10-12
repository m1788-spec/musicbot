import os
import logging
import sqlite3
from datetime import datetime, timezone

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ==========================================
# تنظیمات
# ==========================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

ADMIN_ID = int(os.getenv("ADMIN_ID", "8982085745"))

DB_PATH = os.getenv("DB_PATH", "/data/bot.db")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ==========================================
# دیتابیس
# ==========================================

def get_db():
    folder = os.path.dirname(DB_PATH)

    if folder:
        os.makedirs(folder, exist_ok=True)

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                joined_at TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id TEXT NOT NULL,
                file_type TEXT NOT NULL,
                title TEXT,
                uploader_id INTEGER,
                created_at TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                url TEXT NOT NULL,
                title TEXT
            )
        """)

        conn.commit()


def save_user(user):
    if not user:
        return

    with get_db() as conn:
        conn.execute("""
            INSERT INTO users (
                user_id, username, first_name, joined_at
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name
        """, (
            user.id,
            user.username or "",
            user.first_name or "",
            datetime.now(timezone.utc).isoformat(),
        ))


def save_track(file_id, file_type, title, uploader_id):
    with get_db() as conn:
        cursor = conn.execute("""
            INSERT INTO tracks (
                file_id, file_type, title, uploader_id, created_at
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            file_id,
            file_type,
            title or "آهنگ بدون نام",
            uploader_id,
            datetime.now(timezone.utc).isoformat(),
        ))

        conn.commit()
        return cursor.lastrowid


def get_track(track_id):
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM tracks WHERE id = ?",
            (track_id,),
        ).fetchone()


def get_channels():
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM channels ORDER BY id"
        ).fetchall()


def add_channel(username, url, title=""):
    username = username.strip()

    with get_db() as conn:
        conn.execute("""
            INSERT INTO channels (username, url, title)
            VALUES (?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET
                url = excluded.url,
                title = excluded.title
        """, (username, url, title or username))

        conn.commit()


def delete_channel(channel_id):
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM channels WHERE id = ?",
            (channel_id,),
        )
        conn.commit()
        return cursor.rowcount > 0


def get_stats():
    with get_db() as conn:
        users = conn.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]

        tracks = conn.execute(
            "SELECT COUNT(*) FROM tracks"
        ).fetchone()[0]

        channels = conn.execute(
            "SELECT COUNT(*) FROM channels"
        ).fetchone()[0]

    return users, tracks, channels


def get_all_user_ids():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT user_id FROM users"
        ).fetchall()

    return [row["user_id"] for row in rows]


# ==========================================
# ابزارهای کمکی
# ==========================================

def is_admin(user_id):
    return user_id == ADMIN_ID


async def check_membership(bot, user_id):
    channels = get_channels()

    for channel in channels:
        try:
            member = await bot.get_chat_member(
                chat_id=channel["username"],
                user_id=user_id,
            )

            if member.status in (
                ChatMemberStatus.LEFT,
                ChatMemberStatus.BANNED,
            ):
                return False

            if (
                member.status == ChatMemberStatus.RESTRICTED
                and not getattr(member, "is_member", False)
            ):
                return False

        except Exception:
            logger.exception(
                "Membership check failed for %s",
                channel["username"],
            )
            return False

    return True


def membership_keyboard(track_id=0):
    buttons = []

    for channel in get_channels():
        buttons.append([
            InlineKeyboardButton(
                f"عضویت در {channel['title'] or channel['username']}",
                url=channel["url"],
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "بررسی عضویت ✅",
            callback_data=f"check:{track_id}",
        )
    ])

    return InlineKeyboardMarkup(buttons)


async def send_track(chat_id, track, context):
    caption = f"🎵 {track['title'] or 'آهنگ'}"

    if track["file_type"] == "document":
        await context.bot.send_document(
            chat_id=chat_id,
            document=track["file_id"],
            caption=caption,
        )
    else:
        await context.bot.send_audio(
            chat_id=chat_id,
            audio=track["file_id"],
            caption=caption,
        )


async def send_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    text = (
        "سلام! 🎵\n\n"
        "به ربات دریافت آهنگ خوش اومدی.\n"
        "برای دریافت آهنگ، لینک اختصاصی اون رو باز کن."
    )

    if is_admin(user.id):
        text += (
            "\n\n👑 شما ادمین ربات هستید.\n"
            "برای دیدن دستورهای مدیریتی بزنید:\n"
            "/admin"
        )

    await update.effective_message.reply_text(text)


# ==========================================
# دستور شروع
# ==========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user)

    if not context.args:
        await send_welcome(update, context)
        return

    payload = context.args[0]

    try:
        track_id = int(payload)
    except ValueError:
        await send_welcome(update, context)
        return

    track = get_track(track_id)

    if not track:
        await update.effective_message.reply_text(
            "❌ این آهنگ پیدا نشد یا لینک آن معتبر نیست."
        )
        return

    if not await check_membership(
        context.bot,
        user.id,
    ):
        await update.effective_message.reply_text(
            "🔒 برای دریافت آهنگ، ابتدا عضو کانال‌های زیر شو "
            "و سپس روی بررسی عضویت بزن.",
            reply_markup=membership_keyboard(track_id),
        )
        return

    try:
        await send_track(
            update.effective_chat.id,
            track,
            context,
        )
    except Exception:
        logger.exception("Could not send track")
        await update.effective_message.reply_text(
            "❌ ارسال آهنگ با خطا مواجه شد. لطفاً بعداً تلاش کن."
        )


# ==========================================
# بررسی عضویت
# ==========================================

async def check_membership_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    user = query.from_user
    save_user(user)

    try:
        track_id = int(query.data.split(":", 1)[1])
    except (ValueError, IndexError):
        track_id = 0

    if not await check_membership(context.bot, user.id):
        await query.answer(
            "هنوز عضویت شما در همهٔ کانال‌ها تأیید نشده است.",
            show_alert=True,
        )
        return

    if track_id:
        track = get_track(track_id)

        if not track:
            await query.message.reply_text(
                "❌ این آهنگ پیدا نشد."
            )
            return

        try:
            await send_track(
                query.message.chat_id,
                track,
                context,
            )
        except Exception:
            logger.exception("Could not send track after membership")
            await query.message.reply_text(
                "❌ ارسال آهنگ ناموفق بود."
            )
    else:
        await query.message.reply_text(
            "✅ عضویت شما تأیید شد. حالا لینک آهنگ رو دوباره باز کن."
        )


# ==========================================
# دریافت آهنگ توسط ادمین
# ==========================================

async def upload_track(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user
    message = update.effective_message

    save_user(user)

    if not is_admin(user.id):
        return

    if message.audio:
        file_id = message.audio.file_id
        file_type = "audio"
        title = (
            message.audio.title
            or message.audio.file_name
            or "آهنگ"
        )

    elif message.document:
        file_id = message.document.file_id
        file_type = "document"
        title = message.document.file_name or "فایل صوتی"

    else:
        return

    track_id = save_track(
        file_id=file_id,
        file_type=file_type,
        title=title,
        uploader_id=user.id,
    )

    bot_info = await context.bot.get_me()
    link = f"https://t.me/{bot_info.username}?start={track_id}"

    await message.reply_text(
        "✅ فایل با موفقیت ذخیره شد!\n\n"
        f"🎵 نام: {title}\n"
        f"🆔 شناسه: {track_id}\n\n"
        "🔗 لینک اختصاصی آهنگ:\n"
        f"{link}\n\n"
        "این لینک رو می‌تونی برای دیگران بفرستی."
    )


# ==========================================
# پنل مدیریت
# ==========================================

async def admin_panel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text(
            "⛔ شما اجازهٔ دسترسی ندارید."
        )
        return

    text = (
        "👑 پنل مدیریت ربات\n\n"
        "🎵 ارسال آهنگ یا فایل به ربات:\n"
        "برای ذخیرهٔ آهنگ و ساخت لینک، فایل رو همین‌جا بفرست.\n\n"
        "📊 /stats — آمار ربات\n"
        "➕ /addchannel @channel [link] — افزودن کانال اجباری\n"
        "📋 /channels — فهرست کانال‌ها\n"
        "➖ /delchannel ID — حذف کانال\n"
        "📢 /broadcast متن پیام — ارسال پیام همگانی\n\n"
        "نکته: برای بررسی عضویت، ربات باید به کانال دسترسی داشته باشه."
    )

    await update.effective_message.reply_text(text)


async def stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text(
            "⛔ دسترسی غیرمجاز."
        )
        return

    users, tracks, channels = get_stats()

    await update.effective_message.reply_text(
        "📊 آمار ربات\n\n"
        f"👥 کاربران ثبت‌شده: {users}\n"
        f"🎵 آهنگ‌ها: {tracks}\n"
        f"📢 کانال‌های اجباری: {channels}"
    )


# ==========================================
# مدیریت کانال‌ها
# ==========================================

async def add_channel_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text(
            "⛔ دسترسی غیرمجاز."
        )
        return

    if not context.args:
        await update.effective_message.reply_text(
            "روش استفاده:\n"
            "/addchannel @channel https://t.me/channel\n\n"
            "اگر لینک عمومی کانال با نام کاربری یکی است، "
            "می‌تونی فقط @channel رو بنویسی."
        )
        return

    username = context.args[0].strip()

    if not username.startswith("@"):
        username = "@" + username

    if len(context.args) >= 2:
        url = context.args[1].strip()
    else:
        url = "https://t.me/" + username.lstrip("@")

    if not url.startswith(("https://t.me/", "http://t.me/")):
        await update.effective_message.reply_text(
            "❌ لینک باید یک لینک معتبر تلگرام باشد."
        )
        return

    try:
        chat = await context.bot.get_chat(username)

        add_channel(
            username=username,
            url=url,
            title=chat.title or username,
        )

        await update.effective_message.reply_text(
            "✅ کانال ثبت شد.\n\n"
            f"📢 نام: {chat.title or username}\n"
            f"🔗 لینک: {url}\n\n"
            "برای بررسی عضویت کاربران، مطمئن شو ربات "
            "به کانال دسترسی لازم را دارد."
        )

    except Exception:
        logger.exception("Could not add channel")
        await update.effective_message.reply_text(
            "❌ ثبت کانال ناموفق بود.\n"
            "نام کاربری کانال و دسترسی ربات رو بررسی کن."
        )


async def list_channels(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text(
            "⛔ دسترسی غیرمجاز."
        )
        return

    channels = get_channels()

    if not channels:
        await update.effective_message.reply_text(
            "هنوز کانال اجباری ثبت نشده."
        )
        return

    lines = ["📋 فهرست کانال‌های اجباری:\n"]

    for channel in channels:
        lines.append(
            f"شناسه: {channel['id']}\n"
            f"نام: {channel['title'] or channel['username']}\n"
            f"آدرس: {channel['url']}\n"
        )

    lines.append("برای حذف کانال:\n/delchannel ID")

    await update.effective_message.reply_text(
        "\n".join(lines)
    )


async def del_channel_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text(
            "⛔ دسترسی غیرمجاز."
        )
        return

    if not context.args:
        await update.effective_message.reply_text(
            "روش استفاده:\n/delchannel ID"
        )
        return

    try:
        channel_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text(
            "❌ شناسه باید عدد باشد."
        )
        return

    if delete_channel(channel_id):
        await update.effective_message.reply_text(
            "✅ کانال حذف شد."
        )
    else:
        await update.effective_message.reply_text(
            "❌ چنین شناسه‌ای پیدا نشد."
        )


# ==========================================
# پیام همگانی
# ==========================================

async def broadcast(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text(
            "⛔ دسترسی غیرمجاز."
        )
        return

    text = " ".join(context.args).strip()

    if not text:
        await update.effective_message.reply_text(
            "روش استفاده:\n/broadcast متن پیام"
        )
        return

    user_ids = get_all_user_ids()

    sent = 0
    failed = 0

    status = await update.effective_message.reply_text(
        "⏳ ارسال پیام همگانی شروع شد..."
    )

    for user_id in user_ids:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=text,
            )
            sent += 1

        except Exception:
            failed += 1
            logger.warning(
                "Broadcast failed for user %s",
                user_id,
            )

    await status.edit_text(
        "✅ ارسال همگانی تمام شد.\n\n"
        f"📨 ارسال موفق: {sent}\n"
        f"❌ ناموفق: {failed}"
    )


# ==========================================
# خطاها
# ==========================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.error(
        "Unhandled exception",
        exc_info=context.error,
    )


# ==========================================
# اجرای ربات
# ==========================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "متغیر محیطی BOT_TOKEN در Runflare تنظیم نشده است."
        )

    init_db()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("stats", stats))

    application.add_handler(
        CommandHandler("addchannel", add_channel_command)
    )
    application.add_handler(
        CommandHandler("channels", list_channels)
    )
    application.add_handler(
        CommandHandler("delchannel", del_channel_command)
    )

    application.add_handler(
        CommandHandler("broadcast", broadcast)
    )

    application.add_handler(
        CallbackQueryHandler(
            check_membership_callback,
            pattern=r"^check:",
        )
    )

    application.add_handler(
        MessageHandler(
            filters.AUDIO | filters.Document.ALL,
            upload_track,
        )
    )

    application.add_error_handler(error_handler)

    logger.info("Bot is starting...")

    application.run_polling(
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()

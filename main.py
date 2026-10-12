import os
import sqlite3
import logging
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ChatMemberHandler,
    ContextTypes,
    filters,
)

# CONFIG
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "8982085745"))
DB_PATH = os.getenv("DB_PATH", "bot.db")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


# DATABASE
def db():
    parent = os.path.dirname(os.path.abspath(DB_PATH))
    os.makedirs(parent, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def now():
    return datetime.now(timezone.utc).isoformat()


def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS rb_tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT NOT NULL,
            file_type TEXT NOT NULL,
            title TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS rb_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL UNIQUE,
            title TEXT DEFAULT '',
            url TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS rb_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS rb_admins (
            user_id INTEGER PRIMARY KEY
        );

        CREATE TABLE IF NOT EXISTS rb_campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            title TEXT DEFAULT '',
            invite_link TEXT DEFAULT '',
            target INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS rb_campaign_joins (
            campaign_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            joined_at TEXT NOT NULL,
            PRIMARY KEY (campaign_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS rb_states (
            user_id INTEGER PRIMARY KEY,
            state TEXT NOT NULL
        );
        """)

        con.execute(
            "INSERT OR IGNORE INTO rb_settings(key, value) VALUES(?, ?)",
            ("join_text", "🎧 برای دانلود ریمیکس، ابتدا عضو کانال‌های زیر شو ❤️"),
        )


def get_setting(key, default=""):
    with db() as con:
        row = con.execute(
            "SELECT value FROM rb_settings WHERE key=?",
            (key,),
        ).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    with db() as con:
        con.execute("""
            INSERT INTO rb_settings(key, value) VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """, (key, str(value)))


def set_state(user_id, state):
    with db() as con:
        con.execute("""
            INSERT INTO rb_states(user_id, state) VALUES(?, ?)
            ON CONFLICT(user_id) DO UPDATE SET state=excluded.state
        """, (user_id, state))


def get_state(user_id):
    with db() as con:
        row = con.execute(
            "SELECT state FROM rb_states WHERE user_id=?",
            (user_id,),
        ).fetchone()
    return row["state"] if row else None


def clear_state(user_id):
    with db() as con:
        con.execute("DELETE FROM rb_states WHERE user_id=?", (user_id,))


def is_admin(user_id):
    if user_id == ADMIN_ID:
        return True
    with db() as con:
        row = con.execute(
            "SELECT 1 FROM rb_admins WHERE user_id=?",
            (user_id,),
        ).fetchone()
    return row is not None


# MENUS
def home_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎵 راهنمای دانلود", callback_data="help")]
    ])


def admin_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ افزودن آهنگ", callback_data="addtrack"),
            InlineKeyboardButton("🗑 حذف آهنگ", callback_data="deltrack"),
        ],
        [
            InlineKeyboardButton("📢 کانال‌ها", callback_data="channels"),
            InlineKeyboardButton("🎯 کمپین‌ها", callback_data="campaigns"),
        ],
        [
            InlineKeyboardButton("✏️ متن عضویت", callback_data="jointext"),
            InlineKeyboardButton("📊 آمار", callback_data="stats"),
        ],
        [InlineKeyboardButton("👥 راهنمای مدیران", callback_data="admins")],
    ])


# MEMBERSHIP
async def check_membership(context, user_id):
    with db() as con:
        channels = con.execute("SELECT * FROM rb_channels").fetchall()

    missing = []
    for channel in channels:
        try:
            member = await context.bot.get_chat_member(
                channel["chat_id"], user_id
            )
            if member.status in ("left", "kicked"):
                missing.append(channel)
        except TelegramError:
            log.exception("Membership check failed")
            missing.append(channel)

    return missing


def membership_keyboard(channels):
    rows = []

    for channel in channels:
        url = channel["url"]
        if not url:
            username = channel["chat_id"].lstrip("@")
            url = f"https://t.me/{username}"

        rows.append([
            InlineKeyboardButton(
                f"📢 {channel['title'] or 'عضویت در کانال'}",
                url=url,
            )
        ])

    rows.append([
        InlineKeyboardButton("✅ بررسی عضویت", callback_data="checkjoin")
    ])

    return InlineKeyboardMarkup(rows)


# TRACKS
def save_track(file_id, file_type, title):
    with db() as con:
        cur = con.execute("""
            INSERT INTO rb_tracks(file_id, file_type, title, created_at)
            VALUES (?, ?, ?, ?)
        """, (file_id, file_type, title, now()))
        return cur.lastrowid


async def send_track(update, context, track_id):
    message = update.effective_message
    user = update.effective_user

    with db() as con:
        track = con.execute(
            "SELECT * FROM rb_tracks WHERE id=?",
            (track_id,),
        ).fetchone()

    if not track:
        await message.reply_text("❌ این آهنگ پیدا نشد.")
        return

    missing = await check_membership(context, user.id)

    if missing:
        await message.reply_text(
            get_setting("join_text"),
            reply_markup=membership_keyboard(missing),
        )
        return

    try:
        if track["file_type"] == "audio":
            await message.reply_audio(
                audio=track["file_id"],
                caption="🎧 ریمیکس شما آماده است ❤️",
            )
        else:
            await message.reply_document(
                document=track["file_id"],
                caption="🎧 فایل شما آماده است ❤️",
            )
    except TelegramError:
        log.exception("Track sending failed")
        await message.reply_text("❌ ارسال فایل ناموفق بود.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.effective_message

    if context.args and context.args[0].isdigit():
        await send_track(update, context, int(context.args[0]))
        return

    text = (
        "سلام ❤️ به ربات دانلود ریمیکس خوش اومدی!\n\n"
        "برای دانلود، لینک اختصاصی آهنگ رو باز کن."
    )

    if is_admin(user.id):
        text += "\n\nبرای پنل مدیریت /admin رو بفرست."

    await message.reply_text(text, reply_markup=home_menu())


# ADMIN COMMANDS
async def admin_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔️ دسترسی نداری.")
        return

    await update.effective_message.reply_text(
        "👨‍💻 پنل مدیریت",
        reply_markup=admin_menu(),
    )


async def addchannel_command(update, context):
    message = update.effective_message

    if not is_admin(update.effective_user.id):
        await message.reply_text("⛔️ دسترسی نداری.")
        return

    if not context.args:
        await message.reply_text(
            "فرمت:\n/addchannel @channelusername\n"
            "ربات باید مدیر کانال باشد."
        )
        return

    try:
        chat = await context.bot.get_chat(context.args[0])
        member = await context.bot.get_chat_member(chat.id, context.bot.id)

        if member.status not in ("administrator", "creator"):
            await message.reply_text("❌ ربات باید مدیر کانال باشد.")
            return

        url = f"https://t.me/{chat.username}" if chat.username else ""

        with db() as con:
            con.execute("""
                INSERT OR IGNORE INTO rb_channels(chat_id, title, url)
                VALUES (?, ?, ?)
            """, (str(chat.id), chat.title or "", url))

        await message.reply_text("✅ کانال ثبت شد.")

    except TelegramError:
        await message.reply_text("❌ کانال پیدا نشد یا دسترسی کافی نیست.")


async def channels_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔️ دسترسی نداری.")
        return

    with db() as con:
        rows = con.execute("SELECT * FROM rb_channels ORDER BY id").fetchall()

    if not rows:
        await update.effective_message.reply_text("کانالی ثبت نشده.")
        return

    text = "\n".join(
        f"{r['id']}. {r['title']} — {r['chat_id']}"
        for r in rows
    )
    await update.effective_message.reply_text(text)


async def delchannel_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔️ دسترسی نداری.")
        return

    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text(
            "فرمت:\n/delchannel شناسه"
        )
        return

    with db() as con:
        cur = con.execute(
            "DELETE FROM rb_channels WHERE id=?",
            (int(context.args[0]),),
        )

    await update.effective_message.reply_text(
        "✅ حذف شد." if cur.rowcount else "❌ شناسه پیدا نشد."
    )


async def newcampaign_command(update, context):
    message = update.effective_message

    if not is_admin(update.effective_user.id):
        await message.reply_text("⛔️ دسترسی نداری.")
        return

    if len(context.args) < 2:
        await message.reply_text(
            "فرمت:\n/newcampaign @channel هدف\n"
            "مثال: /newcampaign @mychannel 500\n"
            "هدف 0 یعنی نامحدود."
        )
        return

    try:
        target = int(context.args[1])
        if target < 0:
            raise ValueError

        chat = await context.bot.get_chat(context.args[0])
        member = await context.bot.get_chat_member(chat.id, context.bot.id)

        if member.status not in ("administrator", "creator"):
            await message.reply_text("❌ ربات باید مدیر کانال باشد.")
            return

        invite = await context.bot.create_chat_invite_link(
            chat_id=chat.id,
            name=f"campaign-{int(datetime.now().timestamp())}",
        )

        with db() as con:
            cur = con.execute("""
                INSERT INTO rb_campaigns
                (chat_id, title, invite_link, target, active, created_at)
                VALUES (?, ?, ?, ?, 1, ?)
            """, (
                str(chat.id), chat.title or "",
                invite.invite_link, target, now(),
            ))
            campaign_id = cur.lastrowid

        await message.reply_text(
            f"✅ کمپین ساخته شد\nشناسه: {campaign_id}\n"
            f"هدف: {target or 'نامحدود'}\n"
            f"لینک دعوت:\n{invite.invite_link}"
        )

    except (TelegramError, ValueError):
        await message.reply_text(
            "❌ ساخت کمپین ناموفق بود. اطلاعات و دسترسی ربات را بررسی کن."
        )


async def campaigns_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔️ دسترسی نداری.")
        return

    with db() as con:
        campaigns = con.execute(
            "SELECT * FROM rb_campaigns ORDER BY id DESC"
        ).fetchall()

    if not campaigns:
        await update.effective_message.reply_text("کمپینی ثبت نشده.")
        return

    for c in campaigns:
        with db() as con:
            count = con.execute(
                "SELECT COUNT(*) AS n FROM rb_campaign_joins WHERE campaign_id=?",
                (c["id"],),
            ).fetchone()["n"]

        status = "فعال" if c["active"] else "متوقف"

        await update.effective_message.reply_text(
            f"🎯 کمپین {c['id']}\n"
            f"کانال: {c['title']}\nاعضا: {count}\n"
            f"هدف: {c['target'] or 'نامحدود'}\n"
            f"وضعیت: {status}\nلینک: {c['invite_link']}\n\n"
            f"توقف: /pausecampaign {c['id']}\n"
            f"ادامه: /resumecampaign {c['id']}\n"
            f"حذف: /delcampaign {c['id']}"
        )


async def campaign_action(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔️ دسترسی نداری.")
        return

    command = update.effective_message.text.split()[0].split("@")[0]

    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text("شناسهٔ کمپین را وارد کن.")
        return

    campaign_id = int(context.args[0])

    if command == "/pausecampaign":
        sql = "UPDATE rb_campaigns SET active=0 WHERE id=?"
    elif command == "/resumecampaign":
        sql = "UPDATE rb_campaigns SET active=1 WHERE id=?"
    else:
        sql = "DELETE FROM rb_campaigns WHERE id=?"

    with db() as con:
        cur = con.execute(sql, (campaign_id,))

    await update.effective_message.reply_text(
        "✅ انجام شد." if cur.rowcount else "❌ کمپین پیدا نشد."
    )


async def admins_command(update, context):
    user = update.effective_user
    message = update.effective_message

    if user.id != ADMIN_ID:
        await message.reply_text("⛔️ فقط مدیر اصلی مجاز است.")
        return

    with db() as con:
        rows = con.execute(
            "SELECT user_id FROM rb_admins ORDER BY user_id"
        ).fetchall()

    text = f"مدیر اصلی: {ADMIN_ID}\n"
    text += "\n".join(str(r["user_id"]) for r in rows)
    text += "\n\nافزودن مدیر:\n/addadmin شناسه\nحذف مدیر:\n/deladmin شناسه"

    await message.reply_text(text)


async def admin_manage_command(update, context):
    user = update.effective_user
    message = update.effective_message

    if user.id != ADMIN_ID:
        await message.reply_text("⛔️ فقط مدیر اصلی مجاز است.")
        return

    command = message.text.split()[0].split("@")[0]

    if not context.args or not context.args[0].isdigit():
        await message.reply_text("یک شناسهٔ عددی وارد کن.")
        return

    target = int(context.args[0])

    if target == ADMIN_ID:
        await message.reply_text("مدیر اصلی را نمی‌توان حذف یا اضافه کرد.")
        return

    with db() as con:
        if command == "/addadmin":
            con.execute(
                "INSERT OR IGNORE INTO rb_admins(user_id) VALUES(?)",
                (target,),
            )
            answer = "✅ مدیر اضافه شد."
        else:
            con.execute(
                "DELETE FROM rb_admins WHERE user_id=?",
                (target,),
            )
            answer = "✅ دستور حذف انجام شد."

    await message.reply_text(answer)


async def stats_command(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔️ دسترسی نداری.")
        return

    with db() as con:
        tracks = con.execute(
            "SELECT COUNT(*) AS n FROM rb_tracks"
        ).fetchone()["n"]
        channels = con.execute(
            "SELECT COUNT(*) AS n FROM rb_channels"
        ).fetchone()["n"]
        campaigns = con.execute(
            "SELECT COUNT(*) AS n FROM rb_campaigns"
        ).fetchone()["n"]

    await update.effective_message.reply_text(
        f"📊 آمار ربات\n\n🎵 آهنگ‌ها: {tracks}\n"
        f"📢 کانال‌ها: {channels}\n🎯 کمپین‌ها: {campaigns}"
    )


# CALLBACKS
async def admin_callbacks(update, context):
    query = update.callback_query
    user = update.effective_user
    action = query.data or ""

    await query.answer()

    if action == "help":
        await query.message.reply_text(
            "برای دانلود، لینک اختصاصی آهنگ را باز کن."
        )
        return

    if action == "checkjoin":
        missing = await check_membership(context, user.id)
        if missing:
            await query.message.reply_text(
                get_setting("join_text"),
                reply_markup=membership_keyboard(missing),
            )
        else:
            await query.message.reply_text(
                "✅ عضویت تأیید شد. لینک آهنگ را دوباره باز کن."
            )
        return

    if not is_admin(user.id):
        await query.answer("⛔️ دسترسی نداری.", show_alert=True)
        return

    if action == "addtrack":
        set_state(user.id, "waiting_track")
        await query.message.reply_text(
            "فایل آهنگ را به صورت Audio یا Document بفرست."
        )

    elif action == "deltrack":
        with db() as con:
            tracks = con.execute(
                "SELECT id, title FROM rb_tracks ORDER BY id DESC LIMIT 50"
            ).fetchall()

        if not tracks:
            await query.message.reply_text("آهنگی ثبت نشده.")
            return

        rows = [
            [InlineKeyboardButton(
                f"🗑 {r['id']} - {(r['title'] or 'بدون نام')[:25]}",
                callback_data=f"deltrack:{r['id']}",
            )]
            for r in tracks
        ]

        await query.message.reply_text(
            "آهنگ موردنظر را انتخاب کن:",
            reply_markup=InlineKeyboardMarkup(rows),
        )

    elif action.startswith("deltrack:"):
        track_id = action.split(":", 1)[1]
        if track_id.isdigit():
            with db() as con:
                con.execute(
                    "DELETE FROM rb_tracks WHERE id=?",
                    (int(track_id),),
                )
            await query.message.reply_text("✅ آهنگ حذف شد.")

    elif action == "channels":
        await query.message.reply_text(
            "دستورها:\n/addchannel @channelusername\n"
            "/channels\n/delchannel شناسه"
        )

    elif action == "campaigns":
        await campaigns_command(update, context)

    elif action == "jointext":
        set_state(user.id, "waiting_join_text")
        await query.message.reply_text("متن جدید عضویت را بفرست.")

    elif action == "stats":
        await stats_command(update, context)

    elif action == "admins":
        await query.message.reply_text(
            "مدیریت مدیران:\n/admins\n/addadmin شناسه\n/deladmin شناسه"
        )


# MESSAGES
async def handle_media(update, context):
    user = update.effective_user
    message = update.effective_message

    if not is_admin(user.id):
        return

    if get_state(user.id) != "waiting_track":
        return

    if message.audio:
        file_id = message.audio.file_id
        file_type = "audio"
        title = message.audio.title or message.audio.file_name or "Audio"
    elif message.document:
        file_id = message.document.file_id
        file_type = "document"
        title = message.document.file_name or "File"
    else:
        await message.reply_text("لطفاً فایل صوتی بفرست.")
        return

    track_id = save_track(file_id, file_type, title)
    clear_state(user.id)

    me = await context.bot.get_me()
    link = f"https://t.me/{me.username}?start={track_id}"

    await message.reply_text(
        f"✅ آهنگ ذخیره شد!\nشناسه: {track_id}\nنام: {title}\n\n"
        f"🔗 لینک دانلود:\n{link}"
    )


async def handle_text(update, context):
    user = update.effective_user
    message = update.effective_message

    if not user or not message or not is_admin(user.id):
        return

    state = get_state(user.id)

    if state == "waiting_join_text":
        set_setting("join_text", message.text or "")
        clear_state(user.id)
        await message.reply_text("✅ متن ذخیره شد.")

    elif state == "waiting_track":
        await message.reply_text(
            "لطفاً آهنگ را به صورت فایل Audio یا Document بفرست."
        )


# CAMPAIGN JOIN TRACKING
async def track_campaign_join(update, context):
    change = update.chat_member
    if not change:
        return

    old_status = change.old_chat_member.status
    new_status = change.new_chat_member.status
    joined = ("member", "administrator", "creator")

    if old_status in joined or new_status not in joined:
        return

    user = change.new_chat_member.user
    if user.is_bot:
        return

    invite_link = (
        change.invite_link.invite_link if change.invite_link else None
    )
    if not invite_link:
        return

    with db() as con:
        campaign = con.execute(
            "SELECT * FROM rb_campaigns WHERE invite_link=? AND active=1",
            (invite_link,),
        ).fetchone()

        if not campaign:
            return

        con.execute("""
            INSERT OR IGNORE INTO rb_campaign_joins
            (campaign_id, user_id, joined_at) VALUES (?, ?, ?)
        """, (campaign["id"], user.id, now()))

        count = con.execute(
            "SELECT COUNT(*) AS n FROM rb_campaign_joins WHERE campaign_id=?",
            (campaign["id"],),
        ).fetchone()["n"]

        if campaign["target"] > 0 and count >= campaign["target"]:
            con.execute(
                "UPDATE rb_campaigns SET active=0 WHERE id=?",
                (campaign["id"],),
            )


# ERROR HANDLER
async def error_handler(update, context):
    log.error("Update processing failed", exc_info=context.error)


# MAIN
def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN در تنظیمات Runflare تعریف نشده است."
        )

    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("addchannel", addchannel_command))
    app.add_handler(CommandHandler("channels", channels_command))
    app.add_handler(CommandHandler("delchannel", delchannel_command))
    app.add_handler(CommandHandler("newcampaign", newcampaign_command))
    app.add_handler(CommandHandler("campaigns", campaigns_command))
    app.add_handler(
        CommandHandler(
            ["pausecampaign", "resumecampaign", "delcampaign"],
            campaign_action,
        )
    )
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("admins", admins_command))
    app.add_handler(
        CommandHandler(["addadmin", "deladmin"], admin_manage_command)
    )

    app.add_handler(CallbackQueryHandler(admin_callbacks))
    app.add_handler(
        ChatMemberHandler(
            track_campaign_join,
            ChatMemberHandler.CHAT_MEMBER,
        )
    )
    app.add_handler(
        MessageHandler(filters.AUDIO | filters.Document.ALL, handle_media)
    )
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)
    )
    app.add_error_handler(error_handler)

    log.info("Bot is starting")
    app.run_polling(
        allowed_updates=["message", "callback_query", "chat_member"]
    )


if __name__ == "__main__":
    main()
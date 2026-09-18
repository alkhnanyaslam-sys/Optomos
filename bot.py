import os
import json
import time
import logging
import asyncio
import subprocess
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_CHAT_ID = int(os.environ["ADMIN_CHAT_ID"])
GROUP_INVITE_LINK = os.environ["GROUP_INVITE_LINK"]
RUN_SECONDS = int(os.environ.get("RUN_SECONDS", 21000))  # ~5h50m

DATA_FILE = "users.json"

WELCOME_MSG = (
    "أهلاً بيك! 👋\n\n"
    "البوت ده مخصص لطلبة كلية حاسبات ومعلومات / حاسبات وذكاء اصطناعي بس.\n\n"
    "عشان تنضم لجروب الطلاب، ابعتلي صورة إثبات إنك طالب في الكلية "
    "(بطاقة الكلية، أو إيصال المصروفات، أو صفحة من الجدول عليها اسمك واسم الكلية).\n\n"
    "بعد المراجعة هبعتلك لينك الانضمام للجروب على طول. 🎓"
)

PENDING_MSG = "تمام، وصلني الإثبات وهيتم مراجعته، استنى شوية وهيوصلك رد. ⏳"
ALREADY_PENDING_MSG = "طلبك قيد المراجعة بالفعل، استنى شوية. ⏳"
ALREADY_APPROVED_MSG = "انت متحقق بالفعل ✅ اتفضل لينك الجروب:\n{link}"
REJECTED_RETRY_MSG = (
    "للأسف الإثبات اللي بعتّه مش واضح/مش كفاية 🙏\n"
    "ابعت صورة تانية أوضح توضح إنك طالب حاسبات ومعلومات أو حاسبات وذكاء اصطناعي."
)
APPROVED_USER_MSG = "تم التحقق منك بنجاح ✅\nاتفضل لينك الجروب:\n{link}"

data_lock = asyncio.Lock()


def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    async with data_lock:
        data = load_data()
        uid = str(user.id)
        if uid not in data:
            data[uid] = {
                "username": user.username,
                "first_name": user.first_name,
                "status": "new",
                "first_seen": datetime.utcnow().isoformat(),
            }
            save_data(data)
        status = data[uid]["status"]

    if status == "approved":
        await update.message.reply_text(ALREADY_APPROVED_MSG.format(link=GROUP_INVITE_LINK))
    elif status == "pending":
        await update.message.reply_text(ALREADY_PENDING_MSG)
    else:
        await update.message.reply_text(WELCOME_MSG)


async def handle_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = str(user.id)

    async with data_lock:
        data = load_data()
        if uid not in data:
            data[uid] = {
                "username": user.username,
                "first_name": user.first_name,
                "first_seen": datetime.utcnow().isoformat(),
            }
        data[uid]["status"] = "pending"
        save_data(data)

    await update.message.reply_text(PENDING_MSG)

    caption = (
        f"📩 طلب تحقق جديد\n"
        f"الاسم: {user.first_name or ''} {user.last_name or ''}\n"
        f"اليوزر: @{user.username if user.username else 'مفيش'}\n"
        f"ID: {user.id}"
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ قبول", callback_data=f"approve:{user.id}"),
            InlineKeyboardButton("❌ رفض", callback_data=f"reject:{user.id}"),
        ]
    ])

    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        await context.bot.send_photo(ADMIN_CHAT_ID, file_id, caption=caption, reply_markup=keyboard)
    elif update.message.document:
        file_id = update.message.document.file_id
        await context.bot.send_document(ADMIN_CHAT_ID, file_id, caption=caption, reply_markup=keyboard)
    else:
        await context.bot.send_message(ADMIN_CHAT_ID, caption, reply_markup=keyboard)


async def handle_admin_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_CHAT_ID:
        return

    action, uid = query.data.split(":")

    async with data_lock:
        data = load_data()
        if uid not in data:
            return

        if action == "approve":
            data[uid]["status"] = "approved"
            save_data(data)
            try:
                await context.bot.send_message(int(uid), APPROVED_USER_MSG.format(link=GROUP_INVITE_LINK))
            except Exception as e:
                logger.warning(f"couldn't message {uid}: {e}")
            suffix = "\n\n✅ تم القبول"
        else:
            data[uid]["status"] = "rejected"
            save_data(data)
            try:
                await context.bot.send_message(int(uid), REJECTED_RETRY_MSG)
            except Exception as e:
                logger.warning(f"couldn't message {uid}: {e}")
            suffix = "\n\n❌ تم الرفض"

    try:
        if query.message.caption is not None:
            await query.edit_message_caption(caption=(query.message.caption or "") + suffix, reply_markup=None)
        else:
            await query.edit_message_text(text=(query.message.text or "") + suffix, reply_markup=None)
    except Exception as e:
        logger.warning(f"couldn't edit admin message: {e}")


def git_commit_and_push():
    try:
        subprocess.run(["git", "config", "user.email", "bot@actions.local"], check=True)
        subprocess.run(["git", "config", "user.name", "verify-bot"], check=True)
        subprocess.run(["git", "add", DATA_FILE], check=True)
        result = subprocess.run(["git", "diff", "--cached", "--quiet"])
        if result.returncode == 0:
            return
        subprocess.run(["git", "commit", "-m", "update users.json [skip ci]"], check=True)
        subprocess.run(["git", "push"], check=True)
        logger.info("تم حفظ users.json على الريبو")
    except subprocess.CalledProcessError as e:
        logger.error(f"git push failed: {e}")


async def periodic_commit(interval=300):
    while True:
        await asyncio.sleep(interval)
        async with data_lock:
            git_commit_and_push()


async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler((filters.PHOTO | filters.Document.ALL) & filters.ChatType.PRIVATE, handle_proof))
    app.add_handler(CallbackQueryHandler(handle_admin_decision))

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    commit_task = asyncio.create_task(periodic_commit())

    logger.info(f"البوت شغال، هيقفل بعد {RUN_SECONDS} ثانية")
    try:
        await asyncio.sleep(RUN_SECONDS)
    finally:
        commit_task.cancel()
        async with data_lock:
            git_commit_and_push()
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())

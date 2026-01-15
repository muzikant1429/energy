# main.py
import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters, ConversationHandler
)
from datetime import datetime, timedelta
import re

# === Настройка ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
MAIN_CHAT_ID = int(os.getenv("MAIN_CHAT_ID"))
SYSTEM_CHAT_ID = int(os.getenv("SYSTEM_CHAT_ID"))
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@lihvan_team_sup")

# === Состояния для конкурса ===
(
    WAITING_CHANNELS,
    CHECKING_PERMISSIONS,
    WAITING_MESSAGE,
    WAITING_PHOTO,
    WAITING_START_TIME,
    WAITING_END_TIME,
    WAITING_WINNERS_COUNT,
    WAITING_SETTINGS,
    CONFIRMATION
) = range(9)

# === Глобальные данные ===
user_lotteries = {}  # user_id -> данные конкурса
muted_users = set()  # пользователи в муте

# === Анти-реклама ===
AD_KEYWORDS = ["t.me/", "http", "https", "www.", ".com", ".ru", "vk.com", "instagram", "whatsapp"]

def has_ad(text: str) -> bool:
    if not text:
        return False
    return any(kw in text.lower() for kw in AD_KEYWORDS)

async def check_user_bio(context: ContextTypes.DEFAULT_TYPE, user_id: int, chat_id: int):
    try:
        user = await context.bot.get_chat(user_id)
        bio = (user.bio or "") + (user.first_name or "") + (user.last_name or "")
        if has_ad(bio):
            # Заглушить на 1 час
            muted_users.add(user_id)
            # Уведомить системную группу
            msg = await context.bot.send_message(
                SYSTEM_CHAT_ID,
                f"⚠️ Обнаружена реклама в профиле:\n"
                f"ID: {user_id}\n"
                f"Username: @{user.username or 'нет'}\n"
                f"Био: {bio[:100]}",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("Заблокировать", callback_data=f"ban_{user_id}"),
                        InlineKeyboardButton("Добавить в исключения", callback_data=f"allow_{user_id}")
                    ]
                ])
            )
            # Удалить последнее сообщение пользователя
            # (если нужно — можно хранить message_id)
    except Exception as e:
        logger.error(f"Ошибка проверки профиля: {e}")

# === Обработчик сообщений в основном чате ===
async def handle_main_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    message = update.message

    if chat.id != MAIN_CHAT_ID:
        return

    # Если пользователь в муте — удалить
    if user.id in muted_users:
        try:
            await message.delete()
        except:
            pass
        return

    # Проверка био (можно делать реже, например, раз в день)
    await check_user_bio(context, user.id, chat.id)

    # Проверка текста на рекламу
    text = message.text or message.caption or ""
    if has_ad(text):
        try:
            await message.delete()
        except:
            pass
        # Можно предупредить, но лучше тихо удалять
        return

# === Системная группа: обработка кнопок ===
async def system_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("ban_"):
        user_id = int(data.split("_")[1])
        try:
            await context.bot.ban_chat_member(MAIN_CHAT_ID, user_id)
            await query.edit_message_text("✅ Пользователь заблокирован.")
        except Exception as e:
            await query.edit_message_text(f"❌ Ошибка: {e}")
        muted_users.discard(user_id)

    elif data.startswith("allow_"):
        user_id = int(data.split("_")[1])
        muted_users.discard(user_id)
        await query.edit_message_text("✅ Пользователь добавлен в исключения.")

# === Конкурс: начало ===
async def start_lottery(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎉 Добро пожаловать в мастер розыгрышей!\n"
        "Пожалуйста, пришлите ссылки на каналы/группы, где провести розыгрыш (через запятую):"
    )
    return WAITING_CHANNELS

async def receive_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    links = [link.strip() for link in text.split(",") if link.strip()]
    context.user_data["channels"] = links
    await update.message.reply_text("Проверяю права... Пожалуйста, добавьте меня в эти чаты и дайте права администратора.")
    return CHECKING_PERMISSIONS

async def check_permissions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Здесь можно запросить пользователя нажать "Проверить"
    await update.message.reply_text(
        "Нажмите кнопку, чтобы проверить права:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Проверить", callback_data="check_perms")]])
    )
    return CHECKING_PERMISSIONS

# ... остальные шаги конкурса (сообщение, фото, дата и т.д.)

# === Защита от мошенников: первый комментарий ===
async def post_with_warning(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, photo=None):
    if photo:
        msg = await context.bot.send_photo(chat_id=chat_id, photo=photo, caption=text)
    else:
        msg = await context.bot.send_message(chat_id=chat_id, text=text)
    
    # Отправляем комментарий (если чат поддерживает)
    warning = (
        f"⚠️ Осторожно, мошенники! Мы НИКОГДА не пишем в ЛС с предложениями оплатить или что-то сделать.\n"
        f"Единственный контакт: {SUPPORT_USERNAME}"
    )
    try:
        await context.bot.send_message(chat_id=chat_id, text=warning, reply_to_message_id=msg.message_id)
    except:
        # Если не поддерживается (например, канал), игнорируем
        pass

# === Основной запуск ===
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Модерация
    app.add_handler(MessageHandler(filters.Chat(MAIN_CHAT_ID) & filters.TEXT, handle_main_chat))

    # Системная группа
    app.add_handler(CallbackQueryHandler(system_callback, pattern="^(ban_|allow_)"))

    # Конкурс (пример)
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("lottery", start_lottery)],
        states={
            WAITING_CHANNELS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_channels)],
            CHECKING_PERMISSIONS: [CallbackQueryHandler(check_permissions, pattern="check_perms")]
            # ... остальные состояния
        },
        fallbacks=[]
    )
    app.add_handler(conv_handler)

    # Команда для получения ID (временно)
    app.add_handler(CommandHandler("id", lambda u, c: u.message.reply_text(f"ID: {u.effective_chat.id}")))

    app.run_polling()
    logger.info("Бот запущен")

if __name__ == "__main__":
    main()

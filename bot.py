"""
Постоянно работающий бот (в отличие от post.py, который запускается разово
и завершается). Делает две вещи:

1. Сам постит во все каналы из channels.json по расписанию — каждый день
   в 10:00 и 20:00 по МСК (время задаётся в config.py, POST_TIMES).
2. Отвечает на /start двумя кнопками, которые обновляют текст сообщения:
   - «📊 Статус» — работает бот сейчас или нет, когда следующий автопостинг,
     сколько каналов в базе, результат последнего запуска.
   - «🚀 Запостить сейчас» — запускает внеочередной постинг по команде и
     после завершения показывает, сколько каналов вышло успешно, а сколько
     с ошибкой.

Доступ к боту (и /start, и кнопкам) ограничен списком ADMIN_IDS в config.py.

База каналов (channels.json) и вся логика синхронизации/постинга не
менялись — используются те же storage.py / sync_channels.py / posting.py,
что и в post.py, так что бот продолжит постить туда же, куда постил раньше.

Запуск (процесс должен работать постоянно, например в systemd/screen/tmux
или как соответствующий сервис у хостера):
    python bot.py
"""

import logging
from datetime import datetime, time as dtime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ChatMemberHandler,
    ContextTypes,
)
from telegram.request import HTTPXRequest

from config import (
    BOT_TOKEN,
    DEFAULT_TEXT,
    DEFAULT_IMAGE,
    PROXY_URL,
    IGNORE_ENV_PROXY,
    ADMIN_IDS,
    MSK_TZ,
    POST_TIMES,
)
from storage import load_channels, save_channels
from posting import run_posting_cycle

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

CB_STATUS = "status"
CB_POST_NOW = "post_now"

KEYBOARD = InlineKeyboardMarkup(
    [
        [
            InlineKeyboardButton("📊 Статус", callback_data=CB_STATUS),
            InlineKeyboardButton("🚀 Запостить сейчас", callback_data=CB_POST_NOW),
        ]
    ]
)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def format_last_run(last_run: dict | None) -> str:
    if not last_run:
        return "ещё ни разу не запускался"
    ts = last_run["time"].strftime("%d.%m %H:%M")
    trigger = "автоматически" if last_run["trigger"] == "auto" else "вручную"
    return (
        f"{ts} МСК ({trigger}) — успешно: {last_run['success']}, "
        f"с ошибками: {last_run['failed']}, всего каналов: {last_run['total']}"
    )


def build_status_text(bot_data: dict) -> str:
    channels = load_channels()
    posting_now = bot_data.get("is_posting", False)
    state = "🟡 сейчас идёт постинг…" if posting_now else "🟢 работает, ждёт расписания"
    times = ", ".join(f"{h:02d}:{m:02d}" for h, m in POST_TIMES)
    last_run = bot_data.get("last_run")

    return (
        "<b>Статус бота</b>\n"
        f"Состояние: {state}\n"
        f"Каналов в базе: {len(channels)}\n"
        f"Автопостинг: каждый день в {times} (МСК)\n"
        f"Последний запуск: {format_last_run(last_run)}"
    )


async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ловит апдейты о добавлении/удалении бота из канала в реальном
    времени, пока bot.py работает, и сразу обновляет channels.json.

    Раньше (в post.py/sync_channels.py) это делалось отдельным запросом
    getUpdates с собственным offset.json — это и сейчас работает для
    разового запуска post.py. Но пока bot.py крутится постоянно и сам
    непрерывно опрашивает Telegram через run_polling, второй параллельный
    getUpdates с другим offset'ом конфликтовал бы с ним (Telegram отдаёт
    и подтверждает апдейты по общему для бота курсору). Поэтому здесь
    вместо этого используется обычный хэндлер на my_chat_member — он
    получает те же апдейты через общий поток run_polling и обновляет базу
    сразу, без каких-либо ограничений по времени хранения апдейтов у
    Telegram (раньше было ~24 часа, теперь фактически мгновенно)."""
    mcm = update.my_chat_member
    if mcm is None:
        return

    chat = mcm.chat
    new_status = mcm.new_chat_member.status
    chat_id_str = str(chat.id)
    channels = load_channels()

    if new_status in ("member", "administrator"):
        channels[chat_id_str] = chat.title or chat.username or chat_id_str
        save_channels(channels)
        log.info(f"Бот добавлен в канал: {channels[chat_id_str]} ({chat.id})")
    elif new_status in ("left", "kicked"):
        if chat_id_str in channels:
            log.info(f"Бот удалён из канала: {channels[chat_id_str]} ({chat.id})")
            del channels[chat_id_str]
            save_channels(channels)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ У вас нет доступа к этому боту.")
        return
    await update.message.reply_text(
        build_status_text(context.application.bot_data),
        parse_mode="HTML",
        reply_markup=KEYBOARD,
    )


async def do_posting_run(context: ContextTypes.DEFAULT_TYPE, trigger: str) -> dict:
    """Общий запуск постинга — используется и кнопкой «Запостить сейчас»,
    и автопостингом по расписанию. Пишет результат в bot_data["last_run"]."""
    bot_data = context.application.bot_data
    bot_data["is_posting"] = True
    try:
        # do_sync=False: пока bot.py работает, channels.json и так уже
        # обновляется в реальном времени хэндлером on_my_chat_member —
        # отдельная синхронизация через getUpdates тут не нужна (и
        # конфликтовала бы с непрерывным run_polling, см. комментарий
        # у on_my_chat_member). do_verify=True — лёгкая доп. проверка
        # через get_chat_member, отдельного потока апдейтов не трогает.
        result = await run_posting_cycle(
            context.bot, DEFAULT_TEXT, DEFAULT_IMAGE, do_sync=False, do_verify=True
        )
    except Exception:
        log.exception(f"Ошибка во время постинга ({trigger})")
        result = {"total": 0, "success": 0, "failed": 0}
    finally:
        bot_data["is_posting"] = False

    bot_data["last_run"] = {"time": datetime.now(MSK_TZ), "trigger": trigger, **result}
    log.info(
        f"Постинг ({trigger}) завершён: успешно {result['success']}, "
        f"с ошибками {result['failed']}, всего {result['total']}"
    )
    return bot_data["last_run"]


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Нет доступа", show_alert=True)
        return

    if query.data == CB_STATUS:
        await query.answer()
        await query.edit_message_text(
            build_status_text(context.application.bot_data),
            parse_mode="HTML",
            reply_markup=KEYBOARD,
        )
        return

    if query.data == CB_POST_NOW:
        if context.application.bot_data.get("is_posting"):
            await query.answer("Постинг уже идёт, подожди…", show_alert=True)
            return

        await query.answer("Начинаю постинг…")
        await query.edit_message_text(
            "⏳ Идёт постинг во все каналы…",
            parse_mode="HTML",
            reply_markup=KEYBOARD,
        )
        await do_posting_run(context, trigger="manual")
        await query.edit_message_text(
            build_status_text(context.application.bot_data),
            parse_mode="HTML",
            reply_markup=KEYBOARD,
        )


async def scheduled_post(context: ContextTypes.DEFAULT_TYPE):
    result = await do_posting_run(context, trigger="auto")
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=(
                    "📬 Автопостинг завершён.\n"
                    f"Успешно: {result['success']}, с ошибками: {result['failed']}, "
                    f"всего каналов: {result['total']}"
                ),
            )
        except Exception:
            log.exception(f"Не удалось уведомить админа {admin_id}")


def main():
    if not ADMIN_IDS or ADMIN_IDS == [123456789]:
        log.warning(
            "ADMIN_IDS в config.py не настроен (стоит заглушка) — "
            "впиши туда свой Telegram user id, иначе бот никого не пустит к кнопкам."
        )

    # Тот же способ настройки прокси, что и в post.py — два независимых
    # HTTP-клиента (обычные запросы и get_updates) должны получить
    # одинаковые настройки.
    request_kwargs = dict(
        proxy=PROXY_URL,
        httpx_kwargs={"trust_env": not IGNORE_ENV_PROXY},
    )
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .request(HTTPXRequest(**request_kwargs))
        .get_updates_request(HTTPXRequest(**request_kwargs))
        .build()
    )

    application.bot_data["is_posting"] = False
    application.bot_data["last_run"] = None

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(on_button))
    application.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))

    for hour, minute in POST_TIMES:
        application.job_queue.run_daily(
            scheduled_post,
            time=dtime(hour=hour, minute=minute, tzinfo=MSK_TZ),
            name=f"autopost_{hour:02d}{minute:02d}",
        )

    log.info(
        "Бот запущен. Автопостинг: %s (МСК)",
        ", ".join(f"{h:02d}:{m:02d}" for h, m in POST_TIMES),
    )
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

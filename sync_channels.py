"""
Отслеживает, в какие каналы/группы бот был добавлен (или из каких удалён),
читая апдейты типа my_chat_member, и обновляет channels.json.

Важно: Telegram хранит недоставленные апдейты ограниченное время (обычно
около 24 часов), пока бот их не подтвердит (не сдвинет offset). Поэтому
желательно запускать sync (напрямую или через post.py) не реже раза в день,
иначе апдейт о добавлении в новый канал может "протухнуть" и канал придётся
добавлять вручную.
"""

import logging
from telegram import Bot
from telegram.error import TelegramError

from storage import load_channels, save_channels, load_offset, save_offset

log = logging.getLogger(__name__)


async def sync_channels(bot: Bot) -> dict:
    """Обновляет channels.json на основе новых my_chat_member апдейтов.
    Возвращает актуальный словарь {chat_id_str: title}."""

    channels = load_channels()
    offset = load_offset()

    updates = await bot.get_updates(
        offset=offset,
        allowed_updates=["my_chat_member"],
        timeout=10,
    )

    for upd in updates:
        offset = upd.update_id + 1

        mcm = upd.my_chat_member
        if mcm is None:
            continue

        chat = mcm.chat
        new_status = mcm.new_chat_member.status
        chat_id_str = str(chat.id)

        if new_status in ("member", "administrator"):
            channels[chat_id_str] = chat.title or chat.username or chat_id_str
            log.info(f"Добавлен канал: {channels[chat_id_str]} ({chat.id})")
        elif new_status in ("left", "kicked"):
            if chat_id_str in channels:
                log.info(f"Бот удалён из канала: {channels[chat_id_str]} ({chat.id})")
                del channels[chat_id_str]

    save_offset(offset)
    save_channels(channels)
    return channels


async def verify_channels(bot: Bot, channels: dict) -> dict:
    """Дополнительно проверяет, что бот всё ещё имеет доступ к каждому
    сохранённому каналу (на случай, если апдейт об удалении был пропущен).
    Убирает из списка каналы, куда постить не получается."""

    verified = {}
    for chat_id_str, title in channels.items():
        try:
            member = await bot.get_chat_member(chat_id=int(chat_id_str), user_id=bot.id)
            if member.status in ("member", "administrator"):
                verified[chat_id_str] = title
            else:
                log.warning(f"Бот больше не участник канала: {title} ({chat_id_str})")
        except TelegramError as e:
            log.warning(f"Канал недоступен, убираю из списка: {title} ({chat_id_str}) — {e}")

    if verified != channels:
        save_channels(verified)

    return verified

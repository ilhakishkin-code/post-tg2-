"""
Общая логика постинга: синхронизация каналов + рассылка фото/текста.
Используется и CLI-скриптом (post.py), и постоянно работающим ботом (bot.py),
чтобы не дублировать код.
"""

import asyncio
import logging

from telegram import Bot
from telegram.error import TelegramError

from config import DELAY_BETWEEN_CHANNELS
from storage import load_channels
from sync_channels import sync_channels, verify_channels

log = logging.getLogger(__name__)


async def post_to_channel(bot: Bot, chat_id: int, title: str, text: str, image_path: str, dry_run: bool = False) -> bool:
    if dry_run:
        log.info(f"[DRY-RUN] Запостил бы в '{title}' ({chat_id}): фото={image_path}, текст={text!r}")
        return True

    ok = True
    try:
        with open(image_path, "rb") as photo:
            await bot.send_photo(chat_id=chat_id, photo=photo)
    except TelegramError as e:
        log.error(f"Не удалось отправить фото в '{title}' ({chat_id}): {e}")
        ok = False
    except FileNotFoundError:
        log.error(f"Файл картинки не найден: {image_path}")
        ok = False

    try:
        await bot.send_message(chat_id=chat_id, text=text)
    except TelegramError as e:
        log.error(f"Не удалось отправить текст в '{title}' ({chat_id}): {e}")
        ok = False

    return ok


async def run_posting_cycle(
    bot: Bot,
    text: str,
    image_path: str,
    *,
    do_sync: bool = True,
    do_verify: bool = True,
    dry_run: bool = False,
) -> dict:
    """Синхронизирует список каналов (опционально), проверяет доступность
    (опционально) и постит во все них. Возвращает статистику:
    {"total": int, "success": int, "failed": int}.
    """
    if do_sync:
        log.info("Синхронизация списка каналов...")
        channels = await sync_channels(bot)
    else:
        channels = load_channels()

    if do_verify:
        channels = await verify_channels(bot, channels)

    if not channels:
        log.warning("Список каналов пуст — постить некуда.")
        return {"total": 0, "success": 0, "failed": 0}

    log.info(f"Найдено каналов: {len(channels)}")

    success, failed = 0, 0
    for chat_id_str, title in channels.items():
        ok = await post_to_channel(bot, int(chat_id_str), title, text, image_path, dry_run)
        success += 1 if ok else 0
        failed += 0 if ok else 1
        # ВАЖНО: asyncio.sleep, а не time.sleep — иначе блокируется весь
        # event loop бота (и он перестаёт отвечать на кнопки/апдейты),
        # пока идёт пауза между каналами.
        if not dry_run:
            await asyncio.sleep(DELAY_BETWEEN_CHANNELS)

    log.info(f"Готово. Успешно: {success}, с ошибками: {failed}")
    return {"total": len(channels), "success": success, "failed": failed}

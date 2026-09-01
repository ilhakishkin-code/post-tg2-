"""
CLI-скрипт: синхронизирует список каналов (куда бот был добавлен) и
постит во все них два сообщения — с картинкой и с текстом.

Это разовый запуск "вручную". Для автопостинга по расписанию (10:00 и
20:00 МСК) и управления через кнопки в Telegram используй bot.py — он
работает постоянно (не как разовый скрипт) и переиспользует ту же
логику постинга из posting.py.

Запуск:
    python post.py
    python post.py --text "Другой текст" --image другая_картинка.jpg
    python post.py --no-sync        # не проверять новые каналы, просто постить
    python post.py --dry-run        # только показать, куда бы запостил
"""

import argparse
import asyncio
import logging

from telegram import Bot
from telegram.request import HTTPXRequest

from config import (
    BOT_TOKEN,
    DEFAULT_TEXT,
    DEFAULT_IMAGE,
    PROXY_URL,
    IGNORE_ENV_PROXY,
)
from posting import run_posting_cycle

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


async def main():
    parser = argparse.ArgumentParser(description="Постинг во все каналы, где есть бот")
    parser.add_argument("--text", default=DEFAULT_TEXT, help="Текст поста")
    parser.add_argument("--image", default=DEFAULT_IMAGE, help="Путь к картинке")
    parser.add_argument("--no-sync", action="store_true", help="Не обновлять список каналов перед постингом")
    parser.add_argument("--no-verify", action="store_true", help="Не проверять доступность каждого канала (быстрее)")
    parser.add_argument("--dry-run", action="store_true", help="Ничего не постить, только показать список")
    args = parser.parse_args()

    # httpx-клиент: явный прокси (если задан) и/или игнор системных
    # переменных окружения, которые может выставлять VPN-клиент.
    # Telegram-библиотека использует ДВА разных HTTP-клиента — один для
    # обычных запросов, другой отдельно для get_updates — оба должны
    # получить одинаковые настройки, иначе один из них подхватит системный
    # прокси в обход наших настроек.
    request_kwargs = dict(
        proxy=PROXY_URL,
        httpx_kwargs={"trust_env": not IGNORE_ENV_PROXY},
    )
    bot = Bot(
        token=BOT_TOKEN,
        request=HTTPXRequest(**request_kwargs),
        get_updates_request=HTTPXRequest(**request_kwargs),
    )
    await bot.initialize()

    result = await run_posting_cycle(
        bot,
        args.text,
        args.image,
        do_sync=not args.no_sync,
        do_verify=not args.no_verify,
        dry_run=args.dry_run,
    )

    log.info(f"Итог: успешно {result['success']}, с ошибками {result['failed']}, всего {result['total']}")

    await bot.shutdown()


if __name__ == "__main__":
    asyncio.run(main())

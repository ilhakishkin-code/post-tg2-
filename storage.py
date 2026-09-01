"""Простое JSON-хранилище для списка каналов и offset апдейтов."""

import json
import os
from config import CHANNELS_FILE, OFFSET_FILE


def load_channels() -> dict:
    """Возвращает {chat_id_str: title}"""
    if not os.path.exists(CHANNELS_FILE):
        return {}
    with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_channels(channels: dict) -> None:
    with open(CHANNELS_FILE, "w", encoding="utf-8") as f:
        json.dump(channels, f, ensure_ascii=False, indent=2)


def load_offset() -> int:
    if not os.path.exists(OFFSET_FILE):
        return 0
    with open(OFFSET_FILE, "r", encoding="utf-8") as f:
        return json.load(f).get("offset", 0)


def save_offset(offset: int) -> None:
    with open(OFFSET_FILE, "w", encoding="utf-8") as f:
        json.dump({"offset": offset}, f)

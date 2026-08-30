import os

from telethon.sync import TelegramClient

_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_env_path):
    for _line in open(_env_path):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

API_ID   = int(os.environ.get("TELEGRAM_API_ID", "0"))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "")

if not API_ID or not API_HASH:
    raise SystemExit(
        "Faltan TELEGRAM_API_ID / TELEGRAM_API_HASH. Copia .env.example a .env y rellénalos "
        "con tus credenciales de https://my.telegram.org"
    )

with TelegramClient("sesion_biblioteca", API_ID, API_HASH) as client:
    for d in client.get_dialogs():
        if hasattr(d.entity, "megagroup") or hasattr(d.entity, "broadcast"):
            print(d.id, "|", d.name)

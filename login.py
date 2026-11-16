import asyncio
import getpass
import os
import sys
import tempfile
from datetime import datetime, timezone

for stream in (sys.stdout, sys.stderr):
    stream.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv

BASE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE, "keys.env"))

from telethon import TelegramClient
from guard import lock_to_calls_only


def need(name: str) -> str:
    value = os.environ.get(name, "")
    if not value or "ВСТАВЬТЕ" in value.upper():
        sys.exit(f"Заполни {name} в keys.env (сейчас там пусто или заглушка).")
    return value
from telethon.errors import (
    FloodWaitError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SendCodeUnavailableError,
    SessionPasswordNeededError,
)
from telethon.tl.types import auth

WHERE = {
    auth.SentCodeTypeApp: "в приложение Telegram — чат «Telegram» с синей галочкой (не SMS!)",
    auth.SentCodeTypeSms: "по SMS",
    auth.SentCodeTypeCall: "звонком (робот продиктует код)",
    auth.SentCodeTypeFlashCall: "сбросом звонка (код — последние цифры номера)",
    auth.SentCodeTypeMissedCall: "пропущенным звонком (код — последние цифры номера)",
}


def describe(sent) -> str:
    for kind, text in WHERE.items():
        if isinstance(sent.type, kind):
            return text
    return f"способом {type(sent.type).__name__}"


async def password_step(client: TelegramClient) -> None:
    while True:
        password = getpass.getpass("Включена двухэтапная проверка. Введи облачный пароль Telegram (символы не видны): ")
        try:
            await client.sign_in(password=password)
            return
        except Exception as e:
            print(f"Не подошёл: {e}. Попробуй ещё раз.")


async def login_by_code(client: TelegramClient) -> None:
    phone = input("Номер телефона в международном формате (например +XXX...): ").strip()
    sent = await client.send_code_request(phone)
    print(f"\nКод отправлен {describe(sent)}.")
    if getattr(sent, "next_type", None):
        print("Если код не пришёл за минуту — введи r, и Telegram пришлёт его другим способом.")

    while True:
        code = input("\nВведи код (или r — прислать заново): ").strip()
        if code.lower() in ("r", "к"):
            try:
                sent = await client.send_code_request(phone)
                print(f"Новый код отправлен {describe(sent)}.")
            except FloodWaitError as e:
                print(f"Telegram просит подождать {e.seconds} с перед новым кодом.")
            except SendCodeUnavailableError:
                print("Других способов нет: Telegram шлёт код только в приложение. Ищи его там — старый код ещё действует.")
            continue
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)
            return
        except PhoneCodeInvalidError:
            print("Код неверный, попробуй ещё раз.")
        except PhoneCodeExpiredError:
            print("Код устарел. Введи r, чтобы получить новый.")
        except SessionPasswordNeededError:
            await password_step(client)
            return


QR_PORT = 8765
QR_DIR = os.path.join(tempfile.gettempdir(), "rust-call-bot-qr")


def write_page(body: str, refresh: bool = True) -> None:
    meta = '<meta http-equiv="refresh" content="2">' if refresh else ""
    html = f"""<!doctype html><html><head><meta charset="utf-8">{meta}<title>Вход в Telegram</title>
<style>body{{font-family:sans-serif;background:#fff;color:#111;text-align:center;padding:24px}}
img{{width:340px;height:340px;image-rendering:pixelated}}ol{{display:inline-block;text-align:left;font-size:17px}}</style>
</head><body>{body}</body></html>"""
    tmp = os.path.join(QR_DIR, "index.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(html)
    os.replace(tmp, os.path.join(QR_DIR, "index.html"))


def start_server() -> None:
    import functools
    import http.server
    import threading

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = functools.partial(Quiet, directory=QR_DIR)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", QR_PORT), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()


async def login_by_qr(client: TelegramClient) -> None:
    import base64
    import io

    import qrcode

    os.makedirs(QR_DIR, exist_ok=True)
    write_page("<p>Готовлю QR-код…</p>")
    start_server()
    print(f"QR-код открыт на странице http://localhost:{QR_PORT} — он обновляется сам.")
    print("Telegram на телефоне → Настройки → Устройства → «Подключить устройство» → наведи на QR-код.\n", flush=True)

    qr_login = await client.qr_login()
    while True:
        buf = io.BytesIO()
        qrcode.make(qr_login.url, border=2).save(buf, format="PNG")
        png = base64.b64encode(buf.getvalue()).decode()
        write_page(
            f'<h2>Вход бота в Telegram</h2><img src="data:image/png;base64,{png}" alt="QR">'
            "<ol><li>Открой Telegram на телефоне</li><li>Настройки → Устройства</li>"
            "<li>«Подключить устройство»</li><li>Наведи камеру на этот QR-код</li></ol>"
            "<p>Код обновляется сам, просто держи камеру на нём.</p>"
        )
        seconds_left = (qr_login.expires - datetime.now(timezone.utc)).total_seconds() - 2
        try:
            await qr_login.wait(timeout=max(seconds_left, 5))
            break
        except asyncio.TimeoutError:
            await qr_login.recreate()
        except SessionPasswordNeededError:
            write_page("<h2>Почти готово</h2><p>Введи облачный пароль Telegram в окне терминала.</p>")
            await password_step(client)
            break
    write_page("<h2>✅ Готово! Бот вошёл в Telegram.</h2><p>Эту страницу можно закрыть.</p>", refresh=False)


async def main() -> None:
    client = lock_to_calls_only(
        TelegramClient(os.path.join(BASE, "telegram"), int(need("TG_API_ID")), need("TG_API_HASH"))
    )
    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            print(f"Уже вошёл как {me.first_name}. Ничего делать не нужно.")
            return
        if len(sys.argv) > 1 and sys.argv[1] == "qr":
            await login_by_qr(client)
        else:
            await login_by_code(client)
        me = await client.get_me()
        print(f"\nГотово! Вошёл как {me.first_name}. Вход сохранён, больше не понадобится.")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())

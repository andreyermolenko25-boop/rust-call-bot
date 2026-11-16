# rust-call-bot

Голосовой бот для звонков в Telegram.

## Установка

```bash
py -3.14 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

Папка должна лежать по короткому пути, например `C:\rust-call-bot`.

## Ключи

Скопируй `keys.env.example` в `keys.env` и вставь:

| Поле | Где взять |
|---|---|
| `TG_API_ID`, `TG_API_HASH` | https://my.telegram.org → API development tools |
| `ANTHROPIC_API_KEY` | https://platform.claude.com/settings/keys |
| `google-key.json` | Google Cloud → включить Speech-to-Text и Text-to-Speech → Service accounts → Keys → JSON. Положить рядом с `bot.py` |

## Настройки

Скопируй `settings.env.example` в `settings.env` и вставь:

| Поле | Что вписать |
|---|---|
| `FRIEND` | ник друга с `@` |
| `FRIEND_NAME` | как его называть |
| `CALL_TOPIC` | тема звонка |

## Запуск

```bash
.venv\Scripts\python login.py
```

```bash
Звонилка.bat
```

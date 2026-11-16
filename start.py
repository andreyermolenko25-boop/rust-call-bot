import os
import subprocess
import sys
from pathlib import Path

for stream in (sys.stdout, sys.stderr):
    stream.reconfigure(encoding="utf-8", errors="replace")

from dotenv import dotenv_values

BASE_DIR = Path(__file__).resolve().parent
SETTINGS = BASE_DIR / "settings.env"
PYTHON = sys.executable

HELP = """
Команды:
  /звонок            позвонить (спросит основную тему разговора)
  /звонок <тема>     позвонить сразу с этой темой
  /кому @ник Имя     кому звонить, например: /кому @username Вася
  /тема <текст>      тема по умолчанию (её предложит /звонок)
  /мат вкл | выкл    общаться с матом или без
  /скорость <число>  скорость речи: 1.0 обычная, 1.1 быстрее, 0.9 медленнее
  /настройки         показать текущие настройки
  /помощь            этот список
  /выход             закрыть Звонилку

Во время звонка пиши прямо в это окно и жми Enter:
  <текст>            подсказка ИИ — учтёт в следующем ответе
  !<текст>           бот сразу скажет это слово в слово
  /стоп              положить трубку
"""


def load() -> dict:
    return {k: (v or "") for k, v in dotenv_values(SETTINGS).items()}


def save(key: str, value: str) -> None:
    lines = SETTINGS.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == key and not line.lstrip().startswith("#"):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    SETTINGS.write_text("\n".join(lines) + "\n", encoding="utf-8")


def show_settings() -> None:
    s = load()
    print(
        f"\n  Кому:      {s.get('FRIEND_NAME')} ({s.get('FRIEND')})"
        f"\n  Тема:      {s.get('CALL_TOPIC')}"
        f"\n  Мат:       {'вкл' if s.get('SWEAR') == '1' else 'выкл'}"
        f"\n  Голос:     {s.get('TTS_VOICE')}, скорость {s.get('TTS_RATE')}"
        f"\n  Модель:    {s.get('CLAUDE_MODEL')}\n"
    )


def call(topic: str) -> None:
    s = load()
    default = s.get("CALL_TOPIC", "")
    if not topic:
        entered = input(f"Основная тема звонка (Enter — «{default}»): ").strip()
        topic = entered or default
    answer = input(f"Звоню {s.get('FRIEND_NAME')} ({s.get('FRIEND')}), тема: «{topic}». Enter — звонить, н — отмена: ")
    if answer.strip().lower() in ("н", "n", "нет", "no"):
        print("Отменено.")
        return
    print("\n" + "=" * 60)
    try:
        subprocess.run([PYTHON, str(BASE_DIR / "bot.py"), topic], cwd=BASE_DIR)
    except KeyboardInterrupt:
        print("\nЗвонок прерван.")
    print("=" * 60 + "\nЗвонок завершён. Можно звонить снова: /звонок\n")


def set_friend(args: str) -> None:
    parts = args.split(maxsplit=1)
    if not parts or not parts[0].startswith("@"):
        print("Напиши так: /кому @ник Имя   (например: /кому @username Вася)")
        return
    save("FRIEND", parts[0])
    name = parts[1] if len(parts) > 1 else input("Как бот будет его называть? ").strip() or parts[0].lstrip("@")
    save("FRIEND_NAME", name)
    print(f"Теперь звоню: {name} ({parts[0]})")


def main() -> None:
    os.chdir(BASE_DIR)
    print("=" * 60)
    print("  ЗВОНИЛКА — звонки бота в Telegram")
    print("=" * 60)
    show_settings()
    print(HELP)
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        command, _, args = line.partition(" ")
        command, args = command.lower(), args.strip()

        if command in ("/звонок", "/call", "/позвонить"):
            call(args)
        elif command in ("/кому", "/to"):
            set_friend(args)
        elif command in ("/тема", "/topic"):
            if args:
                save("CALL_TOPIC", args)
                print(f"Тема по умолчанию: {args}")
            else:
                print("Напиши так: /тема позвать в субботу на шашлыки")
        elif command in ("/мат", "/swear"):
            if args in ("вкл", "on", "1", "да"):
                save("SWEAR", "1")
                print("Мат включён.")
            elif args in ("выкл", "off", "0", "нет"):
                save("SWEAR", "0")
                print("Мат выключен.")
            else:
                print("Напиши так: /мат вкл   или   /мат выкл")
        elif command in ("/скорость", "/speed"):
            try:
                rate = float(args.replace(",", "."))
                if not 0.5 <= rate <= 2.0:
                    raise ValueError
                save("TTS_RATE", f"{rate}")
                print(f"Скорость речи: {rate}")
            except ValueError:
                print("Напиши число от 0.5 до 2.0, например: /скорость 1.1")
        elif command in ("/настройки", "/settings"):
            show_settings()
        elif command in ("/помощь", "/help", "/команды"):
            print(HELP)
        elif command in ("/выход", "/exit", "/quit"):
            return
        else:
            print("Не знаю такую команду. Список: /помощь")


if __name__ == "__main__":
    main()

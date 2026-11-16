import asyncio
import sys

for stream in (sys.stdout, sys.stderr):
    stream.reconfigure(encoding="utf-8", errors="replace")

import bot


def ok(msg: str) -> None:
    print(f"  [OK] {msg}", flush=True)


def fail(msg: str, e: Exception) -> None:
    print(f"  [ОШИБКА] {msg}: {type(e).__name__}: {e}", flush=True)


async def main() -> int:
    errors = 0
    pcm = b""

    print("1. Google Text-to-Speech (озвучка)")
    try:
        from google.cloud import texttospeech

        client = texttospeech.TextToSpeechClient()
        pcm = bot.synthesize_pcm(client, "Проверка связи, как слышно?")
        ok(f"голос {bot.TTS_MODEL or 'обычный'} / {bot.TTS_VOICE}, {len(pcm) / bot.BYTES_PER_MS / 1000:.1f} с звука")
    except Exception as e:
        errors += 1
        fail("озвучка не работает", e)

    print("2. Google Speech-to-Text (распознавание)")
    if pcm:
        try:
            from google.cloud import speech

            client = speech.SpeechClient()
            response = client.recognize(
                config=speech.RecognitionConfig(
                    encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
                    sample_rate_hertz=bot.SAMPLE_RATE,
                    language_code=bot.LANGUAGE,
                    enable_automatic_punctuation=True,
                ),
                audio=speech.RecognitionAudio(content=pcm),
            )
            text = " ".join(r.alternatives[0].transcript for r in response.results if r.alternatives)
            ok(f"распознано: «{text}»")
        except Exception as e:
            errors += 1
            fail("распознавание не работает", e)
    else:
        print("  пропущено: нет звука из шага 1")

    print("3. Колонки")
    if not bot.MONITOR:
        print("  пропущено: MONITOR=0")
    elif bot.sounddevice is None:
        errors += 1
        print("  [ОШИБКА] sounddevice не загрузился")
    elif pcm:
        try:
            stream = bot.sounddevice.RawOutputStream(samplerate=bot.SAMPLE_RATE, channels=1, dtype="int16")
            stream.start()
            stream.stop()
            stream.close()
            ok("колонки открываются (звук не проигрывается)")
        except Exception as e:
            errors += 1
            fail("колонки не открылись", e)

    print(f"4. Claude ({bot.CLAUDE_MODEL})")
    try:
        import anthropic

        client = anthropic.AsyncAnthropic()
        extra = {}
        if not bot.CLAUDE_MODEL.startswith("claude-haiku"):
            extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        response = await client.beta.messages.create(
            model=bot.CLAUDE_MODEL,
            max_tokens=4000,
            system=bot.SYSTEM_PROMPT,
            messages=[{"role": "user", "content": bot.OPENING_MESSAGE}],
            output_config={"effort": bot.CLAUDE_EFFORT},
            **extra,
        )
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        ok(f"первая реплика бота: «{text}»")
    except Exception as e:
        errors += 1
        fail("Claude не отвечает", e)

    print("5. Telegram (вход и поиск друга, без звонка)")
    from telethon import TelegramClient

    tg = bot.lock_to_calls_only(TelegramClient(str(bot.BASE_DIR / "telegram"), bot.TG_API_ID, bot.TG_API_HASH))
    try:
        await tg.connect()
        if not await tg.is_user_authorized():
            raise RuntimeError("бот ещё не вошёл в Telegram — запусти login.py")
        me = await tg.get_me()
        ok(f"вошёл как {me.first_name} (@{me.username})")
        friend = await tg.get_entity(bot.FRIEND)
        ok(f"друг найден: {friend.first_name} (@{friend.username})")
    except Exception as e:
        errors += 1
        fail("Telegram", e)
    finally:
        await tg.disconnect()

    print()
    print("Всё готово к звонку!" if errors == 0 else f"Есть ошибки: {errors}. Пришли этот вывод Claude.")
    return errors


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

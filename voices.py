import sys
import time
import wave

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import bot
from google.cloud import texttospeech

PHRASE = (
    "Здарова, друг. Как сам, блядь? Давно не слышались. "
    "Рассказывай, чё нового, только без этой херни, по-честному."
)

STYLE = (
    "Говори медленно, низким, спокойным и серьёзным мужским голосом. "
    "Уверенно и невозмутимо, с небольшими паузами, как хладнокровный ИИ-ассистент."
)

MODEL = "gemini-2.5-pro-tts"

CANDIDATES = ["Charon", "Fenrir", "Orus", "Algenib", "Iapetus", "Alnilam"]


def main() -> None:
    client = texttospeech.TextToSpeechClient()
    folder = bot.BASE_DIR / "samples"
    folder.mkdir(exist_ok=True)
    only = int(sys.argv[1]) if len(sys.argv) > 1 else None

    for number, voice in enumerate(CANDIDATES, start=1):
        if only and number != only:
            continue
        print(f"{number}. TTS_MODEL={MODEL}  TTS_VOICE={voice}", flush=True)
        started = time.monotonic()
        try:
            pcm = bot.synthesize_pcm(client, PHRASE, voice=voice, model=MODEL, style=STYLE)
        except Exception as e:
            print(f"   не получилось: {e}", flush=True)
            continue
        print(f"   озвучено за {time.monotonic() - started:.1f} с", flush=True)
        path = folder / f"{number}-{voice}.wav"
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(bot.SAMPLE_RATE)
            wav.writeframes(pcm)
        if bot.sounddevice is not None:
            stream = bot.sounddevice.RawOutputStream(samplerate=bot.SAMPLE_RATE, channels=1, dtype="int16")
            stream.start()
            stream.write(pcm)
            stream.stop()
            stream.close()
        time.sleep(0.8)
    print(f"\nФайлы лежат в {folder}")


if __name__ == "__main__":
    main()

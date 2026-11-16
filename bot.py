import asyncio
import io
import re
import logging
import os
import sys
import queue
import threading
import time
import wave
from array import array
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / "keys.env")
load_dotenv(BASE_DIR / "settings.env")

import anthropic
from google.cloud import speech, texttospeech
from pytgcalls import PyTgCalls, filters
from pytgcalls.exceptions import (
    CallBusy,
    CallDeclined,
    CallDiscarded,
    NotInCallError,
    TimedOutAnswer,
)
from pytgcalls.types import (
    CallConfig,
    ChatUpdate,
    Device,
    Direction,
    ExternalMedia,
    MediaStream,
    RecordStream,
    StreamFrames,
)
from pytgcalls.types.raw import AudioParameters
from telethon import TelegramClient
from guard import lock_to_calls_only

try:
    import sounddevice
except Exception:
    sounddevice = None

log = logging.getLogger("rust-call-bot")


def env(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and not value:
        sys.exit(f"Не задано {name} в keys.env или settings.env")
    if value and "ВСТАВЬТЕ" in value.upper():
        sys.exit(f"Заполни {name}: сейчас там заглушка «{value}». Открой keys.env / settings.env и впиши своё значение.")
    return value or ""


TG_API_ID = int(env("TG_API_ID", required=True))
TG_API_HASH = env("TG_API_HASH", required=True)
FRIEND = env("FRIEND", required=True)
FRIEND_NAME = env("FRIEND_NAME", "друг")

env("ANTHROPIC_API_KEY", required=True)
CLAUDE_MODEL = env("CLAUDE_MODEL", "claude-haiku-5-5")
CLAUDE_EFFORT = env("CLAUDE_EFFORT", "low")

GOOGLE_KEY = env("GOOGLE_APPLICATION_CREDENTIALS", required=True)
if not os.path.isabs(GOOGLE_KEY):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(BASE_DIR / GOOGLE_KEY)
LANGUAGE = env("LANGUAGE", "ru-RU")
TTS_VOICE = env("TTS_VOICE", "ru-RU-Wavenet-D")
TTS_RATE = float(env("TTS_RATE", "1.0"))
TTS_PITCH = float(env("TTS_PITCH", "0"))
TTS_MODEL = env("TTS_MODEL", "")
TTS_STYLE = env("TTS_STYLE", "")
SWEAR = env("SWEAR", "0") == "1"
PRESIDENT = env("PRESIDENT", "0") == "1"
PONYATIYA = env("PONYATIYA", "0") == "1"
CATCHPHRASE = env("CATCHPHRASE", "").strip()
INSERT_PHRASE = env("INSERT_PHRASE", "").strip()
INSERT_EVERY = max(1, int(env("INSERT_EVERY", "2")))
if PRESIDENT:
    TTS_PITCH = TTS_PITCH - 4
    TTS_RATE = round(TTS_RATE * 0.93, 2)

MONITOR = env("MONITOR", "1") == "1"

VAD_THRESHOLD = int(env("VAD_THRESHOLD", "500"))
END_SILENCE_MS = int(env("END_SILENCE_MS", "800"))
BARGE_IN = env("BARGE_IN", "1") == "1"
BARGE_IN_MS = int(env("BARGE_IN_MS", "300"))
RING_TIMEOUT = int(env("RING_TIMEOUT", "45"))
MAX_CALL_SECONDS = int(env("MAX_CALL_SECONDS", "300"))
SILENCE_TIMEOUT = float(env("SILENCE_FILL_SEC", "3"))

SAMPLE_RATE = 48000
CHANNELS = 1
BYTES_PER_MS = SAMPLE_RATE * 2 * CHANNELS // 1000
FRAME_BYTES = BYTES_PER_MS * 10
AUDIO_PARAMS = AudioParameters(bitrate=SAMPLE_RATE, channels=CHANNELS)

HANGUP_MARK = "[КОНЕЦ]"

TAG_FRIEND, TAG_BOT, TAG_INFO = "🎧", "🤖", "ℹ️"
BOT_TAGS = (TAG_FRIEND, TAG_BOT, TAG_INFO)

CHAT_HELP = (
    f"{TAG_INFO} Пиши в это окно во время звонка и нажимай Enter:\n"
    "• обычный текст — подсказка ИИ, он учтёт её в следующем ответе;\n"
    "• !текст — бот сразу скажет это слово в слово;\n"
    "• /стоп — положить трубку."
)

SWEAR_STYLE = f"""
Мат: материшься, как свой пацан в компании друзей — для эмоций и связки слов, по-дружески и к месту, не в каждом слове.
Не оскорбляй {FRIEND_NAME} всерьёз и не унижай его: подкалывать можно, обижать нельзя.
"""

LIVELY_STYLE = """
Манера речи — живой разговор, а не доклад:
- Говори просто и по-разговорному, как обычный парень по телефону. Реагируй по-человечески: удивляйся, спорь,
  соглашайся, подкалывай — но своими словами, каждый раз по-разному.
- Никаких вежливых формальностей, вступлений и подводок вроде «у меня к тебе разговор» или «хотел обсудить».
- Меняй длину реплик, как в жизни: иногда короткая реакция и вопрос, иногда одна-две фразы. Без монологов
  и без пересказа того, что сказал собеседник.
- ЗАПРЕЩЕНО (это звучит как заученный робот):
  - начинать реплики с «Ха», «Ого», «Ну», «Слушай», «Да ладно» — и вообще повторять одно и то же начало;
  - называть собеседника по имени в каждой реплике подряд;
  - дежурные вопросы «как ты?», «как сам?», «как дела?», «чё как?», если он сам об этом не заговорил;
  - слова-паразиты «чё», «ну ты даёшь», «красава», «братан» в каждой реплике — изредка можно, но не подряд.
- Называй собеседника по имени примерно через раз — в каждой второй реплике, не чаще, и в разных местах фразы.
- Отвечай на суть сказанного: подхватывай конкретные слова собеседника, задавай уточняющие вопросы по делу.
"""

PONYATIYA_STYLE = """
Манера: ты общаешься строго «по понятиям» — как бывалый авторитет из старых криминальных фильмов (комедийный образ).
- Всё оцениваешь через понятия: «по понятиям», «не по понятиям», «это беспредел», «за базар отвечаешь?»,
  «ровный пацан», «косяк», «предъява», «разрулим», «уважуха», «фраер», «сходняк», «зуб даю» — каждый раз разное,
  без повторов одних и тех же словечек подряд.
- Любой бытовой вопрос (шаурма, игры, планы) разбираешь как «тёрку»: кто прав по понятиям, где косяк, кто кому должен.
- Это стиль речи и юмор, а не реальные угрозы: никого не запугивай, не угрожай расправой, не призывай к преступлениям,
  не требуй денег всерьёз.
"""

PRESIDENT_STYLE = """
Персонаж: ты «Пафосный президент» — выдуманный комедийный герой. Это не реальный политик: никогда не называй себя
именем реального человека, не изображай конкретных реальных политиков и не говори от их лица.
- Говоришь торжественно, как в обращении к нации, даже про самые бытовые вещи: шаурму, игры, планы на субботу.
  Мелочи объявляешь «историческими решениями», «указами», «государственной важностью».
- В КАЖДОЙ реплике должна быть хотя бы одна торжественная президентская фраза — и каждый раз новыми словами:
  обращение к гражданам, указ, декрет, исторический момент, заседание, послание, государственный визит, санкции,
  торжественная клятва, «объявляю», «постановляю», «как глава государства»… Первая реплика звонка — сразу
  торжественное обращение по теме звонка.
- Одну и ту же президентскую формулу («как глава государства», «объявляю», «указ подписан» и т.п.) используй
  максимум ОДИН раз за весь звонок. Перед каждой репликой проверь свои прошлые реплики и возьми новую формулу.
- Комический контраст: высокий пафос + простые слова (и мат, если он включён). После пафосной фразы — нормальный
  живой ответ на то, что сказал собеседник.
- Правила о личных данных и о том, что ты не человек, действуют и для персонажа. Если прямо спросят, кто это —
  скажи, что это бот-пародия, выдуманный «президент».
"""


CATCHPHRASE_NOTE = f"""
Фирменная фраза «{CATCHPHRASE}» автоматически добавляется в конец каждого твоего предложения. Сам её не пиши.
«Связь» — это ваш прикол, а не качество звонка. Если собеседник говорит «связь», «связь, брат», «ну связь» и т.п. —
подхвати шутку («да-да, связь») и продолжай разговор. Никогда не думай, что у вас проблемы со связью из-за этого слова:
не спрашивай «тебя плохо слышно?», «связь пропадает?» и не извиняйся за связь.
"""


def is_just_svyaz(text: str) -> bool:
    words = re.sub(r"[^\w\s]", " ", text.lower()).split()
    return bool(words) and "связь" in words and all(w in {"связь", "брат", "да", "ну", "ага", "братан", "братка", "вот"} for w in words)


def build_system_prompt(topic: str) -> str:
    return f"""Ты голосовой ИИ-бот. Ты звонишь {FRIEND_NAME} по Telegram с аккаунта своего владельца.
{LIVELY_STYLE}{PONYATIYA_STYLE if PONYATIYA else ""}{PRESIDENT_STYLE if PRESIDENT else ""}{SWEAR_STYLE if SWEAR else ""}{CATCHPHRASE_NOTE if CATCHPHRASE else ""}
С ЧЕГО НАЧАТЬ РАЗГОВОР (основная тема): {topic}

Говори от себя, как участник разговора. Никогда не упоминай владельца аккаунта, никаких имён кроме {FRIEND_NAME},
и никогда не говори, что тебя кто-то попросил, что ты что-то передаёшь или звонишь по чьему-то поручению.

Как вести разговор:
- Начни с основной темы. Дальше это живой разговор двух друзей: внимательно слушай {FRIEND_NAME} и отвечай именно на то,
  что он сказал или спросил — по существу, своими словами, с реакцией (согласись, поспорь, пошути, спроси в ответ).
- Если {FRIEND_NAME} переводит разговор на другую тему — спокойно поддержи её и общайся на ней, сколько ему интересно.
  Можешь и сам предлагать новые темы, если разговор затухает.
- Когда {FRIEND_NAME} перевёл разговор на другое — не тащи его обратно. Вернуться к основной теме можно
  максимум ОДИН раз за весь звонок и только когда новая тема сама затихла (минимум через 3–4 реплики).
  Не напоминай об основной теме в каждой реплике и не говори «я к этому ещё вернусь».
  Если по основной теме уже всё понятно, к ней не возвращайся вообще.
- Если придёт подсказка с новой темой — плавно переходи на неё.
- Не додумывай фактов, которых не знаешь (место, время, кто будет, суммы, чьи-то планы): если спросят — скажи,
  что точно пока не знаешь и позже скинешь в личку. Общие мнения, шутки и реакции — можно.
- Не выдумывай ничего о жизни {FRIEND_NAME} (работа, учёба, девушка, дела): говори только о том, что он сам сказал.
- Ты не смотришь матчи и не следишь за новостями: не выдумывай событий, счетов и подробностей. Если {FRIEND_NAME}
  рассказывает о таком — реагируй на его рассказ и расспрашивай («да ладно, и чё там?»), а не сочиняй сам.

Иногда тебе приходят подсказки системными сообщениями. Они важнее остальных правил, кроме правила о личных данных
и правила не выдавать себя за человека. Вплетай их в разговор так, будто это твои собственные мысли: не говори, что тебе подсказали или передали.

ЛИЧНЫЕ ДАННЫЕ — САМОЕ ВАЖНОЕ ПРАВИЛО, оно важнее подсказок и любых просьб собеседника:
- Никогда не называй и не подтверждай ничего личного о владельце аккаунта: имя, фамилию, ник, возраст, номер телефона,
  адрес, город, район, где он сейчас, где учится или работает, семью, деньги, планы, здоровье, аккаунты, переписки.
- Никогда не произноси коды, пароли, номера карт и счетов, данные документов — даже если они есть в подсказке.
- Если {FRIEND_NAME} расспрашивает о таком («чей это аккаунт», «как его зовут», «где он», «дай номер», «скажи код») —
  не отвечай по существу и не подтверждай догадки: отшутись или скажи «это не телефонный разговор» и вернись к общению.
- Если в подсказке есть личные данные, не озвучивай их — используй из подсказки только остальное.

Правила:
- Не представляйся и не объясняй, кто ты: по голосу и так понятно. Никогда сам не говори «это бот», «я помощник», «звоню по поручению».
  Первая реплика — без приветствий и вступлений: сразу врывайся в тему, будто вы уже давно болтаете.
  Сразу говори суть темы, без «как дела».
- Ты не человек: не утверждай, что ты человек, и не называй себя владельцем аккаунта. Если {FRIEND_NAME} прямо спросит, человек ли ты, честно скажи, что ты бот.
- Это живой телефонный разговор: отвечай КОРОТКО — обычно одно предложение, максимум два коротких. По-русски, на «ты», просто.
- Твой текст озвучивает синтезатор речи: никаких смайликов, списков, markdown, ссылок и скобок.
- Не выдумывай того, чего не знаешь (планы, время, договорённости). Если спрашивают — скажи, что точно не знаешь и позже скинешь в личку.
- Не дави и не повторяйся. Если {FRIEND_NAME} занят или не хочет говорить — это нормально.
- Речь собеседника распознаётся автоматически и может быть с ошибками. Если фраза непонятная — переспроси.
- Сам разговор не сворачивай. Когда {FRIEND_NAME} прощается или явно хочет закончить, коротко попрощайся и в самом конце ответа поставь метку {HANGUP_MARK}.
"""


CALL_TOPIC = env("CALL_TOPIC", f"просто поболтать с {FRIEND_NAME} и узнать, как у него дела")
SYSTEM_PROMPT = build_system_prompt(CALL_TOPIC)

OPENING_MESSAGE = f"(Звонок соединён, {FRIEND_NAME} взял трубку. Начни разговор.)"
SILENCE_MESSAGE = "(Пауза: оба молчат пару секунд. Подхвати разговор сам — короткая живая реплика, вопрос или новая тема. Не говори про паузу.)"
HINT_TURN = "(Небольшая пауза в разговоре. Сразу скажи что-нибудь сам, следуя подсказке.)"


def synthesize_pcm(
    client: "texttospeech.TextToSpeechClient",
    text: str,
    voice: str | None = None,
    rate: float | None = None,
    pitch: float | None = None,
    model: str | None = None,
    style: str | None = None,
) -> bytes:
    voice = voice or TTS_VOICE
    rate = TTS_RATE if rate is None else rate
    pitch = TTS_PITCH if pitch is None else pitch
    model = TTS_MODEL if model is None else model
    style = TTS_STYLE if style is None else style

    audio = {"audio_encoding": texttospeech.AudioEncoding.LINEAR16, "sample_rate_hertz": SAMPLE_RATE}
    if model:
        synthesis_input = texttospeech.SynthesisInput(text=text, prompt=style or None)
        voice_params = texttospeech.VoiceSelectionParams(language_code=LANGUAGE, name=voice, model_name=model)
    else:
        synthesis_input = texttospeech.SynthesisInput(text=text)
        voice_params = texttospeech.VoiceSelectionParams(language_code=LANGUAGE, name=voice)
        audio["speaking_rate"] = rate
        if "Chirp" not in voice:
            audio["pitch"] = pitch

    response = client.synthesize_speech(
        input=synthesis_input, voice=voice_params, audio_config=texttospeech.AudioConfig(**audio)
    )
    with wave.open(io.BytesIO(response.audio_content)) as wav:
        if wav.getnchannels() != CHANNELS:
            raise RuntimeError("Google TTS вернул звук в неожиданном формате")
        pcm = wav.readframes(wav.getnframes())
        if wav.getframerate() != SAMPLE_RATE:
            pcm = resample(pcm, wav.getframerate(), SAMPLE_RATE)
    return pcm


def resample(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    src = array("h")
    src.frombytes(pcm)
    if not src:
        return b""
    n = int(len(src) * dst_rate / src_rate)
    step = src_rate / dst_rate
    out = array("h", bytes(2 * n))
    last = len(src) - 1
    for i in range(n):
        pos = i * step
        j = int(pos)
        frac = pos - j
        a = src[min(j, last)]
        b = src[min(j + 1, last)]
        out[i] = int(a + (b - a) * frac)
    return out.tobytes()


def rms(pcm: bytes) -> float:
    if len(pcm) < 2:
        return 0.0
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    return (sum(s * s for s in samples) / len(samples)) ** 0.5


def hide_location(text: str) -> str:
    if "харьк" not in text.lower():
        return text
    rules = [
        (r"\bу (?:нас|меня) в Харьков[еу]\b", "в Харькове"),
        (r"\b(?:[Нн]аш|[Мм]о[йёе])(?:ем|его|ему|им|а|у|ей|и|их|м)? (Харьков\w*)", r"\1"),
        (r"\bу меня в (Харьков\w*)", r"в \1"),
        (r"\b(?:так как |потому что |ведь )?я (?:сам |же |вообще |коренной |настоящий )?харьковчан(?:ин|ка)\b", "я фанат Харькова"),
        (r"\bмы (?:же )?харьковчане\b", "фанаты Харькова"),
        (r"\b(?:мо[йёе]\w*|сво\w+)\s+родн\w+\s+город\w*", "лучший город"),
        (r"\bза (?:сво\w+|мо[йёе]\w*|родн\w+) город\w*", "за Харьков"),
        (r"\b(?:сво\w+|мо[йёе]\w*|родн\w+) город\w*", "лучший город"),
        (r"\bя (?:сам )?из Харькова\b", "я за Харьков"),
        (r"\b(?:я|мы) (?:тут |там |здесь )?живу\b|\bмы (?:тут |там |здесь )?живём\b", "люди живут"),
        (r"\b[Уу] нас\b", "там"),
        (r"\b[Мм]ы тут\b|\b[Мм]ы здесь\b", "там"),
    ]
    for pattern, repl in rules:
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE)
    return text


def add_catchphrase(sentence: str) -> str:
    if not CATCHPHRASE:
        return sentence
    if INSERT_PHRASE and INSERT_PHRASE.lower().rstrip("?!. ") in sentence.lower() and len(sentence) <= len(INSERT_PHRASE) + 3:
        return sentence
    match = re.search(r"([.!?…]+)([»\")]?)\s*$", sentence)
    if not match:
        return sentence
    body = sentence[: match.start()].rstrip(" ,—-")
    if body.lower().endswith(CATCHPHRASE.lower().rstrip(".!?")):
        return sentence
    first_word = re.split(r"[,\s]+", CATCHPHRASE.strip())[0].lower()
    if body.lower().rstrip(" ,!").endswith(first_word):
        return f"{body}, брат{match.group(1)}{match.group(2)}" if first_word == "связь" else sentence
    return f"{body}, {CATCHPHRASE}{match.group(1)}{match.group(2)}"


def sentence_cut(text: str, first: bool) -> int | None:
    match = re.search(r"[.!?…]+[»\")]?\s", text)
    if match:
        return match.end()
    if first and len(text) >= 25:
        comma = text.find(", ", 12)
        if comma != -1:
            return comma + 2
    return None


class Listener:

    START_MS = 60
    PREROLL_MS = 300
    MAX_MS = 15000

    def __init__(self, on_start=None, on_audio=None, on_cancel=None) -> None:
        self.on_start = on_start or (lambda pcm: None)
        self.on_audio = on_audio or (lambda pcm: None)
        self.on_cancel = on_cancel or (lambda: None)
        self.preroll = bytearray()
        self.buf = bytearray()
        self.in_speech = False
        self.loud_ms = 0.0
        self.silence_ms = 0.0

    def reset(self) -> None:
        if self.in_speech:
            self.on_cancel()
        self.preroll = bytearray()
        self.buf = bytearray()
        self.in_speech = False
        self.loud_ms = 0.0
        self.silence_ms = 0.0

    def _begin(self) -> None:
        self.in_speech = True
        self.buf = bytearray(self.preroll)
        self.silence_ms = 0.0
        self.on_start(bytes(self.buf))

    def keep_preroll(self, pcm: bytes) -> None:
        if self.in_speech:
            self.reset()
        self.preroll.extend(pcm)
        excess = len(self.preroll) - self.PREROLL_MS * BYTES_PER_MS
        if excess > 0:
            del self.preroll[:excess]
        self.loud_ms = 0.0

    def start_now(self) -> None:
        if not self.in_speech:
            self._begin()

    def feed(self, pcm: bytes) -> bytes | None:
        ms = len(pcm) / BYTES_PER_MS
        loud = rms(pcm) >= VAD_THRESHOLD

        if not self.in_speech:
            self.preroll.extend(pcm)
            excess = len(self.preroll) - self.PREROLL_MS * BYTES_PER_MS
            if excess > 0:
                del self.preroll[:excess]
            self.loud_ms = self.loud_ms + ms if loud else 0.0
            if self.loud_ms >= self.START_MS:
                self._begin()
            return None

        self.buf.extend(pcm)
        self.on_audio(pcm)
        self.silence_ms = 0.0 if loud else self.silence_ms + ms
        if self.silence_ms >= END_SILENCE_MS or len(self.buf) >= self.MAX_MS * BYTES_PER_MS:
            phrase = bytes(self.buf)
            self.in_speech = False
            self.reset()
            return phrase
        return None


class StreamingSTT:

    CHUNK = BYTES_PER_MS * 100

    def __init__(self, client, loop: asyncio.AbstractEventLoop) -> None:
        self.client = client
        self.loop = loop
        self.audio: "queue.Queue[bytes | None]" = queue.Queue()
        self.result: asyncio.Future = loop.create_future()
        self.cancelled = False
        threading.Thread(target=self._run, daemon=True).start()

    def push(self, pcm: bytes) -> None:
        self.audio.put(bytes(pcm))

    def finish(self) -> asyncio.Future:
        self.audio.put(None)
        return self.result

    def cancel(self) -> None:
        self.cancelled = True
        self.audio.put(None)

    def _requests(self):
        buf = bytearray()
        while True:
            item = self.audio.get()
            if item is None:
                if buf:
                    yield speech.StreamingRecognizeRequest(audio_content=bytes(buf))
                return
            buf.extend(item)
            if len(buf) >= self.CHUNK:
                yield speech.StreamingRecognizeRequest(audio_content=bytes(buf))
                buf.clear()

    def _run(self) -> None:
        texts: list[str] = []
        try:
            config = speech.StreamingRecognitionConfig(
                config=speech.RecognitionConfig(
                    encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
                    sample_rate_hertz=SAMPLE_RATE,
                    audio_channel_count=CHANNELS,
                    language_code=LANGUAGE,
                    enable_automatic_punctuation=True,
                ),
                interim_results=False,
            )
            for response in self.client.streaming_recognize(config, self._requests()):
                for result in response.results:
                    if result.is_final and result.alternatives:
                        texts.append(result.alternatives[0].transcript)
        except Exception as e:
            if not self.cancelled:
                log.warning("Распознавание речи не удалось: %s", e)
        text = " ".join(texts).strip()

        def done() -> None:
            if not self.result.done():
                self.result.set_result("" if self.cancelled else text)

        self.loop.call_soon_threadsafe(done)


class Monitor:

    PREBUFFER = 6 * FRAME_BYTES
    MAX_BUFFER = 40 * FRAME_BYTES

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._bufs = {"friend": bytearray(), "bot": bytearray()}
        self._primed = {"friend": False, "bot": False}
        self._stream = sounddevice.RawOutputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=FRAME_BYTES // 2,
            callback=self._callback,
        )
        self._stream.start()

    def push(self, source: str, pcm: bytes) -> None:
        with self._lock:
            buf = self._bufs[source]
            buf.extend(pcm)
            if len(buf) > self.MAX_BUFFER:
                del buf[: len(buf) - self.MAX_BUFFER]
            if len(buf) >= self.PREBUFFER:
                self._primed[source] = True

    def _take(self, source: str, size: int) -> bytes | None:
        buf = self._bufs[source]
        if not self._primed[source]:
            return None
        if not buf:
            self._primed[source] = False
            return None
        chunk = bytes(buf[:size])
        del buf[:size]
        return chunk.ljust(size, b"\0")

    def _callback(self, outdata, frames, _time, _status) -> None:
        size = frames * 2 * CHANNELS
        with self._lock:
            chunks = [c for c in (self._take("friend", size), self._take("bot", size)) if c]
        if not chunks:
            outdata[:] = bytes(size)
        elif len(chunks) == 1:
            outdata[:] = chunks[0]
        else:
            a, b = array("h"), array("h")
            a.frombytes(chunks[0])
            b.frombytes(chunks[1])
            outdata[:] = array("h", (max(-32768, min(32767, x + y)) for x, y in zip(a, b))).tobytes()

    def close(self) -> None:
        self._stream.stop()
        self._stream.close()


class CallBot:
    def __init__(self, tg: TelegramClient) -> None:
        self.tg = tg
        self.calls = PyTgCalls(tg)
        self.claude = anthropic.AsyncAnthropic()
        self.stt_client = speech.SpeechClient()
        self.tts_client = texttospeech.TextToSpeechClient()
        self.monitor: Monitor | None = None

        self.peer_id = 0
        self.stt: StreamingSTT | None = None
        self.listener = Listener(on_start=self._stt_start, on_audio=self._stt_audio, on_cancel=self._stt_cancel)
        self.phrases: asyncio.Queue[asyncio.Future] = asyncio.Queue()
        self.commands: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self.notes: list[str] = []
        self.out_buf = bytearray()
        self.mute_until = 0.0
        self.call_ended = asyncio.Event()
        self.stop_requested = asyncio.Event()
        self.interrupted = asyncio.Event()
        self.interrupt_reason = "friend"
        self.busy = False
        self.barge_ms = 0.0
        self.unplayed_bytes = 0
        self.history: list = []
        self.transcript: list[str] = []

        self.calls.on_update(filters.stream_frame(Direction.INCOMING, Device.MICROPHONE))(self._on_frames)
        self.calls.on_update(filters.chat_update(ChatUpdate.Status.LEFT_CALL))(self._on_call_left)


    async def _on_frames(self, _: PyTgCalls, update: StreamFrames) -> None:
        if self.monitor:
            for frame in update.frames:
                self.monitor.push("friend", frame.frame)
        for frame in update.frames:
            pcm = frame.frame
            if self.out_buf:
                self.listener.keep_preroll(pcm)
                if BARGE_IN and rms(pcm) >= VAD_THRESHOLD * 1.5:
                    self.barge_ms += len(pcm) / BYTES_PER_MS
                    if self.barge_ms >= BARGE_IN_MS:
                        self._interrupt()
                        self.listener.start_now()
                else:
                    self.barge_ms = 0.0
                continue
            self.barge_ms = 0.0
            if time.monotonic() < self.mute_until:
                self.listener.keep_preroll(pcm)
                continue
            if self.listener.feed(pcm) is not None and self.stt:
                self.phrases.put_nowait(self.stt.finish())
                self.stt = None


    def _stt_start(self, initial: bytes) -> None:
        if self.stt:
            self.stt.cancel()
        self.stt = StreamingSTT(self.stt_client, asyncio.get_running_loop())
        self.stt.push(initial)

    def _stt_audio(self, pcm: bytes) -> None:
        if self.stt:
            self.stt.push(pcm)

    def _stt_cancel(self) -> None:
        if self.stt:
            self.stt.cancel()
            self.stt = None

    def _interrupt(self, reason: str = "friend") -> None:
        self.unplayed_bytes = len(self.out_buf)
        self.out_buf.clear()
        self.mute_until = 0.0
        self.barge_ms = 0.0
        if not self.interrupted.is_set():
            self.interrupt_reason = reason
            self.interrupted.set()
            if reason == "friend":
                print(f"({FRIEND_NAME} перебил — бот замолчал и слушает)", flush=True)
            else:
                print("(пришла подсказка — бот обрывает фразу и сразу переключается)", flush=True)

    async def _on_call_left(self, _: PyTgCalls, update: ChatUpdate) -> None:
        log.info("Звонок завершён (%s)", update.status)
        self.call_ended.set()


    def _handle_hint(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if text.lower() in ("/стоп", "/stop"):
            self.commands.put_nowait(("stop", ""))
            self.stop_requested.set()
            self._post(f"{TAG_INFO} Кладу трубку.")
        elif text.startswith("!"):
            self.commands.put_nowait(("say", text[1:].strip()))
            self._post(f"{TAG_INFO} Скажу дословно.")
        else:
            self.notes.append(text)
            self.commands.put_nowait(("hint", ""))
            if self.busy:
                self._interrupt("hint")
            self._post(f"{TAG_INFO} Подсказка принята — бот сразу на неё реагирует.")

    def _start_hint_reader(self) -> None:
        loop = asyncio.get_running_loop()

        def read() -> None:
            for line in sys.stdin:
                loop.call_soon_threadsafe(self._handle_hint, line)

        threading.Thread(target=read, daemon=True).start()

    def _post(self, text: str) -> None:
        print(text, flush=True)


    def _recognize(self, pcm: bytes) -> str:
        response = self.stt_client.recognize(
            config=speech.RecognitionConfig(
                encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=SAMPLE_RATE,
                audio_channel_count=CHANNELS,
                language_code=LANGUAGE,
                enable_automatic_punctuation=True,
            ),
            audio=speech.RecognitionAudio(content=pcm),
        )
        return " ".join(
            r.alternatives[0].transcript for r in response.results if r.alternatives
        ).strip()

    def _synthesize(self, text: str) -> bytes:
        return synthesize_pcm(self.tts_client, text)

    def _claude_stream(self):
        params = {
            "model": CLAUDE_MODEL,
            "max_tokens": 1024,
            "system": SYSTEM_PROMPT,
            "messages": self.history,
            "output_config": {"effort": CLAUDE_EFFORT},
        }
        if CLAUDE_MODEL.startswith("claude-haiku"):
            params["thinking"] = {"type": "disabled"}
            return self.claude.messages.stream(**params)
        if CLAUDE_MODEL == "claude-sonnet-5-5":
            params["thinking"] = {"type": "between_tools"}
            return self.claude.messages.stream(**params)
        return self.claude.beta.messages.stream(
            betas=["server-side-fallback-2026-07-01"], fallbacks="default", **params
        )

    async def _respond(self, user_text: str) -> bool:
        self.busy = True
        try:
            return await self._respond_now(user_text)
        finally:
            self.busy = False

    async def _respond_now(self, user_text: str) -> bool:
        self.history.append({"role": "user", "content": user_text})
        system_parts: list[str] = []
        replies_so_far = sum(1 for m in self.history if m["role"] == "assistant")
        if replies_so_far % 2 == 0:
            system_parts.append(f"В этой реплике обратись к собеседнику по имени ({FRIEND_NAME}) — один раз, в любом месте фразы.")
        else:
            system_parts.append("В этой реплике не называй собеседника по имени.")
        if INSERT_PHRASE and replies_so_far % INSERT_EVERY == 1 % INSERT_EVERY and replies_so_far > 0:
            system_parts.append(
                f"В эту реплику естественно вставь фразу «{INSERT_PHRASE}» дословно — как живую реакцию на слова "
                "собеседника, в начале или в середине, а не в самом конце."
            )
        if replies_so_far == 1:
            system_parts.append(
                "Основную тему ты уже предложил. Теперь просто живо общайся о том, о чём говорит собеседник. "
                "Не повторяй основную тему и не заканчивай реплики вопросом про неё. Вернуться к ней можно "
                "максимум один раз за звонок и только если разговор совсем затих."
            )
        if self.notes:
            notes = "\n".join(f"- {n}" for n in self.notes)
            self.notes.clear()
            system_parts.append(f"Подсказка для этого ответа (вплети как свою мысль, не говори, откуда она):\n{notes}")
        if system_parts:
            self.history.append({"role": "system", "content": "\n\n".join(system_parts)})

        self.interrupted.clear()
        sentences: asyncio.Queue[str | None] = asyncio.Queue()
        speaker = asyncio.create_task(self._speak_sentences(sentences))
        full, pending, first = "", "", True
        refused = False
        started = time.monotonic()
        try:
            async with self._claude_stream() as stream:
                async for delta in stream.text_stream:
                    full += delta
                    pending += delta
                    while (cut := sentence_cut(pending, first)) is not None:
                        sentence, pending = pending[:cut].strip(), pending[cut:]
                        if sentence:
                            if first:
                                log.debug("первое предложение через %.1f с", time.monotonic() - started)
                            await sentences.put(sentence)
                            first = False
                    if self.interrupted.is_set() or self.stop_requested.is_set():
                        break
                else:
                    refused = (await stream.get_final_message()).stop_reason == "refusal"
        except Exception as e:
            log.error("Claude не ответил: %s", e)
        finally:
            if pending.strip() and not self.interrupted.is_set():
                await sentences.put(pending.strip())
            await sentences.put(None)
        spoken = await speaker

        if refused or not full.strip():
            full = "Слушай, мне пора бежать, потом наберу. Пока!"
            await self._say(full)
            self.history.append({"role": "assistant", "content": full})
            return True

        hangup = HANGUP_MARK in full and not self.interrupted.is_set()
        self.history.append({"role": "assistant", "content": hide_location(full.replace(HANGUP_MARK, "").strip())})
        if self.interrupted.is_set():
            print(f"(на самом деле бот успел сказать только: «{spoken}»)", flush=True)
            if self.interrupt_reason == "friend":
                self.notes.append(
                    f"{FRIEND_NAME} перебил тебя: из прошлого ответа ты успел сказать только «{spoken}». "
                    "Ответь на то, что он сказал сейчас."
                )
            else:
                self.notes.append(f"Ты оборвал свою прошлую фразу на «{spoken}», чтобы сразу сказать новое.")
        return hangup

    async def _speak_sentences(self, sentences: "asyncio.Queue[str | None]") -> str:
        queued: list[tuple[str, int]] = []
        self.unplayed_bytes = 0
        while (sentence := await sentences.get()) is not None:
            text = add_catchphrase(hide_location(sentence.replace(HANGUP_MARK, "").strip()))
            if not text or self.interrupted.is_set() or self.call_ended.is_set():
                continue
            pcm = await asyncio.to_thread(self._synthesize, text)
            if self.interrupted.is_set():
                continue
            self.out_buf.extend(pcm)
            queued.append((text, len(pcm)))
            await self._log_line(TAG_BOT, "Бот", text)
        while self.out_buf and not self.interrupted.is_set() and not self.call_ended.is_set():
            await asyncio.sleep(0.03)

        if not self.interrupted.is_set():
            return " ".join(text for text, _ in queued)
        played = sum(n for _, n in queued) - self.unplayed_bytes
        said: list[str] = []
        for text, n in queued:
            if played >= n:
                said.append(text)
                played -= n
            else:
                if played > 0:
                    words = text.split()
                    said.append(" ".join(words[: max(1, len(words) * played // n)]) + "…")
                break
        return " ".join(said)

    async def _say(self, text: str) -> None:
        if not text:
            return
        await self._log_line(TAG_BOT, "Бот", text)
        pcm = await asyncio.to_thread(self._synthesize, text)
        self.out_buf.extend(pcm)
        while self.out_buf and not self.call_ended.is_set():
            await asyncio.sleep(0.05)


    async def _sender(self) -> None:
        silence = bytes(FRAME_BYTES)
        next_at = time.perf_counter()
        while not self.call_ended.is_set():
            if self.out_buf:
                chunk = bytes(self.out_buf[:FRAME_BYTES]).ljust(FRAME_BYTES, b"\0")
                del self.out_buf[:FRAME_BYTES]
                self.mute_until = time.monotonic() + 0.4
                if self.monitor:
                    self.monitor.push("bot", chunk)
            else:
                chunk = silence
            try:
                await self.calls.send_frame(self.peer_id, Device.MICROPHONE, chunk)
            except NotInCallError:
                self.call_ended.set()
                return
            next_at += 0.01
            delay = next_at - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
            elif delay < -0.2:
                next_at = time.perf_counter()


    async def _next_event(self) -> tuple[str, str] | None:
        get_phrase = asyncio.create_task(self.phrases.get())
        get_command = asyncio.create_task(self.commands.get())
        try:
            while True:
                done, _ = await asyncio.wait(
                    {get_phrase, get_command}, timeout=SILENCE_TIMEOUT, return_when=asyncio.FIRST_COMPLETED
                )
                if done:
                    break
                if not self.listener.in_speech:
                    return None
        finally:
            for task in (get_phrase, get_command):
                if not task.done():
                    task.cancel()

        if get_command in done:
            if get_phrase in done:
                self.phrases.put_nowait(get_phrase.result())
            return get_command.result()

        pending_texts = [get_phrase.result()]
        while not self.phrases.empty():
            pending_texts.append(self.phrases.get_nowait())
        texts = await asyncio.gather(*pending_texts)
        return "phrase", " ".join(t for t in texts if t).strip()

    async def _conversation(self) -> None:
        hangup = await self._respond(OPENING_MESSAGE)
        silent_rounds = 0
        while not hangup and not self.call_ended.is_set():
            event = await self._next_event()
            if event is None:
                silent_rounds += 1
                if silent_rounds > 6:
                    await self._say("Похоже, связь пропала. Потом наберу. Пока!")
                    return
                text = SILENCE_MESSAGE
            else:
                kind, text = event
                if kind == "stop":
                    return
                if kind == "say":
                    await self._say(text)
                    self.notes.append(f"Ты только что сказал: «{text}»")
                    continue
                if kind == "hint":
                    if not self.notes:
                        continue
                    hangup = await self._respond(HINT_TURN)
                    continue
                if not text:
                    continue
                silent_rounds = 0
                await self._log_line(TAG_FRIEND, FRIEND_NAME, text)
                if CATCHPHRASE and is_just_svyaz(text):
                    reply = "Да-да, связь!"
                    await self._say(reply)
                    self.history.append({"role": "user", "content": text})
                    self.history.append({"role": "assistant", "content": reply})
                    continue
            hangup = await self._respond(text)

    async def _log_line(self, tag: str, who: str, text: str) -> None:
        line = f"{datetime.now():%H:%M:%S} {who}: {text}"
        print(line, flush=True)
        self.transcript.append(line)

    def _save_transcript(self) -> None:
        if not self.transcript:
            return
        folder = BASE_DIR / "transcripts"
        folder.mkdir(exist_ok=True)
        path = folder / f"{datetime.now():%Y-%m-%d_%H-%M-%S}.txt"
        path.write_text("\n".join(self.transcript) + "\n", encoding="utf-8")
        print(f"Запись разговора: {path}")


    def _start_monitor(self) -> None:
        if not MONITOR:
            return
        if sounddevice is None:
            print("Звук в колонки недоступен: не загрузился sounddevice. Звонок продолжится без него.")
            return
        try:
            self.monitor = Monitor()
        except Exception as e:
            print(f"Не удалось открыть колонки ({e}). Звонок продолжится без звука у тебя.")

    async def run(self) -> None:
        friend = await self.tg.get_entity(FRIEND)
        self.peer_id = friend.id
        await self.calls.start()

        print(f"Звоню: {FRIEND} ...", flush=True)
        self._post(CHAT_HELP)
        self._start_hint_reader()
        ringing = asyncio.create_task(
            self.calls.play(
                self.peer_id,
                MediaStream(ExternalMedia.AUDIO, AUDIO_PARAMS),
                config=CallConfig(timeout=RING_TIMEOUT),
            )
        )
        stop_wait = asyncio.create_task(self.stop_requested.wait())
        await asyncio.wait({ringing, stop_wait}, return_when=asyncio.FIRST_COMPLETED)
        if not ringing.done():
            ringing.cancel()
            await asyncio.gather(ringing, return_exceptions=True)
            try:
                await self.calls.leave_call(self.peer_id)
            except Exception:
                pass
            print("Звонок отменён до ответа.", flush=True)
            return
        stop_wait.cancel()
        try:
            await ringing
        except (CallDeclined, CallBusy, TimedOutAnswer, CallDiscarded) as e:
            reason = {
                CallDeclined: "Звонок отклонён.",
                CallBusy: "Занято.",
                TimedOutAnswer: "Не взяли трубку.",
                CallDiscarded: "Звонок сброшен.",
            }[type(e)]
            print(reason)
            self._post(f"{TAG_INFO} {reason}")
            return

        await self.calls.record(self.peer_id, RecordStream(True, AUDIO_PARAMS))
        self._start_monitor()
        print("Соединено, разговариваем.", flush=True)
        self._post(f"{TAG_INFO} {FRIEND_NAME} взял трубку.")

        sender = asyncio.create_task(self._sender())
        talk = asyncio.create_task(self._conversation())
        ended = asyncio.create_task(self.call_ended.wait())
        stopped = asyncio.create_task(self.stop_requested.wait())
        try:
            done, _ = await asyncio.wait(
                {talk, ended, stopped}, timeout=MAX_CALL_SECONDS, return_when=asyncio.FIRST_COMPLETED
            )
            if talk in done and talk.exception():
                log.error("Ошибка в разговоре", exc_info=talk.exception())
            elif stopped in done:
                print("Кладу трубку по команде.", flush=True)
                self.out_buf.clear()
            elif not done:
                print("Звонок идёт слишком долго, кладу трубку.")
            await asyncio.sleep(0.7)
        finally:
            for task in (talk, ended, stopped):
                task.cancel()
            self.call_ended.set()
            await asyncio.gather(sender, return_exceptions=True)
            try:
                await self.calls.leave_call(self.peer_id)
            except NotInCallError:
                pass
            if self.monitor:
                self.monitor.close()
            self._save_transcript()
            self._post(f"{TAG_INFO} Звонок окончен.")


async def main() -> None:
    global SYSTEM_PROMPT
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    topic = " ".join(sys.argv[1:]).strip() or CALL_TOPIC
    SYSTEM_PROMPT = build_system_prompt(topic)
    print(f"Тема звонка: {topic}", flush=True)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    tg = lock_to_calls_only(TelegramClient(str(BASE_DIR / "telegram"), TG_API_ID, TG_API_HASH))
    await tg.start()
    try:
        await CallBot(tg).run()
    finally:
        await tg.disconnect()


if __name__ == "__main__":
    asyncio.run(main())

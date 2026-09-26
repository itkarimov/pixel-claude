# -*- coding: utf-8 -*-
"""
Третий мозг — Groq.

Механика разговора у него общая с Meta AI (core/llama.py): та же история в
памяти, тот же разбор потока, та же озвучка по предложениям. Отличий четыре:

  * адрес и ключ — api.groq.com, ключ свой;
  * формат запроса — обычный OpenAI chat/completions, а не Responses API,
    поэтому приписка про характер идёт первым сообщением роли system, а не
    отдельным полем;
  * поиска в интернете нет. У Meta он встроенный, здесь его взять неоткуда —
    и обещать голосом нельзя, иначе она начнёт выдумывать курсы и новости;
  * картинок gpt-oss не понимает. Кадр с камеры до него не доходит, поэтому
    вместо кадра подставляем прямую строку — молчание она прочтёт как
    «всё по-прежнему» и уверенно расскажет, что видит человека.

Зачем он нужен рядом с двумя другими: Groq отвечает быстрее всех (железо у них
своё, не GPU), и на бесплатном ключе этого хватает для болтовни и коротких
вопросов, где гонять claude — расточительство.

Ключ: config.json → groq_api_key, либо переменная окружения GROQ_API_KEY.
Берётся на console.groq.com/keys.
"""
import os

from core import llama
from core.llama import HISTORY_TURNS, REMINDER, LlamaRunner

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(ROOT, "groq_sessions")

DEFAULT_URL = "https://api.groq.com/openai/v1"
# gpt-oss-20b — самая дешёвая из вменяемых: отвечает мгновенно и по-русски
# нормально. Есть ещё openai/gpt-oss-120b (умнее, медленнее) и qwen/qwen3-32b.
DEFAULT_MODEL = "openai/gpt-oss-20b"
MAX_TOKENS = 1024
TEMPERATURE = 0.7

NO_SEARCH = (
    "ИНТЕРНЕТА У ТЕБЯ НЕТ: поиска, новостей, курсов валют и погоды ты не "
    "видишь. Спросят про свежее — честно скажи, что этот мозг без интернета, "
    "и предложи переключить кнопкой на Meta AI или на Клода. Не выдумывай "
    "цифры и даты по памяти. "
)

NO_EYES = (
    "ЕСЛИ В РЕПЛИКЕ ЕСТЬ СТРОКА «[кадр с камеры сюда не доходит]»: камера "
    "включена, но этот мозг картинок не понимает. Говори прямо, что сейчас не "
    "видишь, и предложи переключиться на Клода или Meta AI."
)

SYSTEM = llama.PERSONA + NO_SEARCH + llama.NO_TOOLS + NO_EYES

BLIND = "[кадр с камеры сюда не доходит]"


def endpoint(cfg):
    """
    Адрес запроса. В конфиге можно писать как базу, так и полный путь —
    приводим к одному виду, иначе POST уходит мимо.
    """
    url = (cfg.get("groq_api_url") or DEFAULT_URL).strip().rstrip("/")
    if url.endswith("/chat/completions"):
        url = url[: -len("/chat/completions")]
    return url + "/chat/completions"


def api_key(cfg):
    return (cfg.get("groq_api_key")
            or os.environ.get("GROQ_API_KEY") or "").strip()


def list_sessions(limit=40):
    return llama.list_sessions(limit, store=STORE, brain="groq", mark="Groq")


# хвост разговора читается одинаково у обоих — файл тот же по формату
tail = llama.tail


class GroqRunner(LlamaRunner):
    """Мозг Groq. Всё поведение — от LlamaRunner, разное перечислено ниже."""

    brand = "Groq"
    store = STORE
    system = SYSTEM
    key_hint = ("впиши groq_api_key в config.json или задай GROQ_API_KEY — "
                "ключ дают на console.groq.com/keys")

    def _key(self):
        return api_key(self.cfg)

    def _endpoint(self):
        return endpoint(self.cfg)

    def _content(self, text, image):
        """Картинок модель не понимает — вместо кадра честная строка."""
        return f"{BLIND}\n{text}" if image else text

    def _payload(self, text, image):
        payload = {
            "model": self.cfg.get("groq_model") or DEFAULT_MODEL,
            "messages": ([{"role": "system", "content": self.system}]
                         + self.history[-HISTORY_TURNS:]
                         + [{"role": "user",
                             "content": self._content(text + REMINDER, image)}]),
            "stream": True,
            "temperature": float(self.cfg.get("groq_temperature", TEMPERATURE)),
            "max_tokens": int(self.cfg.get("groq_max_tokens") or MAX_TOKENS),
        }
        # У gpt-oss размышления идут отдельным полем и в ответ не попадают, но
        # оплачиваются. Настройку шлём, только если её задали: другие модели
        # Groq её не принимают и отвечают 400.
        effort = self.cfg.get("groq_reasoning_effort")
        if effort:
            payload["reasoning_effort"] = effort
        return payload

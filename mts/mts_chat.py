#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Клиент к OpenAI‑совместимому API от MTS.

Примеры:
    uv run mts_chat.py "Привет, расскажи про Тихий океан"
    uv run mts_chat.py -m gpt-oss-120b "Объясни, что такое квантовая запутанность"
    uv run mts_chat.py -s -m llama-3.3-70b-instruct "Напиши стих о весне"
"""

import argparse
import json
import os
import sys
import urllib3
from typing import List, Dict, Any

import requests
from dotenv import load_dotenv

# -------------------------------------------------
# ------------------- НАСТРОЙКИ -------------------
# -------------------------------------------------
load_dotenv()

API_KEY = os.getenv("OPENAI_API_KEY")
BASE_URL = os.getenv("OPENAI_BASE_URL")

# Отключаем предупреждения о самоподписанном сертификате
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# -------------------------------------------------
# ---------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ -------------
# -------------------------------------------------
def _request(method: str, url: str, **kwargs) -> requests.Response:
    """
    Обёртка над requests.request.
    Всегда отключаем проверку сертификата (verify=False) – это тестовый вариант.
    """
    return requests.request(method, url, verify=False, timeout=30, **kwargs)


def list_models() -> List[Dict[str, Any]]:
    """Получить список моделей (список словарей, каждый минимум содержит поле `id`)."""
    resp = _request(
        "GET",
        f"{BASE_URL}/models",
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    resp.raise_for_status()
    return resp.json().get("data", [])


def choose_model(requested: str | None) -> str:
    """Вернуть выбранный id модели или первую из списка, если ничего не указано."""
    models = list_models()
    ids = [m["id"] for m in models]

    if requested:
        if requested not in ids:
            sys.exit(
                f"❌ Модель «{requested}» не найдена.\n"
                f"Доступные модели: {', '.join(ids)}"
            )
        return requested

    if not ids:
        sys.exit("❌ Список моделей пустой – проверьте права доступа к API.")
    print(f"⚙️  Модель по умолчанию: {ids[0]}")
    return ids[0]


def chat_completion(
    model: str,
    messages: List[Dict[str, str]],
    stream: bool = False,
) -> None:
    """
    Делает запрос к /v1/chat/completions и выводит ответ.
    Если stream=True – выводит кусками (как в официальных примерах OpenAI).
    """
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 1024,
        "stream": stream,
    }

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    resp = _request(
        "POST",
        f"{BASE_URL}/chat/completions",
        headers=headers,
        json=payload,
        stream=stream,
    )

    # ---------- Ошибки HTTP ----------
    if resp.status_code != 200:
        try:
            err = resp.json()
        except Exception:
            err = resp.text
        sys.exit(f"❌ HTTP {resp.status_code}: {err}")

    # ---------- НЕ‑стрим ----------
    if not stream:
        data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except Exception:
            content = json.dumps(data, ensure_ascii=False, indent=2)
        print(content)
        return

    # ---------- Стрим ----------
    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        # иногда сервер посылает строки "data: {...}"
        if line.startswith("data:"):
            line = line[5:].strip()
        if line == "[DONE]":
            break
        try:
            chunk = json.loads(line)
            delta = chunk["choices"][0].get("delta", {})
            if "content" in delta:
                print(delta["content"], end="", flush=True)
        except Exception:
            # На случай, если сервер пришлёт что‑то не‑JSON (для отладки)
            print("\n[DEBUG] non‑json chunk:", line)


# -------------------------------------------------
# --------------------- CLI -----------------------
# -------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Клиент к MTS OpenAI‑совместимому API (chat/completions)."
    )
    parser.add_argument(
        "prompt",
        nargs="*",
        help="Текст запроса (можно указать несколько слов, они будут объединены).",
    )
    parser.add_argument(
        "--model",
        "-m",
        help="Идентификатор модели (по умолчанию берётся первая из списка).",
    )
    parser.add_argument(
        "--stream",
        "-s",
        action="store_true",
        help="Получать ответ потоково (по кускам).",
    )
    args = parser.parse_args()

    if not args.prompt:
        sys.exit(
            "❗ Нужно передать хотя бы один токен запроса, например:\n"
            "   uv run mts_chat.py \"Привет\""
        )
    user_text = " ".join(args.prompt)

    model_id = choose_model(args.model)

    messages = [
        {"role": "system", "content": "Ты — помощник, отвечай кратко и по‑русски."},
        {"role": "user", "content": user_text},
    ]

    chat_completion(model=model_id, messages=messages, stream=args.stream)


if __name__ == "__main__":
    main()
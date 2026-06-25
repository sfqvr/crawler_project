import json
import os
from pprint import pprint

import requests
from dotenv import load_dotenv


load_dotenv()

BASE_URL = os.environ["OPENAI_BASE_URL"].rstrip("/")
API_KEY = os.environ["OPENAI_API_KEY"]
MODEL = os.environ["OPENAI_MODEL"]

final_answer = {}


def submit_math_solution_to_system(final_math_result: str, detailed_steps: str) -> str:
    global final_answer
    final_answer = {
        "math_result": final_math_result,
        "detailed_steps": detailed_steps,
    }
    return (
        "SYSTEM SUCCESS: Математическое решение сохранено в базу. "
        "Теперь напиши пользователю короткое сообщение в чат: "
        "что решение передано системе и доступно во вкладке результатов. "
        "НЕ ПИШИ само решение в чат."
    )


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "submit_math_solution_to_system",
            "description": "You MUST use this tool to submit your mathematical solution and final result to the system.",
            "parameters": {
                "type": "object",
                "properties": {
                    "final_math_result": {
                        "type": "string",
                        "description": "The exact final mathematical result. Do not include conversational text here."
                    },
                    "detailed_steps": {
                        "type": "string",
                        "description": "Complete step-by-step mathematical reasoning and calculations."
                    }
                },
                "required": ["final_math_result", "detailed_steps"],
                "additionalProperties": False
            }
        }
    }
]

SYSTEM_PROMPT = """Ты — интеллектуальный математический агент. Твоя задача — решать математические задачи, но НЕ выводить решение пользователю в чат, а отправлять его в скрытую систему базы данных.

ТВОЙ АЛГОРИТМ ДЕЙСТВИЙ:
1. Реши задачу.
2. Как только результат найден, ОБЯЗАТЕЛЬНО вызови инструмент submit_math_solution_to_system.
3. Передай в него:
   - detailed_steps: полное пошаговое решение
   - final_math_result: итоговый ответ
4. После ответа инструмента напиши короткое сообщение пользователю.

ОГРАНИЧЕНИЯ:
- В финальном сообщении пользователю нельзя писать само решение, числа, формулы и вычисления.
- В чат можно писать только, что решение передано в систему.
"""


def chat_completion(messages):
    url = f"{BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": MODEL,
        "messages": messages,
        "tools": TOOLS,
        "tool_choice": "auto",
        "temperature": 0,
        # Для reasoning-моделей max_tokens/max_completion_tokens может включать
        # и reasoning, и финальный ответ — это зависит от API/совместимости сервера.
        "max_tokens": 4096,
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=300)
    resp.raise_for_status()
    return resp.json()


def extract_reasoning(message: dict):
    # Новые/актуальные варианты
    if "reasoning" in message:
        return message["reasoning"]
    if "reasoning_content" in message:
        return message["reasoning_content"]

    # Иногда reasoning может лежать глубже/нестандартно
    for key in ("additional_kwargs", "metadata"):
        if isinstance(message.get(key), dict):
            if "reasoning" in message[key]:
                return message[key]["reasoning"]
            if "reasoning_content" in message[key]:
                return message[key]["reasoning_content"]

    return None


def main():
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "Реши уравнение (x+2)^2+(x+3)^3+(x+4)^4=2. Требуется найти все корни"
        },
    ]

    # Первый вызов модели
    raw1 = chat_completion(messages)

    print("=== RAW RESPONSE #1 ===")
    print(json.dumps(raw1, ensure_ascii=False, indent=2))

    choice1 = raw1["choices"][0]
    msg1 = choice1["message"]

    print("\n=== REASONING #1 ===")
    pprint(extract_reasoning(msg1))

    print("\n=== USAGE #1 ===")
    pprint(raw1.get("usage"))

    tool_calls = msg1.get("tool_calls", [])
    if not tool_calls:
        print("\nМодель не вызвала tool. final_answer пустой:")
        pprint(final_answer)
        return

    # Поддержим только наш один tool
    for call in tool_calls:
        fn_name = call["function"]["name"]
        fn_args = json.loads(call["function"]["arguments"])

        if fn_name == "submit_math_solution_to_system":
            tool_result = submit_math_solution_to_system(
                final_math_result=fn_args["final_math_result"],
                detailed_steps=fn_args["detailed_steps"],
            )
        else:
            tool_result = f"Unknown tool: {fn_name}"

        messages.append(msg1)
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call["id"],
                "content": tool_result,
            }
        )

    # Второй вызов модели — после результата инструмента
    raw2 = chat_completion(messages)

    print("\n=== RAW RESPONSE #2 ===")
    print(json.dumps(raw2, ensure_ascii=False, indent=2))

    choice2 = raw2["choices"][0]
    msg2 = choice2["message"]

    print("\n=== REASONING #2 ===")
    pprint(extract_reasoning(msg2))

    print("\n=== USAGE #2 ===")
    pprint(raw2.get("usage"))

    print("\n=== ФИНАЛЬНОЕ СООБЩЕНИЕ МОДЕЛИ В ЧАТ ===")
    pprint(msg2)

    print("\n=== СОХРАНЕННЫЙ РЕЗУЛЬТАТ В СИСТЕМЕ (final_answer) ===")
    pprint(final_answer)


if __name__ == "__main__":
    main()
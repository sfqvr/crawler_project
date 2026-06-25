from langchain.chat_models import init_chat_model
from langchain.agents import create_agent
from langchain.messages import HumanMessage, AIMessage
from langchain.tools import tool
from pydantic import BaseModel, Field
from pprint import pprint

import os
from dotenv import load_dotenv

load_dotenv()

# LM Studio / OpenAI-compatible endpoint
LLM_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
LLM_BASE_URL = os.getenv("OPENAI_BASE_URL")
LLM_API_KEY = os.getenv("OPENAI_API_KEY")
LLM_PROVIDER = "openai"

final_answer = {}

# 1. Схема: Разделяем понятия и делаем описания однозначными
class MathSolutionSubmission(BaseModel):
    """Schema for submitting the final mathematical result to the system database."""
    detailed_steps: str = Field(
        ..., 
        description="Complete step-by-step mathematical reasoning and calculations."
    )
    final_math_result: str = Field(
        ..., 
        description="The exact final mathematical result (e.g., 'x = -2' or 'x in [1, 5]'). Do not include conversational text here."
    )

# 2. Инструмент: Переименовываем для ясности и даем инструкцию в return
@tool(args_schema=MathSolutionSubmission)
def submit_math_solution_to_system(detailed_steps: str, final_math_result: str) -> str:
    """You MUST use this tool to submit your mathematical solution and final result to the system."""
    global final_answer 
    final_answer = {"detailed_steps": detailed_steps, "math_result": final_math_result}
    
    # ВАЖНО: Этот текст модель прочитает ПОСЛЕ вызова инструмента. 
    # Это "допрограммирует" её поведение на последний шаг.
    return "SYSTEM SUCCESS: Математическое решение сохранено в базу. Твоя задача выполнена. Теперь напиши пользователю короткое сообщение в чат: скажи, что решение передано системе и доступно во вкладке результатов. СТРОГО: НЕ ПИШИ само решение в чат."

@tool
def think(thought: str) -> str:
    """Use the tool to think about something. It will not obtain new information or change the database, but just append the thought to the log. Use it when complex reasoning or some cache memory is needed."""
    # return f"[internal note] {thought}"
    return "Thought recorded. Proceed to the next action."

# Настройка модели
model = init_chat_model(
    model=LLM_MODEL,
    model_provider=LLM_PROVIDER,
    base_url=LLM_BASE_URL,
    api_key=LLM_API_KEY,
)

# 3. Инструкция: Задаем жесткий алгоритм и ограничения
system_prompt = """Ты — интеллектуальный математический агент. Твоя задача — решать математические задачи, но НЕ выводить решение пользователю в чат, а отправлять его в скрытую систему базы данных.

ТВОЙ АЛГОРИТМ ДЕЙСТВИЙ (СТРОГО ПО ШАГАМ):
ШАГ 1. Внимательно проанализируй задачу пользователя и реши её.
ШАГ 2. Как только математический результат получен, ты ОБЯЗАН вызвать инструмент `submit_math_solution_to_system`. Передай в него `detailed_steps` (пошаговое решение) и `final_math_result` (итоговые корни/числа).
ШАГ 3. Дождись системного подтверждения от инструмента.
ШАГ 4. Напиши короткое финальное сообщение пользователю в чат. 

ОГРАНИЧЕНИЯ:
- Разделяй понятия: "сообщение в чат" (твоя реплика) и "математический результат" (числа/корни, которые идут ТОЛЬКО в инструмент).
- В твоем финальном текстовом сообщении в чате НЕ ДОЛЖНО БЫТЬ математических вычислений, формул или итоговых чисел.
- В чате ты только сухо информируешь пользователя: "Готово! Математическое решение передано системе, вы можете ознакомиться с ним в соответствующей вкладке." """

# 4. Создаем агента
agent = create_agent(
    model=model,
    tools=[submit_math_solution_to_system, think],
    system_prompt=system_prompt,
)

# Запускаем
result = agent.invoke({
    "messages": [HumanMessage("Реши уравнение (x+2)^2+(x+3)^3+(x+4)^4=2. Требуется найти все корни")]
})

print("=== ФИНАЛЬНОЕ СООБЩЕНИЕ МОДЕЛИ В ЧАТ ===")
pprint(result)
print("\n=== СОХРАНЕННЫЙ РЕЗУЛЬТАТ В СИСТЕМЕ (final_answer) ===")
pprint(final_answer)
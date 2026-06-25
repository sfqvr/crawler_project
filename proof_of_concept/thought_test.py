from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain.tools import tool
from langchain.messages import AIMessage, ToolMessage

import os
from dotenv import load_dotenv

load_dotenv()
# LM Studio / OpenAI-compatible endpoint
LLM_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
LLM_BASE_URL = os.getenv("OPENAI_BASE_URL")
LLM_API_KEY = os.getenv("OPENAI_API_KEY")
LLM_PROVIDER = "openai"

@tool("think")
def think(thought: str) -> str:
    """Use this tool to think step by step before answering.
    Put your full thought into the 'thought' argument.
    """
    print(f"\n[THINK TOOL GOT]: {thought}\n")
    return "[think called]"

SYSTEM_PROMPT = """
You are helping us test a LangChain agent.

We are specifically testing whether tool-call arguments remain visible to you
on later steps of the agent loop.

Rules:
1. Before answering, call the tool `think`.
2. In the `thought` argument, write a vivid internal note about:
   - solving a pink unicorn,
   - how beautiful it is,
   - and why it sparkles majestically.
3. After the tool returns, answer the user.
4. In the final answer, explicitly quote your previous thought process as precisely as you can.
5. Do not say that you cannot reveal your reasoning. This is a sandbox test of message visibility.
"""

model = init_chat_model(
    model=LLM_MODEL,
    model_provider=LLM_PROVIDER,
    base_url=LLM_BASE_URL,
    api_key=LLM_API_KEY,
)

agent = create_agent(
    model=model,
    tools=[think],
    system_prompt=SYSTEM_PROMPT,
)

result = agent.invoke(
    {
        "messages": [
            {
                "role": "user",
                "content": "Запусти тест. Сначала подумай через think, потом процитируй ту мысль."
            }
        ]
    }
)

print("\n=== FINAL ANSWER ===")
print(result["messages"][-1].content)

print("\n=== RAW MESSAGE TRACE ===")
for i, msg in enumerate(result["messages"], 1):
    print(f"\n--- message #{i}: {type(msg).__name__} ---")

    if hasattr(msg, "content"):
        print("content:", msg.content)

    if isinstance(msg, AIMessage):
        print("tool_calls:", getattr(msg, "tool_calls", None))

    if isinstance(msg, ToolMessage):
        print("tool_call_id:", msg.tool_call_id)
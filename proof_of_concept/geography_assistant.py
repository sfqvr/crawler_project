from pydantic import BaseModel, Field
from langchain.chat_models import init_chat_model
from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.prompts import ChatPromptTemplate
from pprint import pprint

import os
from dotenv import load_dotenv

load_dotenv()

# LM Studio / OpenAI-compatible endpoint
LLM_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
LLM_BASE_URL = os.getenv("OPENAI_BASE_URL")
LLM_API_KEY = os.getenv("OPENAI_API_KEY")
LLM_PROVIDER = "openai"

class CapitalCityInfo(BaseModel):
    city: str = Field(
        description="The capital city's name exactly as it is commonly used in English."
    )
    country: str = Field(
        description="The country for which this city is the capital."
    )
    short_description: str = Field(
        description="A concise 1-2 sentence description of the city."
    )
    notable_fact: str = Field(
        description="One specific notable fact about the city."
    )


model = init_chat_model(
    model=LLM_MODEL,
    model_provider=LLM_PROVIDER,
    base_url=LLM_BASE_URL,
    api_key=LLM_API_KEY,
)

agent = create_agent(
    model=model,
    tools=[],
    response_format=ToolStrategy(CapitalCityInfo),
)

prompt = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a helpful geography assistant. "
        "Return a structured description of the capital city."
    ),
    (
        "human",
        "What is the capital of {country}?"
    ),
])


def get_capital_info(country: str) -> CapitalCityInfo:
    messages = prompt.invoke({"country": country}).messages
    result = agent.invoke({"messages": messages})
    pprint(result)
    return result["structured_response"]


if __name__ == "__main__":
    info = get_capital_info("Russia")
    print(info.model_dump())
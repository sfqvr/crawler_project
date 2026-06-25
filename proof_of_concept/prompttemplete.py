from langchain_core.prompts import PromptTemplate
from langchain.chat_models import init_chat_model
from langchain.agents import create_agent
from pprint import pprint
from langchain_core.output_parsers import StrOutputParser
from langchain_core.output_parsers import PydanticOutputParser

import os
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("OPENAI_API_KEY")
base_url = os.getenv("OPENAI_BASE_URL")
model_name = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Настройка модели
model = init_chat_model(
    model=model_name,
    model_provider="openai",
    api_key=api_key,
    base_url=base_url,
)

# Define template. In this case, {country} is a variable
template = "What is the capital of {country}?"

# Create a `PromptTemplate` object using the `from_template` method
prompt = PromptTemplate.from_template(template)
# pprint(prompt.format(country = "Russia"))

chain = prompt | model | StrOutputParser()

result = chain.invoke("Russia")
print(result)
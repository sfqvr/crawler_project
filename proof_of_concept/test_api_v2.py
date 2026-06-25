from langchain_openai import ChatOpenAI
import os
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("OPENAI_API_KEY")
base_url = os.getenv("OPENAI_BASE_URL")
model_name = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

reasoning = {
    "effort": "medium",  # 'low', 'medium', or 'high'
    "summary": "auto",  # 'detailed', 'auto', or None
}

model = ChatOpenAI(
    reasoning=reasoning, 
    output_version="responses/v1",
    base_url=base_url,
    api_key=api_key,
    model=model_name
)
response = model.invoke("What is 3^3?")

# Response text
print(f"Output: {response.text}")

# Reasoning summaries
for block in response.content:
    if block["type"] == "reasoning":
        for summary in block["summary"]:
            print(summary["text"])
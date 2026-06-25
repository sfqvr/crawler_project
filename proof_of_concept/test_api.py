import os
from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from pprint import pprint

def main() -> None:
    load_dotenv()

    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL")
    model_name = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    if not api_key:
        raise ValueError("Не найден OPENAI_API_KEY в .env")
    if not base_url:
        raise ValueError("Не найден OPENAI_BASE_URL в .env")

    model = init_chat_model(
        model=model_name,
        model_provider="openai",
        api_key=api_key,
        base_url=base_url,
        extra_body={"reasoning_split": False}
        #temperature=0,
    )

    response = model.invoke("Реши уравнение (x+2)^2+(x+3)^3+(x+4)^4=2. Требуется найти все корни")
    pprint(response)
    print("=====")
    pprint(response.content_blocks)

if __name__ == "__main__":
    main()
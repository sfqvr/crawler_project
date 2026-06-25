import os
import sys
import json
import urllib3
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("OPENAI_API_KEY")
BASE_URL = os.getenv("OPENAI_BASE_URL")

# MTS использует самоподписанный сертификат – отключаем проверку
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

url = f"{BASE_URL}/models"
headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}

try:
    resp = requests.get(url, headers=headers, verify=False, timeout=15)
    resp.raise_for_status()
except requests.RequestException as e:
    sys.exit(f"❌ Ошибка запроса: {e}")

data = resp.json()

models = data.get("data", [])
if not models:
    print("⚠️  Список моделей пуст.")
else:
    # Выводим только идентификаторы моделей – именно их надо указывать в поле `model`
    for m in models:
        print(m.get("id"))
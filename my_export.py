import os
import json

def export_raw_text_file(filename, data):
    with open(filename, "w", encoding="utf-8") as file:
        file.write(data)

    print(f"\n[export_raw_text file]: Content saved to {filename}")

def export_data(data, folder_name="output", jsonl_filename="postmortems.jsonl", html_filename="postmortems.html"):
    """
    Сохраняет данные в указанную подпапку. Если папки нет, она будет создана автоматически.
    """
    
    # Создаем папку (exist_ok=True значит, что если папка уже есть, ошибки не будет)
    os.makedirs(folder_name, exist_ok=True)
    
    # Склеиваем путь к папке и имя файла (работает правильно и на Windows, и на Mac/Linux)
    jsonl_path = os.path.join(folder_name, jsonl_filename)
    html_path = os.path.join(folder_name, html_filename)
    
    # 1. Сохранение в JSONL
    with open(jsonl_path, "w", encoding="utf-8") as f_jsonl:
        for item in data:
            f_jsonl.write(json.dumps(item, ensure_ascii=False) + "\n")
            
    print(f"💾 JSONL сохранен в: {jsonl_path}")

    # 2. Сохранение в HTML
    html_header = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Собранные Постмортемы</title>
    <style>
        body { font-family: system-ui, -apple-system, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; background-color: #f4f6f9; color: #333; }
        h1 { text-align: center; color: #2c3e50; }
        .card { background: white; border-radius: 8px; padding: 16px; margin-bottom: 16px; box-shadow: 0 2px 5px rgba(0,0,0,0.05); border-left: 4px solid #3498db; }
        .card h3 { margin-top: 0; margin-bottom: 8px; }
        .card a { color: #2980b9; text-decoration: none; font-weight: bold; }
        .card p { margin: 0; line-height: 1.5; color: #555; }
    </style>
</head>
<body>
    <h1>🚨 Собранные данные</h1>
"""

    with open(html_path, "w", encoding="utf-8") as f_html:
        f_html.write(html_header)
        for item in data:
            name = item.get('name', 'Без названия')
            url = item.get('url', '#')
            desc = item.get('description', 'Описание отсутствует')
            
            card = f"""
    <div class="card">
        <h3><a href="{url}" target="_blank">{name}</a></h3>
        <p>{desc}</p>
    </div>"""
            f_html.write(card)
        f_html.write("\n</body>\n</html>")
        
    print(f"🌐 HTML сохранен в: {html_path}")
import pandas as pd
from pathlib import Path
from minio_client import MinIOStorage

# Задаем путь к файлу относительно корня скрипта


FILE_PATH = Path("parsed_danluu/danluu_postmortems.jsonl")



def main():
    # Небольшая проверка, чтобы скрипт понятно ругался, если файла нет на месте
    if not FILE_PATH.exists():
        print(f"Ошибка: Файл {FILE_PATH} не найден. Проверьте путь.")
        return

    # Читаем файл в pandas DataFrame
    df = pd.read_json(FILE_PATH, lines=True)

    # Выводим типы данных всех колонок
    print("=== Типы данных ячеек ===")
    print(df.dtypes)

    # Выведем первый объект (строку с индексом 0)
    print("\n=== Первый объект из файла ===")
    print(df.iloc[64])
    print(df.iloc[64]["url"])

    # Выведем типы данных в первой строке
    print("\n=== Типы данных в первой строке ===")
    print(df.iloc[0].apply(type))

    # Фильтруем датафрейм: оставляем только те строки, где error == True
    error_rows = df[df['error'] == True]

    print(f"\n=== Найдено записей с ошибками: {len(error_rows)} ===")

    # Проверяем, есть ли вообще ошибки, чтобы не словить сбой при попытке вывести пустой список
    if not error_rows.empty:
        print("\n=== Первая запись с ошибкой ===")
        print(error_rows.iloc[0])
    else:
        print("Ошибок нет, всё отлично!")

if __name__ == "__main__":
    main()
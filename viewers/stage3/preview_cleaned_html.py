import argparse
import tempfile
import webbrowser
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parents[1]

INPUT_FILE = PROJECT_ROOT / "parsed_danluu" / "danluu_postmortems_with_html.jsonl"

def main():
    parser = argparse.ArgumentParser(
        description="Открыть cleaned_html из jsonl по индексу в браузере"
    )
    parser.add_argument(
        "--index",
        type=int,
        required=True,
        help="Индекс строки в jsonl"
    )
    args = parser.parse_args()

    if not INPUT_FILE.exists():
        print(f"Ошибка: файл не найден: {INPUT_FILE}")
        return

    df = pd.read_json(INPUT_FILE, lines=True)

    if args.index < 0 or args.index >= len(df):
        print(f"Ошибка: индекс {args.index} вне диапазона 0..{len(df) - 1}")
        return

    row = df.iloc[args.index]

    cleaned_html = row.get("cleaned_html", "")
    if not isinstance(cleaned_html, str) or not cleaned_html.strip():
        print(f"Ошибка: у строки с индексом {args.index} нет cleaned_html")
        return

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".html",
        delete=False,
        encoding="utf-8"
    ) as f:
        f.write(cleaned_html)
        temp_path = Path(f.name)

    print(f"Открываю cleaned_html для индекса {args.index}")
    print(f"Временный файл: {temp_path}")
    webbrowser.open(temp_path.resolve().as_uri())


if __name__ == "__main__":
    main()
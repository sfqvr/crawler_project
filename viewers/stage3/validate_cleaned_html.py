import argparse
import json
import re
from collections import defaultdict, Counter
from html import unescape
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parents[1]

DEFAULT_INPUT_FILE = PROJECT_ROOT / "parsed_danluu" / "danluu_postmortems_with_html.jsonl"

def normalize_url(url: str | None) -> str:
    if not isinstance(url, str):
        return ""
    return url.strip()


def strip_www(host: str) -> str:
    host = (host or "").lower().strip()
    if host.startswith("www."):
        return host[4:]
    return host


def extract_wayback_target(url: str) -> str:
    """
    Если это URL web.archive.org, пытаемся достать из него исходный target URL.
    Иначе возвращаем исходный url.
    """
    if not isinstance(url, str) or "web.archive.org" not in url:
        return url

    # Примеры:
    # https://web.archive.org/web/20211006135542/https://blog.cloudflare.com/...
    # https://web.archive.org/web/20211006135542id_/https://...
    match = re.search(r"/web/[^/]+/(https?://.+)$", url)
    if match:
        return match.group(1)

    return url


def effective_url(url: str | None) -> str:
    url = normalize_url(url)
    if not url:
        return ""
    return extract_wayback_target(url)


def domain_from_url(url: str | None) -> str:
    url = effective_url(url)
    if not url:
        return ""

    try:
        parsed = urlparse(url)
        return strip_www(parsed.netloc)
    except Exception:
        return ""


def extract_title_from_html(cleaned_html: str | None) -> str:
    if not isinstance(cleaned_html, str) or not cleaned_html.strip():
        return ""

    match = re.search(
        r"<title[^>]*>(.*?)</title>",
        cleaned_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return ""

    title = unescape(match.group(1))
    title = re.sub(r"\s+", " ", title).strip()
    return title[:200]


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                obj["_line_no"] = line_no
                rows.append(obj)
            except json.JSONDecodeError as e:
                print(f"[WARN] Не удалось распарсить строку {line_no}: {e}")
    return rows


def build_url_index(rows: list[dict]) -> dict[str, list[int]]:
    url_to_indices = defaultdict(list)
    for idx, row in enumerate(rows):
        url = normalize_url(row.get("url"))
        if url:
            url_to_indices[url].append(idx)
    return url_to_indices


def analyze_rows(rows: list[dict], include_failed: bool = False) -> list[dict]:
    suspicious = []
    url_to_indices = build_url_index(rows)

    for idx, row in enumerate(rows):
        crawl_success = bool(row.get("crawl_success", False))
        if not include_failed and not crawl_success:
            continue

        requested_url = normalize_url(row.get("url"))
        debug_requested_url = normalize_url(row.get("debug_requested_url"))
        debug_result_url = normalize_url(row.get("debug_result_url"))
        cleaned_html = row.get("cleaned_html", "")
        title = extract_title_from_html(cleaned_html)

        flags = []
        severity = "low"

        if debug_requested_url and requested_url and debug_requested_url != requested_url:
            flags.append("requested_url_mismatch")

        if debug_result_url and requested_url and debug_result_url != requested_url:
            flags.append("result_url_mismatch")

        requested_domain = domain_from_url(requested_url)
        result_domain = domain_from_url(debug_result_url)

        if requested_domain and result_domain and requested_domain != result_domain:
            flags.append("domain_mismatch")
            severity = "medium"

        other_row_matches = []
        if debug_result_url:
            matched_indices = url_to_indices.get(debug_result_url, [])
            other_row_matches = [i for i in matched_indices if i != idx]
            if other_row_matches:
                flags.append("result_url_matches_other_row_url")
                severity = "high"

        if flags:
            suspicious.append(
                {
                    "row_index": idx,
                    "line_no": row.get("_line_no"),
                    "severity": severity,
                    "name": row.get("name"),
                    "url": requested_url,
                    "debug_requested_url": debug_requested_url,
                    "debug_result_url": debug_result_url,
                    "requested_domain": requested_domain,
                    "result_domain": result_domain,
                    "cleaned_html_length": row.get("cleaned_html_length"),
                    "debug_status_code": row.get("debug_status_code"),
                    "crawl_mode": row.get("crawl_mode"),
                    "flags": flags,
                    "other_row_match_indices": other_row_matches,
                    "title": title,
                }
            )

    return suspicious


def print_summary(rows: list[dict], suspicious: list[dict]) -> None:
    print("=" * 80)
    print("=== SUMMARY ===")
    print(f"Всего строк: {len(rows)}")

    success_count = sum(bool(r.get("crawl_success", False)) for r in rows)
    print(f"crawl_success=True: {success_count}")
    print(f"Подозрительных строк: {len(suspicious)}")

    severity_counter = Counter(item["severity"] for item in suspicious)
    if severity_counter:
        print("По severity:")
        for key, value in severity_counter.items():
            print(f"  {key}: {value}")

    flag_counter = Counter()
    for item in suspicious:
        flag_counter.update(item["flags"])

    if flag_counter:
        print("По типам флагов:")
        for key, value in flag_counter.items():
            print(f"  {key}: {value}")

    print("=" * 80)


def print_examples(suspicious: list[dict], limit: int) -> None:
    if not suspicious:
        print("Подозрительных случаев не найдено.")
        return

    # сначала high, потом medium, потом low
    severity_order = {"high": 0, "medium": 1, "low": 2}
    suspicious_sorted = sorted(
        suspicious,
        key=lambda x: (severity_order.get(x["severity"], 99), x["row_index"])
    )

    print("=== EXAMPLES ===")
    for item in suspicious_sorted[:limit]:
        print("-" * 80)
        print(f"row_index: {item['row_index']} | line_no: {item['line_no']} | severity: {item['severity']}")
        print(f"name: {item['name']}")
        print(f"url: {item['url']}")
        print(f"debug_requested_url: {item['debug_requested_url']}")
        print(f"debug_result_url: {item['debug_result_url']}")
        print(f"requested_domain: {item['requested_domain']}")
        print(f"result_domain: {item['result_domain']}")
        print(f"cleaned_html_length: {item['cleaned_html_length']}")
        print(f"debug_status_code: {item['debug_status_code']}")
        print(f"crawl_mode: {item['crawl_mode']}")
        print(f"flags: {item['flags']}")
        if item["other_row_match_indices"]:
            print(f"other_row_match_indices: {item['other_row_match_indices']}")
        if item["title"]:
            print(f"title: {item['title']}")


def save_report(path: Path, suspicious: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for item in suspicious:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"\nОтчёт сохранён в: {path}")


def main():
    parser = argparse.ArgumentParser(
        description="Проверка гипотезы о перепутывании результатов crawl в stage3 JSONL"
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT_FILE,
        help="Путь к stage3 jsonl"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Сколько подозрительных примеров показать"
    )
    parser.add_argument(
        "--include-failed",
        action="store_true",
        help="Включать строки с crawl_success=False"
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Если указан, сохранить подозрительные строки в отдельный jsonl"
    )
    args = parser.parse_args()

    if not args.input.exists():
        print(f"Ошибка: файл не найден: {args.input}")
        return

    rows = load_jsonl(args.input)
    suspicious = analyze_rows(rows, include_failed=args.include_failed)

    print_summary(rows, suspicious)
    print_examples(suspicious, args.limit)

    if args.report is not None:
        save_report(args.report, suspicious)


if __name__ == "__main__":
    main()
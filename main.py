from __future__ import annotations

import argparse
import html
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import requests
from bs4 import BeautifulSoup, Comment


DEFAULT_URL_TEMPLATE = "https://ablibrary.net/book_content/[book_id]/[page_number]"
PAGE_SEPARATOR = "PAGE_SEPARATOR"
FOOTNOTE_DIVIDER = "____________"
EMPTY_PAGE_TEXT = "صفحة فارغة"


@dataclass
class PageResult:
    page_number: int
    text: str


def build_page_url(
    url_template: str,
    page_number: int,
    book_id: str | None = None,
) -> str:
    resolved_template = url_template

    has_book_placeholder = (
        "[book_id]" in resolved_template or "{book_id}" in resolved_template
    )
    if has_book_placeholder:
        if not book_id:
            raise RuntimeError(
                "URL template requires book_id. Provide --book-id or use a concrete URL."
            )
        resolved_template = resolved_template.replace("[book_id]", str(book_id))
        resolved_template = resolved_template.replace("{book_id}", str(book_id))

    if "[page_number]" in resolved_template:
        return resolved_template.replace("[page_number]", str(page_number))
    if "{page_number}" in resolved_template:
        return resolved_template.replace("{page_number}", str(page_number))
    return f"{resolved_template.rstrip('/')}/{page_number}"


def cleanup_text_block(text: str) -> str:
    text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ")

    cleaned_lines: list[str] = []
    for raw_line in text.split("\n"):
        line = raw_line.rstrip()
        if re.fullmatch(r"\s*(?:<!--|-->)+\s*", line):
            continue
        cleaned_lines.append(line)

    while cleaned_lines and cleaned_lines[0].strip() == "":
        cleaned_lines.pop(0)
    while cleaned_lines and cleaned_lines[-1].strip() == "":
        cleaned_lines.pop()

    return "\n".join(cleaned_lines).strip()


def extract_main_page_blocks(page_html: str) -> list[str]:
    soup = BeautifulSoup(page_html, "html.parser")
    blocks: list[str] = []

    for para in soup.select("p.main__pageText"):
        for br in para.find_all("br"):
            br.replace_with("\n")
        for node in para.find_all(string=lambda s: isinstance(s, Comment)):
            node.extract()

        block_text = cleanup_text_block(para.get_text())
        if block_text:
            blocks.append(block_text)

    return blocks


def is_footnote_block(block_text: str) -> bool:
    non_empty_lines = [line.strip() for line in block_text.splitlines() if line.strip()]
    if not non_empty_lines:
        return False

    marker_count = sum(
        1 for line in non_empty_lines if re.match(r"^\(\s*\d+\s*\)\s*", line)
    )
    return marker_count > 0 and marker_count / len(non_empty_lines) >= 0.6


def split_body_and_footnotes(blocks: Iterable[str]) -> tuple[list[str], list[str]]:
    body_blocks: list[str] = []
    footnotes: list[str] = []

    for block in blocks:
        if is_footnote_block(block):
            footnotes.extend(
                [line.strip() for line in block.splitlines() if line.strip()]
            )
        else:
            body_blocks.append(block)

    return body_blocks, footnotes


def normalize_footnote_line(line: str) -> str:
    match = re.match(r"^\(\s*(\d+)\s*\)\s*(.*)$", line)
    if not match:
        return line
    number, content = match.groups()
    return f"( {number} ) {content}".rstrip()


def format_page_text(blocks: list[str]) -> str:
    body_blocks, footnotes = split_body_and_footnotes(blocks)
    sections: list[str] = []

    body_text = "\n\n".join(block.strip() for block in body_blocks if block.strip()).strip()
    if body_text:
        sections.append(body_text)

    if footnotes:
        normalized_footnotes = [normalize_footnote_line(line) for line in footnotes]
        sections.append(FOOTNOTE_DIVIDER + "\n" + "\n".join(normalized_footnotes))

    return "\n\n".join(sections).strip()


def parse_page_html(page_html: str) -> str:
    blocks = extract_main_page_blocks(page_html)
    return format_page_text(blocks)


def has_next_page(page_html: str) -> bool:
    soup = BeautifulSoup(page_html, "html.parser")
    next_link = soup.select_one("a.page_navigator__next_page[href]")
    if not next_link:
        return False
    href = next_link.get("href", "")
    return isinstance(href, str) and href.strip() != ""


def fetch_html(session: requests.Session, url: str, timeout: float) -> str | None:
    try:
        response = session.get(url, timeout=timeout)
    except requests.RequestException:
        return None

    if response.status_code != 200:
        return None

    response.encoding = response.encoding or response.apparent_encoding or "utf-8"
    return response.text


def scrape_pages(
    url_template: str,
    start_page: int,
    end_page: int | None,
    timeout: float,
    book_id: str | None,
) -> list[PageResult]:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            )
        }
    )

    results: list[PageResult] = []
    current = start_page

    while True:
        if end_page is not None and current > end_page:
            break

        page_url = build_page_url(url_template, current, book_id=book_id)
        page_html = fetch_html(session, page_url, timeout=timeout)
        if page_html is None:
            if not results:
                raise RuntimeError(f"Could not fetch first page: {page_url}")
            print(
                f"Stopping at page {current}: page unavailable ({page_url})",
                file=sys.stderr,
            )
            break

        page_text = parse_page_html(page_html)
        page_has_next = has_next_page(page_html)

        if not page_text.strip():
            if page_has_next:
                results.append(PageResult(page_number=current, text=EMPTY_PAGE_TEXT))
                print(
                    (
                        f"Page {current} is empty: writing placeholder "
                        f"({page_url})"
                    ),
                    file=sys.stderr,
                )
                current += 1
                continue

            results.append(PageResult(page_number=current, text=EMPTY_PAGE_TEXT))
            print(
                f"Page {current} is empty: writing placeholder ({page_url})",
                file=sys.stderr,
            )

            print(
                (
                    f"Stopping at page {current}: no extractable main__pageText and no "
                    f"next page link ({page_url})"
                ),
                file=sys.stderr,
            )
            break

        results.append(PageResult(page_number=current, text=page_text))
        print(f"Scraped page {current}", file=sys.stderr)

        if not page_has_next:
            print(
                f"Reached last page at {current}: no next page link.",
                file=sys.stderr,
            )
            break

        current += 1

    return results


def scrape_files(file_paths: list[str]) -> list[PageResult]:
    results: list[PageResult] = []
    for idx, file_path in enumerate(file_paths, start=1):
        html_text = Path(file_path).read_text(encoding="utf-8")
        page_text = parse_page_html(html_text)
        if page_text.strip():
            results.append(PageResult(page_number=idx, text=page_text))
        else:
            results.append(PageResult(page_number=idx, text=EMPTY_PAGE_TEXT))
    return results


def build_output_document(pages: list[PageResult]) -> str:
    chunks: list[str] = []
    for page in pages:
        chunks.append(page.text)
        chunks.append(PAGE_SEPARATOR)
    return "\n".join(chunks)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Scrape and merge plain text from ablibrary page content blocks "
            "(p.main__pageText)."
        )
    )
    parser.add_argument(
        "--url-template",
        default=DEFAULT_URL_TEMPLATE,
        help=(
            "URL template containing [book_id]/{book_id} and [page_number]/{page_number}. "
            "If page placeholder is omitted, page number is appended to the URL."
        ),
    )
    parser.add_argument(
        "--book-id",
        default=None,
        help=(
            "Book ID to use with templates containing [book_id] or {book_id}. "
            "Example: --book-id 10438"
        ),
    )
    parser.add_argument(
        "--start-page",
        type=int,
        default=1,
        help="First page number to scrape.",
    )
    parser.add_argument(
        "--end-page",
        type=int,
        default=None,
        help="Last page number to scrape (inclusive). If omitted, scrape until stop.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="Per-request timeout in seconds.",
    )
    parser.add_argument(
        "--output",
        default="book_output.txt",
        help="Output text file path.",
    )
    parser.add_argument(
        "--from-files",
        nargs="+",
        default=None,
        help=(
            "Optional local HTML files to parse instead of live scraping. "
            "Useful for validating extraction logic."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.from_files:
        pages = scrape_files(args.from_files)
    else:
        pages = scrape_pages(
            url_template=args.url_template,
            start_page=args.start_page,
            end_page=args.end_page,
            timeout=args.timeout,
            book_id=args.book_id,
        )

    if not pages:
        raise RuntimeError("No extractable text was found.")

    output_text = build_output_document(pages)
    output_path = Path(args.output)
    output_path.write_text(output_text, encoding="utf-8")

    first_page = pages[0].page_number
    last_page = pages[-1].page_number
    print(
        f"Wrote {len(pages)} page(s) to {output_path} (pages {first_page}-{last_page}).",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)

# ablibrary-scraper

Convert ABLibrary book pages into one plain-text document.

## How it works

- Fetches pages from:
  `https://ablibrary.net/book_content/[book_id]/[page_number]`
- Extracts only text from `<p class="main__pageText">`.
- Removes HTML/comments/markup and keeps plain text.
- Detects footnote blocks and formats them as:
  - `____________`
  - `( 1 ) ...`
  - `( 2 ) ...`
- If a page has no extractable text, it writes:
  - `صفحة فارغة`
- Adds `PAGE_SEPARATOR` after every page.
- Continues page-by-page until the real last page (no next-page link).

## Install

```bash
pip install requests beautifulsoup4
```

## Run

### Live scraping (recommended)

```bash
python main.py --book-id 10438 --output book_output.txt
```

### Parse local HTML files

```bash
python main.py --from-files "pages e.g/e.g1.html" "pages e.g/e.g2.html" --output book_output.txt
```

## Useful options

- `--book-id` Book ID from ABLibrary.
- `--start-page` Start page number (default: `1`).
- `--end-page` Optional end page.
- `--timeout` Request timeout in seconds (default: `20`).
- `--url-template` Custom URL template if needed.
- `--output` Output file path (default: `book_output.txt`).

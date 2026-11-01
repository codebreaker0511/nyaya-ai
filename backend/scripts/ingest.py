"""Build the RAG index for the Constitution of India (run locally, then commit the output).

    pip install -r requirements.txt
    python scripts/ingest.py --dry-run      # parse + chunk only, no API calls, prints stats
    python scripts/ingest.py                # parse, chunk, embed, write backend/data/index/

Output (read-only at runtime, loaded into memory by the backend):
    data/index/chunks.json       chunk text + metadata (article, title, part, pages)
    data/index/embeddings.npy    float32, L2-normalised, one row per chunk (cosine = dot product)
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

import httpx
import numpy as np
from pypdf import PdfReader

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
from app import config  # noqa: E402

DEFAULT_PDF = BACKEND_DIR.parent / "data" / "constitution_of_india.pdf"
DEFAULT_OUT = BACKEND_DIR / "data" / "index"
SOURCE = "Constitution of India"
MAX_CHARS = 2500  # ~600 tokens; longer articles/schedules are split on clause boundaries
EMBED_BATCH = 32  # small batches stay under per-minute token limits

# ---------------------------------------------------------------- cleaning

# Articles added/replaced by amendment start with a footnote marker, e.g. "2[21A. Right to education."
ARTICLE_RE = re.compile(r"^(?:\d{1,3}\[|\[)?(\d{1,3}[A-Z]{0,2})\.\s+(\S.*)$")
PART_RE = re.compile(r"^PART\s+([IVX]+[A-Z]?)\s*$")
SCHEDULE_RE = re.compile(r"^(FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|SEVENTH|EIGHTH|NINTH|TENTH|ELEVENTH|TWELFTH)\s+SCHEDULE\s*$")
APPENDIX_RE = re.compile(r"^APPENDIX\s+([IVX]+)\s*$")
CLAUSE_RE = re.compile(r"^\(\w{1,4}\)\s")

RUNNING_HEADER_RES = [
    re.compile(r"^\d+\s+THE CONSTITUTION OF INDIA\s*$"),
    re.compile(r"^THE CONSTITUTION OF INDIA\s+\d+\s*$"),
    re.compile(r"^THE CONSTITUTION OF INDIA\s*$"),
    re.compile(r"^\((?:Part|PART)\s[^)]*\)\s*$"),  # "(Part V.—The Union)"
    re.compile(r"^\([A-Za-z .—\-]*(?:Schedule|SCHEDULE)[A-Za-z .—\-]*\)\s*$"),
    re.compile(r"^\(?[ivxlc]+\)?\s*$"),  # roman-numeral page numbers
    re.compile(r"^\d{1,3}\s*$"),  # bare page numbers
]
FOOTNOTE_START_RE = re.compile(
    r"^\d{1,3}\.\s+(?:Subs\.|Ins\.|Omitted|Added|Rep\.|Re-?numbered|Renumbered|The (?:words|figures|clause|sub-clause|entry|entries|article|proviso|Explanation)|"
    r"Cl\.|Sub-clause|Entry|Entries|Art\.|Article \d+|Certain|Clause|See |Now |Came into|Declared|Paragraph|Para\.|Items?\b|Part|Sections?\b|S\.)"
)


def clean_pages(reader: PdfReader) -> list[tuple[int, str]]:
    """(page_number, line) pairs for the body of the document with headers/footnotes removed."""
    lines: list[tuple[int, str]] = []
    started = False
    for idx, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        if not started:
            # skip cover + table of contents; body starts at Part I / Article 1
            if "shall be a Union of States" in text:  # Article 1 text; the contents pages only list titles
                started = True
            else:
                continue
        in_footnote = False
        for raw in text.split("\n"):
            # drop private-use glyphs (footnote symbols in the PDF font), then normalise whitespace
            line = re.sub(r"\s+", " ", re.sub(r"[-]", "", raw)).strip()
            if not line:
                continue
            if in_footnote:
                if line.endswith("."):
                    in_footnote = False
                continue
            if any(rx.match(line) for rx in RUNNING_HEADER_RES):
                continue
            if FOOTNOTE_START_RE.match(line):
                in_footnote = not line.endswith(".")
                continue
            lines.append((idx + 1, line))
    return lines


# ---------------------------------------------------------------- sectioning


def article_key(num: str) -> tuple[int, str]:
    m = re.match(r"(\d+)([A-Z]*)", num)
    return int(m.group(1)), m.group(2)


def title_from(first_line: str) -> str:
    """'65. The Vice-President to act as President.—(1) In ...' -> 'The Vice-President to act as President'."""
    body = ARTICLE_RE.match(first_line).group(2)
    m = re.search(r"\.\s*[—–═-╰⸺\-⎯—]", body)
    title = body[: m.start()] if m else body
    return title.strip().rstrip(".—⎯ ")[:200]


def build_sections(lines: list[tuple[int, str]]) -> list[dict]:
    sections: list[dict] = []
    cur: dict | None = None
    part = ""
    last_key = (0, "")
    mode = "articles"  # -> "annex" once the Schedules/Appendices begin

    def start(**kw):
        nonlocal cur
        cur = {"part": part, "lines": [], "pages": set(), **kw}
        sections.append(cur)

    i = 0
    while i < len(lines):
        page, line = lines[i]

        m = PART_RE.match(line)
        if m and mode == "articles":
            # next line(s) in caps are the part title
            title_parts = []
            j = i + 1
            while j < len(lines) and len(title_parts) < 3 and lines[j][1].isupper() and not ARTICLE_RE.match(lines[j][1]):
                title_parts.append(lines[j][1])
                j += 1
            raw_title = re.sub(r"\s*\d*\*+.*$", "", " ".join(title_parts))  # cut footnote markers like "1***"
            part = f"Part {m.group(1)}: {raw_title.title()}".strip(": ")
            i = j
            continue

        sm = SCHEDULE_RE.match(line)
        am = APPENDIX_RE.match(line)
        if (sm or am) and last_key[0] >= 390:
            mode = "annex"
            name = f"{sm.group(1).title()} Schedule" if sm else f"Appendix {am.group(1)}"
            part = ""
            start(kind="schedule" if sm else "appendix", number=name, title=name)
            i += 1
            continue

        if mode == "articles":
            am_ = ARTICLE_RE.match(line)
            if am_:
                key = article_key(am_.group(1))
                # articles appear in order; this rejects stray numbered lines inside text
                if key > last_key and key[0] <= last_key[0] + 5:
                    last_key = key
                    start(kind="article", number=am_.group(1), title=title_from(line))
                    cur["lines"].append(line)
                    cur["pages"].add(page)
                    i += 1
                    continue

        if cur is not None:
            cur["lines"].append(line)
            cur["pages"].add(page)
        i += 1

    # Article headings often wrap onto a second line, so read the title from the first few lines
    for sec in sections:
        if sec["kind"] == "article" and sec["lines"]:
            sec["title"] = title_from(" ".join(sec["lines"][:3])) or sec["title"]
    return sections


# ---------------------------------------------------------------- chunking


def label(sec: dict) -> str:
    if sec["kind"] == "article":
        head = f"Article {sec['number']} - {sec['title']}"
    else:
        head = sec["title"]
    return f"{SOURCE} | {head}" + (f" | {sec['part']}" if sec["part"] else "")


def split_lines(lines: list[str]) -> list[str]:
    """Pack lines into <= MAX_CHARS pieces, preferring to break before a new (clause)."""
    pieces: list[str] = []
    buf: list[str] = []
    size = 0
    for line in lines:
        if buf and size + len(line) > MAX_CHARS and (CLAUSE_RE.match(line) or size + len(line) > MAX_CHARS * 1.3):
            pieces.append(" ".join(buf))
            buf, size = [], 0
        buf.append(line)
        size += len(line) + 1
    if buf:
        pieces.append(" ".join(buf))
    return pieces


def make_chunks(sections: list[dict]) -> list[dict]:
    chunks = []
    for sec in sections:
        if not sec["lines"]:
            continue
        pieces = split_lines(sec["lines"])
        pages = sorted(sec["pages"])
        for n, piece in enumerate(pieces, 1):
            head = label(sec) + (f" (part {n}/{len(pieces)})" if len(pieces) > 1 else "")
            chunks.append(
                {
                    "id": f"{sec['kind']}-{sec['number'].replace(' ', '-').lower()}-{n}",
                    "source": SOURCE,
                    "kind": sec["kind"],
                    "number": sec["number"],
                    "title": sec["title"],
                    "part": sec["part"],
                    "pages": [pages[0], pages[-1]],
                    "text": f"{head}\n{piece}",
                }
            )
    return chunks


# ---------------------------------------------------------------- embedding


def embed(texts: list[str]) -> np.ndarray:
    if not config.LLM_API_KEY:
        sys.exit("LLM_API_KEY is not set in backend/.env")
    headers = {"Authorization": f"Bearer {config.LLM_API_KEY}"}
    out: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH):
        batch = texts[start : start + EMBED_BATCH]
        for attempt in range(8):
            try:
                resp = httpx.post(
                    f"{config.LLM_BASE_URL}/embeddings",
                    json={"model": config.EMBEDDING_MODEL, "input": batch},
                    headers=headers,
                    timeout=60,
                )
            except httpx.TimeoutException:
                print("  timeout, retrying ...")
                time.sleep(5)
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                # 429 = per-minute token quota used up: wait for it to refill, then retry the same batch
                wait = float(resp.headers.get("retry-after", 15 * (attempt + 1)))
                print(f"  HTTP {resp.status_code}, waiting {wait:.0f}s then retrying ...")
                time.sleep(wait)
                continue
            if resp.status_code != 200:  # e.g. 401 bad key, 400 bad request: retrying will not help
                sys.exit(f"Embeddings request failed: HTTP {resp.status_code} {resp.text[:300]}")
            data = sorted(resp.json()["data"], key=lambda d: d["index"])
            out.extend(d["embedding"] for d in data)
            break
        else:
            sys.exit("Giving up after repeated failures; re-run the script to try again.")
        print(f"  embedded {min(start + EMBED_BATCH, len(texts))}/{len(texts)}")
    arr = np.asarray(out, dtype=np.float32)
    arr /= np.linalg.norm(arr, axis=1, keepdims=True)  # normalise so cosine similarity == dot product
    return arr


# ---------------------------------------------------------------- main


def report(chunks: list[dict]) -> None:
    arts = [c for c in chunks if c["kind"] == "article"]
    nums = sorted({article_key(c["number"]) for c in arts})
    sizes = [len(c["text"]) for c in chunks]
    print(f"chunks: {len(chunks)}  (articles: {len(arts)}, schedules/appendices: {len(chunks) - len(arts)})")
    print(f"unique articles: {len(nums)}  first: {nums[0] if nums else None}  last: {nums[-1] if nums else None}")
    print(f"chunk chars  min/median/max: {min(sizes)}/{int(np.median(sizes))}/{max(sizes)}")
    expected = set(range(1, 396))
    missing = sorted(expected - {n[0] for n in nums})
    print(f"article numbers 1-395 with no chunk (omitted/repealed articles are expected here): {missing}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--dry-run", action="store_true", help="parse and chunk only; no API calls, no files written")
    ap.add_argument("--show", metavar="ARTICLE", help="print the chunk(s) for an article number, e.g. 21")
    args = ap.parse_args()

    if not args.pdf.exists():
        sys.exit(f"PDF not found: {args.pdf}")
    print(f"reading {args.pdf}")
    lines = clean_pages(PdfReader(str(args.pdf)))
    chunks = make_chunks(build_sections(lines))
    report(chunks)

    if args.show:
        for c in chunks:
            if c["kind"] == "article" and c["number"] == args.show:
                print("\n" + "-" * 70 + f"\n{c['id']}  pages {c['pages']}\n{c['text']}")
    if args.dry_run:
        return

    print(f"embedding with {config.EMBEDDING_MODEL} ...")
    vectors = embed([c["text"] for c in chunks])
    args.out.mkdir(parents=True, exist_ok=True)
    np.save(args.out / "embeddings.npy", vectors)
    meta = {"model": config.EMBEDDING_MODEL, "dim": int(vectors.shape[1]), "chunks": chunks}
    (args.out / "chunks.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {len(chunks)} chunks x {vectors.shape[1]} dims to {args.out}")


if __name__ == "__main__":
    main()

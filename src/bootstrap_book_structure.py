from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import List, Tuple


CHAPTER_RE = re.compile(
    r"^\s*(cap[ií]tulo|capitulo)\s+(\d+)(?:\s*[:.\-]\s*(.*))?\s*$",
    flags=re.IGNORECASE,
)

SUMARIO_RE = re.compile(
    r"^\s*(sumario|indice|índice|contents|table of contents)\s*$",
    flags=re.IGNORECASE,
)

INDEX_LINE_RE = re.compile(
    r"^\s*(cap[ií]tulo|capitulo)\s+\d+.*\d+\s*$",
    flags=re.IGNORECASE,
)

DIALOGUE_LINE_RE = re.compile(r"^\s*—")
DIALOGUE_PREFIX_RE = re.compile(r"^\s*[A-ZÁÉÍÓÚÑ0-9_ ]+\s*:\s*.+$")


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def save_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def normalize(text: str) -> str:
    return (
        text.lower()
        .replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
        .replace("ü", "u")
        .replace("ñ", "n")
    )


def split_lines(text: str) -> List[str]:
    return [line.rstrip() for line in text.splitlines()]


def is_chapter_line(line: str) -> bool:
    return CHAPTER_RE.match(line.strip()) is not None


def parse_chapter_line(line: str) -> Tuple[int, str]:
    m = CHAPTER_RE.match(line.strip())
    if not m:
        return 0, ""
    num = int(m.group(2))
    title = (m.group(3) or "").strip()
    return num, title


def is_sumario_line(line: str) -> bool:
    return SUMARIO_RE.match(line.strip()) is not None


def is_index_like_line(line: str) -> bool:
    return INDEX_LINE_RE.match(line.strip()) is not None


def is_dialogue_line(line: str) -> bool:
    s = line.strip()
    return bool(DIALOGUE_LINE_RE.match(s) or DIALOGUE_PREFIX_RE.match(s))


def beat_kind(text: str) -> str:
    s = text.strip()
    if not s:
        return "transition"
    if is_dialogue_line(s):
        return "dialogue"
    if len(s.split()) <= 8:
        return "transition"
    return "narration"


def scene_kind_from_beats(beats: List[str]) -> str:
    if not beats:
        return "narration"
    dialogue_count = sum(1 for b in beats if beat_kind(b) == "dialogue")
    if dialogue_count == 0:
        return "narration"
    if dialogue_count == len(beats):
        return "dialogue"
    return "mixed"


def find_first_chapter_index(lines: List[str]) -> int:
    for i, line in enumerate(lines):
        if is_chapter_line(line):
            return i
    return -1


def trim_front_matter(lines: List[str]) -> List[str]:
    first_chapter = find_first_chapter_index(lines)
    if first_chapter == -1:
        return lines
    return lines[first_chapter:]


def remove_sumario_block(lines: List[str]) -> List[str]:
    out: List[str] = []
    in_sumario = False

    for line in lines:
        stripped = line.strip()

        if is_sumario_line(stripped):
            in_sumario = True
            continue

        if in_sumario:
            if not stripped:
                continue
            if is_index_like_line(stripped):
                continue
            if is_chapter_line(stripped):
                in_sumario = False
                out.append(line)
                continue
            if re.search(r"\d+\s*$", stripped):
                continue
            in_sumario = False

        out.append(line)

    return out


def split_into_chapters(lines: List[str]) -> List[Tuple[str, List[str]]]:
    chapters: List[Tuple[str, List[str]]] = []
    current_title = ""
    current_lines: List[str] = []

    for line in lines:
        if is_chapter_line(line):
            if current_lines:
                chapters.append((current_title, current_lines))
            num, title = parse_chapter_line(line)
            current_title = f"Capítulo {num}" + (f": {title}" if title else "")
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines or current_title:
        chapters.append((current_title or "Capítulo 1", current_lines))

    return chapters


def group_nonempty_paragraphs(lines: List[str]) -> List[str]:
    paragraphs: List[str] = []
    buf: List[str] = []

    def flush():
        nonlocal buf
        if buf:
            text = "\n".join(buf).strip()
            if text:
                paragraphs.append(text)
            buf = []

    for line in lines:
        if not line.strip():
            flush()
            continue
        buf.append(line)

    flush()
    return paragraphs


def split_paragraph_into_beats(paragraph: str) -> List[str]:
    lines = [ln.rstrip() for ln in paragraph.splitlines() if ln.strip()]
    if len(lines) <= 1:
        return [paragraph.strip()] if paragraph.strip() else []

    beats: List[str] = []
    current: List[str] = []

    for line in lines:
        starts_new = is_dialogue_line(line) and len(current) > 0
        if starts_new:
            beats.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)

    if current:
        beats.append("\n".join(current).strip())

    return [b for b in beats if b.strip()]


def chapter_lines_to_beats(lines: List[str]) -> List[str]:
    paragraphs = group_nonempty_paragraphs(lines)
    beats: List[str] = []

    for p in paragraphs:
        joined = " ".join(p.split())
        if is_index_like_line(joined):
            continue
        beats.extend(split_paragraph_into_beats(p))

    return beats


def book_to_text(chapters: List[Tuple[str, List[str]]]) -> str:
    lines: List[str] = []

    for ep_idx, (title, beats) in enumerate(chapters, start=1):
        episode_id = f"E{ep_idx:02d}"
        block_id = f"{episode_id}_B01"
        scene_id = f"{block_id}_S01"
        skind = scene_kind_from_beats(beats)

        lines.append(f'{{episode id="{episode_id}" title="{title}"}}')
        lines.append("")
        lines.append(f'{{block id="{block_id}" kind="chapter_block"}}')
        lines.append("")
        lines.append(f'{{scene id="{scene_id}" kind="{skind}" location="" mood=""}}')
        lines.append("")

        for bt_idx, beat_text in enumerate(beats, start=1):
            beat_id = f"{scene_id}_BT{bt_idx:02d}"
            lines.append(f'{{beat id="{beat_id}" kind="{beat_kind(beat_text)}"}}')
            lines.append(beat_text.strip())
            lines.append("{/beat}")
            lines.append("")

        lines.append("{/scene}")
        lines.append("")
        lines.append("{/block}")
        lines.append("")
        lines.append("{/episode}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def bootstrap(input_text: str) -> str:
    lines = split_lines(input_text)
    lines = trim_front_matter(lines)
    lines = remove_sumario_block(lines)

    chapters_raw = split_into_chapters(lines)

    if not chapters_raw:
        beats = chapter_lines_to_beats(lines)
        chapters = [("Capítulo 1", beats)]
        return book_to_text(chapters)

    chapters: List[Tuple[str, List[str]]] = []
    for title, ch_lines in chapters_raw:
        beats = chapter_lines_to_beats(ch_lines)
        if beats:
            chapters.append((title, beats))

    if not chapters:
        chapters = [("Capítulo 1", chapter_lines_to_beats(lines))]

    return book_to_text(chapters)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Genera un primer libro estructurado detectando capítulos y beats."
    )
    parser.add_argument("--input", required=True, help="Ruta del txt plano")
    parser.add_argument("--output", required=True, help="Ruta del txt estructurado")
    args = parser.parse_args()

    input_path = Path(args.input).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()

    if not input_path.exists():
        raise FileNotFoundError(f"No existe el archivo de entrada: {input_path}")

    structured_text = bootstrap(load_text(input_path))
    save_text(output_path, structured_text)
    print(f"[OK] Archivo generado: {output_path}")


if __name__ == "__main__":
    main()
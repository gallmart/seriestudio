import re
from pathlib import Path
from typing import Dict, List

from studio_models import Beat, Block, Episode, Scene


def parse_attrs(raw: str) -> Dict[str, str]:
    return {k: v.strip() for k, v in re.findall(r'(\w+)\s*=\s*"([^"]*)"', raw, flags=re.DOTALL)}


def format_attrs(attrs: Dict[str, str]) -> str:
    return " ".join([f'{k}="{v}"' for k, v in attrs.items() if str(v) != ""])


def parse_structured_text(text: str) -> List[Episode]:
    ep_pattern = re.compile(r"\{episode(?P<attrs>.*?)\}(?P<body>.*?)\{/episode\}", re.DOTALL)
    block_pattern = re.compile(r"\{block(?P<attrs>.*?)\}(?P<body>.*?)\{/block\}", re.DOTALL)
    scene_pattern = re.compile(r"\{scene(?P<attrs>.*?)\}(?P<body>.*?)\{/scene\}", re.DOTALL)
    beat_pattern = re.compile(r"\{beat(?P<attrs>.*?)\}(?P<body>.*?)\{/beat\}", re.DOTALL)

    episodes: List[Episode] = []
    for ep_match in ep_pattern.finditer(text or ""):
        ep = Episode(parse_attrs(ep_match.group("attrs")))
        for block_match in block_pattern.finditer(ep_match.group("body")):
            block = Block(parse_attrs(block_match.group("attrs")))
            for scene_match in scene_pattern.finditer(block_match.group("body")):
                scene = Scene(parse_attrs(scene_match.group("attrs")))
                for beat_match in beat_pattern.finditer(scene_match.group("body")):
                    scene.beats.append(Beat(parse_attrs(beat_match.group("attrs")), beat_match.group("body").strip()))
                block.scenes.append(scene)
            ep.blocks.append(block)
        episodes.append(ep)
    return episodes


def book_to_text(episodes: List[Episode]) -> str:
    lines: List[str] = []
    for ep in episodes:
        lines.append(f'{{episode {format_attrs(ep.attrs)}}}')
        lines.append("")
        for block in ep.blocks:
            lines.append(f'{{block {format_attrs(block.attrs)}}}')
            lines.append("")
            for scene in block.scenes:
                lines.append(f'{{scene {format_attrs(scene.attrs)}}}')
                lines.append("")
                for beat in scene.beats:
                    lines.append(f'{{beat {format_attrs(beat.attrs)}}}')
                    lines.append(beat.text.strip())
                    lines.append("{/beat}")
                    lines.append("")
                lines.append("{/scene}")
                lines.append("")
            lines.append("{/block}")
            lines.append("")
        lines.append("{/episode}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def load_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return default


def parse_book(book_path: Path) -> List[Episode]:
    return parse_structured_text(load_text(book_path, ""))

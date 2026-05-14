import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="Project Studio", layout="wide")

DEFAULT_JSON_FILES = [
    "assets_manifest.json",
    "audio_manifest.json",
    "style_rules.json",
    "aliases.json",
    "character_aliases.json",
    "location_keywords.json",
    "function_rules.json",
    "shot_rules.json",
    "transition_rules.json",
    "director_defaults.json",
    "beat_overrides.json",
    "dialogue_rules.json",
    "dialogue_entities.json",
    "metadata_schema.json",
    "segmentation_marks.json",
]


# --------------------------------------------------
# Basic IO
# --------------------------------------------------
def load_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return default


def save_text(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def load_json_file(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json_file(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def json_valid(text: str):
    try:
        return True, json.loads(text), ""
    except Exception as e:
        return False, None, str(e)


# --------------------------------------------------
# Helpers
# --------------------------------------------------
def split_csv(value: str) -> List[str]:
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def normalize(text: str) -> str:
    if not text:
        return ""
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


def contains_whole_phrase(text: str, phrase: str) -> bool:
    return re.search(r"\b" + re.escape(normalize(phrase)) + r"\b", normalize(text)) is not None


def ordered_unique(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def safe_select(label: str, options: List[str], value: str, key: str) -> str:
    opts = [""] + [o for o in options if o != ""]
    idx = opts.index(value) if value in opts else 0
    return st.selectbox(label, opts, index=idx, key=key)


def safe_multiselect(label: str, options: List[str], value_csv: str, key: str) -> str:
    current = split_csv(value_csv)
    selected = st.multiselect(
        label,
        options,
        default=[x for x in current if x in options],
        key=key,
    )
    return ", ".join(selected)


def estimate_duration_seconds(text: str) -> int:
    words = len((text or "").split())
    estimated = round(words / 14) if words > 0 else 2
    return max(2, min(10, estimated))


def safe_float(value, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def html_escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def compact_preview(text: str, size: int = 80) -> str:
    clean = " ".join((text or "").split())
    return clean[:size] + ("..." if len(clean) > size else "")


# --------------------------------------------------
# Data model
# --------------------------------------------------
@dataclass
class Beat:
    attrs: Dict[str, str]
    text: str


@dataclass
class Scene:
    attrs: Dict[str, str]
    beats: List[Beat] = field(default_factory=list)


@dataclass
class Block:
    attrs: Dict[str, str]
    scenes: List[Scene] = field(default_factory=list)


@dataclass
class Episode:
    attrs: Dict[str, str]
    blocks: List[Block] = field(default_factory=list)


# --------------------------------------------------
# Book parsing / serialization
# --------------------------------------------------
def parse_attrs(raw: str) -> Dict[str, str]:
    return {k: v.strip() for k, v in re.findall(r'(\w+)\s*=\s*"([^"]*)"', raw, flags=re.DOTALL)}


def format_attrs(attrs: Dict[str, str]) -> str:
    return " ".join([f'{k}="{v}"' for k, v in attrs.items() if str(v) != ""])


def parse_book(book_path: Path) -> List[Episode]:
    text = load_text(book_path, "")
    ep_pattern = re.compile(r"\{episode(?P<attrs>.*?)\}(?P<body>.*?)\{/episode\}", re.DOTALL)
    block_pattern = re.compile(r"\{block(?P<attrs>.*?)\}(?P<body>.*?)\{/block\}", re.DOTALL)
    scene_pattern = re.compile(r"\{scene(?P<attrs>.*?)\}(?P<body>.*?)\{/scene\}", re.DOTALL)
    beat_pattern = re.compile(r"\{beat(?P<attrs>.*?)\}(?P<body>.*?)\{/beat\}", re.DOTALL)

    episodes: List[Episode] = []
    for ep_match in ep_pattern.finditer(text):
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


# --------------------------------------------------
# Project config
# --------------------------------------------------
def load_project_jsons(project_root: Path):
    config = project_root / "config"
    return {
        "assets_manifest": load_json_file(config / "assets_manifest.json", {"characters": {}, "locations": {}, "props": {}}),
        "aliases": load_json_file(config / "aliases.json", {}),
        "character_aliases": load_json_file(config / "character_aliases.json", {}),
        "location_keywords": load_json_file(config / "location_keywords.json", {}),
        "function_rules": load_json_file(config / "function_rules.json", {}),
        "shot_rules": load_json_file(config / "shot_rules.json", {}),
        "transition_rules": load_json_file(config / "transition_rules.json", {}),
        "director_defaults": load_json_file(config / "director_defaults.json", {}),
        "style_rules": load_json_file(config / "style_rules.json", {}),
        "dialogue_rules": load_json_file(config / "dialogue_rules.json", {}),
        "dialogue_entities": load_json_file(config / "dialogue_entities.json", {}),
        "metadata_schema": load_json_file(config / "metadata_schema.json", {}),
    }


def overrides_path(project_root: Path) -> Path:
    return project_root / "config" / "beat_overrides.json"


def load_overrides(project_root: Path) -> Dict[str, Dict[str, str]]:
    return load_json_file(overrides_path(project_root), {})


def save_overrides(project_root: Path, data: Dict[str, Dict[str, str]]):
    save_json_file(overrides_path(project_root), data)


def apply_overrides_to_beat(beat_id: str, beat_attrs: Dict[str, str], overrides: Dict[str, Dict[str, str]]) -> Dict[str, str]:
    merged = dict(beat_attrs)
    beat_override = overrides.get(beat_id, {})
    for k, v in beat_override.items():
        if v != "":
            merged[k] = v
    return merged


def schema_enum(schema: Dict, section: str, field: str) -> List[str]:
    return list((((schema or {}).get(section, {}) or {}).get("enums", {}) or {}).get(field, []))


def build_catalogs(project_root: Path):
    data = load_project_jsons(project_root)
    assets = data["assets_manifest"]
    location_keywords = data["location_keywords"]
    schema = data["metadata_schema"]

    return {
        "characters": sorted(list(assets.get("characters", {}).keys())),
        "props": sorted(list(assets.get("props", {}).keys())),
        "locations": sorted(set(list(assets.get("locations", {}).keys()) + list(location_keywords.keys()) + schema_enum(schema, "scene", "location"))),
        "block_kinds": schema_enum(schema, "block", "kind"),
        "block_themes": schema_enum(schema, "block", "theme"),
        "scene_kinds": schema_enum(schema, "scene", "kind"),
        "scene_times": schema_enum(schema, "scene", "time"),
        "scene_moods": schema_enum(schema, "scene", "mood"),
        "scene_audio": schema_enum(schema, "scene", "audio"),
        "scene_video": schema_enum(schema, "scene", "video"),
        "scene_visual_styles": schema_enum(schema, "scene", "visual_style"),
        "scene_shot_styles": schema_enum(schema, "scene", "shot_style"),
        "beat_shots": schema_enum(schema, "beat", "shot"),
        "beat_framings": schema_enum(schema, "beat", "framing"),
        "beat_cameras": schema_enum(schema, "beat", "camera"),
        "beat_transitions": schema_enum(schema, "beat", "transition"),
        "beat_audio": schema_enum(schema, "beat", "audio"),
        "beat_video": schema_enum(schema, "beat", "video"),
        "beat_visual_styles": schema_enum(schema, "beat", "visual_style"),
        "beat_kinds": schema_enum(schema, "beat", "kind"),
        "beat_music_tags": schema_enum(schema, "beat", "music_tag"),
        "beat_bgm_levels": schema_enum(schema, "beat", "bgm_level"),
        "beat_sfx_levels": schema_enum(schema, "beat", "sfx_level"),
        "beat_voice_intensity": schema_enum(schema, "beat", "voice_intensity"),
        "schema": schema,
    }


# --------------------------------------------------
# Active source book selector
# --------------------------------------------------
def list_text_files(root: Path) -> List[Path]:
    patterns = ["*.txt", "books/*.txt", "source/*.txt", "input/*.txt", "texts/*.txt"]
    found = []
    for pattern in patterns:
        found.extend(root.glob(pattern))
    return sorted(set([p.resolve() for p in found if p.is_file()]))


def get_active_book_path(root: Path) -> Path:
    default = root / "libro.txt"
    value = st.session_state.get("book_path", str(default))
    return Path(value)


def sidebar_book_selector(root: Path) -> Path:
    st.sidebar.subheader("Archivo de libro activo")
    files = list_text_files(root)
    default = root / "libro.txt"

    options = [str(p) for p in files] if files else [str(default)]
    if "book_path" not in st.session_state:
        st.session_state["book_path"] = str(default if default.exists() else options[0])

    current = st.session_state.get("book_path", options[0])
    index = options.index(current) if current in options else 0

    selected = st.sidebar.selectbox("Archivo .txt", options, index=index, key="sidebar_book_select")
    st.session_state["book_path"] = selected

    manual_path = st.sidebar.text_input("Ruta manual del .txt", value=selected, key="sidebar_book_manual_path")
    if st.sidebar.button("Usar ruta manual"):
        st.session_state["book_path"] = manual_path
        st.rerun()

    st.sidebar.caption(f"Activo: `{st.session_state['book_path']}`")
    return Path(st.session_state["book_path"])


# --------------------------------------------------
# Segmentation marks
# --------------------------------------------------
def segmentation_marks_path(root: Path) -> Path:
    return root / "config" / "segmentation_marks.json"


def load_segmentation_all(root: Path) -> Dict[str, Dict]:
    return load_json_file(segmentation_marks_path(root), {})


def save_segmentation_all(root: Path, data: Dict[str, Dict]):
    save_json_file(segmentation_marks_path(root), data)


def load_segmentation_marks(root: Path, source_file: Path) -> Dict:
    all_marks = load_segmentation_all(root)
    key = str(source_file.resolve())
    base = {
        "source_file": key,
        "episode_starts": [0],
        "block_starts": [0],
        "scene_starts": [0],
        "beat_starts": [0],
    }
    data = all_marks.get(key, {})
    merged = dict(base)
    merged.update(data)
    for field in ["episode_starts", "block_starts", "scene_starts", "beat_starts"]:
        values = merged.get(field, [0]) or [0]
        values = sorted(set([int(v) for v in values if isinstance(v, int) or str(v).isdigit()]))
        if 0 not in values:
            values = [0] + values
        merged[field] = values
    return merged


def save_segmentation_marks(root: Path, source_file: Path, marks: Dict):
    all_marks = load_segmentation_all(root)
    key = str(source_file.resolve())
    all_marks[key] = marks
    save_segmentation_all(root, all_marks)


def split_source_into_paragraphs(text: str) -> List[str]:
    raw = re.split(r"\n\s*\n+", text.strip(), flags=re.DOTALL)
    return [p.strip() for p in raw if p.strip()]


def toggle_start_mark(marks: Dict, field: str, idx: int):
    values = set(marks.get(field, []))
    if idx == 0:
        values.add(0)
    else:
        if idx in values:
            values.remove(idx)
        else:
            values.add(idx)
    values.add(0)
    marks[field] = sorted(values)


def paragraph_is_marked(marks: Dict, field: str, idx: int) -> bool:
    return idx in set(marks.get(field, []))


def build_structured_book_from_marks(root: Path, source_file: Path) -> List[Episode]:
    text = load_text(source_file, "")
    paragraphs = split_source_into_paragraphs(text)
    if not paragraphs:
        return []

    marks = load_segmentation_marks(root, source_file)

    episode_starts = sorted(set(marks.get("episode_starts", [0])))
    block_starts = sorted(set(marks.get("block_starts", [0])))
    scene_starts = sorted(set(marks.get("scene_starts", [0])))
    beat_starts = sorted(set(marks.get("beat_starts", [0])))

    if 0 not in episode_starts:
        episode_starts.insert(0, 0)
    if 0 not in block_starts:
        block_starts.insert(0, 0)
    if 0 not in scene_starts:
        scene_starts.insert(0, 0)
    if 0 not in beat_starts:
        beat_starts.insert(0, 0)

    episodes: List[Episode] = []
    current_episode = None
    current_block = None
    current_scene = None

    def new_episode():
        nonlocal current_episode, current_block, current_scene
        current_episode = Episode(attrs={"id": "", "title": ""}, blocks=[])
        episodes.append(current_episode)
        current_block = None
        current_scene = None

    def new_block():
        nonlocal current_episode, current_block, current_scene
        if current_episode is None:
            new_episode()
        current_block = Block(attrs={"id": "", "kind": "", "theme": ""}, scenes=[])
        current_episode.blocks.append(current_block)
        current_scene = None

    def new_scene():
        nonlocal current_block, current_scene
        if current_block is None:
            new_block()
        current_scene = Scene(attrs={"id": "", "kind": "", "location": ""}, beats=[])
        current_block.scenes.append(current_scene)

    new_episode()
    new_block()
    new_scene()

    for idx, paragraph in enumerate(paragraphs):
        if idx != 0 and idx in episode_starts:
            new_episode()
            new_block()
            new_scene()
        elif idx != 0 and idx in block_starts:
            new_block()
            new_scene()
        elif idx != 0 and idx in scene_starts:
            new_scene()

        if current_scene is None:
            new_scene()

        if not current_scene.beats or idx in beat_starts:
            current_scene.beats.append(Beat(attrs={"id": ""}, text=paragraph))
        else:
            current_scene.beats[-1].text = (current_scene.beats[-1].text + "\n\n" + paragraph).strip()

    ensure_ids(episodes)

    for e_i, ep in enumerate(episodes, start=1):
        ep.attrs["id"] = ep.attrs.get("id") or f"E{e_i:02d}"
        if not ep.attrs.get("title"):
            ep.attrs["title"] = f"Episodio {e_i:02d}"

        for b_i, block in enumerate(ep.blocks, start=1):
            block.attrs["id"] = block.attrs.get("id") or f"{ep.attrs['id']}_B{b_i:02d}"

            for s_i, scene in enumerate(block.scenes, start=1):
                scene.attrs["id"] = scene.attrs.get("id") or f"{block.attrs['id']}_S{s_i:02d}"

                for bt_i, beat in enumerate(scene.beats, start=1):
                    beat.attrs["id"] = beat.attrs.get("id") or f"{scene.attrs['id']}_BT{bt_i:02d}"

    return episodes

def bootstrap_is_dialogue_paragraph(p: str) -> bool:
    s = (p or "").strip()
    return s.startswith("—") or bool(re.match(r"^\s*[A-ZÁÉÍÓÚÑ0-9_ ]+\s*:\s*.+$", s, flags=re.DOTALL))


def bootstrap_paragraph_kind(p: str) -> str:
    s = (p or "").strip()
    if bootstrap_is_dialogue_paragraph(s):
        return "dialogue"
    if len(s.split()) <= 8:
        return "transition"
    return "narration"


def bootstrap_scene_kind(paragraphs: List[str]) -> str:
    if not paragraphs:
        return "narration"
    dialogue_count = sum(1 for p in paragraphs if bootstrap_is_dialogue_paragraph(p))
    if dialogue_count == 0:
        return "narration"
    if dialogue_count == len(paragraphs):
        return "dialogue"
    return "mixed"


def bootstrap_likely_scene_break(prev_p: str, current_p: str) -> bool:
    prev_n = normalize(prev_p)
    cur_n = normalize(current_p)

    markers = [
        "mas tarde", "horas despues", "al amanecer", "al anochecer", "por la noche",
        "a la manana siguiente", "en otro lugar", "mientras tanto", "de vuelta",
        "en el bar", "en la oficina", "en el estadio", "en el vestuario",
        "en el tunel", "en la sala", "en el despacho", "afuera", "fuera",
        "dentro", "interior", "exterior"
    ]
    if any(m in cur_n for m in markers):
        return True

    prev_dialogue = bootstrap_is_dialogue_paragraph(prev_p)
    cur_dialogue = bootstrap_is_dialogue_paragraph(current_p)
    if prev_dialogue != cur_dialogue:
        if len(current_p.split()) > 18 or len(prev_p.split()) > 18:
            return True

    return False


def bootstrap_detect_block_starts(paragraphs: List[str], block_size: int) -> List[int]:
    starts = [0]
    if block_size <= 0:
        return starts
    idx = block_size
    while idx < len(paragraphs):
        starts.append(idx)
        idx += block_size
    return sorted(set(starts))


def bootstrap_detect_scene_starts(paragraphs: List[str]) -> List[int]:
    starts = [0]
    for i in range(1, len(paragraphs)):
        if bootstrap_likely_scene_break(paragraphs[i - 1], paragraphs[i]):
            starts.append(i)
    return sorted(set(starts))


def bootstrap_group_ranges(starts: List[int], total: int) -> List[tuple[int, int]]:
    ordered = sorted(set(x for x in starts if 0 <= x < total))
    if not ordered or ordered[0] != 0:
        ordered = [0] + ordered
    ranges = []
    for i, start in enumerate(ordered):
        end = ordered[i + 1] if i + 1 < len(ordered) else total
        ranges.append((start, end))
    return ranges


def bootstrap_structured_book_from_plain_text(text: str, episode_title: str, block_size: int) -> List[Episode]:
    paragraphs = split_source_into_paragraphs(text)
    if not paragraphs:
        return []

    episode_id = "E01"
    block_starts = bootstrap_detect_block_starts(paragraphs, block_size)
    scene_starts = bootstrap_detect_scene_starts(paragraphs)
    block_ranges = bootstrap_group_ranges(block_starts, len(paragraphs))

    episodes: List[Episode] = [
        Episode(attrs={"id": episode_id, "title": episode_title}, blocks=[])
    ]

    for b_idx, (b_start, b_end) in enumerate(block_ranges, start=1):
        block_id = f"{episode_id}_B{b_idx:02d}"
        block_kind = f"plot_{min(b_idx, 4)}" if b_idx <= 4 else "reflection"
        block = Block(attrs={"id": block_id, "kind": block_kind, "theme": ""}, scenes=[])

        local_scene_starts = [s for s in scene_starts if b_start <= s < b_end]
        if not local_scene_starts or local_scene_starts[0] != b_start:
            local_scene_starts = [b_start] + local_scene_starts

        ordered_scene_starts = sorted(set(local_scene_starts))
        scene_ranges = []
        for i, start in enumerate(ordered_scene_starts):
            end = ordered_scene_starts[i + 1] if i + 1 < len(ordered_scene_starts) else b_end
            scene_ranges.append((start, end))

        for s_idx, (s_start, s_end) in enumerate(scene_ranges, start=1):
            scene_id = f"{block_id}_S{s_idx:02d}"
            scene_paragraphs = paragraphs[s_start:s_end]
            skind = bootstrap_scene_kind(scene_paragraphs)
            scene = Scene(
                attrs={
                    "id": scene_id,
                    "kind": skind,
                    "location": "",
                    "mood": "",
                },
                beats=[],
            )

            for bt_idx, p in enumerate(scene_paragraphs, start=1):
                beat_id = f"{scene_id}_BT{bt_idx:02d}"
                bkind = bootstrap_paragraph_kind(p)
                scene.beats.append(Beat(attrs={"id": beat_id, "kind": bkind}, text=p.strip()))

            block.scenes.append(scene)

        episodes[0].blocks.append(block)

    ensure_ids(episodes)
    return episodes




# --------------------------------------------------
# Working structured book state
# --------------------------------------------------
def get_working_book(root: Path) -> List[Episode]:
    book_path = get_active_book_path(root)
    if "working_book" not in st.session_state or st.session_state.get("working_book_path") != str(book_path):
        st.session_state["working_book"] = parse_book(book_path) if book_path.exists() else []
        st.session_state["working_book_path"] = str(book_path)
    return st.session_state["working_book"]


def reset_working_book(root: Path):
    book_path = get_active_book_path(root)
    st.session_state["working_book"] = parse_book(book_path) if book_path.exists() else []
    st.session_state["working_book_path"] = str(book_path)


def ensure_minimum_structure(root: Path):
    episodes = get_working_book(root)
    if not episodes:
        episodes.append(
            Episode(
                attrs={"id": "E01", "title": ""},
                blocks=[
                    Block(
                        attrs={"id": "E01_B01", "kind": "", "theme": ""},
                        scenes=[
                            Scene(
                                attrs={"id": "E01_B01_S01", "kind": "", "location": ""},
                                beats=[Beat(attrs={"id": "E01_B01_S01_BT01"}, text="")]
                            )
                        ],
                    )
                ],
            )
        )


# --------------------------------------------------
# IDs
# --------------------------------------------------
def infer_next_id(prefix: str, existing_ids: List[str]) -> str:
    max_num = 0
    for eid in existing_ids:
        m = re.search(rf"{re.escape(prefix)}(\d+)", eid or "", flags=re.IGNORECASE)
        if m:
            max_num = max(max_num, int(m.group(1)))
    return f"{prefix}{max_num + 1:02d}"


def ensure_ids(episodes: List[Episode]):
    ep_ids = [ep.attrs.get("id", "") for ep in episodes]
    for ep in episodes:
        if not ep.attrs.get("id"):
            ep.attrs["id"] = infer_next_id("E", ep_ids)
            ep_ids.append(ep.attrs["id"])

        block_ids = [b.attrs.get("id", "") for b in ep.blocks]
        for block in ep.blocks:
            if not block.attrs.get("id"):
                block.attrs["id"] = infer_next_id(f"{ep.attrs['id']}_B", block_ids)
                block_ids.append(block.attrs["id"])

            scene_ids = [s.attrs.get("id", "") for s in block.scenes]
            for scene in block.scenes:
                if not scene.attrs.get("id"):
                    scene.attrs["id"] = infer_next_id(f"{block.attrs['id']}_S", scene_ids)
                    scene_ids.append(scene.attrs["id"])

                beat_ids = [bt.attrs.get("id", "") for bt in scene.beats]
                for beat in scene.beats:
                    if not beat.attrs.get("id"):
                        beat.attrs["id"] = infer_next_id(f"{scene.attrs['id']}_BT", beat_ids)
                        beat_ids.append(beat.attrs["id"])


# --------------------------------------------------
# Structural operations
# --------------------------------------------------
def insert_empty_beat(scene: Scene, index: int):
    scene.beats.insert(index, Beat(attrs={"id": ""}, text=""))


def split_beat_by_marker(scene: Scene, beat_index: int, marker: str) -> bool:
    beat = scene.beats[beat_index]
    if not marker or marker not in beat.text:
        return False
    left, right = beat.text.split(marker, 1)
    left = left.strip()
    right = (marker + right).strip()
    if not left or not right:
        return False
    original_attrs = dict(beat.attrs)
    beat.text = left
    scene.beats.insert(beat_index + 1, Beat(attrs=dict(original_attrs), text=right))
    scene.beats[beat_index + 1].attrs["id"] = ""
    return True


def split_beat_by_paragraph(scene: Scene, beat_index: int, paragraph_index: int) -> bool:
    beat = scene.beats[beat_index]
    paragraphs = [p for p in beat.text.split("\n") if p.strip()]
    if len(paragraphs) < 2 or paragraph_index <= 0 or paragraph_index >= len(paragraphs):
        return False
    left = "\n".join(paragraphs[:paragraph_index]).strip()
    right = "\n".join(paragraphs[paragraph_index:]).strip()
    if not left or not right:
        return False
    original_attrs = dict(beat.attrs)
    beat.text = left
    scene.beats.insert(beat_index + 1, Beat(attrs=dict(original_attrs), text=right))
    scene.beats[beat_index + 1].attrs["id"] = ""
    return True


def split_scene_from_beat(block: Block, scene_index: int, beat_index: int) -> bool:
    scene = block.scenes[scene_index]
    if beat_index <= 0 or beat_index >= len(scene.beats):
        return False
    new_scene_beats = scene.beats[beat_index:]
    scene.beats = scene.beats[:beat_index]
    new_scene = Scene(attrs=dict(scene.attrs), beats=new_scene_beats)
    new_scene.attrs["id"] = ""
    block.scenes.insert(scene_index + 1, new_scene)
    return True


def split_block_from_scene(ep: Episode, block_index: int, scene_index: int) -> bool:
    block = ep.blocks[block_index]
    if scene_index <= 0 or scene_index >= len(block.scenes):
        return False
    new_block_scenes = block.scenes[scene_index:]
    block.scenes = block.scenes[:scene_index]
    new_block = Block(attrs=dict(block.attrs), scenes=new_block_scenes)
    new_block.attrs["id"] = ""
    ep.blocks.insert(block_index + 1, new_block)
    return True


def move_beat(scene: Scene, beat_index: int, direction: int) -> bool:
    new_index = beat_index + direction
    if 0 <= new_index < len(scene.beats):
        scene.beats[beat_index], scene.beats[new_index] = scene.beats[new_index], scene.beats[beat_index]
        return True
    return False


def move_scene(block: Block, scene_index: int, direction: int) -> bool:
    new_index = scene_index + direction
    if 0 <= new_index < len(block.scenes):
        block.scenes[scene_index], block.scenes[new_index] = block.scenes[new_index], block.scenes[scene_index]
        return True
    return False


def move_block(ep: Episode, block_index: int, direction: int) -> bool:
    new_index = block_index + direction
    if 0 <= new_index < len(ep.blocks):
        ep.blocks[block_index], ep.blocks[new_index] = ep.blocks[new_index], ep.blocks[block_index]
        return True
    return False


def delete_beat(scene: Scene, beat_index: int) -> bool:
    if len(scene.beats) <= 1:
        return False
    del scene.beats[beat_index]
    return True


def delete_scene(block: Block, scene_index: int) -> bool:
    if len(block.scenes) <= 1:
        return False
    del block.scenes[scene_index]
    return True


def delete_block(ep: Episode, block_index: int) -> bool:
    if len(ep.blocks) <= 1:
        return False
    del ep.blocks[block_index]
    return True


def add_scene_after(block: Block, scene_index: int):
    block.scenes.insert(
        scene_index + 1,
        Scene(attrs={"id": "", "kind": "", "location": ""}, beats=[Beat(attrs={"id": ""}, text="")]),
    )


def add_block_after(ep: Episode, block_index: int):
    ep.blocks.insert(
        block_index + 1,
        Block(
            attrs={"id": "", "kind": "", "theme": ""},
            scenes=[Scene(attrs={"id": "", "kind": "", "location": ""}, beats=[Beat(attrs={"id": ""}, text="")])],
        ),
    )


def create_episode_after(episodes: List[Episode], ep_index: int):
    episodes.insert(
        ep_index + 1,
        Episode(
            attrs={"id": "", "title": ""},
            blocks=[
                Block(
                    attrs={"id": "", "kind": "", "theme": ""},
                    scenes=[Scene(attrs={"id": "", "kind": "", "location": ""}, beats=[Beat(attrs={"id": ""}, text="")])],
                )
            ],
        ),
    )


# --------------------------------------------------
# Propagation
# --------------------------------------------------
def propagate_beat_attr(scene: Scene, start_idx: int, field: str, value: str, overwrite: bool):
    for i in range(start_idx, len(scene.beats)):
        current = scene.beats[i].attrs.get(field, "")
        if overwrite or current == "":
            if value == "":
                scene.beats[i].attrs.pop(field, None)
            else:
                scene.beats[i].attrs[field] = value


def propagate_scene_attr(block: Block, start_scene_idx: int, field: str, value: str, overwrite: bool):
    for i in range(start_scene_idx, len(block.scenes)):
        current = block.scenes[i].attrs.get(field, "")
        if overwrite or current == "":
            if value == "":
                block.scenes[i].attrs.pop(field, None)
            else:
                block.scenes[i].attrs[field] = value


def propagate_block_attr(ep: Episode, start_block_idx: int, field: str, value: str, overwrite: bool):
    for i in range(start_block_idx, len(ep.blocks)):
        current = ep.blocks[i].attrs.get(field, "")
        if overwrite or current == "":
            if value == "":
                ep.blocks[i].attrs.pop(field, None)
            else:
                ep.blocks[i].attrs[field] = value


# --------------------------------------------------
# Inspector
# --------------------------------------------------
def inspect_beat(project_root: Path, text: str, scene_attrs: Dict[str, str], beat_attrs: Dict[str, str]):
    data = load_project_jsons(project_root)
    assets_manifest = data["assets_manifest"]
    aliases = data["aliases"]
    character_aliases = data["character_aliases"]
    location_keywords = data["location_keywords"]
    function_rules = data["function_rules"]
    shot_rules = data["shot_rules"]
    transition_rules = data["transition_rules"]
    director_defaults = data["director_defaults"]
    style_rules = data["style_rules"]

    characters = list(assets_manifest.get("characters", {}).keys())
    props = list(assets_manifest.get("props", {}).keys())

    found_characters = []
    for canonical_name, alias_list in character_aliases.items():
        for alias in sorted(alias_list, key=lambda x: len(x), reverse=True):
            if contains_whole_phrase(text, alias):
                found_characters.append(canonical_name)
                break
    for canonical_name in characters:
        if canonical_name not in found_characters and contains_whole_phrase(text, canonical_name):
            found_characters.append(canonical_name)

    found_props = []
    for prop in props:
        if contains_whole_phrase(text, prop):
            found_props.append(prop)
    for raw_alias, canonical in aliases.items():
        if canonical in props and contains_whole_phrase(text, raw_alias):
            found_props.append(canonical)
    found_props = list(dict.fromkeys(found_props))

    location_candidates = []
    for loc, kws in location_keywords.items():
        score = 0
        longest = 0
        for kw in kws:
            if contains_whole_phrase(text, kw):
                score += 1
                longest = max(longest, len(normalize(kw)))
        if score:
            location_candidates.append((loc, score, longest))
    location_candidates.sort(key=lambda x: (x[1], x[2], x[0]), reverse=True)
    inferred_location = location_candidates[0][0] if location_candidates else ""
    location = beat_attrs.get("location") or scene_attrs.get("location") or inferred_location or "stadium_exterior"

    dialogue_match = re.match(r'^\s*([A-ZÁÉÍÓÚÑ0-9_ ]+)\s*:\s*(.+)$', text.strip(), flags=re.DOTALL)
    speaker = dialogue_match.group(1).title().strip() if dialogue_match else ""
    spoken = dialogue_match.group(2).strip() if dialogue_match else text.strip()

    focus = beat_attrs.get("focus", "")
    if not focus:
        focus = speaker or (found_characters[0] if found_characters else (found_props[0] if found_props else ""))

    def contains_any(t: str, kws: List[str]) -> bool:
        return any(normalize(k) in normalize(t) for k in kws)

    scores = {k: 0 for k in ["environment", "object_emphasis", "action", "reaction", "statement", "confrontation", "transition"]}
    wc = len(text.split())
    has_dialogue = bool(dialogue_match) or text.strip().startswith("—")

    if has_dialogue:
        scores["statement"] += 4
        if contains_any(text, function_rules.get("confrontation_keywords", [])):
            scores["confrontation"] += 5
        if wc <= 8:
            scores["confrontation"] += 1
        if found_props and contains_any(text, function_rules.get("object_keywords", [])):
            scores["object_emphasis"] += 1
    if found_props:
        scores["object_emphasis"] += 2
        if contains_any(text, function_rules.get("object_keywords", [])):
            scores["object_emphasis"] += 3
        if wc <= 12:
            scores["object_emphasis"] += 1
    if contains_any(text, function_rules.get("action_keywords", [])):
        scores["action"] += 4
        if found_characters:
            scores["action"] += 1
        if wc <= 14:
            scores["action"] += 1
    if contains_any(text, function_rules.get("reaction_keywords", [])):
        scores["reaction"] += 4
        if found_characters:
            scores["reaction"] += 1
    if contains_any(text, function_rules.get("environment_keywords", [])):
        scores["environment"] += 3
        if not found_characters:
            scores["environment"] += 2
        if wc <= 18:
            scores["environment"] += 1
    if found_characters and not has_dialogue:
        scores["reaction"] += 1
        scores["transition"] += 1
    if not found_characters and found_props:
        scores["object_emphasis"] += 1
    if wc <= 10:
        scores["transition"] += 2
    elif wc <= 18:
        scores["transition"] += 1

    priority = ["confrontation", "statement", "action", "object_emphasis", "reaction", "environment", "transition"]
    best = max(scores.values()) if scores else 0
    candidates = [k for k, v in scores.items() if v == best]
    function_type = next((p for p in priority if p in candidates), "transition")

    shot_map = shot_rules.get("function_to_shot", {})
    previous_speaker = scene_attrs.get("_previous_speaker", "")
    if beat_attrs.get("shot"):
        shot = beat_attrs.get("shot")
    elif function_type == "statement":
        if len(found_characters) >= 2 and not previous_speaker:
            shot = shot_rules.get("dialogue_opening_shot", "medium_two_shot")
        elif previous_speaker and speaker and previous_speaker != speaker:
            shot = shot_rules.get("dialogue_reply_shot", "over_the_shoulder")
        else:
            shot = shot_map.get(function_type, shot_rules.get("fallback_shot", "medium"))
    else:
        shot = shot_map.get(function_type, shot_rules.get("fallback_shot", "medium"))

    framing = beat_attrs.get("framing") or director_defaults.get("framing_by_shot", {}).get(shot, "medium")
    camera = beat_attrs.get("camera") or director_defaults.get("camera_by_shot", {}).get(shot, "static")
    scene_kind = beat_attrs.get("kind") or scene_attrs.get("kind") or ("dialogue" if has_dialogue else "narration")

    transition = beat_attrs.get("transition")
    if not transition:
        if scene_attrs.get("_scene_index", 0) == 0 and scene_attrs.get("_beat_index", 0) == 0:
            transition = transition_rules.get("episode_start", "fade_in")
        else:
            transition = transition_rules.get("scene_kind_defaults", {}).get(scene_kind, "cut")

    visual_style = beat_attrs.get("visual_style") or scene_attrs.get("visual_style")
    if not visual_style:
        block_kind = scene_attrs.get("block_kind", "")
        visual_style = style_rules.get("block_kind", {}).get(block_kind, {}).get("visual_style", "tradicion")

    prompt = ", ".join(
        [p for p in [
            shot,
            framing,
            camera,
            f"{scene_attrs.get('mood', 'dramatic')} mood",
            f"{visual_style} visual style",
            f"{scene_kind} scene",
            f"set in {location}",
            f"focus on {focus}" if focus else "",
            f"characters present: {','.join(found_characters)}" if found_characters else "",
            f"visible props: {','.join(found_props)}" if found_props else "",
            f"spoken dialogue by {speaker}: {spoken}" if speaker else f"dramatic action or narration: {spoken}",
        ] if p]
    )

    return {
        "detected_characters": found_characters,
        "detected_props": found_props,
        "detected_location": location,
        "focus": focus,
        "speaker": speaker,
        "function_scores": scores,
        "function_type": function_type,
        "shot": shot,
        "framing": framing,
        "camera": camera,
        "transition": transition,
        "visual_style": visual_style,
        "motion_prompt_preview": prompt,
        "location": location,
        "kind": scene_kind,
        "characters": found_characters,
        "props": found_props,
        "text": text,
    }


# --------------------------------------------------
# Sidebar beat toolbar
# --------------------------------------------------
def beat_status_icons(beat: Beat) -> str:
    text = (beat.text or "").strip()
    icons: List[str] = []

    has_dialogue = text.startswith("—") or bool(re.match(r'^\s*([A-ZÁÉÍÓÚÑ0-9_ ]+)\s*:\s*(.+)$', text, flags=re.DOTALL))
    if has_dialogue:
        icons.append("🗣")

    if beat.attrs.get("focus") or beat.attrs.get("kind") or beat.attrs.get("shot"):
        icons.append("🏷")

    if re.match(r'^\s*([A-ZÁÉÍÓÚÑ0-9_ ]+)\s*:\s*(.+)$', text, flags=re.DOTALL):
        icons.append("👤")

    if not text or not beat.attrs.get("kind"):
        icons.append("⚠")

    return " ".join(icons)


def render_scene_beats_sidebar(scene: Scene, block: Block, scene_idx: int, episodes: List[Episode], key_prefix: str):
    st.markdown("### Beats de la escena")
    selected_key = f"{key_prefix}_beat_list_idx"

    for i, bt in enumerate(scene.beats):
        preview = bt.text.strip().replace("\n", " ")
        preview = preview[:46] + ("..." if len(preview) > 46 else "")
        label = f'{bt.attrs.get("id", f"BT{i+1}")} — {preview or "[vacío]"}'
        icons = beat_status_icons(bt)

        c1, c2, c3, c4, c5, c6, c7 = st.columns([8, 1.1, 1.1, 1.1, 1.1, 1.1, 2.0])

        with c1:
            if st.button(label, key=f"{key_prefix}_select_beat_{scene.attrs.get('id','scene')}_{i}"):
                st.session_state[selected_key] = i
                st.rerun()
        with c2:
            if st.button("↑", key=f"{key_prefix}_move_up_{scene.attrs.get('id','scene')}_{i}"):
                if move_beat(scene, i, -1):
                    ensure_ids(episodes)
                    st.session_state[selected_key] = max(0, i - 1)
                    st.rerun()
        with c3:
            if st.button("↓", key=f"{key_prefix}_move_down_{scene.attrs.get('id','scene')}_{i}"):
                if move_beat(scene, i, 1):
                    ensure_ids(episodes)
                    st.session_state[selected_key] = min(len(scene.beats) - 1, i + 1)
                    st.rerun()
        with c4:
            if st.button("+", key=f"{key_prefix}_insert_after_{scene.attrs.get('id','scene')}_{i}"):
                insert_empty_beat(scene, i + 1)
                ensure_ids(episodes)
                st.session_state[selected_key] = i + 1
                st.rerun()
        with c5:
            if st.button("✂", key=f"{key_prefix}_split_scene_here_{scene.attrs.get('id','scene')}_{i}"):
                if i > 0:
                    split_scene_from_beat(block, scene_idx, i)
                    ensure_ids(episodes)
                    st.session_state[selected_key] = 0
                    st.rerun()
        with c6:
            if st.button("×", key=f"{key_prefix}_delete_beat_{scene.attrs.get('id','scene')}_{i}"):
                if delete_beat(scene, i):
                    ensure_ids(episodes)
                    st.session_state[selected_key] = max(0, min(i, len(scene.beats) - 1))
                    st.rerun()
        with c7:
            st.markdown(
                f"<div style='text-align:center; padding-top:0.35rem;'>{icons or '&nbsp;'}</div>",
                unsafe_allow_html=True,
            )


# --------------------------------------------------
# Selection
# --------------------------------------------------
def select_script_entities(root: Path, key_prefix: str = ""):
    ensure_minimum_structure(root)
    episodes = get_working_book(root)
    ensure_ids(episodes)

    if not episodes:
        return None, None, None, None, episodes, 0, 0, 0, 0

    ep_idx = st.selectbox(
        "Episodio",
        range(len(episodes)),
        format_func=lambda i: f'{episodes[i].attrs.get("id","?")} — {episodes[i].attrs.get("title","")}',
        key=f"{key_prefix}_ep",
    )
    ep = episodes[ep_idx]

    if not ep.blocks:
        ep.blocks.append(Block(attrs={"id": ""}, scenes=[Scene(attrs={"id": ""}, beats=[Beat(attrs={"id": ""}, text="")])]))

    block_idx = st.selectbox(
        "Bloque",
        range(len(ep.blocks)),
        format_func=lambda i: f'{ep.blocks[i].attrs.get("id","?")} — {ep.blocks[i].attrs.get("kind","")}',
        key=f"{key_prefix}_block",
    )
    block = ep.blocks[block_idx]

    if not block.scenes:
        block.scenes.append(Scene(attrs={"id": ""}, beats=[Beat(attrs={"id": ""}, text="")]))

    scene_idx = st.selectbox(
        "Escena",
        range(len(block.scenes)),
        format_func=lambda i: f'{block.scenes[i].attrs.get("id","?")} — {block.scenes[i].attrs.get("kind","")}',
        key=f"{key_prefix}_scene",
    )
    scene = block.scenes[scene_idx]

    if not scene.beats:
        scene.beats.append(Beat(attrs={"id": ""}, text=""))

    selector_key = f"{key_prefix}_beat_selector_idx"
    list_key = f"{key_prefix}_beat_list_idx"

    if list_key in st.session_state:
        desired_idx = st.session_state[list_key]
    elif selector_key in st.session_state:
        desired_idx = st.session_state[selector_key]
    else:
        desired_idx = 0

    desired_idx = max(0, min(desired_idx, len(scene.beats) - 1))

    if selector_key not in st.session_state or st.session_state[selector_key] >= len(scene.beats):
        st.session_state[selector_key] = desired_idx

    beat_idx = st.selectbox(
        "Beat",
        range(len(scene.beats)),
        format_func=lambda i: scene.beats[i].attrs.get("id", "?"),
        key=selector_key,
    )

    st.session_state[list_key] = beat_idx

    beat = scene.beats[beat_idx]
    return ep, block, scene, beat, episodes, ep_idx, block_idx, scene_idx, beat_idx


# --------------------------------------------------
# Beat map
# --------------------------------------------------
PALETTE = [
    "#4E79A7", "#F28E2B", "#E15759", "#76B7B2", "#59A14F",
    "#EDC948", "#B07AA1", "#FF9DA7", "#9C755F", "#BAB0AC",
    "#1F77B4", "#FF7F0E", "#2CA02C", "#D62728", "#9467BD",
]


def value_color_map(values: List[str]) -> Dict[str, str]:
    uniq = ordered_unique(values)
    mapping = {}
    for i, v in enumerate(uniq):
        mapping[v] = PALETTE[i % len(PALETTE)]
    return mapping


def beat_map_value(meta_field: str, beat: Beat, inspected: Dict[str, str]) -> str:
    if meta_field == "speaker":
        return inspected.get("speaker", "") or "∅"
    value = beat.attrs.get(meta_field, "")
    return value if value else "∅"


def render_beat_color_map(scene: Scene, project_root: Path, selected_field: str):
    inspected_rows = []
    for beat in scene.beats:
        inspected = inspect_beat(project_root, beat.text, dict(scene.attrs), beat.attrs)
        inspected_rows.append(inspected)

    values = [beat_map_value(selected_field, beat, inspected_rows[i]) for i, beat in enumerate(scene.beats)]
    color_by_value = value_color_map(values)

    blocks = []
    for i, beat in enumerate(scene.beats):
        value = values[i]
        color = color_by_value[value]
        label = beat.attrs.get("id", f"BT{i+1}")
        title = f"{label} | {selected_field}={value}"
        blocks.append(
            f"""
            <div title="{html_escape(title)}"
                 style="
                    flex:1;
                    min-width:110px;
                    border-radius:8px;
                    background:{color};
                    color:white;
                    padding:10px 6px;
                    text-align:center;
                    font-size:12px;
                    font-weight:600;
                    box-shadow: inset 0 0 0 1px rgba(255,255,255,0.18);
                 ">
                {html_escape(label)}
            </div>
            """
        )

    legend = []
    for value, color in color_by_value.items():
        legend.append(
            f"""
            <div style="display:flex; align-items:center; gap:8px; margin-right:18px; margin-bottom:8px;">
                <div style="width:14px; height:14px; border-radius:3px; background:{color};"></div>
                <div style="font-size:13px;">{html_escape(value)}</div>
            </div>
            """
        )

    html = f"""
    <div style="font-family: sans-serif;">
        <div style="font-size:18px; font-weight:600; margin-bottom:12px;">Mapa de beats por color</div>

        <div style="
            display:flex;
            gap:8px;
            align-items:stretch;
            margin-bottom:14px;
            overflow-x:auto;
            padding-bottom:4px;
        ">
            {''.join(blocks)}
        </div>

        <div style="
            display:flex;
            flex-wrap:wrap;
            align-items:center;
            margin-top:8px;
        ">
            {''.join(legend)}
        </div>
    </div>
    """
    components.html(html, height=170, scrolling=False)


# --------------------------------------------------
# Sidebar
# --------------------------------------------------
def sidebar_project_root() -> Path:
    st.sidebar.header("Proyecto")
    default_root = st.session_state.get("project_root", str(Path.cwd()))
    root_str = st.sidebar.text_input("Ruta del proyecto", value=default_root)
    root = Path(root_str).expanduser().resolve()
    st.session_state["project_root"] = str(root)
    st.sidebar.write(f"Usando: `{root}`")

    checks = {
        "config/": (root / "config").exists(),
        "assets/": (root / "assets").exists(),
        "output/": (root / "output").exists(),
        "src/series_studio.py": (root / "src" / "series_studio.py").exists(),
        "config/beat_overrides.json": (root / "config" / "beat_overrides.json").exists(),
    }
    st.sidebar.subheader("Comprobación")
    for label, ok in checks.items():
        st.sidebar.write(f"{'✅' if ok else '❌'} {label}")

    st.sidebar.markdown("---")
    return root


# --------------------------------------------------
# Tabs
# --------------------------------------------------
def tab_project(root: Path):
    st.header("Proyecto")
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Config")
        config_dir = root / "config"
        if config_dir.exists():
            for p in sorted(config_dir.glob("*.json")):
                st.write(f"• {p.name}")
        else:
            st.warning("No existe config/")
    with c2:
        st.subheader("Assets")
        for folder in ["characters", "locations", "props", "audio"]:
            st.write(f"• {folder}: {'✅' if (root / 'assets' / folder).exists() else '❌'}")

    st.markdown("---")
    active_book = get_active_book_path(root)
    st.write(f"**Archivo activo:** `{active_book}`")
    if active_book.exists():
        st.caption(f"Tamaño: {active_book.stat().st_size} bytes")


def tab_json_editor(root: Path):
    st.header("Editor de JSON")
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    json_files = sorted({p.name for p in config_dir.glob("*.json")} | set(DEFAULT_JSON_FILES))
    selected = st.selectbox("Archivo JSON", json_files)
    target = config_dir / selected
    current_text = load_text(target, "{}")
    json_text = st.text_area("Contenido JSON", value=current_text, height=500)
    valid, parsed, error = json_valid(json_text)

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("Validar JSON"):
            st.success("JSON válido") if valid else st.error(error)
    with col2:
        if st.button("Guardar JSON"):
            if valid:
                save_text(target, json.dumps(parsed, ensure_ascii=False, indent=2))
                st.success(f"Guardado: {target}")
            else:
                st.error(error)
    with col3:
        if st.button("Formatear JSON"):
            if valid:
                st.code(json.dumps(parsed, ensure_ascii=False, indent=2), language="json")
            else:
                st.error(error)


def tab_segmentation(root: Path):
    st.header("Segmentación")
    source_file = get_active_book_path(root)

    st.write(f"**Archivo activo para segmentar:** `{source_file}`")

    if not source_file.exists():
        st.warning("El archivo activo no existe.")
        raw = st.text_area("Crear contenido del archivo activo", value="", height=300)
        if st.button("Guardar archivo activo"):
            save_text(source_file, raw)
            st.success(f"Guardado: {source_file}")
            st.rerun()
        return

    text = load_text(source_file, "")
    paragraphs = split_source_into_paragraphs(text)
    marks = load_segmentation_marks(root, source_file)

    st.subheader("Bootstrap rápido desde texto plano")
    b1, b2 = st.columns(2)
    with b1:
        bootstrap_title = st.text_input(
            "Título inicial del episodio",
            value="Borrador inicial",
            key="seg_bootstrap_title",
        )
    with b2:
        bootstrap_block_size = st.number_input(
            "Párrafos aproximados por bloque",
            min_value=4,
            max_value=50,
            value=12,
            step=1,
            key="seg_bootstrap_block_size",
        )


    top1, top2, top3, top4 = st.columns(4)

    with top1:
        if st.button("Guardar marcas de segmentación"):
            save_segmentation_marks(root, source_file, marks)
            st.success("Marcas guardadas")

    with top2:
        if st.button("Resetear marcas"):
            marks = {
                "source_file": str(source_file.resolve()),
                "episode_starts": [0],
                "block_starts": [0],
                "scene_starts": [0],
                "beat_starts": [0],
            }
            save_segmentation_marks(root, source_file, marks)
            st.success("Marcas reseteadas")
            st.rerun()

    with top3:
        if st.button("Generar archivo estructurado activo"):
            episodes = build_structured_book_from_marks(root, source_file)
            if not episodes:
                st.error("No se pudo generar estructura.")
            else:
                save_text(source_file, book_to_text(episodes))
                st.session_state["working_book"] = episodes
                st.session_state["working_book_path"] = str(source_file)
                st.success(f"Estructura generada en: {source_file}")
                st.rerun()

    with top4:
        if st.button("Generar borrador estructurado"):
            plain_text = load_text(source_file, "")
            episodes = bootstrap_structured_book_from_plain_text(
                text=plain_text,
                episode_title=bootstrap_title,
                block_size=int(bootstrap_block_size),
            )
            if not episodes:
                st.error("No se pudo generar el borrador inicial.")
            else:
                save_text(source_file, book_to_text(episodes))
                st.session_state["working_book"] = episodes
                st.session_state["working_book_path"] = str(source_file)
                st.success(f"Borrador estructurado generado en: {source_file}")
                st.rerun()

    st.caption(f"Párrafos detectados: {len(paragraphs)}")

    for idx, paragraph in enumerate(paragraphs):
        with st.container():
            c1, c2, c3, c4, c5 = st.columns([5.8, 1, 1, 1, 1])

            with c1:
                badges = []
                if paragraph_is_marked(marks, "episode_starts", idx):
                    badges.append("E")
                if paragraph_is_marked(marks, "block_starts", idx):
                    badges.append("B")
                if paragraph_is_marked(marks, "scene_starts", idx):
                    badges.append("S")
                if paragraph_is_marked(marks, "beat_starts", idx):
                    badges.append("BT")
                badge_text = " · ".join(badges) if badges else "-"
                st.markdown(f"**[{idx}]** `{badge_text}`")
                st.write(paragraph)

            with c2:
                if st.button("Epis.", key=f"seg_ep_{idx}"):
                    toggle_start_mark(marks, "episode_starts", idx)
                    save_segmentation_marks(root, source_file, marks)
                    st.rerun()
            with c3:
                if st.button("Block", key=f"seg_block_{idx}"):
                    toggle_start_mark(marks, "block_starts", idx)
                    save_segmentation_marks(root, source_file, marks)
                    st.rerun()
            with c4:
                if st.button("Scene", key=f"seg_scene_{idx}"):
                    toggle_start_mark(marks, "scene_starts", idx)
                    save_segmentation_marks(root, source_file, marks)
                    st.rerun()
            with c5:
                if st.button("Beat", key=f"seg_beat_{idx}"):
                    toggle_start_mark(marks, "beat_starts", idx)
                    save_segmentation_marks(root, source_file, marks)
                    st.rerun()

            st.markdown("---")

    with st.expander("Vista previa del libro estructurado generado desde las marcas"):
        preview_episodes = build_structured_book_from_marks(root, source_file)
        if preview_episodes:
            st.code(book_to_text(preview_episodes), language="text")
        else:
            st.info("Todavía no hay estructura previa para mostrar.")


def tab_structure_editor(root: Path):
    st.subheader("Estructura del libro")
    selected = select_script_entities(root, "script_structure")
    if not selected or selected[0] is None:
        st.warning("No se ha podido cargar el libro")
        return

    ep, block, scene, beat, episodes, ep_idx, block_idx, scene_idx, beat_idx = selected
    ensure_ids(episodes)
    catalogs = build_catalogs(root)

    top1, top2, top3, top4 = st.columns(4)
    with top1:
        if st.button("Guardar archivo estructurado activo", key="struct_save_book"):
            book_path = get_active_book_path(root)
            ensure_ids(episodes)
            save_text(book_path, book_to_text(episodes))
            st.success(f"Guardado: {book_path}")
    with top2:
        if st.button("Resetear cambios no guardados", key="struct_reset_book"):
            reset_working_book(root)
            st.rerun()
    with top3:
        if st.button("Nuevo episodio después", key="struct_new_episode_after"):
            create_episode_after(episodes, ep_idx)
            ensure_ids(episodes)
            st.rerun()
    with top4:
        st.info(
            f"Episodios: {len(episodes)} · "
            f"Bloques en episodio: {len(ep.blocks)} · "
            f"Escenas en bloque: {len(block.scenes)} · "
            f"Beats en escena: {len(scene.beats)}"
        )

    left_panel, right_panel = st.columns([1.2, 2.4])

    with left_panel:
        render_scene_beats_sidebar(scene, block, scene_idx, episodes, "script_structure")

    with right_panel:
        st.subheader("Metadatos estructurales")

        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown("#### Episode")
            ep.attrs["id"] = st.text_input("episode.id", value=ep.attrs.get("id", ""), key="struct_episode_id")
            ep.attrs["title"] = st.text_input("episode.title", value=ep.attrs.get("title", ""), key="struct_episode_title")

        with c2:
            st.markdown("#### Block")
            block.attrs["id"] = st.text_input("block.id", value=block.attrs.get("id", ""), key="struct_block_id")
            block.attrs["kind"] = safe_select("block.kind", catalogs["block_kinds"], block.attrs.get("kind", ""), "struct_block_kind")
            block.attrs["theme"] = safe_select("block.theme", catalogs["block_themes"], block.attrs.get("theme", ""), "struct_block_theme")
            block.attrs["source_part"] = st.text_input("block.source_part", value=block.attrs.get("source_part", ""), key="struct_block_source_part")

        with c3:
            st.markdown("#### Scene")
            scene.attrs["id"] = st.text_input("scene.id", value=scene.attrs.get("id", ""), key="struct_scene_id")
            scene.attrs["kind"] = safe_select("scene.kind", catalogs["scene_kinds"], scene.attrs.get("kind", ""), "struct_scene_kind")
            scene.attrs["location"] = safe_select("scene.location", catalogs["locations"], scene.attrs.get("location", ""), "struct_scene_location")
            scene.attrs["time"] = safe_select("scene.time", catalogs["scene_times"], scene.attrs.get("time", ""), "struct_scene_time")
            scene.attrs["mood"] = safe_select("scene.mood", catalogs["scene_moods"], scene.attrs.get("mood", ""), "struct_scene_mood")

        c4, c5 = st.columns(2)
        with c4:
            scene.attrs["characters"] = safe_multiselect("scene.characters", catalogs["characters"], scene.attrs.get("characters", ""), "struct_scene_characters")
            scene.attrs["props"] = safe_multiselect("scene.props", catalogs["props"], scene.attrs.get("props", ""), "struct_scene_props")
            scene.attrs["audio"] = safe_select("scene.audio", catalogs["scene_audio"], scene.attrs.get("audio", ""), "struct_scene_audio")
            scene.attrs["video"] = safe_select("scene.video", catalogs["scene_video"], scene.attrs.get("video", ""), "struct_scene_video")

        with c5:
            scene.attrs["shot_style"] = safe_select("scene.shot_style", catalogs["scene_shot_styles"], scene.attrs.get("shot_style", ""), "struct_scene_shot_style")
            scene.attrs["visual_style"] = safe_select("scene.visual_style", catalogs["scene_visual_styles"], scene.attrs.get("visual_style", ""), "struct_scene_visual_style")

        st.subheader("Editor del beat")

        beat.attrs["id"] = st.text_input("beat.id", value=beat.attrs.get("id", ""), key="struct_beat_id")
        beat.text = st.text_area("Texto del beat", value=beat.text, height=220, key="struct_beat_text")

        bmeta1, bmeta2, bmeta3 = st.columns(3)
        with bmeta1:
            beat.attrs["kind"] = safe_select("beat.kind", catalogs["beat_kinds"], beat.attrs.get("kind", ""), "struct_beat_kind")
            beat.attrs["shot"] = safe_select("beat.shot", catalogs["beat_shots"], beat.attrs.get("shot", ""), "struct_beat_shot")
            beat.attrs["framing"] = safe_select("beat.framing", catalogs["beat_framings"], beat.attrs.get("framing", ""), "struct_beat_framing")
            beat.attrs["camera"] = safe_select("beat.camera", catalogs["beat_cameras"], beat.attrs.get("camera", ""), "struct_beat_camera")
            beat.attrs["focus"] = safe_select("beat.focus", catalogs["characters"] + catalogs["props"], beat.attrs.get("focus", ""), "struct_beat_focus")

        with bmeta2:
            beat.attrs["transition"] = safe_select("beat.transition", catalogs["beat_transitions"], beat.attrs.get("transition", ""), "struct_beat_transition")

            duration_default = str(estimate_duration_seconds(beat.text))
            beat.attrs["duration"] = st.text_input(
                "beat.duration",
                value=beat.attrs.get("duration", duration_default) or duration_default,
                key="struct_beat_duration",
            )

            beat.attrs["audio"] = safe_select("beat.audio", catalogs["beat_audio"], beat.attrs.get("audio", ""), "struct_beat_audio")
            beat.attrs["video"] = safe_select("beat.video", catalogs["beat_video"], beat.attrs.get("video", ""), "struct_beat_video")
            beat.attrs["visual_style"] = safe_select("beat.visual_style", catalogs["beat_visual_styles"], beat.attrs.get("visual_style", ""), "struct_beat_visual_style")

        with bmeta3:
            beat.attrs["music_tag"] = safe_select("beat.music_tag", catalogs["beat_music_tags"], beat.attrs.get("music_tag", ""), "struct_beat_music_tag")
            beat.attrs["bgm_level"] = safe_select("beat.bgm_level", catalogs["beat_bgm_levels"], beat.attrs.get("bgm_level", ""), "struct_beat_bgm_level")
            beat.attrs["sfx_level"] = safe_select("beat.sfx_level", catalogs["beat_sfx_levels"], beat.attrs.get("sfx_level", ""), "struct_beat_sfx_level")
            beat.attrs["voice_intensity"] = safe_select("beat.voice_intensity", catalogs["beat_voice_intensity"], beat.attrs.get("voice_intensity", ""), "struct_beat_voice_intensity")

            speech_rate_val = safe_float(beat.attrs.get("speech_rate", 1.0), 1.0)
            pause_before_val = safe_float(beat.attrs.get("pause_before", 0.0), 0.0)
            pause_after_val = safe_float(beat.attrs.get("pause_after", 0.0), 0.0)

            beat.attrs["speech_rate"] = f"{st.slider('beat.speech_rate', 0.85, 1.10, speech_rate_val, 0.01, key='struct_beat_speech_rate'):.2f}"
            beat.attrs["pause_before"] = f"{st.slider('beat.pause_before', 0.0, 0.5, pause_before_val, 0.01, key='struct_beat_pause_before'):.2f}"
            beat.attrs["pause_after"] = f"{st.slider('beat.pause_after', 0.0, 0.5, pause_after_val, 0.01, key='struct_beat_pause_after'):.2f}"

        b1, b2, b3, b4, b5, b6 = st.columns(6)
        with b1:
            if st.button("Insertar beat después", key="struct_insert_beat_after_main"):
                insert_empty_beat(scene, beat_idx + 1)
                ensure_ids(episodes)
                st.session_state["script_structure_beat_list_idx"] = beat_idx + 1
                st.rerun()
        with b2:
            if st.button("Nueva escena desde este beat", key="struct_new_scene_from_beat_main"):
                ok = split_scene_from_beat(block, scene_idx, beat_idx)
                if ok:
                    ensure_ids(episodes)
                    st.session_state["script_structure_beat_list_idx"] = 0
                    st.rerun()
                else:
                    st.warning("No se puede partir la escena desde el primer beat ni desde fuera de rango.")
        with b3:
            if st.button("Nuevo bloque desde esta escena", key="struct_new_block_from_scene_main"):
                ok = split_block_from_scene(ep, block_idx, scene_idx)
                if ok:
                    ensure_ids(episodes)
                    st.session_state["script_structure_beat_list_idx"] = 0
                    st.rerun()
                else:
                    st.warning("No se puede partir el bloque desde la primera escena ni desde fuera de rango.")
        with b4:
            if st.button("Subir beat", key="struct_move_beat_up_main"):
                if move_beat(scene, beat_idx, -1):
                    st.session_state["script_structure_beat_list_idx"] = max(0, beat_idx - 1)
                    st.rerun()
        with b5:
            if st.button("Bajar beat", key="struct_move_beat_down_main"):
                if move_beat(scene, beat_idx, 1):
                    st.session_state["script_structure_beat_list_idx"] = min(len(scene.beats) - 1, beat_idx + 1)
                    st.rerun()
        with b6:
            if st.button("Borrar beat", key="struct_delete_beat_main"):
                if delete_beat(scene, beat_idx):
                    ensure_ids(episodes)
                    st.session_state["script_structure_beat_list_idx"] = max(0, min(beat_idx, len(scene.beats) - 1))
                    st.rerun()
                else:
                    st.warning("La escena debe tener al menos un beat.")

        st.subheader("Propagar metadato")

        prop1, prop2, prop3, prop4 = st.columns([1.2, 1.3, 1.2, 1.1])

        target_level = prop1.selectbox("Nivel", ["beat", "scene", "block"], key="struct_propagation_level")

        if target_level == "beat":
            fields = ["kind", "shot", "framing", "camera", "focus", "transition", "duration", "audio", "video", "visual_style", "music_tag", "bgm_level", "sfx_level", "speech_rate", "voice_intensity", "pause_before", "pause_after"]
            current_value_source = beat.attrs
            scope_options = ["hasta final de escena"]
        elif target_level == "scene":
            fields = ["kind", "location", "time", "mood", "characters", "props", "audio", "video", "visual_style", "shot_style"]
            current_value_source = scene.attrs
            scope_options = ["hasta final del bloque"]
        else:
            fields = ["kind", "theme", "source_part"]
            current_value_source = block.attrs
            scope_options = ["hasta final del episodio"]

        field_to_propagate = prop2.selectbox("Campo", fields, key="struct_propagation_field")
        _ = prop3.selectbox("Alcance", scope_options, key="struct_propagation_scope")
        overwrite_existing = prop4.checkbox("Sobrescribir", value=False, key="struct_propagation_overwrite")

        current_value_to_propagate = current_value_source.get(field_to_propagate, "")
        st.caption(f"Valor actual a propagar: `{current_value_to_propagate}`")

        if st.button("Aplicar de aquí para adelante", key="struct_apply_propagation"):
            if target_level == "beat":
                propagate_beat_attr(scene, beat_idx, field_to_propagate, current_value_to_propagate, overwrite_existing)
            elif target_level == "scene":
                propagate_scene_attr(block, scene_idx, field_to_propagate, current_value_to_propagate, overwrite_existing)
            else:
                propagate_block_attr(ep, block_idx, field_to_propagate, current_value_to_propagate, overwrite_existing)
            st.success("Propagación aplicada")
            st.rerun()

        st.subheader("Operaciones de división")

        split_marker = st.text_input("Dividir beat por marcador de texto", value="", key="struct_split_marker")
        s1, s2, s3 = st.columns(3)

        with s1:
            if st.button("Dividir beat por marcador", key="struct_split_by_marker"):
                if split_beat_by_marker(scene, beat_idx, split_marker):
                    ensure_ids(episodes)
                    st.session_state["script_structure_beat_list_idx"] = beat_idx + 1
                    st.rerun()
                else:
                    st.warning("No se pudo dividir. Comprueba que el marcador exista y deje dos partes con texto.")

        paragraphs = [p for p in beat.text.split("\n") if p.strip()]
        paragraph_labels = [f"{i}: {p[:80]}" for i, p in enumerate(paragraphs)]
        paragraph_split_index = 0
        if len(paragraphs) > 1:
            paragraph_split_index = st.selectbox(
                "Dividir a partir del párrafo",
                range(len(paragraphs)),
                format_func=lambda i: paragraph_labels[i],
                index=1 if len(paragraphs) > 1 else 0,
                key="struct_paragraph_split_index",
            )

        with s2:
            if st.button("Dividir beat por párrafo", key="struct_split_by_paragraph"):
                if split_beat_by_paragraph(scene, beat_idx, paragraph_split_index):
                    ensure_ids(episodes)
                    st.session_state["script_structure_beat_list_idx"] = beat_idx + 1
                    st.rerun()
                else:
                    st.warning("No se pudo dividir por párrafo.")

        with s3:
            if st.button("Insertar escena vacía después", key="struct_insert_empty_scene_after"):
                add_scene_after(block, scene_idx)
                ensure_ids(episodes)
                st.session_state["script_structure_beat_list_idx"] = 0
                st.rerun()

        st.subheader("Mover o borrar niveles superiores")

        m1, m2, m3, m4, m5, m6 = st.columns(6)
        with m1:
            if st.button("Subir escena", key="struct_move_scene_up"):
                if move_scene(block, scene_idx, -1):
                    st.session_state["script_structure_beat_list_idx"] = beat_idx
                    st.rerun()
        with m2:
            if st.button("Bajar escena", key="struct_move_scene_down"):
                if move_scene(block, scene_idx, 1):
                    st.session_state["script_structure_beat_list_idx"] = beat_idx
                    st.rerun()
        with m3:
            if st.button("Borrar escena", key="struct_delete_scene"):
                if delete_scene(block, scene_idx):
                    ensure_ids(episodes)
                    st.session_state["script_structure_beat_list_idx"] = 0
                    st.rerun()
                else:
                    st.warning("El bloque debe tener al menos una escena.")
        with m4:
            if st.button("Insertar bloque vacío después", key="struct_insert_empty_block_after"):
                add_block_after(ep, block_idx)
                ensure_ids(episodes)
                st.session_state["script_structure_beat_list_idx"] = 0
                st.rerun()
        with m5:
            if st.button("Subir bloque", key="struct_move_block_up"):
                if move_block(ep, block_idx, -1):
                    st.session_state["script_structure_beat_list_idx"] = 0
                    st.rerun()
        with m6:
            if st.button("Borrar bloque", key="struct_delete_block"):
                if delete_block(ep, block_idx):
                    ensure_ids(episodes)
                    st.session_state["script_structure_beat_list_idx"] = 0
                    st.rerun()
                else:
                    st.warning("El episodio debe tener al menos un bloque.")

        with st.expander("Vista previa reconstruida del libro"):
            ensure_ids(episodes)
            st.code(book_to_text(episodes), language="text")


def tab_tag_editor(root: Path):
    st.subheader("Etiquetas y sugerencias")
    selected = select_script_entities(root, "script_tags")
    if not selected or selected[0] is None:
        st.warning("No se ha podido cargar el libro")
        return

    ep, block, scene, beat, episodes, ep_idx, block_idx, scene_idx, beat_idx = selected
    catalogs = build_catalogs(root)

    suggestion_scene_attrs = dict(scene.attrs)
    suggestion_scene_attrs["block_kind"] = block.attrs.get("kind", "")
    suggestion_scene_attrs["_scene_index"] = scene_idx
    suggestion_scene_attrs["_beat_index"] = beat_idx
    suggestion = inspect_beat(root, beat.text, suggestion_scene_attrs, beat.attrs)

    left, center, right = st.columns([1.2, 1.2, 1.5])

    with left:
        st.markdown("### Texto")
        st.code(beat.text, language="text")
        st.markdown("### Detectado")
        st.json({
            "characters": suggestion.get("detected_characters", []),
            "props": suggestion.get("detected_props", []),
            "location": suggestion.get("detected_location", ""),
            "focus": suggestion.get("focus", ""),
            "speaker": suggestion.get("speaker", ""),
            "function_type": suggestion.get("function_type", ""),
        })

    with center:
        st.markdown("### Sugerencias")
        st.json({
            "beat.kind": suggestion.get("function_type", ""),
            "beat.shot": suggestion.get("shot", ""),
            "beat.framing": suggestion.get("framing", ""),
            "beat.camera": suggestion.get("camera", ""),
            "beat.transition": suggestion.get("transition", ""),
            "beat.focus": suggestion.get("focus", ""),
            "scene.location": suggestion.get("location", ""),
            "scene.kind": suggestion.get("kind", ""),
            "scene.visual_style": suggestion.get("visual_style", ""),
        })

        if st.button("Aplicar sugerencias al beat", key="tag_apply_suggestions"):
            beat.attrs["kind"] = suggestion.get("function_type", "") or beat.attrs.get("kind", "")
            beat.attrs["shot"] = suggestion.get("shot", "") or beat.attrs.get("shot", "")
            beat.attrs["framing"] = suggestion.get("framing", "") or beat.attrs.get("framing", "")
            beat.attrs["camera"] = suggestion.get("camera", "") or beat.attrs.get("camera", "")
            beat.attrs["focus"] = suggestion.get("focus", "") or beat.attrs.get("focus", "")
            beat.attrs["transition"] = suggestion.get("transition", "") or beat.attrs.get("transition", "")
            if not scene.attrs.get("location"):
                scene.attrs["location"] = suggestion.get("location", "")
            if not scene.attrs.get("kind"):
                scene.attrs["kind"] = suggestion.get("kind", "")
            if not scene.attrs.get("visual_style"):
                scene.attrs["visual_style"] = suggestion.get("visual_style", "")
            st.rerun()

    with right:
        st.markdown("### Editor guiado")

        block.attrs["theme"] = safe_select("block.theme", catalogs["block_themes"], block.attrs.get("theme", ""), "tag_block_theme")
        scene.attrs["kind"] = safe_select("scene.kind", catalogs["scene_kinds"], scene.attrs.get("kind", ""), "tag_scene_kind")
        scene.attrs["location"] = safe_select("scene.location", catalogs["locations"], scene.attrs.get("location", ""), "tag_scene_location")
        scene.attrs["time"] = safe_select("scene.time", catalogs["scene_times"], scene.attrs.get("time", ""), "tag_scene_time")
        scene.attrs["mood"] = safe_select("scene.mood", catalogs["scene_moods"], scene.attrs.get("mood", ""), "tag_scene_mood")
        scene.attrs["audio"] = safe_select("scene.audio", catalogs["scene_audio"], scene.attrs.get("audio", ""), "tag_scene_audio")
        scene.attrs["video"] = safe_select("scene.video", catalogs["scene_video"], scene.attrs.get("video", ""), "tag_scene_video")
        scene.attrs["shot_style"] = safe_select("scene.shot_style", catalogs["scene_shot_styles"], scene.attrs.get("shot_style", ""), "tag_scene_shot_style")
        scene.attrs["visual_style"] = safe_select("scene.visual_style", catalogs["scene_visual_styles"], scene.attrs.get("visual_style", ""), "tag_scene_visual_style")
        scene.attrs["characters"] = safe_multiselect("scene.characters", catalogs["characters"], scene.attrs.get("characters", ""), "tag_scene_characters")
        scene.attrs["props"] = safe_multiselect("scene.props", catalogs["props"], scene.attrs.get("props", ""), "tag_scene_props")

        st.markdown("#### Beat")
        beat.attrs["kind"] = safe_select("beat.kind", catalogs["beat_kinds"], beat.attrs.get("kind", ""), "tag_beat_kind")
        beat.attrs["shot"] = safe_select("beat.shot", catalogs["beat_shots"], beat.attrs.get("shot", ""), "tag_beat_shot")
        beat.attrs["framing"] = safe_select("beat.framing", catalogs["beat_framings"], beat.attrs.get("framing", ""), "tag_beat_framing")
        beat.attrs["camera"] = safe_select("beat.camera", catalogs["beat_cameras"], beat.attrs.get("camera", ""), "tag_beat_camera")
        beat.attrs["focus"] = safe_select("beat.focus", catalogs["characters"] + catalogs["props"], beat.attrs.get("focus", ""), "tag_beat_focus")
        beat.attrs["transition"] = safe_select("beat.transition", catalogs["beat_transitions"], beat.attrs.get("transition", ""), "tag_beat_transition")
        beat.attrs["audio"] = safe_select("beat.audio", catalogs["beat_audio"], beat.attrs.get("audio", ""), "tag_beat_audio")
        beat.attrs["video"] = safe_select("beat.video", catalogs["beat_video"], beat.attrs.get("video", ""), "tag_beat_video")
        beat.attrs["visual_style"] = safe_select("beat.visual_style", catalogs["beat_visual_styles"], beat.attrs.get("visual_style", ""), "tag_beat_visual_style")
        beat.attrs["music_tag"] = safe_select("beat.music_tag", catalogs["beat_music_tags"], beat.attrs.get("music_tag", ""), "tag_beat_music_tag")
        beat.attrs["bgm_level"] = safe_select("beat.bgm_level", catalogs["beat_bgm_levels"], beat.attrs.get("bgm_level", ""), "tag_beat_bgm_level")
        beat.attrs["sfx_level"] = safe_select("beat.sfx_level", catalogs["beat_sfx_levels"], beat.attrs.get("sfx_level", ""), "tag_beat_sfx_level")
        beat.attrs["voice_intensity"] = safe_select("beat.voice_intensity", catalogs["beat_voice_intensity"], beat.attrs.get("voice_intensity", ""), "tag_beat_voice_intensity")

        duration_default = str(estimate_duration_seconds(beat.text))
        beat.attrs["duration"] = st.text_input(
            "beat.duration",
            value=beat.attrs.get("duration", duration_default) or duration_default,
            key="tag_beat_duration",
        )

        speech_rate_val = safe_float(beat.attrs.get("speech_rate", 1.0), 1.0)
        pause_before_val = safe_float(beat.attrs.get("pause_before", 0.0), 0.0)
        pause_after_val = safe_float(beat.attrs.get("pause_after", 0.0), 0.0)

        beat.attrs["speech_rate"] = f"{st.slider('beat.speech_rate', 0.85, 1.10, speech_rate_val, 0.01, key='tag_beat_speech_rate'):.2f}"
        beat.attrs["pause_before"] = f"{st.slider('beat.pause_before', 0.0, 0.5, pause_before_val, 0.01, key='tag_beat_pause_before'):.2f}"
        beat.attrs["pause_after"] = f"{st.slider('beat.pause_after', 0.0, 0.5, pause_after_val, 0.01, key='tag_beat_pause_after'):.2f}"

    st.markdown("---")
    g1, g2 = st.columns(2)
    with g1:
        if st.button("Guardar archivo activo con etiquetas", key="tag_save_book"):
            book_path = get_active_book_path(root)
            ensure_ids(episodes)
            save_text(book_path, book_to_text(episodes))
            st.success(f"Guardado: {book_path}")
    with g2:
        if st.button("Ver preview del beat enriquecido", key="tag_preview_beat"):
            preview_scene = Scene(attrs=dict(scene.attrs), beats=[Beat(attrs=dict(beat.attrs), text=beat.text)])
            preview_block = Block(attrs=dict(block.attrs), scenes=[preview_scene])
            preview_episode = Episode(attrs=dict(ep.attrs), blocks=[preview_block])
            st.code(book_to_text([preview_episode]), language="text")


def tab_script_editor(root: Path):
    st.header("Guion")
    st.markdown(
        """
        <style>
        div.stButton > button {
            padding: 0.14rem 0.30rem;
            min-height: 1.9rem;
            line-height: 1;
            font-size: 0.88rem;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    subtabs = st.tabs(["Segmentación", "Estructura", "Etiquetas"])
    with subtabs[0]:
        tab_segmentation(root)
    with subtabs[1]:
        tab_structure_editor(root)
    with subtabs[2]:
        tab_tag_editor(root)


def tab_inspector(root: Path):
    st.header("Inspector automático")
    selected = select_script_entities(root, "insp")
    if not selected or selected[0] is None:
        st.warning("No hay archivo estructurado activo")
        return

    ep, block, scene, beat, episodes, ep_idx, block_idx, scene_idx, beat_idx = selected
    overrides = load_overrides(root)
    merged_beat_attrs = apply_overrides_to_beat(beat.attrs.get("id", ""), beat.attrs, overrides)

    scene_attrs = dict(scene.attrs)
    scene_attrs["block_kind"] = block.attrs.get("kind", "")
    scene_attrs["_scene_index"] = 0
    scene_attrs["_beat_index"] = 0

    result = inspect_beat(root, beat.text, scene_attrs, merged_beat_attrs)

    left, right = st.columns(2)
    with left:
        st.subheader("Texto")
        st.code(beat.text, language="text")
        st.subheader("Detección")
        st.json({
            "detected_characters": result["detected_characters"],
            "detected_props": result["detected_props"],
            "detected_location": result["detected_location"],
            "focus": result["focus"],
            "speaker": result["speaker"],
        })
        st.subheader("Función dramática")
        st.write(f"**function_type:** `{result['function_type']}`")
        st.json(result["function_scores"])

    with right:
        st.subheader("Dirección")
        st.json({
            "shot": result["shot"],
            "framing": result["framing"],
            "camera": result["camera"],
            "transition": result["transition"],
            "visual_style": result["visual_style"],
        })
        st.subheader("Prompt de movimiento")
        st.code(result["motion_prompt_preview"], language="text")


def import_shot_planner(project_root: Path):
    src_dir = project_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    try:
        from shot_planner import plan_scene_shots
        return plan_scene_shots
    except Exception:
        return None


def build_scene_storyboard(project_root: Path, scene, block, beats, overrides: Dict[str, Dict[str, str]]):
    directions = []
    previous_speaker = ""
    previous_focus = ""
    for i, beat in enumerate(beats):
        scene_attrs = dict(scene.attrs)
        scene_attrs["block_kind"] = block.attrs.get("kind", "")
        scene_attrs["_scene_index"] = 0
        scene_attrs["_beat_index"] = i
        scene_attrs["_previous_speaker"] = previous_speaker
        scene_attrs["_previous_focus"] = previous_focus

        merged_beat_attrs = apply_overrides_to_beat(beat.attrs.get("id", ""), beat.attrs, overrides)
        direction = inspect_beat(project_root=project_root, text=beat.text, scene_attrs=scene_attrs, beat_attrs=merged_beat_attrs)
        direction["beat_id"] = beat.attrs.get("id", "")
        direction["text"] = beat.text
        previous_speaker = direction.get("speaker", "") or previous_speaker
        previous_focus = direction.get("focus", "") or previous_focus
        directions.append(direction)

    plan_scene_shots = import_shot_planner(project_root)
    if plan_scene_shots:
        try:
            directions = plan_scene_shots(directions)
        except Exception:
            pass
    return directions


def storyboard_rows(storyboard: List[Dict[str, str]]):
    rows = []
    for idx, d in enumerate(storyboard, start=1):
        rows.append({
            "beat": idx,
            "id": d.get("beat_id", ""),
            "shot": d.get("shot", ""),
            "framing": d.get("framing", ""),
            "camera": d.get("camera", ""),
            "focus": d.get("focus", ""),
            "speaker": d.get("speaker", ""),
            "transition": d.get("transition", ""),
            "function": d.get("function_type", ""),
            "location": d.get("location", ""),
        })
    return rows


def tab_storyboard(root: Path):
    st.header("Storyboard de escena")
    selected = select_script_entities(root, "story")
    if not selected or selected[0] is None:
        st.warning("No hay archivo estructurado activo")
        return

    ep, block, scene, beat, episodes, ep_idx, block_idx, scene_idx, beat_idx = selected
    overrides = load_overrides(root)
    storyboard = build_scene_storyboard(root, scene, block, scene.beats, overrides)
    rows = storyboard_rows(storyboard)

    st.subheader(f"Escena {scene.attrs.get('id', '')}")
    st.table(rows)

    st.subheader("Timeline visual")
    for r in rows:
        st.markdown(
            f"""**{r["beat"]} — {r["shot"]}**

focus: `{r["focus"]}`  
speaker: `{r["speaker"]}`  
transition: `{r["transition"]}`  
function: `{r["function"]}`  
location: `{r["location"]}`
"""
        )

    st.markdown("---")
    beat_color_field = st.selectbox(
        "Colorear beats por metadato",
        ["kind", "shot", "camera", "focus", "transition", "music_tag", "bgm_level", "speaker"],
        key="story_beat_color_field",
    )
    render_beat_color_map(scene, root, beat_color_field)


def run_render_command(root: Path, episode_id: str, scene_id: str = ""):
    script = root / "src" / "series_studio.py"
    if not script.exists():
        return False, f"No existe {script}"

    book_path = get_active_book_path(root)
    cmd = ["python", str(script), "render", "--book", str(book_path), "--episode", episode_id]
    if scene_id:
        cmd += ["--scene", scene_id]

    try:
        proc = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, timeout=1800)
        output = (proc.stdout or "") + "\n" + (proc.stderr or "")
        return proc.returncode == 0, output.strip()
    except Exception as e:
        return False, str(e)


def tab_render(root: Path):
    st.header("Render")
    selected = select_script_entities(root, "render")
    if not selected or selected[0] is None:
        st.warning("No hay archivo estructurado activo")
        return

    ep, block, scene, beat, episodes, ep_idx, block_idx, scene_idx, beat_idx = selected
    book_path = get_active_book_path(root)

    st.write("Puedes lanzar render de escena desde la interfaz.")
    cmd_preview = f'python src/series_studio.py render --book "{book_path}" --episode {ep.attrs.get("id","")} --scene {scene.attrs.get("id","")}'
    st.code(cmd_preview, language="bash")

    if st.button("Renderizar escena seleccionada", key="render_scene_button"):
        ok, output = run_render_command(root, ep.attrs.get("id", ""), scene.attrs.get("id", ""))
        if ok:
            st.success("Render ejecutado")
        else:
            st.error("El render devolvió error")
        st.text_area("Salida del render", value=output, height=250, key="render_output")


# --------------------------------------------------
# Main
# --------------------------------------------------
def main():
    root = sidebar_project_root()
    sidebar_book_selector(root)
    ensure_minimum_structure(root)

    tabs = st.tabs(["Proyecto", "JSON", "Guion", "Inspector", "Storyboard", "Render"])
    with tabs[0]:
        tab_project(root)
    with tabs[1]:
        tab_json_editor(root)
    with tabs[2]:
        tab_script_editor(root)
    with tabs[3]:
        tab_inspector(root)
    with tabs[4]:
        tab_storyboard(root)
    with tabs[5]:
        tab_render(root)


if __name__ == "__main__":
    main()
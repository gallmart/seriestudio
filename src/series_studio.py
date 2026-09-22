import argparse
import json
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv
from gtts import gTTS
from PIL import Image, ImageOps
from moviepy import AudioFileClip, ColorClip, ImageClip, VideoFileClip, concatenate_videoclips, concatenate_audioclips

from detector_contexto import analyse_beat
from dialogue_parser import parse_dialogue
from director_cinematografico import apply_direction_to_beat, direct_beat, DIRECTOR_DEFAULTS, trim_prompt_for_model

from editor_montaje import build_scene_edit_plan, apply_edit_decisions_to_beats, split_csv

try:
    import fal_client
except ImportError:
    fal_client = None
    
from editor_montaje import build_scene_edit_plan, apply_edit_decisions_to_beats
from scene_state_manager import SceneStateManager

from scene_blocking_engine import SceneBlockingEngine

from moviepy.audio.AudioClip import AudioClip

from audio_engine import generate_audio

# -----------------------------
# Rutas y configuración
# -----------------------------
ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
CONFIG = ROOT / "config"
TEMP = ROOT / "temp"
OUTPUT = ROOT / "output"

for p in [TEMP, OUTPUT]:
    p.mkdir(parents=True, exist_ok=True)

load_dotenv(ROOT / ".env")

FAL_KEY = os.getenv("FAL_KEY", "")
ELEVENLABS_API_KEY = os.getenv("ELEVEN_API_KEY", "")
FAL_DIALOGUE_MODEL = os.getenv("FAL_DIALOGUE_MODEL", "fal-ai/sadtalker")
FAL_SCENE_MODEL = os.getenv("FAL_SCENE_MODEL", "fal-ai/vidu/image-to-video")

CAN_USE_FAL = bool(FAL_KEY and fal_client is not None)
if CAN_USE_FAL:
    os.environ["FAL_KEY"] = FAL_KEY


# -----------------------------
# Utilidades de carga
# -----------------------------
def load_json_file(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


ASSETS_MANIFEST = load_json_file(CONFIG / "assets_manifest.json", {"characters": {}, "locations": {}, "props": {}})
AUDIO_MANIFEST = load_json_file(CONFIG / "audio_manifest.json", {"voices": {}, "sfx": {}, "bgm": {}})
STYLE_RULES = load_json_file(CONFIG / "style_rules.json", {"visual_styles": {}, "factions": {}, "block_kind": {}})
ALIASES = load_json_file(CONFIG / "aliases.json", {})

VOICE_MAP = AUDIO_MANIFEST.get("voices", {})


def slugify(value: str) -> str:
    value = value.strip().lower()
    replacements = str.maketrans("áéíóúüñ", "aeiouun")
    value = value.translate(replacements)
    value = re.sub(r"[^a-z0-9_./-]+", "_", value)
    value = re.sub(r"_+", "_", value)
    return value.strip("_")


def apply_alias(name: str) -> str:
    if not name:
        return name
    return ALIASES.get(name, ALIASES.get(name.strip(), name)).strip()


def normalize_character_name(name: str) -> str:
    name = apply_alias(name)
    mapping = {
        "Tomás": "Tomas",
        "Lucía": "Lucia",
        "Víctor": "Victor",
        "Víctor Andrade": "Andrade",
        "Sun_Tzu_Narrador": "Narrator",
        "Narrador": "Narrator",
    }
    return mapping.get(name, name)


def parse_attrs(raw: str) -> Dict[str, str]:
    return {k: v.strip() for k, v in re.findall(r'(\w+)\s*=\s*"([^"]*)"', raw, flags=re.DOTALL)}



def safe_duration(value: Optional[str], fallback_text: str = "") -> float:
    if value:
        try:
            return float(value)
        except ValueError:
            pass
    words = len(fallback_text.split())
    return max(4.0, math.ceil(words / 2.6) + 1.0)


def split_dialogue(text: str) -> Tuple[Optional[str], str]:
    info = parse_dialogue(text)
    speaker = info.get("speaker")
    if speaker:
        speaker = normalize_character_name(str(speaker))
    spoken = info.get("spoken_text") or text.strip()
    return speaker, spoken


# -----------------------------
# Modelo de datos
# -----------------------------
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


# -----------------------------
# Parser del libro
# -----------------------------
def parse_book(book_path: Path) -> List[Episode]:
    text = book_path.read_text(encoding="utf-8")

    ep_pattern = re.compile(r"\{episode(?P<attrs>.*?)\}(?P<body>.*?)\{/episode\}", re.DOTALL)
    block_pattern = re.compile(r"\{block(?P<attrs>.*?)\}(?P<body>.*?)\{/block\}", re.DOTALL)
    scene_pattern = re.compile(r"\{scene(?P<attrs>.*?)\}(?P<body>.*?)\{/scene\}", re.DOTALL)
    beat_pattern = re.compile(r"\{beat(?P<attrs>.*?)\}(?P<body>.*?)\{/beat\}", re.DOTALL)

    episodes: List[Episode] = []

    for ep_match in ep_pattern.finditer(text):
        ep = Episode(parse_attrs(ep_match.group("attrs")))
        ep_body = ep_match.group("body")

        for block_match in block_pattern.finditer(ep_body):
            block = Block(parse_attrs(block_match.group("attrs")))
            block_body = block_match.group("body")

            for scene_match in scene_pattern.finditer(block_body):
                scene = Scene(parse_attrs(scene_match.group("attrs")))
                scene_body = scene_match.group("body")

                for beat_match in beat_pattern.finditer(scene_body):
                    beat = Beat(parse_attrs(beat_match.group("attrs")), beat_match.group("body").strip())
                    scene.beats.append(beat)

                block.scenes.append(scene)
            ep.blocks.append(block)
        episodes.append(ep)

    return episodes


# -----------------------------
# Reglas cinematográficas
# -----------------------------


def attr_is_true(value, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "si", "sí", "on"}


def continuity_clauses(scene_attrs: Dict[str, str], beat: Beat) -> List[str]:
    clauses = ["preserve cinematic continuity from the previous beat"]

    if attr_is_true(beat.attrs.get("lock_location"), True) and scene_attrs.get("location"):
        clauses.append(f"keep the same location: {scene_attrs.get('location')}")

    if attr_is_true(beat.attrs.get("lock_props"), True) and scene_attrs.get("props"):
        clauses.append(f"keep the same important props: {scene_attrs.get('props')}")

    if attr_is_true(beat.attrs.get("lock_visual_style"), True):
        style = beat.attrs.get("visual_style") or scene_attrs.get("visual_style", "")
        if style:
            clauses.append(f"maintain the same visual style: {style}")

    scene_notes = (scene_attrs.get("continuity_notes") or "").strip()
    beat_notes = (beat.attrs.get("continuity_notes") or "").strip()
    if scene_notes:
        clauses.append(f"scene continuity notes: {scene_notes}")
    if beat_notes:
        clauses.append(f"beat continuity notes: {beat_notes}")

    if scene_attrs.get("characters"):
        clauses.append(f"same character continuity: {scene_attrs.get('characters')}")

    return clauses


def merge_scene_with_block_rules(block: Block, scene: Scene) -> Dict[str, str]:
    merged = dict(scene.attrs)
    block_kind = block.attrs.get("kind", "")
    rule = STYLE_RULES.get("block_kind", {}).get(block_kind, {})
    for key, value in rule.items():
        if key == "scene_kind":
            merged.setdefault("kind", value)
        else:
            merged.setdefault(key, value)
    return merged


def merge_beat_with_scene_rules(scene_attrs: Dict[str, str], beat: Beat) -> Dict[str, str]:
    merged = dict(scene_attrs)
    merged.update(beat.attrs)
    return merged


# -----------------------------
# Validación
# -----------------------------
def validate_episodes(episodes: List[Episode]) -> List[str]:
    errors = []
    for ep in episodes:
        if "id" not in ep.attrs:
            errors.append("Episode sin id")

        for block in ep.blocks:
            if "id" not in block.attrs:
                errors.append(f"Block sin id en episodio {ep.attrs.get('id', '?')}")

            for scene in block.scenes:
                sid = scene.attrs.get("id", "?")
                merged_scene = merge_scene_with_block_rules(block, scene)

                if "kind" not in merged_scene:
                    errors.append(f"Scene {sid} sin kind")
                if "location" not in merged_scene:
                    errors.append(f"Scene {sid} sin location")
                if not scene.beats:
                    errors.append(f"Scene {sid} sin beats")

                for beat in scene.beats:
                    bid = beat.attrs.get("id", "?")
                    merged_beat = merge_beat_with_scene_rules(merged_scene, beat)
                    if "shot" not in merged_beat:
                        # errors.append(f"Beat {bid} sin shot")
                        pass 

    return errors


# -----------------------------
# Plan de producción
# -----------------------------
def build_plan(episodes: List[Episode], target_episode: str = "", target_block: str = "", target_scene: str = "") -> Dict:
    out = {"episodes": []}

    for ep in episodes:
        if target_episode and ep.attrs.get("id") != target_episode:
            continue

        ep_dict = {"id": ep.attrs.get("id"), "title": ep.attrs.get("title", ""), "blocks": []}

        for block in ep.blocks:
            if target_block and block.attrs.get("id") != target_block:
                continue

            block_dict = {
                "id": block.attrs.get("id"),
                "kind": block.attrs.get("kind", ""),
                "scenes": [],
            }

            for scene in block.scenes:
                if target_scene and scene.attrs.get("id") != target_scene:
                    continue

                merged_scene = merge_scene_with_block_rules(block, scene)
                scene_dict = {
                    "id": scene.attrs.get("id"),
                    "kind": merged_scene.get("kind", ""),
                    "location": merged_scene.get("location", ""),
                    "characters": merged_scene.get("characters", ""),
                    "audio": merged_scene.get("audio", ""),
                    "video": merged_scene.get("video", ""),
                    "beats": [],
                }

                for beat in scene.beats:
                    merged_beat = merge_beat_with_scene_rules(merged_scene, beat)
                    speaker, spoken = split_dialogue(beat.text)

                    scene_dict["beats"].append(
                        {
                            "id": beat.attrs.get("id"),
                            "shot": merged_beat.get("shot", ""),
                            "framing": merged_beat.get("framing", ""),
                            "camera": merged_beat.get("camera", ""),
                            "focus": merged_beat.get("focus", ""),
                            "duration": safe_duration(merged_beat.get("duration"), beat.text),
                            "speaker": speaker,
                            "text": spoken if speaker else beat.text.strip(),
                        }
                    )

                block_dict["scenes"].append(scene_dict)

            ep_dict["blocks"].append(block_dict)

        out["episodes"].append(ep_dict)

    return out


# -----------------------------
# Asset manager
# -----------------------------
class AssetManager:
    def __init__(self, assets_root: Path):
        self.assets_root = assets_root
        self.characters_root = assets_root / "characters"
        self.locations_root = assets_root / "locations"
        self.props_root = assets_root / "props"

    def _resolve_manifest_path(self, manifest_value: str) -> Optional[Path]:
        p = Path(manifest_value)
        if p.is_absolute() and p.exists():
            return p
        candidate = ROOT / manifest_value
        if candidate.exists():
            return candidate
        return None

    def _find_by_name(self, folder: Path, key: str) -> Optional[Path]:
        key_slug = slugify(key)
        candidates = list(folder.glob(f"{key_slug}.*"))
        return candidates[0] if candidates else None

    def character(self, name: str) -> Optional[Path]:
        canonical = normalize_character_name(name)
        manifest_path = ASSETS_MANIFEST.get("characters", {}).get(canonical) or ASSETS_MANIFEST.get("characters", {}).get(apply_alias(canonical))
        resolved = self._resolve_manifest_path(manifest_path) if manifest_path else None
        return resolved or self._find_by_name(self.characters_root, canonical)

    def location(self, name: str) -> Optional[Path]:
        canonical = apply_alias(name)
        manifest_path = ASSETS_MANIFEST.get("locations", {}).get(canonical)
        resolved = self._resolve_manifest_path(manifest_path) if manifest_path else None
        return resolved or self._find_by_name(self.locations_root, canonical)

    def prop(self, name: str) -> Optional[Path]:
        canonical = apply_alias(name)
        manifest_path = ASSETS_MANIFEST.get("props", {}).get(canonical)
        resolved = self._resolve_manifest_path(manifest_path) if manifest_path else None
        return resolved or self._find_by_name(self.props_root, canonical)


# -----------------------------
# Composición de imagen
# -----------------------------
def _open_rgba(path: Path, size=None):
    img = Image.open(path).convert("RGBA")
    if size:
        img.thumbnail(size, Image.LANCZOS)
    return img


def _fit_bg(path: Path) -> Image.Image:
    bg = _open_rgba(path)
    return ImageOps.fit(bg, (1920, 1080), method=Image.LANCZOS)


def compose_beat_image(
    asset_mgr: AssetManager,
    scene_attrs: Dict[str, str],
    beat: Beat,
    out_path: Path,
    scene_state_manager=None,
    scene_blocking=None,
) -> Path:
    location_key = scene_attrs.get("location", "")
    focus_items = split_csv(beat.attrs.get("focus", "") or scene_attrs.get("focus", ""))
    framing = beat.attrs.get("framing", scene_attrs.get("framing", "medium"))
    shot = beat.attrs.get("shot", "")
    camera = beat.attrs.get("camera", "")
    visual_style = scene_attrs.get("visual_style", "")
    transition = beat.attrs.get("transition", "")
    characters = [normalize_character_name(c) for c in split_csv(scene_attrs.get("characters", ""))]
    props = split_csv(scene_attrs.get("props", ""))

    current_speaker = normalize_character_name(scene_attrs.get("_current_render_speaker", ""))
    previous_speaker = normalize_character_name(scene_attrs.get("_previous_render_speaker", ""))

    # -------------------------------------------------
    # 1) Fondo base
    # -------------------------------------------------
    bg_path = asset_mgr.location(location_key) or asset_mgr.location("stadium_exterior")
    canvas = _fit_bg(bg_path) if bg_path and bg_path.exists() else Image.new("RGBA", (1920, 1080), (16, 18, 24, 255))

    # -------------------------------------------------
    # 2) Sujetos prioritarios
    # -------------------------------------------------
    focused_characters = [normalize_character_name(i) for i in focus_items if asset_mgr.character(i)]
    focused_props = [i for i in focus_items if asset_mgr.prop(i)]

    # prioridad 1: scene_blocking
    if scene_blocking is not None:
        suggested = scene_blocking.suggest_characters_for_shot(
            shot=shot,
            focus=(focus_items[0] if focus_items else ""),
            current_speaker=current_speaker,
            previous_speaker=previous_speaker,
            detected_characters=characters,
        )
        suggested = [normalize_character_name(c) for c in suggested if c and asset_mgr.character(c)]
        if suggested:
            focused_characters = suggested

    # prioridad 2: lógica local si no hay blocking suficiente
    if not focused_characters:
        if shot == "over_the_shoulder":
            if current_speaker and previous_speaker and current_speaker != previous_speaker:
                focused_characters = [previous_speaker, current_speaker]
            elif current_speaker and len(characters) >= 2:
                other_chars = [c for c in characters if c != current_speaker]
                if other_chars:
                    focused_characters = [other_chars[0], current_speaker]
                else:
                    focused_characters = [current_speaker]
            elif len(characters) >= 2:
                focused_characters = characters[:2]
            elif characters:
                focused_characters = [characters[0]]

        elif shot == "medium_two_shot" or framing == "two_shot":
            if current_speaker and len(characters) >= 2:
                other_chars = [c for c in characters if c != current_speaker]
                if other_chars:
                    focused_characters = [current_speaker, other_chars[0]]
                else:
                    focused_characters = characters[:2]
            else:
                focused_characters = characters[:2]

        elif shot == "close_up" or framing == "close_up":
            if current_speaker:
                focused_characters = [current_speaker]
            elif characters:
                focused_characters = [characters[0]]

        else:
            if current_speaker:
                focused_characters = [current_speaker]
            elif characters:
                focused_characters = [characters[0]]

    if not focused_props:
        focused_props = [p for p in props if asset_mgr.prop(p)]

    # -------------------------------------------------
    # 3) Caso especial: insert
    # -------------------------------------------------
    if shot == "insert" and focused_props:
        prop_name = focused_props[0]
        prop_path = asset_mgr.prop(prop_name)

        if prop_path:
            overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 90))
            canvas = Image.alpha_composite(canvas, overlay)

            prop_img = _open_rgba(prop_path, size=(900, 900))
            prop_img = ImageOps.contain(prop_img, (900, 900), method=Image.LANCZOS)

            px = (1920 - prop_img.width) // 2
            py = (1080 - prop_img.height) // 2
            canvas.alpha_composite(prop_img, (px, py))

            if transition in ("fade", "fade_in", "dissolve"):
                fade_overlay = Image.new("RGBA", canvas.size, (255, 255, 255, 18))
                canvas = Image.alpha_composite(canvas, fade_overlay)

            out_path.parent.mkdir(parents=True, exist_ok=True)
            canvas.convert("RGB").save(out_path, quality=95)
            return out_path

    # -------------------------------------------------
    # 4) Layouts por tipo de plano
    # -------------------------------------------------
    placements = []

    if shot == "wide_establishing":
        if focused_characters:
            placements = [(760, 300, (380, 560), 0.45)]
        else:
            placements = []

    elif shot == "close_up" or framing == "close_up":
        if focused_characters:
            placements = [(500, 10, (1080, 1080), 0.14)]

    elif shot == "medium_two_shot" or framing == "two_shot":
        if len(focused_characters) >= 2:
            # si hay blocking, respetar eje visual
            if scene_blocking is not None:
                left_char = scene_blocking.get_left_character()
                right_char = scene_blocking.get_right_character()
                ordered = []
                if left_char in focused_characters:
                    ordered.append(left_char)
                if right_char in focused_characters and right_char not in ordered:
                    ordered.append(right_char)
                for c in focused_characters:
                    if c not in ordered:
                        ordered.append(c)
                focused_characters = ordered[:2]

            placements = [
                (140, 140, (720, 860), 0.35),
                (1060, 140, (720, 860), 0.35),
            ]
        elif len(focused_characters) == 1:
            placements = [(650, 120, (820, 880), 0.35)]

    elif shot == "over_the_shoulder":
        if len(focused_characters) >= 2:
            placements = [
                (20, 140, (820, 980), 0.42),   # foreground shoulder
                (980, 120, (720, 860), 0.18),  # background subject
            ]
        elif len(focused_characters) == 1:
            placements = [(620, 120, (860, 920), 0.35)]

    elif shot == "tracking":
        if len(focused_characters) >= 2:
            placements = [
                (220, 160, (650, 820), 0.35),
                (1020, 120, (700, 860), 0.35),
            ]
        elif focused_characters:
            placements = [(620, 120, (860, 900), 0.35)]

    elif framing == "full_body":
        if focused_characters:
            placements = [(560, 70, (820, 990), 0.5)]

    else:
        if len(focused_characters) >= 2:
            placements = [
                (180, 150, (680, 840), 0.35),
                (1060, 150, (680, 840), 0.35),
            ]
        elif focused_characters:
            placements = [(620, 120, (820, 900), 0.35)]

    # -------------------------------------------------
    # 5) Pintar personajes
    # -------------------------------------------------
    for idx, char_name in enumerate(focused_characters[: len(placements)]):
        char_path = asset_mgr.character(char_name)
        if not char_path:
            continue

        x, y, size, centering_y = placements[idx]
        char_img = _open_rgba(char_path, size=size)

        if shot in ("close_up", "over_the_shoulder") or framing == "close_up":
            char_img = ImageOps.fit(
                char_img,
                size,
                method=Image.LANCZOS,
                centering=(0.5, centering_y),
            )
        else:
            char_img = ImageOps.contain(char_img, size, method=Image.LANCZOS)

        if shot == "over_the_shoulder":
            if idx == 0:
                # foreground shoulder silhouette
                shoulder_overlay = Image.new("RGBA", char_img.size, (0, 0, 0, 80))
                char_img = Image.alpha_composite(char_img, shoulder_overlay)
            elif idx == 1:
                # background subject slightly clearer
                clarity_overlay = Image.new("RGBA", char_img.size, (255, 255, 255, 8))
                char_img = Image.alpha_composite(char_img, clarity_overlay)

        if shot == "close_up" and current_speaker and char_name == current_speaker:
            speaker_overlay = Image.new("RGBA", char_img.size, (255, 255, 255, 6))
            char_img = Image.alpha_composite(char_img, speaker_overlay)

        canvas.alpha_composite(char_img, (x, y))

    # -------------------------------------------------
    # 6) Props secundarios
    # -------------------------------------------------
    secondary_props = []
    for item in focused_props:
        if item not in secondary_props:
            secondary_props.append(item)
    for item in props:
        if item not in secondary_props and asset_mgr.prop(item):
            secondary_props.append(item)

    if shot != "insert":
        prop_positions = []

        if shot == "wide_establishing":
            prop_positions = [(1500, 790, (160, 160))]
        elif shot == "close_up":
            prop_positions = [(1470, 760, (210, 210))]
        elif shot == "medium_two_shot":
            prop_positions = [(860, 805, (170, 170))]
        elif shot == "over_the_shoulder":
            prop_positions = [(1500, 780, (180, 180))]
        else:
            prop_positions = [
                (70, 760, (220, 220)),
                (1560, 760, (220, 220)),
                (860, 800, (170, 170)),
            ]

        for idx, prop_name in enumerate(secondary_props[: len(prop_positions)]):
            prop_path = asset_mgr.prop(prop_name)
            if not prop_path:
                continue
            x, y, size = prop_positions[idx]
            prop_img = _open_rgba(prop_path, size=size)
            prop_img = ImageOps.contain(prop_img, size, method=Image.LANCZOS)
            canvas.alpha_composite(prop_img, (x, y))

    # -------------------------------------------------
    # 7) Promover foco en blocking
    # -------------------------------------------------
    if scene_blocking is not None and beat.attrs.get("focus"):
        scene_blocking.promote_focus(normalize_character_name(beat.attrs.get("focus", "")))

    # -------------------------------------------------
    # 8) Tratamiento visual según estilo
    # -------------------------------------------------
    overlay_alpha = 34
    overlay_color = (0, 0, 0, overlay_alpha)

    if shot == "wide_establishing":
        overlay_color = (0, 0, 0, 20)
    elif shot == "close_up":
        overlay_color = (0, 0, 0, 48)
    elif shot == "insert":
        overlay_color = (0, 0, 0, 70)
    elif shot == "over_the_shoulder":
        overlay_color = (0, 0, 0, 40)

    if visual_style == "datos":
        overlay_color = (10, 30, 60, max(overlay_color[3], 38))
    elif visual_style == "tradicion":
        overlay_color = (35, 18, 10, max(overlay_color[3], 28))
    elif visual_style == "choque_mundos":
        overlay_color = (20, 20, 30, max(overlay_color[3], 42))
    elif visual_style == "sumi_e":
        overlay_color = (0, 0, 0, max(overlay_color[3], 50))

    overlay = Image.new("RGBA", canvas.size, overlay_color)
    canvas = Image.alpha_composite(canvas, overlay)

    # -------------------------------------------------
    # 9) Efecto por cámara
    # -------------------------------------------------
    if camera in ("tracking_right", "tracking_left"):
        side_overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        band = Image.new("RGBA", (420, 1080), (0, 0, 0, 28))

        if camera == "tracking_right":
            side_overlay.alpha_composite(band, (0, 0))
        else:
            side_overlay.alpha_composite(band, (1500, 0))

        canvas = Image.alpha_composite(canvas, side_overlay)

    elif camera == "static_subtle_push":
        center_glow = Image.new("RGBA", canvas.size, (255, 255, 255, 0))
        glow = Image.new("RGBA", (900, 600), (255, 255, 255, 14))
        center_glow.alpha_composite(glow, (510, 240))
        canvas = Image.alpha_composite(canvas, center_glow)

    # -------------------------------------------------
    # 10) Efecto por transición
    # -------------------------------------------------
    if transition in ("fade", "fade_in"):
        trans_overlay = Image.new("RGBA", canvas.size, (255, 255, 255, 12))
        canvas = Image.alpha_composite(canvas, trans_overlay)
    elif transition == "dissolve":
        trans_overlay = Image.new("RGBA", canvas.size, (220, 220, 220, 10))
        canvas = Image.alpha_composite(canvas, trans_overlay)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(out_path, quality=95)
    return out_path
# -----------------------------
# Audio
# -----------------------------
def elevenlabs_tts(text: str, voice_id: str, out_path: Path) -> Optional[Path]:
    if not ELEVENLABS_API_KEY or not voice_id:
        return None

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
    }

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=120)
        if r.status_code != 200:
            return None
        out_path.write_bytes(r.content)
        return out_path
    except Exception:
        return None


def fallback_gtts(text: str, out_path: Path) -> Optional[Path]:
    try:
        tts = gTTS(text=text, lang="es")
        tts.save(str(out_path))
        return out_path
    except Exception:
        return None


# def generate_audio(text: str, speaker: Optional[str], beat_id: str, beat_attrs: Optional[Dict[str, str]] = None) -> Tuple[Optional[Path], float]:
#     out_path = TEMP / f"{beat_id}.mp3"
#     canonical_speaker = normalize_character_name(speaker or "Narrator")
#     voice_id = VOICE_MAP.get(canonical_speaker, "")
# 
#     beat_attrs = beat_attrs or {}
# 
#     # Por ahora speech_rate/voice_intensity quedan como metadatos
#     # porque ElevenLabs no siempre expone un control directo simple aquí.
#     # Las pausas sí las aplicamos físicamente al audio final.
#     path = elevenlabs_tts(text, voice_id, out_path) or fallback_gtts(text, out_path)
# 
#     if not path:
#         return None, safe_duration(None, text)
# 
#     final_path, duration = apply_audio_plan_to_file(path, beat_id, beat_attrs)
#     return final_path, duration

def make_silence_clip(duration: float, fps: int = 44100) -> AudioClip:
    duration = max(0.0, float(duration))
    return AudioClip(lambda t: 0, duration=duration, fps=fps)


def add_leading_trailing_silence(audio_path: Path, pause_before: float, pause_after: float, out_path: Path) -> Path:
    """
    Inserta silencio antes y después del audio.
    """
    source = AudioFileClip(str(audio_path))

    clips = []
    if pause_before and pause_before > 0:
        clips.append(make_silence_clip(pause_before))
    clips.append(source)
    if pause_after and pause_after > 0:
        clips.append(make_silence_clip(pause_after))

    final = concatenate_audioclips(clips)
    final.write_audiofile(str(out_path), fps=44100, logger=None)

    final.close()
    source.close()
    for c in clips:
        try:
            c.close()
        except Exception:
            pass

    return out_path


def apply_audio_plan_to_file(audio_path: Path, beat_id: str, beat_attrs: Dict[str, str]) -> Tuple[Path, float]:
    """
    Aplica pausas antes/después y devuelve nueva ruta + duración.
    """
    pause_before = float(beat_attrs.get("pause_before", "0") or 0)
    pause_after = float(beat_attrs.get("pause_after", "0") or 0)

    if pause_before <= 0 and pause_after <= 0:
        audio = AudioFileClip(str(audio_path))
        duration = audio.duration
        audio.close()
        return audio_path, duration

    padded_path = TEMP / f"{beat_id}_padded.mp3"
    add_leading_trailing_silence(audio_path, pause_before, pause_after, padded_path)

    audio = AudioFileClip(str(padded_path))
    duration = audio.duration
    audio.close()

    return padded_path, duration
    
# -----------------------------
# Vídeo IA
# -----------------------------
def _upload_file(path: Path) -> str:
    return fal_client.upload_file(str(path))

def generate_fal_video(
    start_image_path: Path,
    audio_path: Optional[Path],
    mode: str,
    out_path: Path,
    motion_prompt: str = "",
    end_image_path: Optional[Path] = None,
) -> Optional[Path]:
    if not CAN_USE_FAL:
        return None

    try:
        start_img_url = _upload_file(start_image_path)

        if mode == "lip_sync" and audio_path:
            aud_url = _upload_file(audio_path)
            result = fal_client.subscribe(
                FAL_DIALOGUE_MODEL,
                arguments={
                    "source_image_url": start_img_url,
                    "driven_audio_url": aud_url,
                },
            )
            video_url = result.get("video_url") or result.get("video", {}).get("url")
        else:
            arguments = {
                "image_url": start_img_url,
                "prompt": motion_prompt or "cinematic camera movement, subtle motion, dramatic lighting, premium animation shot",
            }

            if end_image_path is not None:
                end_img_url = _upload_file(end_image_path)
                arguments["end_image_url"] = end_img_url

            result = fal_client.subscribe(
                FAL_SCENE_MODEL,
                arguments=arguments,
            )
            video_url = result.get("video_url") or result.get("video", {}).get("url")

        if not video_url:
            return None

        r = requests.get(video_url, timeout=300)
        r.raise_for_status()
        out_path.write_bytes(r.content)
        return out_path
    except Exception:
        return None

def motion_prompt_for_beat(scene_attrs: Dict[str, str], beat: Beat) -> str:
    """
    Construye el prompt de movimiento para Fal a partir de:
    - atributos de escena
    - atributos del beat
    - reglas configurables en director_defaults.json
    """

    prompt_rules = DIRECTOR_DEFAULTS.get("prompt_rules", {})

    shot_map = prompt_rules.get("shot_descriptions", {})
    framing_map = prompt_rules.get("framing_descriptions", {})
    camera_map = prompt_rules.get("camera_descriptions", {})
    transition_map = prompt_rules.get("transition_descriptions", {})
    quality_tags = prompt_rules.get("quality_tags", [])

    shot = beat.attrs.get("shot", "")
    framing = beat.attrs.get("framing", "")
    camera = beat.attrs.get("camera", "")
    transition = beat.attrs.get("transition", "")
    focus = beat.attrs.get("focus", "") or scene_attrs.get("focus", "")
    mood = scene_attrs.get("mood", "dramatic")
    visual_style = scene_attrs.get("visual_style", "")
    location = scene_attrs.get("location", "")
    characters = scene_attrs.get("characters", "")
    props = scene_attrs.get("props", "")
    scene_kind = scene_attrs.get("kind", "")

    raw_text = beat.text.strip()
    speaker, spoken = split_dialogue(raw_text)
    content_text = spoken if speaker else raw_text

    parts = []

    # -------------------------------------------------
    # 1) Lenguaje cinematográfico estructural
    # -------------------------------------------------
    if shot and shot in shot_map:
        parts.append(shot_map[shot])
    elif shot:
        parts.append(shot)

    if framing and framing in framing_map:
        parts.append(framing_map[framing])
    elif framing:
        parts.append(framing)

    if camera and camera in camera_map:
        parts.append(camera_map[camera])
    elif camera:
        parts.append(camera)

    if transition and transition in transition_map:
        parts.append(transition_map[transition])

    # -------------------------------------------------
    # 2) Contexto dramático
    # -------------------------------------------------
    if mood:
        parts.append(f"{mood} mood")

    if scene_kind:
        parts.append(f"{scene_kind} scene")

    if visual_style:
        parts.append(f"{visual_style} visual style")

    if location:
        parts.append(f"set in {location}")

    if focus:
        parts.append(f"focus on {focus}")

    if characters:
        parts.append(f"characters present: {characters}")

    if props:
        parts.append(f"visible props: {props}")

    parts.extend(continuity_clauses(scene_attrs, beat))

    # -------------------------------------------------
    # 3) Texto dramático
    # -------------------------------------------------
    if speaker:
        parts.append(f"spoken dialogue by {speaker}: {content_text}")
    elif content_text:
        parts.append(f"dramatic action or narration: {content_text}")

    # -------------------------------------------------
    # 4) Reglas finas por tipo de plano
    # -------------------------------------------------
    if shot == "close_up":
        parts.append("clear emotional readability in the face")
        parts.append("facial expression is the main priority")

    elif shot == "over_the_shoulder":
        parts.append("foreground shoulder silhouette, background subject in clear focus")
        parts.append("strong conversational perspective")

    elif shot == "medium_two_shot":
        parts.append("balanced staging between two characters")
        parts.append("clear conversational geometry")

    elif shot == "insert":
        parts.append("object is centered and visually dominant")
        parts.append("background remains secondary")

    elif shot == "wide_establishing":
        parts.append("environment and atmosphere are the main priority")
        parts.append("characters remain secondary to the setting")

    elif shot == "tracking":
        parts.append("smooth directional movement with readable body motion")

    # -------------------------------------------------
    # 5) Reglas finas por estilo visual
    # -------------------------------------------------
    if visual_style == "datos":
        parts.append("cold digital atmosphere")
        parts.append("clean analytical composition")

    elif visual_style == "tradicion":
        parts.append("warm human drama")
        parts.append("grounded emotional realism")

    elif visual_style == "choque_mundos":
        parts.append("contrast between cold strategic logic and warm human tension")

    elif visual_style == "sumi_e":
        parts.append("minimalist poetic feeling")
        parts.append("strong graphic contrast and contemplative rhythm")

    # -------------------------------------------------
    # 6) Calidad global
    # -------------------------------------------------
    parts.extend(quality_tags)

    # ------------------------------------
    # ajustar prompt al modelo de vídeo
    # ------------------------------------
    model_name = scene_attrs.get("video_model", "fal")
    parts = trim_prompt_for_model(parts, model_name)

    return ", ".join([p for p in parts if p])


# -----------------------------
# Render por beat
# -----------------------------
def render_static_video(image_path: Path, audio_path: Optional[Path], duration: float, out_path: Path, fps: int = 24) -> Path:
    if image_path and image_path.exists():
        clip = ImageClip(str(image_path)).with_duration(duration)
    else:
        clip = ColorClip(size=(1920, 1080), color=(16, 18, 24)).with_duration(duration)

    aud_clip = None
    if audio_path and audio_path.exists():
        aud_clip = AudioFileClip(str(audio_path))
        clip = clip.with_audio(aud_clip)

    clip.write_videofile(str(out_path), fps=fps, codec="libx264", audio_codec="aac", logger=None)

    if aud_clip:
        aud_clip.close()
    clip.close()
    return out_path

def continuity_tags(value: str) -> List[str]:
    return [x.strip().upper() for x in (value or "").split(",") if x.strip()]


def apply_continuity_to_video_prompt(base_prompt: str, continuity_value: str) -> str:
    tags = continuity_tags(continuity_value)
    parts = [base_prompt]

    if "CONT_LOCATION" in tags:
        parts.append("same location")
    if "CONT_CAMERA" in tags:
        parts.append("same camera angle")
    if "CONT_VISUAL" in tags:
        parts.append("same composition and lighting")
    if "CONT_TIME" in tags:
        parts.append("continuous time")
    if "CONT_CHARACTER" in tags:
        parts.append("same main subject")
    if "CONT_EMOTION" in tags:
        parts.append("same emotional tone")
    if "CONT_VOICE" in tags:
        parts.append("same voice energy")
    if "CONT_AUDIO" in tags:
        parts.append("same ambient sound feeling")

    if "SHIFT_CHARACTER" in tags:
        parts.append("subject gradually changes")
    if "SHIFT_VISUAL" in tags:
        parts.append("visual appearance gradually changes")
    if "SHIFT_LOCATION" in tags:
        parts.append("location changes")
    if "SHIFT_EMOTION" in tags:
        parts.append("emotional tone changes")
    if "SHIFT_VOICE" in tags:
        parts.append("voice character changes")
    if "SHIFT_AUDIO" in tags:
        parts.append("sound atmosphere changes")

    if "SOFT_TRANSITION" in tags:
        parts.append("soft cinematic transition")
    if "HARD_CUT" in tags:
        parts.append("hard cut feeling")
    if "TIME_JUMP" in tags:
        parts.append("clear time jump")

    return ", ".join(parts)


def auto_video_prompt_for_beat(scene_attrs: Dict[str, str], beat, next_beat=None) -> str:
    explicit_prompt = (beat.attrs.get("video_prompt", "") or "").strip()
    if explicit_prompt:
        return explicit_prompt

    visual_transition = (beat.attrs.get("visual_transition", "NONE") or "NONE").strip().upper()
    continuity_value = beat.attrs.get("continuity_notes", scene_attrs.get("continuity_notes", ""))

    base_prompt = motion_prompt_for_beat(scene_attrs, beat)

    if visual_transition == "MORPH":
        base_prompt = (
            f"{base_prompt}, smooth visual transformation, no hard cut, continuous metamorphosis"
        )
    elif visual_transition == "SOFT_TRANSITION":
        base_prompt = f"{base_prompt}, soft cinematic transition"
    elif visual_transition == "DISSOLVE":
        base_prompt = f"{base_prompt}, dissolve transition"
    elif visual_transition == "HARD_CUT":
        base_prompt = f"{base_prompt}, hard cut feeling"

    return apply_continuity_to_video_prompt(base_prompt, continuity_value)


def should_chain_from_previous(prev_beat, current_beat) -> bool:
    prev_tags = continuity_tags(prev_beat.attrs.get("continuity_notes", ""))
    curr_tags = continuity_tags(current_beat.attrs.get("continuity_notes", ""))

    chain_tags = {"CONTINUOUS", "CONT_LOCATION", "CONT_CAMERA", "CONT_VISUAL", "CONT_TIME"}

    return bool(set(prev_tags) & chain_tags) or bool(set(curr_tags) & chain_tags)


def resolve_reference_image(asset_mgr: AssetManager, value: str) -> Optional[Path]:
    ref = (value or "").strip()
    if not ref:
        return None

    as_location = asset_mgr.location(ref)
    if as_location and as_location.exists():
        return as_location

    candidate = Path(ref)
    if candidate.is_absolute() and candidate.exists():
        return candidate

    rooted = ROOT / ref
    if rooted.exists():
        return rooted

    return None


def render_beat(
    asset_mgr: AssetManager,
    scene_attrs: Dict[str, str],
    beat: Beat,
    scene_index: int = 0,
    beat_index: int = 0,
    scene_state_manager=None,
    scene_blocking=None,
    scene_beats: Optional[List[Beat]] = None,
) -> Path:
    # -------------------------------------------------
    # 1) Detección básica de contexto desde el texto
    # -------------------------------------------------
    context = analyse_beat(beat.text)

    if not scene_attrs.get("characters") and context.get("characters"):
        scene_attrs["characters"] = ",".join(context["characters"])

    if not scene_attrs.get("location") and context.get("location"):
        scene_attrs["location"] = context["location"]

    if not scene_attrs.get("props") and context.get("props"):
        scene_attrs["props"] = ",".join(context["props"])

    if not beat.attrs.get("focus") and context.get("focus"):
        beat.attrs["focus"] = context["focus"]

    # -------------------------------------------------
    # 2) Dirección cinematográfica automática
    # -------------------------------------------------
    block_kind = scene_attrs.get("block_kind", "")

    beat, scene_attrs, direction = apply_direction_to_beat(
        beat=beat,
        scene_attrs=scene_attrs,
        block_kind=block_kind,
        scene_index=scene_index,
        beat_index=beat_index,
        scene_state_manager=scene_state_manager,
    )

    # -------------------------------------------------
    # 3) Texto y voz
    # -------------------------------------------------
    beat_id = beat.attrs.get("id", "beat")
    speaker, spoken = split_dialogue(beat.text)
   
    current_speaker = speaker or ""
    previous_speaker = scene_attrs.get("_previous_render_speaker", "")

    scene_attrs["_current_render_speaker"] = current_speaker
    if current_speaker:
        scene_attrs["_previous_render_speaker"] = current_speaker
    
    text_for_audio = spoken if speaker else beat.text.strip()

    merged_beat = merge_beat_with_scene_rules(scene_attrs, beat)
    
    if merged_beat.get("speech_rate"):
        print(
            f"[AUDIO] {beat_id} "
            f"rate={merged_beat.get('speech_rate')} "
            f"pause_before={merged_beat.get('pause_before')} "
            f"pause_after={merged_beat.get('pause_after')} "
            f"voice_intensity={merged_beat.get('voice_intensity')} "
            f"bgm={merged_beat.get('bgm_level')} "
            f"sfx={merged_beat.get('sfx_level')}"
        )
    
    audio_mode = merged_beat.get("audio", "narrator")
    video_mode = merged_beat.get("video", "environment_animation")

    # -------------------------------------------------
    # 4) Resolver imagen inicial / final
    # -------------------------------------------------
    start_ref = (beat.attrs.get("ref_image_start", "") or "").strip()
    end_ref = (beat.attrs.get("ref_image_end", "") or "").strip()
    reference_mode = (beat.attrs.get("reference_mode", "AUTO") or "AUTO").strip()
    visual_transition = (beat.attrs.get("visual_transition", "NONE") or "NONE").strip()
    video_prompt_override = (beat.attrs.get("video_prompt", "") or "").strip()

    start_image_path = None
    end_image_path = None

    if reference_mode in ("START_IMAGE", "START_END_IMAGES") and start_ref:
        start_image_path = resolve_reference_image(asset_mgr, start_ref)

    if reference_mode == "START_END_IMAGES" and end_ref:
        end_image_path = resolve_reference_image(asset_mgr, end_ref)

    prev_beat = None
    if scene_beats is not None and 0 < beat_index < len(scene_beats):
        prev_beat = scene_beats[beat_index - 1]

    if start_image_path is None and reference_mode == "AUTO":
        if prev_beat is not None and should_chain_from_previous(prev_beat, beat):
            prev_end_ref = (prev_beat.attrs.get("ref_image_end", "") or "").strip()
            prev_start_ref = (prev_beat.attrs.get("ref_image_start", "") or "").strip()

            if prev_end_ref:
                start_image_path = resolve_reference_image(asset_mgr, prev_end_ref)

            if start_image_path is None and prev_start_ref:
                start_image_path = resolve_reference_image(asset_mgr, prev_start_ref)

        if start_image_path is None:
            previous_visual_anchor = (scene_attrs.get("_previous_visual_anchor", "") or "").strip()
            if previous_visual_anchor:
                candidate = Path(previous_visual_anchor)
                if candidate.exists():
                    start_image_path = candidate

    if start_image_path is None:
        start_image_path = TEMP / f"{beat_id}.jpg"
        compose_beat_image(
            asset_mgr,
            scene_attrs,
            beat,
            start_image_path,
            scene_state_manager=scene_state_manager,
            scene_blocking=scene_blocking,
        )
        
    # -------------------------------------------------
    # 5) Decidir voz
    # -------------------------------------------------
    speaker_for_voice = None

    if audio_mode in ("narrator", "narrator_plus_dialogue") and not speaker:
        speaker_for_voice = "Narrator"
    elif speaker:
        speaker_for_voice = speaker
    elif audio_mode == "single_voice":
        chars = [normalize_character_name(c) for c in split_csv(scene_attrs.get("characters", ""))]
        speaker_for_voice = chars[0] if chars else "Narrator"

    # -------------------------------------------------
    # 6) Generar audio
    # -------------------------------------------------
    audio_path = None
        
    duration = safe_duration(merged_beat.get("duration"), text_for_audio)

    if audio_mode != "ambient_only":
        audio_path, measured = generate_audio(
            text_for_audio,
            speaker_for_voice,
            beat_id,
            beat_attrs=merged_beat,
            scene_attrs=scene_attrs,
        )
        duration = max(duration, measured)

    # -------------------------------------------------
    # 7) Generar vídeo
    # -------------------------------------------------
    out_path = TEMP / f"{beat_id}.mp4"
    fal_mode = "lip_sync" if video_mode == "lip_sync" and audio_path else "scene"

    clip = generate_fal_video(
        start_image_path=start_image_path,
        audio_path=audio_path,
        mode=fal_mode,
        out_path=out_path,
        motion_prompt=auto_video_prompt_for_beat(scene_attrs, beat),
        end_image_path=end_image_path,
    )

    current_visual_anchor = end_image_path or start_image_path
    
    # -------------------------------------------------
    # 8) Si Fal devuelve vídeo sin audio en modo escena,
    #    se lo incrustamos después
    # -------------------------------------------------
    if clip:
        if audio_path and video_mode != "lip_sync":
            v = VideoFileClip(str(out_path))
            a = AudioFileClip(str(audio_path))
            v = v.with_audio(a)

            final_out = TEMP / f"{beat_id}_audio.mp4"
            v.write_videofile(
                str(final_out),
                fps=24,
                codec="libx264",
                audio_codec="aac",
                logger=None,
            )
            v.close()
            a.close()

            out_path.unlink(missing_ok=True)
            final_out.rename(out_path)

        scene_attrs["_previous_visual_anchor"] = str(current_visual_anchor)
        return out_path

    # -------------------------------------------------
    # 9) Fallback estático
    # -------------------------------------------------
    scene_attrs["_previous_visual_anchor"] = str(current_visual_anchor)
    return render_static_video(start_image_path, audio_path, duration, out_path)

# -----------------------------
# Render episodio / bloque / escena
# -----------------------------
def iter_target(episodes: List[Episode], episode_id: str = "", block_id: str = "", scene_id: str = ""):
    for ep in episodes:
        if episode_id and ep.attrs.get("id") != episode_id:
            continue
        for block in ep.blocks:
            if block_id and block.attrs.get("id") != block_id:
                continue
            for scene in block.scenes:
                if scene_id and scene.attrs.get("id") != scene_id:
                    continue
                yield ep, block, scene


def render_selection(book_path: Path, episode_id: str = "", block_id: str = "", scene_id: str = "") -> Path:
    episodes = parse_book(book_path)
    errors = validate_episodes(episodes)
    if errors:
        raise ValueError("Guion inválido:\n- " + "\n- ".join(errors))

    asset_mgr = AssetManager(ASSETS)
    clips = []
    chosen_episode = episode_id or "selection"

    global_scene_index = 0

    for ep in episodes:
        if episode_id and ep.attrs.get("id") != episode_id:
            continue

        chosen_episode = ep.attrs.get("id", chosen_episode)

        for block in ep.blocks:
            if block_id and block.attrs.get("id") != block_id:
                continue

            for scene in block.scenes:
                if scene_id and scene.attrs.get("id") != scene_id:
                    continue

                merged_scene = merge_scene_with_block_rules(block, scene)
                merged_scene["block_kind"] = block.attrs.get("kind", "")

                if "id" not in merged_scene and scene.attrs.get("id"):
                    merged_scene["id"] = scene.attrs["id"]

                # -----------------------------------------
                # 1) crear estado persistente de escena
                # -----------------------------------------
                scene_state_manager = SceneStateManager()
                scene_state_manager.start_scene(
                    scene_id=merged_scene.get("id", ""),
                    block_kind=merged_scene.get("block_kind", ""),
                    location=merged_scene.get("location", ""),
                    characters=split_csv(merged_scene.get("characters", "")),
                    props=split_csv(merged_scene.get("props", "")),
                )
                
                scene_blocking = SceneBlockingEngine()
                scene_blocking.start_scene(
                    scene_id=merged_scene.get("id", ""),
                    block_kind=merged_scene.get("block_kind", ""),
                    location=merged_scene.get("location", ""),
                    characters=split_csv(merged_scene.get("characters", "")),
                )

                # -----------------------------------------
                # 2) plan base + refinado de montaje
                # -----------------------------------------
                base_directions, refined_directions = build_scene_edit_plan(
                    beats=scene.beats,
                    scene_attrs=merged_scene,
                    block_kind=merged_scene.get("block_kind", ""),
                    direct_beat_fn=direct_beat,
                )

                scene.beats = apply_edit_decisions_to_beats(scene.beats, refined_directions)

                # -----------------------------------------
                # 3) render beat a beat usando el mismo state manager
                # -----------------------------------------
                for local_beat_index, beat in enumerate(scene.beats):
                    print(f"Renderizando {beat.attrs.get('id')}")

                    clip_path = render_beat(
                        asset_mgr=asset_mgr,
                        scene_attrs=merged_scene,
                        beat=beat,
                        scene_index=global_scene_index,
                        beat_index=local_beat_index,
                        scene_state_manager=scene_state_manager,
                        scene_blocking=scene_blocking,
                        scene_beats=scene.beats,
                    )
                    clips.append(str(clip_path))

                global_scene_index += 1

    if not clips:
        raise ValueError("No se encontraron beats para renderizar.")

    out_file = OUTPUT / f"{chosen_episode}.mp4"
    if block_id:
        out_file = OUTPUT / f"{chosen_episode}_{slugify(block_id)}.mp4"
    if scene_id:
        out_file = OUTPUT / f"{chosen_episode}_{slugify(scene_id)}.mp4"

    video_clips = [VideoFileClip(c) for c in clips]
    final = concatenate_videoclips(video_clips, method="compose")
    final.write_videofile(str(out_file), fps=24, codec="libx264", audio_codec="aac", logger=None)
    final.close()

    for c in video_clips:
        c.close()

    return out_file


# -----------------------------
# CLI
# -----------------------------
def cmd_validate(args):
    episodes = parse_book(Path(args.book))
    errors = validate_episodes(episodes)
    if errors:
        print("INVALID")
        for e in errors:
            print("-", e)
        raise SystemExit(1)
    print("OK")


def cmd_list(args):
    episodes = parse_book(Path(args.book))
    print(json.dumps(build_plan(episodes), ensure_ascii=False, indent=2))


def cmd_plan(args):
    episodes = parse_book(Path(args.book))
    plan = build_plan(episodes, args.episode or "", args.block or "", args.scene or "")
    out = OUTPUT / f"plan_{args.episode or 'all'}.json"
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out)


def cmd_render(args):
    out = render_selection(Path(args.book), args.episode or "", args.block or "", args.scene or "")
    print(out)


def build_cli():
    p = argparse.ArgumentParser(description="Series Studio")
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("validate")
    a.add_argument("--book", default=str(ROOT / "libro.txt"))
    a.set_defaults(func=cmd_validate)

    a = sub.add_parser("list")
    a.add_argument("--book", default=str(ROOT / "libro.txt"))
    a.set_defaults(func=cmd_list)

    a = sub.add_parser("plan")
    a.add_argument("--book", default=str(ROOT / "libro.txt"))
    a.add_argument("--episode")
    a.add_argument("--block")
    a.add_argument("--scene")
    a.set_defaults(func=cmd_plan)

    a = sub.add_parser("render")
    a.add_argument("--book", default=str(ROOT / "libro.txt"))
    a.add_argument("--episode", required=True)
    a.add_argument("--block")
    a.add_argument("--scene")
    a.set_defaults(func=cmd_render)

    return p


if __name__ == "__main__":
    args = build_cli().parse_args()
    args.func(args)

from pathlib import Path
from typing import Dict, List, Any
import re
import json

from shot_planner import plan_scene_shots


def load_json_file(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def normalize(text: str) -> str:
    if not text:
        return ""
    text = text.lower()
    return (
        text.replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
        .replace("ü", "u")
        .replace("ñ", "n")
    )


def contains_whole_phrase(text: str, phrase: str) -> bool:
    return re.search(r"\b" + re.escape(normalize(phrase)) + r"\b", normalize(text)) is not None


def split_csv(value: str) -> List[str]:
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def inspect_beat_for_storyboard(project_root: Path, text: str, scene_attrs: Dict[str, str], beat_attrs: Dict[str, str]) -> Dict[str, Any]:
    config = project_root / "config"

    assets_manifest = load_json_file(config / "assets_manifest.json", {"characters": {}, "locations": {}, "props": {}})
    aliases = load_json_file(config / "aliases.json", {})
    character_aliases = load_json_file(config / "character_aliases.json", {})
    location_keywords = load_json_file(config / "location_keywords.json", {})
    function_rules = load_json_file(config / "function_rules.json", {})
    shot_rules = load_json_file(config / "shot_rules.json", {})
    transition_rules = load_json_file(config / "transition_rules.json", {})
    director_defaults = load_json_file(config / "director_defaults.json", {})
    style_rules = load_json_file(config / "style_rules.json", {})

    characters_catalog = list(assets_manifest.get("characters", {}).keys())
    props_catalog = list(assets_manifest.get("props", {}).keys())

    found_characters = []
    for canonical_name, alias_list in character_aliases.items():
        for alias in sorted(alias_list, key=lambda x: len(x), reverse=True):
            if contains_whole_phrase(text, alias):
                found_characters.append(canonical_name)
                break

    for canonical_name in characters_catalog:
        if canonical_name not in found_characters and contains_whole_phrase(text, canonical_name):
            found_characters.append(canonical_name)

    found_props = []
    for prop in props_catalog:
        if contains_whole_phrase(text, prop):
            found_props.append(prop)

    for raw_alias, canonical in aliases.items():
        if canonical in props_catalog and contains_whole_phrase(text, raw_alias):
            found_props.append(canonical)

    found_props = list(dict.fromkeys(found_props))

    scored_locations = {}
    for loc, keywords in location_keywords.items():
        score = sum(1 for kw in keywords if contains_whole_phrase(text, kw))
        if score:
            scored_locations[loc] = score

    location = (
        beat_attrs.get("location")
        or scene_attrs.get("location")
        or (max(scored_locations, key=scored_locations.get) if scored_locations else "stadium_exterior")
    )

    dialogue_match = re.match(r'^\s*([A-ZÁÉÍÓÚÑ0-9_ ]+)\s*:\s*(.+)$', text.strip(), flags=re.DOTALL)
    speaker = dialogue_match.group(1).title().strip() if dialogue_match else ""
    spoken = dialogue_match.group(2).strip() if dialogue_match else text.strip()

    focus = beat_attrs.get("focus", "")
    if not focus:
        focus = speaker or (found_characters[0] if found_characters else (found_props[0] if found_props else ""))

    def contains_any(t: str, keywords: List[str]) -> bool:
        nt = normalize(t)
        return any(normalize(k) in nt for k in keywords)

    scores = {
        "environment": 0,
        "object_emphasis": 0,
        "action": 0,
        "reaction": 0,
        "statement": 0,
        "confrontation": 0,
        "transition": 0,
    }

    wc = len(text.split())
    has_dialogue = bool(dialogue_match)

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
        shot = beat_attrs["shot"]
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

    return {
        "beat_id": beat_attrs.get("id", ""),
        "text": text,
        "speaker": speaker,
        "spoken_text": spoken,
        "characters": found_characters,
        "props": found_props,
        "location": location,
        "focus": focus,
        "kind": scene_kind,
        "function_type": function_type,
        "function_scores": scores,
        "shot": shot,
        "framing": framing,
        "camera": camera,
        "transition": transition,
        "visual_style": visual_style,
    }


def build_scene_storyboard(project_root: Path, scene, block, beats) -> List[Dict[str, Any]]:
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

        direction = inspect_beat_for_storyboard(
            project_root=project_root,
            text=beat.text,
            scene_attrs=scene_attrs,
            beat_attrs=beat.attrs,
        )

        previous_speaker = direction.get("speaker", "") or previous_speaker
        previous_focus = direction.get("focus", "") or previous_focus

        directions.append(direction)

    planned = plan_scene_shots(directions)
    return planned


def storyboard_rows(storyboard: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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
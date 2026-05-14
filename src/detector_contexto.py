import json
import re
from pathlib import Path
from typing import Dict, List

from dialogue_parser import parse_dialogue

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"


def load_json_file(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


ASSETS_MANIFEST = load_json_file(CONFIG / "assets_manifest.json", {"characters": {}, "locations": {}, "props": {}})
ALIASES = load_json_file(CONFIG / "aliases.json", {})
CHARACTER_ALIASES = load_json_file(CONFIG / "character_aliases.json", {})
LOCATION_KEYWORDS = load_json_file(CONFIG / "location_keywords.json", {})
CHARACTERS = list(ASSETS_MANIFEST.get("characters", {}).keys())
LOCATIONS = list(ASSETS_MANIFEST.get("locations", {}).keys())
PROPS = list(ASSETS_MANIFEST.get("props", {}).keys())


def normalize(text: str) -> str:
    if not text:
        return ""
    return (text.lower().replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u").replace("ü", "u").replace("ñ", "n"))


def apply_alias(name: str) -> str:
    if not name:
        return name
    return ALIASES.get(name, ALIASES.get(name.strip(), name)).strip()


def ordered_unique(items: List[str]) -> List[str]:
    seen, out = set(), []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def contains_whole_phrase(text: str, phrase: str) -> bool:
    pattern = r"\b" + re.escape(normalize(phrase)) + r"\b"
    return re.search(pattern, normalize(text)) is not None


def find_characters(text: str) -> List[str]:
    found = []
    for canonical_name, aliases in CHARACTER_ALIASES.items():
        for alias in sorted(aliases, key=lambda x: len(x), reverse=True):
            if contains_whole_phrase(text, alias):
                found.append(canonical_name)
                break
    for canonical_name in CHARACTERS:
        if canonical_name not in found and contains_whole_phrase(text, canonical_name):
            found.append(canonical_name)
    return ordered_unique(found)


def find_props(text: str) -> List[str]:
    found = []
    for prop in PROPS:
        if contains_whole_phrase(text, prop):
            found.append(prop)
    for raw_alias, canonical in ALIASES.items():
        if canonical in PROPS and contains_whole_phrase(text, raw_alias):
            found.append(canonical)
    return ordered_unique(found)


def guess_location(text: str) -> str | None:
    norm_text = normalize(text)
    candidates = []

    for location, keywords in LOCATION_KEYWORDS.items():
        score = 0
        longest_match = 0

        for keyword in keywords:
            norm_keyword = normalize(keyword)
            if contains_whole_phrase(text, keyword):
                score += 1
                longest_match = max(longest_match, len(norm_keyword))

        if score > 0:
            candidates.append((location, score, longest_match))

    if not candidates:
        return None

    # Orden:
    # 1) más coincidencias
    # 2) keyword más larga (más específica)
    # 3) nombre de location estable para desempate
    candidates.sort(key=lambda x: (x[1], x[2], x[0]), reverse=True)
    return candidates[0][0]


def detect_focus(text: str, characters: List[str], props: List[str], dialogue: Dict[str, object] | None = None) -> str:
    dialogue = dialogue or {}
    if dialogue.get("speaker"):
        return str(dialogue["speaker"])
    first_words = " ".join(text.strip().split()[:8])
    for character in characters:
        aliases = CHARACTER_ALIASES.get(character, [character])
        for alias in sorted(aliases, key=lambda x: len(x), reverse=True):
            if contains_whole_phrase(first_words, alias):
                return character
    if characters:
        return characters[0]
    if props:
        return props[0]
    return ""


def analyse_beat(text: str, previous_speaker: str = "") -> Dict[str, object]:
    characters = find_characters(text)
    props = find_props(text)
    location = guess_location(text)
    dialogue = parse_dialogue(text, context={"characters": characters, "previous_speaker": previous_speaker})
    speaker = dialogue.get("speaker")
    if speaker and speaker not in characters:
        characters = ordered_unique([speaker] + characters)
    focus = detect_focus(text, characters, props, dialogue)
    return {
        "characters": characters,
        "props": props,
        "location": location,
        "focus": focus,
        "has_dialogue": dialogue.get("has_dialogue", False),
        "speaker": dialogue.get("speaker"),
        "spoken_text": dialogue.get("spoken_text") if dialogue.get("has_dialogue", False) else None,
        "speech_verb": dialogue.get("speech_verb"),
        "speech_verb_raw": dialogue.get("speech_verb_raw"),
        "speech_type": dialogue.get("speech_type", "statement"),
        "dialogue_mode": dialogue.get("dialogue_mode", "none"),
        "dialogue_confidence": dialogue.get("dialogue_confidence", 0.0),
        "addressee": dialogue.get("addressee"),
        "dialogue_segments": dialogue.get("dialogue_segments", [])
    }


if __name__ == "__main__":
    samples = [
        "Vega cerró el portátil mientras la lluvia golpeaba la grada vacía.",
        "TOMÁS: No era más simple, presidente. Solo era más invisible.",
        "—Nos han pillado la matrícula, presi —murmuró Ferrer, acercándose a Javier con un botellín.",
        "—¿Para qué? —preguntó la directora financiera.",
        "—No tenemos un problema, Alejandro."
    ]
    for s in samples:
        print("=" * 60)
        print(s)
        print(json.dumps(analyse_beat(s), ensure_ascii=False, indent=2))

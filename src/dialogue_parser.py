import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from speaker_resolver import SpeakerResolver, normalize as normalize_basic

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"


def load_json_file(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


ALIASES = load_json_file(CONFIG / "aliases.json", {})
CHARACTER_ALIASES = load_json_file(CONFIG / "character_aliases.json", {})
DIALOGUE_RULES = load_json_file(CONFIG / "dialogue_rules.json", {})
DIALOGUE_ENTITIES = load_json_file(CONFIG / "dialogue_entities.json", {})

SPEECH_VERBS = set(normalize_basic(v) for v in DIALOGUE_RULES.get("speech_verbs", []))
SPEECH_TYPE_BY_VERB = {
    normalize_basic(k): v
    for k, v in DIALOGUE_RULES.get("speech_type_by_verb", {}).items()
}

resolver = SpeakerResolver(
    CHARACTER_ALIASES,
    ALIASES,
    DIALOGUE_ENTITIES,
    DIALOGUE_RULES
)

PREFIX_DIALOGUE_RE = re.compile(
    r"^\s*([A-ZÁÉÍÓÚÑ0-9_ ]{2,})\s*:\s*(.+)$",
    flags=re.DOTALL
)
DASH_INLINE_RE = re.compile(
    r"^\s*—\s*(.*?)\s*—\s*([^—]+?)\s*(?:—\s*(.*))?$",
    flags=re.DOTALL
)
LEADING_DASH_RE = re.compile(r"^\s*—\s*(.+)$", flags=re.DOTALL)

ATTRIBUTION_ENTITY_RE = re.compile(
    r"\b(?:dijo|pregunt[oó]|respondi[oó]|replic[oó]|murmur[oó]|susurr[oó]|grit[oó]|rugi[oó]|sentenci[oó]|gruñ[oó]|musit[oó]|admiti[oó]|añadi[oó]|continu[oó]|exclam[oó]|espet[oó]|intervino|contest[oó]|observ[oó]|balbuce[oó])\s+(?:el|la|los|las|un|una)?\s*([A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ]+)*)"
)
POST_SAID_ENTITY_RE = re.compile(
    r"\b([A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ]+)*)\s+(?:dijo|pregunt[oó]|respondi[oó]|replic[oó]|murmur[oó]|susurr[oó]|grit[oó]|rugi[oó]|sentenci[oó]|gruñ[oó]|musit[oó]|admiti[oó]|añadi[oó]|continu[oó]|exclam[oó]|espet[oó]|intervino|contest[oó]|observ[oó]|balbuce[oó])\b"
)


def normalize_dialogue_text(text: str) -> str:
    text = (text or "").replace("–", "—").replace("―", "—")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", " ", text)
    return text.strip()


def contains_dialogue_markers(text: str) -> bool:
    t = normalize_dialogue_text(text)
    return bool(PREFIX_DIALOGUE_RE.match(t) or LEADING_DASH_RE.match(t) or "—" in t)


def detect_speech_verb(text: str) -> tuple[Optional[str], Optional[str]]:
    raw_text = text or ""
    normalized_text = normalize_basic(raw_text)

    for raw_verb in DIALOGUE_RULES.get("speech_verbs", []):
        raw_verb_str = str(raw_verb)
        if re.search(rf"\b{re.escape(raw_verb_str)}\b", raw_text, flags=re.IGNORECASE):
            return normalize_basic(raw_verb_str), raw_verb_str

    for verb in SPEECH_VERBS:
        if re.search(rf"\b{re.escape(verb)}\b", normalized_text):
            return verb, None

    return None, None


def classify_speech_type(spoken_text: str, speech_verb: Optional[str]) -> str:
    if speech_verb and speech_verb in SPEECH_TYPE_BY_VERB:
        return SPEECH_TYPE_BY_VERB[speech_verb]
    if "?" in spoken_text or "¿" in spoken_text:
        return "question"
    if "!" in spoken_text or "¡" in spoken_text:
        return "exclamation"
    return "statement"


def _extract_attribution_entity(text: str) -> Optional[str]:
    m = ATTRIBUTION_ENTITY_RE.search(text or "")
    if m:
        return resolver.resolve_entity(m.group(1))

    m = POST_SAID_ENTITY_RE.search(text or "")
    if m:
        return resolver.resolve_entity(m.group(1))

    lowered = normalize_basic(text or "")
    for canonical, aliases in CHARACTER_ALIASES.items():
        for alias in [canonical] + list(aliases or []):
            if re.search(rf"\b{re.escape(normalize_basic(alias))}\b", lowered):
                return canonical
    return None


def _explicit_characters_from_text(text: str) -> List[str]:
    lowered = normalize_basic(text)
    found = []
    for canonical, aliases in CHARACTER_ALIASES.items():
        for alias in [canonical] + list(aliases or []):
            if re.search(rf"\b{re.escape(normalize_basic(alias))}\b", lowered):
                found.append(canonical)
                break
    return found


def _infer_addressee_fallback(spoken_text: str) -> Optional[str]:
    vocatives = resolver.extract_vocatives(spoken_text or "")
    if vocatives:
        return vocatives[0]
    return None


def parse_dialogue(text: str, context: Optional[Dict[str, object]] = None) -> Dict[str, object]:
    context = context or {}
    clean = normalize_dialogue_text(text)

    result = {
        "has_dialogue": False,
        "speaker": None,
        "spoken_text": None,
        "speech_verb": None,
        "speech_verb_raw": None,
        "speech_type": "statement",
        "dialogue_mode": "none",
        "dialogue_confidence": 0.0,
        "addressee": None,
        "dialogue_segments": []
    }

    explicit_characters = _explicit_characters_from_text(clean)
    focus = context.get("focus")
    previous_speaker = context.get("previous_speaker")
    active_characters = context.get("characters") or explicit_characters

    m = PREFIX_DIALOGUE_RE.match(clean)
    if m:
        speaker_raw = m.group(1).title().strip()
        speaker = resolver.resolve_entity(speaker_raw) or ALIASES.get(speaker_raw, speaker_raw)
        spoken = m.group(2).strip()
        addrs = resolver.extract_vocatives(spoken)
        addressee = addrs[0] if addrs else _infer_addressee_fallback(spoken)

        return {
            "has_dialogue": True,
            "speaker": speaker,
            "spoken_text": spoken,
            "speech_verb": None,
            "speech_verb_raw": None,
            "speech_type": classify_speech_type(spoken, None),
            "dialogue_mode": "explicit_prefix",
            "dialogue_confidence": 0.99 if speaker else 0.60,
            "addressee": addressee,
            "dialogue_segments": [
                {
                    "speaker": speaker,
                    "spoken_text": spoken,
                    "mode": "explicit_prefix",
                    "confidence": 0.99 if speaker else 0.60
                }
            ]
        }

    if not contains_dialogue_markers(clean):
        return result

    inline = DASH_INLINE_RE.match(clean)
    if inline:
        spoken = " ".join(
            [p for p in [(inline.group(1) or "").strip(), (inline.group(3) or "").strip()] if p]
        ).strip()
        attr = (inline.group(2) or "").strip()
        speech_verb, speech_verb_raw = detect_speech_verb(attr)
        attribution_entity = _extract_attribution_entity(attr)

        speaker, conf, reasons, addressee = resolver.score_candidates(
            attribution_entity,
            spoken,
            explicit_characters,
            focus,
            previous_speaker,
            active_characters
        )

        if not addressee:
            addressee = _infer_addressee_fallback(spoken)

        return {
            "has_dialogue": True,
            "speaker": speaker,
            "spoken_text": spoken,
            "speech_verb": speech_verb,
            "speech_verb_raw": speech_verb_raw,
            "speech_type": classify_speech_type(spoken, speech_verb),
            "dialogue_mode": "dash_inline_attribution",
            "dialogue_confidence": conf,
            "addressee": addressee,
            "dialogue_segments": [
                {
                    "speaker": speaker,
                    "spoken_text": spoken,
                    "attribution_text": attr,
                    "speech_verb": speech_verb,
                    "speech_verb_raw": speech_verb_raw,
                    "mode": "dash_inline_attribution",
                    "confidence": conf,
                    "reasons": reasons
                }
            ]
        }

    leading = LEADING_DASH_RE.match(clean)
    if leading:
        spoken = leading.group(1).strip()

        speaker, conf, reasons, addressee = resolver.score_candidates(
            None,
            spoken,
            explicit_characters,
            focus,
            previous_speaker,
            active_characters
        )

        if not addressee:
            addressee = _infer_addressee_fallback(spoken)

        return {
            "has_dialogue": True,
            "speaker": speaker,
            "spoken_text": spoken,
            "speech_verb": None,
            "speech_verb_raw": None,
            "speech_type": classify_speech_type(spoken, None),
            "dialogue_mode": "dash_only",
            "dialogue_confidence": conf,
            "addressee": addressee,
            "dialogue_segments": [
                {
                    "speaker": speaker,
                    "spoken_text": spoken,
                    "mode": "dash_only",
                    "confidence": conf,
                    "reasons": reasons
                }
            ]
        }

    result.update(
        {
            "has_dialogue": True,
            "dialogue_mode": "uncertain",
            "dialogue_confidence": 0.20,
            "dialogue_segments": [
                {
                    "speaker": None,
                    "spoken_text": clean,
                    "mode": "uncertain",
                    "confidence": 0.20
                }
            ]
        }
    )
    return result
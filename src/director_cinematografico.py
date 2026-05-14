import json
import re
from pathlib import Path
from typing import Dict, List

from detector_contexto import analyse_beat, normalize
from dialogue_parser import parse_dialogue

from emotion_director import apply_emotion_direction

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"


def load_json_file(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


STYLE_RULES = load_json_file(CONFIG / "style_rules.json", {"visual_styles": {}, "factions": {}, "block_kind": {}})
FUNCTION_RULES = load_json_file(CONFIG / "function_rules.json", {})
SHOT_RULES = load_json_file(CONFIG / "shot_rules.json", {})
TRANSITION_RULES = load_json_file(CONFIG / "transition_rules.json", {})
DIRECTOR_DEFAULTS = load_json_file(CONFIG / "director_defaults.json", {"defaults": {}})

DIALOGUE_RE = re.compile(r'^\s*([A-ZÁÉÍÓÚÑ0-9_ ]+)\s*:\s*(.+)$', flags=re.DOTALL)


def split_csv(value: str) -> List[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def word_count(text: str) -> int:
    return len(text.strip().split())


def is_dialogue(text: str) -> bool:
    return bool(parse_dialogue(text).get("has_dialogue"))


def speaker_from_dialogue(text: str) -> str:
    return parse_dialogue(text).get("speaker") or ""


def contains_any(text: str, keywords: List[str]) -> bool:
    t = normalize(text)
    return any(normalize(k) in t for k in keywords)


def get_block_rules(block_kind: str) -> Dict[str, str]:
    return STYLE_RULES.get("block_kind", {}).get(block_kind, {})


def infer_scene_kind_from_text(text: str) -> str:
    info = parse_dialogue(text)
    return "dialogue" if info.get("has_dialogue") else "narration"


def classify_beat_function(text: str, context: Dict[str, object]) -> str:
    """
    Clasifica el beat por puntuación.

    Posibles salidas:
    - environment
    - object_emphasis
    - action
    - reaction
    - statement
    - confrontation
    - transition
    """
    t = normalize(text)
    characters = context.get("characters", [])
    props = context.get("props", [])

    environment_keywords = FUNCTION_RULES.get("environment_keywords", [])
    object_keywords = FUNCTION_RULES.get("object_keywords", [])
    action_keywords = FUNCTION_RULES.get("action_keywords", [])
    reaction_keywords = FUNCTION_RULES.get("reaction_keywords", [])
    confrontation_keywords = FUNCTION_RULES.get("confrontation_keywords", [])

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
    has_dialogue = is_dialogue(text)

    # -------------------------------------------------
    # 1) Diálogo
    # -------------------------------------------------
    if has_dialogue:
        scores["statement"] += 4

        if contains_any(text, confrontation_keywords):
            scores["confrontation"] += 5

        # diálogos muy cortos suelen ser réplicas tensas
        if wc <= 8:
            scores["confrontation"] += 1

        # si menciona props, no quitamos statement,
        # pero damos algo de peso a object_emphasis
        if props and contains_any(text, object_keywords):
            scores["object_emphasis"] += 1

    # -------------------------------------------------
    # 2) Props / objetos
    # -------------------------------------------------
    if props:
        scores["object_emphasis"] += 2

        if contains_any(text, object_keywords):
            scores["object_emphasis"] += 3

        # si el beat es corto y gira alrededor de un objeto, reforzar
        if wc <= 12:
            scores["object_emphasis"] += 1

    # -------------------------------------------------
    # 3) Acción
    # -------------------------------------------------
    if contains_any(text, action_keywords):
        scores["action"] += 4

        if characters:
            scores["action"] += 1

        if wc <= 14:
            scores["action"] += 1

    # -------------------------------------------------
    # 4) Reacción
    # -------------------------------------------------
    if contains_any(text, reaction_keywords):
        scores["reaction"] += 4

        if characters:
            scores["reaction"] += 1

    # -------------------------------------------------
    # 5) Entorno
    # -------------------------------------------------
    if contains_any(text, environment_keywords):
        scores["environment"] += 3

        if not characters:
            scores["environment"] += 2

        if wc <= 18:
            scores["environment"] += 1

    # -------------------------------------------------
    # 6) Heurísticas narrativas adicionales
    # -------------------------------------------------
    # Si hay personajes pero no acción fuerte ni diálogo,
    # puede ser reacción o transición
    if characters and not has_dialogue:
        scores["reaction"] += 1
        scores["transition"] += 1

    # Si no hay personajes pero sí entorno, reforzar ambiente
    if not characters and contains_any(text, environment_keywords):
        scores["environment"] += 1

    # Si no hay personajes pero sí props, reforzar insert
    if not characters and props:
        scores["object_emphasis"] += 1

    # Beats muy cortos sin señales fuertes suelen ser transición
    if wc <= 10:
        scores["transition"] += 2
    elif wc <= 18:
        scores["transition"] += 1

    # -------------------------------------------------
    # 7) Ajustes para evitar clasificaciones raras
    # -------------------------------------------------
    # Si hay acción fuerte, suele dominar sobre transición
    if scores["action"] >= 4:
        scores["transition"] -= 1

    # Si hay confrontación, suele dominar sobre statement
    if scores["confrontation"] > scores["statement"]:
        scores["statement"] -= 1

    # Si object_emphasis y reaction empatan, priorizar objeto
    # cuando hay props explícitos
    if props and scores["object_emphasis"] == scores["reaction"]:
        scores["object_emphasis"] += 1

    # Nunca dejar puntuaciones negativas
    for k in scores:
        if scores[k] < 0:
            scores[k] = 0

    # -------------------------------------------------
    # 8) Elegir mejor categoría
    # -------------------------------------------------
    # prioridad de desempate
    priority = [
        "confrontation",
        "statement",
        "action",
        "object_emphasis",
        "reaction",
        "environment",
        "transition",
    ]

    best_score = max(scores.values())

    best_candidates = [k for k, v in scores.items() if v == best_score]

    for p in priority:
        if p in best_candidates:
            return p

    return "transition"

def classify_beat_function_debug(text: str, context: Dict[str, object]) -> Dict[str, int]:
    t = normalize(text)
    characters = context.get("characters", [])
    props = context.get("props", [])

    environment_keywords = FUNCTION_RULES.get("environment_keywords", [])
    object_keywords = FUNCTION_RULES.get("object_keywords", [])
    action_keywords = FUNCTION_RULES.get("action_keywords", [])
    reaction_keywords = FUNCTION_RULES.get("reaction_keywords", [])
    confrontation_keywords = FUNCTION_RULES.get("confrontation_keywords", [])

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
    has_dialogue = is_dialogue(text)

    if has_dialogue:
        scores["statement"] += 4
        if contains_any(text, confrontation_keywords):
            scores["confrontation"] += 5
        if wc <= 8:
            scores["confrontation"] += 1
        if props and contains_any(text, object_keywords):
            scores["object_emphasis"] += 1

    if props:
        scores["object_emphasis"] += 2
        if contains_any(text, object_keywords):
            scores["object_emphasis"] += 3
        if wc <= 12:
            scores["object_emphasis"] += 1

    if contains_any(text, action_keywords):
        scores["action"] += 4
        if characters:
            scores["action"] += 1
        if wc <= 14:
            scores["action"] += 1

    if contains_any(text, reaction_keywords):
        scores["reaction"] += 4
        if characters:
            scores["reaction"] += 1

    if contains_any(text, environment_keywords):
        scores["environment"] += 3
        if not characters:
            scores["environment"] += 2
        if wc <= 18:
            scores["environment"] += 1

    if characters and not has_dialogue:
        scores["reaction"] += 1
        scores["transition"] += 1

    if not characters and contains_any(text, environment_keywords):
        scores["environment"] += 1

    if not characters and props:
        scores["object_emphasis"] += 1

    if wc <= 10:
        scores["transition"] += 2
    elif wc <= 18:
        scores["transition"] += 1

    if scores["action"] >= 4:
        scores["transition"] -= 1

    if scores["confrontation"] > scores["statement"]:
        scores["statement"] -= 1

    if props and scores["object_emphasis"] == scores["reaction"]:
        scores["object_emphasis"] += 1

    for k in scores:
        if scores[k] < 0:
            scores[k] = 0

    return scores

def choose_shot_from_function(function_type: str, context: Dict[str, object], history: Dict[str, str]) -> str:
    """
    Decide el shot en función de:
    - tipo dramático del beat
    - personajes presentes
    - speaker actual
    - speaker anterior
    - foco actual / anterior
    - shot anterior

    history esperado:
    {
        "current_speaker": "...",
        "previous_speaker": "...",
        "previous_focus": "...",
        "current_focus": "...",
        "previous_shot": "..."
    }
    """
    characters = context.get("characters", [])
    props = context.get("props", [])

    current_speaker = history.get("current_speaker", "")
    previous_speaker = history.get("previous_speaker", "")
    previous_focus = history.get("previous_focus", "")
    current_focus = history.get("current_focus", "")
    previous_shot = history.get("previous_shot", "")

    dialogue_opening_shot = SHOT_RULES.get("dialogue_opening_shot", "medium_two_shot")
    dialogue_reply_shot = SHOT_RULES.get("dialogue_reply_shot", "over_the_shoulder")
    fallback_shot = SHOT_RULES.get("fallback_shot", "medium")
    mapping = SHOT_RULES.get("function_to_shot", {})

    # -------------------------------------------------
    # 1) Objeto importante -> insert
    # -------------------------------------------------
    if function_type == "object_emphasis" and props:
        return mapping.get("object_emphasis", "insert")

    # -------------------------------------------------
    # 2) Entorno -> wide
    # -------------------------------------------------
    if function_type == "environment":
        return mapping.get("environment", "wide_establishing")

    # -------------------------------------------------
    # 3) Acción -> tracking o medium
    # -------------------------------------------------
    if function_type == "action":
        if characters:
            return mapping.get("action", "tracking")
        return fallback_shot

    # -------------------------------------------------
    # 4) Reacción -> close_up
    # -------------------------------------------------
    if function_type == "reaction":
        # si ya venimos de close_up y el foco no cambia, evitar repetición
        if previous_shot == "close_up" and previous_focus == current_focus and len(characters) >= 2:
            return "over_the_shoulder"
        return mapping.get("reaction", "close_up")

    # -------------------------------------------------
    # 5) Statement (diálogo normal)
    # -------------------------------------------------
    if function_type == "statement":
        # apertura de conversación entre dos personajes
        if len(characters) >= 2 and not previous_speaker:
            return dialogue_opening_shot

        # cambio de speaker -> contraplano más natural
        if previous_speaker and current_speaker and previous_speaker != current_speaker:
            return dialogue_reply_shot

        # si mismo speaker sigue hablando mucho tiempo, variar
        if previous_speaker and current_speaker and previous_speaker == current_speaker:
            if previous_shot == "close_up":
                return "medium"
            return "close_up"

        # si hay dos personajes y cambio de foco
        if len(characters) >= 2 and previous_focus and current_focus and previous_focus != current_focus:
            return "over_the_shoulder"

        # un único personaje hablando -> close_up
        return mapping.get("statement", "close_up")

    # -------------------------------------------------
    # 6) Confrontación
    # -------------------------------------------------
    if function_type == "confrontation":
        # si hay dos personajes y réplica, usar over-the-shoulder
        if len(characters) >= 2:
            if previous_speaker and current_speaker and previous_speaker != current_speaker:
                return "over_the_shoulder"

            # si cambia foco entre dos personajes, también
            if previous_focus and current_focus and previous_focus != current_focus:
                return "over_the_shoulder"

            # si empieza confrontación, abrir en two-shot
            if not previous_speaker:
                return "medium_two_shot"

        # fallback confrontación
        return mapping.get("confrontation", "close_up")

    # -------------------------------------------------
    # 7) Transition
    # -------------------------------------------------
    if function_type == "transition":
        if props:
            return "insert"
        if len(characters) >= 2:
            return "medium_two_shot"
        if characters:
            return "medium"
        return mapping.get("transition", fallback_shot)

    # -------------------------------------------------
    # 8) Fallback genérico
    # -------------------------------------------------
    return mapping.get(function_type, fallback_shot)


def infer_visual_style(scene_attrs: Dict[str, str], characters: List[str], block_kind: str) -> str:
    if scene_attrs.get("visual_style"):
        return scene_attrs["visual_style"]

    block_rules = get_block_rules(block_kind)
    if block_rules.get("visual_style"):
        return block_rules["visual_style"]

    factions = STYLE_RULES.get("factions", {})
    if any(c in factions.get("datos", []) for c in characters):
        if any(c in factions.get("tradicion", []) for c in characters):
            return "choque_mundos"
        return "datos"
    if any(c in factions.get("tradicion", []) for c in characters):
        return "tradicion"
    return "tradicion"


def choose_audio_mode(scene_kind: str, scene_attrs: Dict[str, str], block_kind: str) -> str:
    if scene_attrs.get("audio"):
        return scene_attrs["audio"]

    block_rules = get_block_rules(block_kind)
    if block_rules.get("audio"):
        if scene_kind == "dialogue":
            return "single_voice"
        return block_rules["audio"]

    return DIRECTOR_DEFAULTS.get("defaults", {}).get(scene_kind, {}).get("audio", "narrator")


def choose_video_mode(scene_kind: str, scene_attrs: Dict[str, str], block_kind: str) -> str:
    if scene_attrs.get("video"):
        return scene_attrs["video"]

    block_rules = get_block_rules(block_kind)
    if block_rules.get("video"):
        return block_rules["video"]

    return DIRECTOR_DEFAULTS.get("defaults", {}).get(scene_kind, {}).get("video", "environment_animation")


def choose_duration(text: str, scene_kind: str, shot: str, function_type: str = "transition") -> str:
    """
    Decide duración por reglas configurables en director_defaults.json.
    """
    wc = word_count(text)

    duration_rules = DIRECTOR_DEFAULTS.get("duration_rules", {})
    base_by_function = duration_rules.get("base_by_function", {})
    shot_adjustments = duration_rules.get("shot_adjustments", {})
    scene_kind_adjustments = duration_rules.get("scene_kind_adjustments", {})
    word_rules = duration_rules.get("word_count_rules", {})

    min_duration = duration_rules.get("min_duration", 3)
    max_duration = duration_rules.get("max_duration", 9)

    score = base_by_function.get(function_type, 4)
    score += shot_adjustments.get(shot, 0)
    score += scene_kind_adjustments.get(scene_kind, 0)

    very_short_max = word_rules.get("very_short_max", 6)
    short_max = word_rules.get("short_max", 12)
    medium_max = word_rules.get("medium_max", 20)

    very_short_adjustment = word_rules.get("very_short_adjustment", -1)
    short_adjustment = word_rules.get("short_adjustment", 0)
    medium_adjustment = word_rules.get("medium_adjustment", 1)
    long_adjustment = word_rules.get("long_adjustment", 2)

    if wc <= very_short_max:
        score += very_short_adjustment
    elif wc <= short_max:
        score += short_adjustment
    elif wc <= medium_max:
        score += medium_adjustment
    else:
        score += long_adjustment

    if shot == "insert":
        score = min(score, 4)

    if function_type == "confrontation" and shot in ("close_up", "over_the_shoulder"):
        score += 1

    if function_type == "environment" and wc <= 10:
        score = max(score, 5)

    if function_type == "reaction" and wc <= 8:
        score = min(score, 5)

    score = max(min_duration, min(max_duration, score))
    return str(score)


def infer_framing(shot: str) -> str:
    return DIRECTOR_DEFAULTS.get("framing_by_shot", {}).get(shot, "medium")


def infer_camera(shot: str, scene_kind: str, scene_attrs: Dict[str, str], block_kind: str) -> str:
    if scene_attrs.get("camera"):
        return scene_attrs["camera"]

    block_rules = get_block_rules(block_kind)
    if block_rules.get("camera"):
        return block_rules["camera"]

    return DIRECTOR_DEFAULTS.get("camera_by_shot", {}).get(shot, DIRECTOR_DEFAULTS.get("defaults", {}).get(scene_kind, {}).get("camera", "static"))


def infer_transition(
    scene_index: int,
    beat_index: int,
    block_kind: str = "",
    scene_kind: str = "",
    shot: str = "",
    current_location: str = "",
    previous_location: str = "",
    current_focus: str = "",
    previous_focus: str = "",
    current_speaker: str = "",
    previous_speaker: str = "",
    previous_scene_kind: str = "",
) -> str:
    if scene_index == 0 and beat_index == 0:
        return TRANSITION_RULES.get("episode_start", "fade_in")

    if previous_location and current_location and previous_location != current_location:
        return TRANSITION_RULES.get("location_change", "fade")

    if block_kind in TRANSITION_RULES.get("block_kind_defaults", {}):
        if shot in TRANSITION_RULES.get("shot_overrides", {}):
            return TRANSITION_RULES["shot_overrides"][shot]
        return TRANSITION_RULES["block_kind_defaults"][block_kind]

    if previous_scene_kind == "narration" and scene_kind == "dialogue":
        return TRANSITION_RULES.get("narration_to_dialogue", "dissolve")

    if previous_scene_kind == "dialogue" and scene_kind == "narration":
        return TRANSITION_RULES.get("dialogue_to_narration", "fade")

    if scene_kind == "dialogue":
        if previous_speaker and current_speaker and previous_speaker != current_speaker:
            return TRANSITION_RULES.get("speaker_change_in_dialogue", "cut")
        return TRANSITION_RULES.get("scene_kind_defaults", {}).get("dialogue", "cut")

    if shot in TRANSITION_RULES.get("shot_overrides", {}):
        return TRANSITION_RULES["shot_overrides"][shot]

    if previous_focus and current_focus and previous_focus != current_focus:
        return TRANSITION_RULES.get("focus_change", "match_cut")

    if scene_kind in TRANSITION_RULES.get("scene_kind_defaults", {}):
        return TRANSITION_RULES["scene_kind_defaults"][scene_kind]

    return TRANSITION_RULES.get("fallback", "cut")


def enrich_context_with_scene_defaults(context: Dict[str, object], scene_attrs: Dict[str, str]) -> Dict[str, object]:
    out = dict(context)

    if scene_attrs.get("characters") and not out.get("characters"):
        out["characters"] = split_csv(scene_attrs["characters"])

    if scene_attrs.get("location") and not out.get("location"):
        out["location"] = scene_attrs["location"]

    if scene_attrs.get("focus") and not out.get("focus"):
        out["focus"] = scene_attrs["focus"]

    if scene_attrs.get("props") and not out.get("props"):
        out["props"] = split_csv(scene_attrs["props"])

    return out


def direct_beat(
    text: str,
    scene_attrs: Dict[str, str] = None,
    block_kind: str = "",
    scene_index: int = 0,
    beat_index: int = 0,
    scene_state_manager=None,
) -> Dict[str, object]:
    scene_attrs = scene_attrs or {}

    context = analyse_beat(text)
    context = enrich_context_with_scene_defaults(context, scene_attrs)

    scene_kind = scene_attrs.get("kind") or get_block_rules(block_kind).get("scene_kind") or infer_scene_kind_from_text(text)
    function_type = classify_beat_function(text, context)

    current_speaker = speaker_from_dialogue(text) if is_dialogue(text) else ""
    current_focus = context.get("focus", "")

    if scene_state_manager is not None:
        history = scene_state_manager.to_history_dict(
            current_focus=current_focus,
            current_speaker=current_speaker,
        )
    else:
        history = {
            "current_speaker": current_speaker,
            "previous_speaker": scene_attrs.get("_previous_speaker", ""),
            "current_focus": current_focus,
            "previous_focus": scene_attrs.get("_previous_focus", ""),
            "previous_shot": scene_attrs.get("_previous_shot", ""),
            "previous_location": scene_attrs.get("_previous_location", ""),
            "foreground_character": "",
            "background_character": "",
        }

    shot = choose_shot_from_function(function_type, context, history)
    framing = infer_framing(shot)
    camera = infer_camera(shot, scene_kind, scene_attrs, block_kind)
    audio = choose_audio_mode(scene_kind, scene_attrs, block_kind)
    video = choose_video_mode(scene_kind, scene_attrs, block_kind)
    visual_style = infer_visual_style(scene_attrs, context.get("characters", []), block_kind)
    duration = choose_duration(text, scene_kind, shot, function_type)

    transition = infer_transition(
        scene_index=scene_index,
        beat_index=beat_index,
        block_kind=block_kind,
        scene_kind=scene_kind,
        shot=shot,
        current_location=context.get("location", ""),
        previous_location=scene_attrs.get("_previous_location", ""),
        current_focus=context.get("focus", ""),
        previous_focus=scene_attrs.get("_previous_focus", ""),
        current_speaker=history["current_speaker"],
        previous_speaker=history["previous_speaker"],
        previous_scene_kind=scene_attrs.get("_previous_scene_kind", ""),
    )

    result = {
        "characters": context.get("characters", []),
        "props": context.get("props", []),
        "location": context.get("location", "stadium_exterior"),
        "focus": context.get("focus", ""),
        "kind": scene_kind,
        "function_type": function_type,
        "shot": shot,
        "framing": framing,
        "camera": camera,
        "audio": audio,
        "video": video,
        "duration": duration,
        "visual_style": visual_style,
        "transition": transition,
    }

    if is_dialogue(text):
        result["speaker"] = current_speaker

    scene_attrs["_previous_location"] = context.get("location", "")
    scene_attrs["_previous_focus"] = context.get("focus", "")
    scene_attrs["_previous_speaker"] = current_speaker
    scene_attrs["_previous_scene_kind"] = scene_kind
    scene_attrs["_previous_shot"] = shot

    if scene_state_manager is not None:
        scene_state_manager.update_after_beat(result)

    result = apply_emotion_direction(text, result)
    return result


def apply_direction_to_beat(
    beat,
    scene_attrs: Dict[str, str],
    block_kind: str = "",
    scene_index: int = 0,
    beat_index: int = 0,
    scene_state_manager=None,
):
    direction = direct_beat(
        text=beat.text,
        scene_attrs=scene_attrs,
        block_kind=block_kind,
        scene_index=scene_index,
        beat_index=beat_index,
        scene_state_manager=scene_state_manager,
    )

    for key, value in direction.items():
        if key in ("characters", "props", "location", "focus"):
            continue
        if not beat.attrs.get(key):
            beat.attrs[key] = ",".join(value) if isinstance(value, list) else str(value)

    if not scene_attrs.get("location") and direction.get("location"):
        scene_attrs["location"] = direction["location"]

    if not scene_attrs.get("characters") and direction.get("characters"):
        scene_attrs["characters"] = ",".join(direction["characters"])

    if not scene_attrs.get("props") and direction.get("props"):
        scene_attrs["props"] = ",".join(direction["props"])

    if not beat.attrs.get("focus") and direction.get("focus"):
        beat.attrs["focus"] = direction["focus"]

    return beat, scene_attrs, direction
    
def trim_prompt_for_model(parts: List[str], model_name: str) -> List[str]:
    profiles = DIRECTOR_DEFAULTS.get("model_prompt_profiles", {})
    profile = profiles.get(model_name, {"mode": "balanced", "max_parts": 20})

    mode = profile.get("mode", "balanced")
    max_parts = profile.get("max_parts", 20)

    if mode == "short":
        return parts[:max_parts]

    if mode == "balanced":
        if len(parts) > max_parts:
            head = parts[: int(max_parts * 0.6)]
            tail = parts[-int(max_parts * 0.4):]
            return head + tail
        return parts

    if mode == "long":
        return parts[:max_parts]

    return parts[:max_parts]
from typing import Dict, List, Tuple
from scene_state_manager import SceneStateManager
from dramatic_arc_engine import apply_dramatic_arc
from audio_drama_engine import apply_audio_drama_to_scene
from shot_planner import plan_scene_shots


def split_csv(value: str) -> List[str]:
    return [v.strip() for v in value.split(",") if v.strip()]

def is_dialogue_beat(direction: Dict[str, object]) -> bool:
    return direction.get("kind") == "dialogue" or bool(direction.get("speaker"))


def normalize_text(value: str) -> str:
    return (value or "").strip()


def copy_direction(direction: Dict[str, object]) -> Dict[str, object]:
    return dict(direction)


def has_prop_focus(direction: Dict[str, object]) -> bool:
    props = direction.get("props", []) or []
    shot = direction.get("shot", "")
    focus = normalize_text(direction.get("focus", ""))
    if shot == "insert":
        return True
    return bool(props and focus and focus in props)


def same_focus(a: Dict[str, object], b: Dict[str, object]) -> bool:
    return normalize_text(a.get("focus", "")) == normalize_text(b.get("focus", ""))


def same_speaker(a: Dict[str, object], b: Dict[str, object]) -> bool:
    return normalize_text(a.get("speaker", "")) == normalize_text(b.get("speaker", ""))


def same_kind(a: Dict[str, object], b: Dict[str, object]) -> bool:
    return normalize_text(a.get("kind", "")) == normalize_text(b.get("kind", ""))


def is_two_character_dialogue(direction: Dict[str, object]) -> bool:
    chars = direction.get("characters", []) or []
    return is_dialogue_beat(direction) and len(chars) >= 2


def is_visual_beat(direction: Dict[str, object]) -> bool:
    return direction.get("kind") == "narration" and not direction.get("speaker")


def enforce_dialogue_opening(direction: Dict[str, object], beat_index: int) -> Dict[str, object]:
    d = copy_direction(direction)

    if not is_two_character_dialogue(d):
        return d

    if beat_index == 0:
        d["shot"] = "medium_two_shot"
        d["framing"] = "two_shot"
        d["camera"] = "static"
    return d


def enforce_dialogue_reply(prev_direction: Dict[str, object], direction: Dict[str, object]) -> Dict[str, object]:
    d = copy_direction(direction)

    if not is_two_character_dialogue(d):
        return d

    prev_speaker = normalize_text(prev_direction.get("speaker", ""))
    curr_speaker = normalize_text(d.get("speaker", ""))

    if prev_speaker and curr_speaker and prev_speaker != curr_speaker:
        d["shot"] = "over_the_shoulder"
        d["framing"] = "medium_close_up"
        d["camera"] = "static"
        d["transition"] = "cut"

    return d


def avoid_repetitive_closeups(prev_direction: Dict[str, object], direction: Dict[str, object]) -> Dict[str, object]:
    d = copy_direction(direction)

    if prev_direction.get("shot") == "close_up" and d.get("shot") == "close_up":
        if same_focus(prev_direction, d):
            # si el mismo personaje sigue en primer plano, abrir un poco
            if is_dialogue_beat(d):
                d["shot"] = "medium"
                d["framing"] = "medium"
                d["camera"] = "static_subtle_push"
            else:
                d["shot"] = "medium"
                d["framing"] = "medium"

    return d


def promote_reaction_closeup(prev_direction: Dict[str, object], direction: Dict[str, object]) -> Dict[str, object]:
    d = copy_direction(direction)

    function_type = d.get("function_type", "")
    if function_type == "reaction":
        d["shot"] = "close_up"
        d["framing"] = "close_up"
        d["camera"] = "static_subtle_push"

        if prev_direction.get("kind") == "dialogue":
            d["transition"] = "cut"

    return d


def promote_object_insert(prev_direction: Dict[str, object], direction: Dict[str, object]) -> Dict[str, object]:
    d = copy_direction(direction)

    if d.get("function_type") == "object_emphasis" or has_prop_focus(d):
        d["shot"] = "insert"
        d["framing"] = "detail"
        d["camera"] = "static"
        d["transition"] = "cut"

    return d


def soften_environment_after_dialogue(prev_direction: Dict[str, object], direction: Dict[str, object]) -> Dict[str, object]:
    d = copy_direction(direction)

    if prev_direction.get("kind") == "dialogue" and d.get("function_type") == "environment":
        d["shot"] = "wide_establishing"
        d["framing"] = "wide"
        d["camera"] = "slow_pan"
        d["transition"] = "fade"

    return d


def stabilize_tracking_sequences(prev_direction: Dict[str, object], direction: Dict[str, object]) -> Dict[str, object]:
    d = copy_direction(direction)

    if prev_direction.get("shot") == "tracking" and d.get("shot") == "tracking":
        # evitar tracking infinito: segundo tracking pasa a medium
        d["shot"] = "medium"
        d["framing"] = "medium"
        d["camera"] = "static"

    return d


def improve_scene_opening(direction: Dict[str, object], beat_index: int) -> Dict[str, object]:
    d = copy_direction(direction)

    if beat_index != 0:
        return d

    if d.get("function_type") == "environment":
        d["shot"] = "wide_establishing"
        d["framing"] = "wide"
        d["camera"] = "slow_pan"
    elif d.get("kind") == "dialogue" and len(d.get("characters", []) or []) >= 2:
        d["shot"] = "medium_two_shot"
        d["framing"] = "two_shot"
        d["camera"] = "static"

    return d


def improve_scene_closing(direction: Dict[str, object], is_last: bool) -> Dict[str, object]:
    d = copy_direction(direction)

    if not is_last:
        return d

    if d.get("function_type") == "environment":
        d["transition"] = "fade"
    elif d.get("function_type") == "reaction":
        d["transition"] = "dissolve"
    elif d.get("shot") == "insert":
        d["transition"] = "cut"

    return d


def refine_single_direction(
    directions: List[Dict[str, object]],
    index: int,
) -> Dict[str, object]:
    current = copy_direction(directions[index])
    prev_direction = directions[index - 1] if index > 0 else {}
    is_last = index == len(directions) - 1

    current = improve_scene_opening(current, index)
    current = enforce_dialogue_opening(current, index)
    current = enforce_dialogue_reply(prev_direction, current)
    current = promote_object_insert(prev_direction, current)
    current = promote_reaction_closeup(prev_direction, current)
    current = soften_environment_after_dialogue(prev_direction, current)
    current = stabilize_tracking_sequences(prev_direction, current)
    current = avoid_repetitive_closeups(prev_direction, current)
    current = improve_scene_closing(current, is_last)

    return current


def refine_scene_directions(directions):
    """
    Mejora decisiones del director para evitar planos repetidos
    y crear lenguaje visual cinematográfico.
    """

    refined = []
    prev_shot = None
    prev_speaker = None

    for i, d in enumerate(directions):

        shot = d.get("shot", "")
        speaker = d.get("speaker", "")

        # -------------------------------------------------
        # Regla 1: evitar close_up repetidos
        # -------------------------------------------------

        if shot == "close_up" and prev_shot == "close_up":
            d["shot"] = "medium"
            shot = "medium"

        # -------------------------------------------------
        # Regla 2: diálogo alternado
        # -------------------------------------------------

        if speaker and prev_speaker and speaker != prev_speaker:

            if prev_shot not in ("over_the_shoulder", "medium_two_shot"):
                d["shot"] = "over_the_shoulder"

        # -------------------------------------------------
        # Regla 3: inicio de conversación
        # -------------------------------------------------

        if i == 0 and speaker:
            d["shot"] = "medium_two_shot"

        # -------------------------------------------------
        # Regla 4: reacción silenciosa
        # -------------------------------------------------

        if not speaker and prev_speaker:
            d["shot"] = "close_up"

        # -------------------------------------------------
        # Regla 5: evitar planos idénticos seguidos
        # -------------------------------------------------

        if prev_shot == d.get("shot"):
            if d["shot"] == "medium_two_shot":
                d["shot"] = "medium"
            elif d["shot"] == "medium":
                d["shot"] = "close_up"

        prev_shot = d.get("shot")
        prev_speaker = speaker

        refined.append(d)

    return refined

def apply_edit_decisions_to_beats(beats, directions: List[Dict[str, object]]):
    """
    Aplica las decisiones refinadas del montaje a los beat.attrs.
    """
    for beat, direction in zip(beats, directions):
        for key in (
            "shot",
            "framing",
            "camera",
            "audio",
            "video",
            "duration",
            "transition",
            "visual_style",
            "speech_rate",
            "pause_before",
            "pause_after",
            "voice_intensity",
            "bgm_level",
            "sfx_level",
            "music_tag",
        ):
            if direction.get(key) is not None:
                beat.attrs[key] = str(direction[key])

        if direction.get("focus"):
            beat.attrs["focus"] = str(direction["focus"])

    return beats


def build_scene_edit_plan(
    beats,
    scene_attrs: Dict[str, str],
    block_kind: str,
    direct_beat_fn,
):
    """
    1) Genera directions base usando direct_beat()
    2) Las refina con reglas de montaje
    """
    base_directions = []

    local_scene_attrs = dict(scene_attrs)

    state_manager = SceneStateManager()
    state_manager.start_scene(
        scene_id=local_scene_attrs.get("id", ""),
        block_kind=block_kind,
        location=local_scene_attrs.get("location", ""),
        characters=split_csv(local_scene_attrs.get("characters", "")),
        props=split_csv(local_scene_attrs.get("props", "")),
    )

    for beat_index, beat in enumerate(beats):
        direction = direct_beat_fn(
            text=beat.text,
            scene_attrs=local_scene_attrs,
            block_kind=block_kind,
            scene_index=0,
            beat_index=beat_index,
            scene_state_manager=state_manager,
        )
        base_directions.append(direction)

    refined_directions = refine_scene_directions(base_directions)
    refined_directions = plan_scene_shots(refined_directions)
    refined_directions = apply_dramatic_arc(refined_directions)
    refined_directions = apply_audio_drama_to_scene(refined_directions)

    return base_directions, refined_directions


# -------------------------------------------------
# DEBUG
# -------------------------------------------------
if __name__ == "__main__":
    sample_directions = [
        {
            "kind": "dialogue",
            "function_type": "statement",
            "speaker": "Javier",
            "characters": ["Javier", "Tomas"],
            "focus": "Javier",
            "shot": "close_up",
            "framing": "close_up",
            "camera": "static_subtle_push",
            "transition": "cut",
        },
        {
            "kind": "dialogue",
            "function_type": "confrontation",
            "speaker": "Tomas",
            "characters": ["Javier", "Tomas"],
            "focus": "Tomas",
            "shot": "close_up",
            "framing": "close_up",
            "camera": "static_subtle_push",
            "transition": "cut",
        },
        {
            "kind": "narration",
            "function_type": "reaction",
            "characters": ["Javier"],
            "focus": "Javier",
            "shot": "medium",
            "framing": "medium",
            "camera": "static",
            "transition": "cut",
        },
        {
            "kind": "narration",
            "function_type": "object_emphasis",
            "props": ["laptop"],
            "focus": "laptop",
            "shot": "medium",
            "framing": "medium",
            "camera": "static",
            "transition": "cut",
        },
    ]

    refined = refine_scene_directions(sample_directions)

    import json
    print(json.dumps(refined, ensure_ascii=False, indent=2))
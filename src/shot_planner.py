from typing import Dict, List


OPENING_SHOTS = {
    "dialogue": "medium_two_shot",
    "narration": "wide_establishing",
    "mixed": "medium",
}

REACTION_SHOTS = {"close_up", "medium_close_up"}
DIALOGUE_REPLY_SHOTS = {"over_the_shoulder", "close_up"}
ENVIRONMENT_SHOTS = {"wide_establishing"}
OBJECT_SHOTS = {"insert"}
ACTION_SHOTS = {"tracking", "medium"}

REPETITIVE_LIMITS = {
    "close_up": 2,
    "over_the_shoulder": 2,
    "medium_two_shot": 2,
    "medium": 3,
    "wide_establishing": 2,
    "insert": 2,
    "tracking": 2,
}


def safe_str(value) -> str:
    return str(value or "").strip()


def get_function(direction: Dict[str, object]) -> str:
    return safe_str(direction.get("function_type")) or "transition"


def get_kind(direction: Dict[str, object]) -> str:
    return safe_str(direction.get("kind")) or "narration"


def get_speaker(direction: Dict[str, object]) -> str:
    return safe_str(direction.get("speaker"))


def get_focus(direction: Dict[str, object]) -> str:
    return safe_str(direction.get("focus"))


def get_shot(direction: Dict[str, object]) -> str:
    return safe_str(direction.get("shot")) or "medium"


def set_shot(direction: Dict[str, object], shot: str):
    direction["shot"] = shot


def set_framing_for_shot(direction: Dict[str, object]):
    shot = get_shot(direction)

    framing_map = {
        "wide_establishing": "wide",
        "medium_two_shot": "two_shot",
        "over_the_shoulder": "medium_close_up",
        "close_up": "close_up",
        "medium_close_up": "medium_close_up",
        "insert": "detail",
        "tracking": "medium",
        "medium": "medium",
    }
    direction["framing"] = framing_map.get(shot, direction.get("framing", "medium"))


def set_camera_for_shot(direction: Dict[str, object]):
    shot = get_shot(direction)

    camera_map = {
        "wide_establishing": "slow_pan",
        "medium_two_shot": "static",
        "over_the_shoulder": "static",
        "close_up": "static_subtle_push",
        "medium_close_up": "static_subtle_push",
        "insert": "static",
        "tracking": "tracking_right",
        "medium": "static",
    }
    direction["camera"] = camera_map.get(shot, direction.get("camera", "static"))


def opening_shot_for_scene(direction: Dict[str, object]) -> str:
    scene_kind = get_kind(direction)
    function_type = get_function(direction)

    if function_type == "environment":
        return "wide_establishing"
    if function_type == "object_emphasis":
        return "insert"
    return OPENING_SHOTS.get(scene_kind, "medium")


def choose_dialogue_shot(prev_direction: Dict[str, object], current: Dict[str, object]) -> str:
    prev_speaker = get_speaker(prev_direction)
    curr_speaker = get_speaker(current)
    prev_focus = get_focus(prev_direction)
    curr_focus = get_focus(current)

    characters = current.get("characters", []) or []

    if not prev_direction:
        return "medium_two_shot" if len(characters) >= 2 else "close_up"

    if prev_speaker and curr_speaker and prev_speaker != curr_speaker:
        return "over_the_shoulder"

    if prev_focus and curr_focus and prev_focus != curr_focus:
        return "close_up"

    if len(characters) >= 2:
        return "medium_two_shot"

    return "close_up"


def choose_narration_shot(prev_direction: Dict[str, object], current: Dict[str, object]) -> str:
    function_type = get_function(current)

    if function_type == "environment":
        return "wide_establishing"
    if function_type == "object_emphasis":
        return "insert"
    if function_type == "reaction":
        return "close_up"
    if function_type == "action":
        return "tracking"

    prev_shot = get_shot(prev_direction) if prev_direction else ""
    if prev_shot == "close_up":
        return "medium"

    return "medium"


def count_recent_same_shot(planned: List[Dict[str, object]], shot: str) -> int:
    count = 0
    for item in reversed(planned):
        if get_shot(item) == shot:
            count += 1
        else:
            break
    return count


def diversify_shot_if_needed(planned: List[Dict[str, object]], current: Dict[str, object]) -> Dict[str, object]:
    d = dict(current)
    shot = get_shot(d)
    limit = REPETITIVE_LIMITS.get(shot, 2)
    repeated = count_recent_same_shot(planned, shot)

    if repeated < limit:
        return d

    function_type = get_function(d)
    kind = get_kind(d)

    alternatives = {
        "dialogue": ["over_the_shoulder", "close_up", "medium_two_shot", "medium"],
        "narration": ["medium", "close_up", "wide_establishing", "insert"],
        "mixed": ["medium", "close_up", "over_the_shoulder"],
    }

    if function_type == "object_emphasis":
        candidate_order = ["insert", "close_up", "medium"]
    elif function_type == "environment":
        candidate_order = ["wide_establishing", "medium", "close_up"]
    elif function_type == "action":
        candidate_order = ["tracking", "medium", "close_up"]
    elif function_type == "reaction":
        candidate_order = ["close_up", "medium_close_up", "medium"]
    else:
        candidate_order = alternatives.get(kind, ["medium", "close_up"])

    for candidate in candidate_order:
        if candidate != shot and count_recent_same_shot(planned, candidate) == 0:
            set_shot(d, candidate)
            set_framing_for_shot(d)
            set_camera_for_shot(d)
            return d

    return d


def improve_transition(prev_direction: Dict[str, object], current: Dict[str, object]) -> Dict[str, object]:
    d = dict(current)

    if not prev_direction:
        d["transition"] = "fade_in"
        return d

    prev_location = safe_str(prev_direction.get("location"))
    curr_location = safe_str(d.get("location"))
    prev_speaker = get_speaker(prev_direction)
    curr_speaker = get_speaker(d)
    prev_focus = get_focus(prev_direction)
    curr_focus = get_focus(d)

    if prev_location and curr_location and prev_location != curr_location:
        d["transition"] = "fade"
        return d

    if prev_speaker and curr_speaker and prev_speaker != curr_speaker:
        d["transition"] = "cut"
        return d

    if prev_focus and curr_focus and prev_focus != curr_focus:
        d["transition"] = "match_cut"
        return d

    if get_function(d) == "environment":
        d["transition"] = "dissolve"
        return d

    if get_function(d) == "object_emphasis":
        d["transition"] = "cut"
        return d

    d["transition"] = safe_str(d.get("transition")) or "cut"
    return d


def plan_single_shot(planned: List[Dict[str, object]], current: Dict[str, object], index: int, total: int) -> Dict[str, object]:
    d = dict(current)
    prev_direction = planned[-1] if planned else {}

    # 1) opening
    if index == 0:
        set_shot(d, opening_shot_for_scene(d))
        set_framing_for_shot(d)
        set_camera_for_shot(d)

    else:
        kind = get_kind(d)
        function_type = get_function(d)

        if kind == "dialogue":
            set_shot(d, choose_dialogue_shot(prev_direction, d))
        else:
            set_shot(d, choose_narration_shot(prev_direction, d))

        # function-based overrides
        if function_type == "object_emphasis":
            set_shot(d, "insert")
        elif function_type == "environment" and index < max(2, total // 4):
            set_shot(d, "wide_establishing")
        elif function_type == "reaction":
            if get_shot(prev_direction) == "close_up" and get_focus(prev_direction) == get_focus(d):
                set_shot(d, "medium")
            else:
                set_shot(d, "close_up")
        elif function_type == "action":
            set_shot(d, "tracking")

        set_framing_for_shot(d)
        set_camera_for_shot(d)

    # 2) diversify
    d = diversify_shot_if_needed(planned, d)

    # 3) ending beats
    if index == total - 1:
        if get_function(d) == "reaction":
            set_shot(d, "close_up")
            set_framing_for_shot(d)
            set_camera_for_shot(d)
            d["transition"] = "dissolve"
        elif get_function(d) == "environment":
            set_shot(d, "wide_establishing")
            set_framing_for_shot(d)
            set_camera_for_shot(d)
            d["transition"] = "fade"

    # 4) transition
    d = improve_transition(prev_direction, d)

    return d


def plan_scene_shots(directions: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """
    Toma una lista de directions base y devuelve una versión con
    planificación de planos más consistente.
    """
    planned: List[Dict[str, object]] = []
    total = len(directions)

    for idx, direction in enumerate(directions):
        planned_direction = plan_single_shot(planned, direction, idx, total)
        planned.append(planned_direction)

    return planned


if __name__ == "__main__":
    sample = [
        {
            "kind": "dialogue",
            "function_type": "statement",
            "speaker": "Javier",
            "focus": "Javier",
            "characters": ["Javier", "Tomas"],
            "location": "empty_stands",
            "shot": "close_up",
        },
        {
            "kind": "dialogue",
            "function_type": "confrontation",
            "speaker": "Tomas",
            "focus": "Tomas",
            "characters": ["Javier", "Tomas"],
            "location": "empty_stands",
            "shot": "close_up",
        },
        {
            "kind": "narration",
            "function_type": "reaction",
            "focus": "Javier",
            "characters": ["Javier"],
            "location": "empty_stands",
            "shot": "medium",
        },
        {
            "kind": "narration",
            "function_type": "object_emphasis",
            "focus": "laptop",
            "props": ["laptop"],
            "location": "empty_stands",
            "shot": "medium",
        },
    ]

    import json
    print(json.dumps(plan_scene_shots(sample), ensure_ascii=False, indent=2))
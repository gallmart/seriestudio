from typing import Dict, List


# -------------------------------------------------
# Intensidad base por función dramática
# -------------------------------------------------
FUNCTION_INTENSITY = {
    "environment": 1,
    "transition": 1,
    "object_emphasis": 2,
    "reaction": 2,
    "statement": 2,
    "action": 3,
    "confrontation": 4,
}


def clamp(value: int, min_value: int, max_value: int) -> int:
    return max(min_value, min(max_value, value))


def build_scene_intensity_curve(directions: List[Dict[str, object]]) -> List[int]:
    """
    Calcula una intensidad por beat combinando:
    - function_type
    - posición dentro de la escena
    """
    total = len(directions)
    if total == 0:
        return []

    intensities = []

    for idx, direction in enumerate(directions):
        function_type = direction.get("function_type", "transition")
        base = FUNCTION_INTENSITY.get(function_type, 1)

        # curva dramática simple:
        # inicio bajo, centro creciente, penúltimo/pico alto, último caída leve
        progress = idx / max(1, total - 1)

        arc_bonus = 0
        if progress < 0.20:
            arc_bonus = 0
        elif progress < 0.50:
            arc_bonus = 1
        elif progress < 0.80:
            arc_bonus = 2
        elif progress < 0.95:
            arc_bonus = 3
        else:
            arc_bonus = 1

        intensity = clamp(base + arc_bonus, 1, 5)
        intensities.append(intensity)

    return intensities


def apply_intensity_to_direction(direction: Dict[str, object], intensity: int, is_last: bool = False) -> Dict[str, object]:
    """
    Ajusta el lenguaje visual según intensidad.
    """
    d = dict(direction)

    current_shot = d.get("shot", "medium")
    function_type = d.get("function_type", "transition")
    kind = d.get("kind", "narration")

    # -----------------------------------------
    # Intensidad baja: respirar
    # -----------------------------------------
    if intensity == 1:
        if function_type == "environment":
            d["shot"] = "wide_establishing"
            d["framing"] = "wide"
            d["camera"] = "slow_pan"
            d["transition"] = "fade"
        else:
            if current_shot == "close_up":
                d["shot"] = "medium"
                d["framing"] = "medium"
            d["camera"] = "static"

    # -----------------------------------------
    # Intensidad media-baja
    # -----------------------------------------
    elif intensity == 2:
        if function_type == "reaction":
            d["shot"] = "close_up"
            d["framing"] = "close_up"
            d["camera"] = "static_subtle_push"
        elif function_type == "object_emphasis":
            d["shot"] = "insert"
            d["framing"] = "detail"
            d["camera"] = "static"
        else:
            if current_shot == "wide_establishing":
                d["camera"] = "slow_pan"
            else:
                d["camera"] = d.get("camera", "static")

    # -----------------------------------------
    # Intensidad media
    # -----------------------------------------
    elif intensity == 3:
        if kind == "dialogue":
            if current_shot == "medium_two_shot":
                d["camera"] = "static"
            elif current_shot in ("close_up", "over_the_shoulder"):
                d["camera"] = "static_subtle_push"
        elif function_type == "action":
            d["shot"] = "tracking"
            d["framing"] = "medium"
            d["camera"] = "tracking_right"

    # -----------------------------------------
    # Intensidad alta
    # -----------------------------------------
    elif intensity == 4:
        if function_type in ("confrontation", "statement") and kind == "dialogue":
            d["shot"] = "over_the_shoulder"
            d["framing"] = "medium_close_up"
            d["camera"] = "static_subtle_push"
            d["transition"] = "cut"
        elif function_type == "action":
            d["shot"] = "tracking"
            d["camera"] = "tracking_right"
            d["transition"] = "cut"
        elif function_type == "reaction":
            d["shot"] = "close_up"
            d["framing"] = "close_up"
            d["camera"] = "static_subtle_push"

    # -----------------------------------------
    # Intensidad máxima / clímax
    # -----------------------------------------
    elif intensity == 5:
        if function_type == "confrontation":
            d["shot"] = "close_up"
            d["framing"] = "close_up"
            d["camera"] = "static_subtle_push"
            d["transition"] = "cut"
        elif function_type == "action":
            d["shot"] = "tracking"
            d["framing"] = "medium"
            d["camera"] = "tracking_right"
            d["transition"] = "cut"
        elif function_type == "object_emphasis":
            d["shot"] = "insert"
            d["framing"] = "detail"
            d["camera"] = "static"
            d["transition"] = "cut"

    # -----------------------------------------
    # Final de escena: pequeña caída o cierre
    # -----------------------------------------
    if is_last:
        if d.get("function_type") == "reaction":
            d["transition"] = "dissolve"
        elif d.get("function_type") == "environment":
            d["transition"] = "fade"

    d["dramatic_intensity"] = intensity
    return d


def apply_dramatic_arc(directions: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """
    Aplica un arco dramático a una lista de directions ya calculadas.
    """
    intensities = build_scene_intensity_curve(directions)
    refined = []

    for idx, (direction, intensity) in enumerate(zip(directions, intensities)):
        is_last = idx == len(directions) - 1
        new_direction = apply_intensity_to_direction(direction, intensity, is_last=is_last)
        refined.append(new_direction)

    return refined


# -------------------------------------------------
# DEBUG
# -------------------------------------------------
if __name__ == "__main__":
    sample_directions = [
        {"function_type": "environment", "kind": "narration", "shot": "wide_establishing"},
        {"function_type": "statement", "kind": "dialogue", "shot": "medium_two_shot"},
        {"function_type": "statement", "kind": "dialogue", "shot": "over_the_shoulder"},
        {"function_type": "reaction", "kind": "narration", "shot": "close_up"},
        {"function_type": "confrontation", "kind": "dialogue", "shot": "over_the_shoulder"},
        {"function_type": "reaction", "kind": "narration", "shot": "close_up"},
    ]

    import json
    result = apply_dramatic_arc(sample_directions)
    print(json.dumps(result, ensure_ascii=False, indent=2))
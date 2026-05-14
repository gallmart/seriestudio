from typing import Dict, List


FUNCTION_AUDIO_PROFILE = {
    "environment": {
        "speech_rate": 0.92,
        "pause_before": 0.2,
        "pause_after": 0.4,
        "voice_intensity": "low",
        "bgm_level": "medium",
        "sfx_level": "medium",
        "prefer_silence": False,
    },
    "transition": {
        "speech_rate": 0.98,
        "pause_before": 0.15,
        "pause_after": 0.2,
        "voice_intensity": "low",
        "bgm_level": "low",
        "sfx_level": "low",
        "prefer_silence": False,
    },
    "object_emphasis": {
        "speech_rate": 0.95,
        "pause_before": 0.1,
        "pause_after": 0.25,
        "voice_intensity": "low",
        "bgm_level": "low",
        "sfx_level": "high",
        "prefer_silence": False,
    },
    "reaction": {
        "speech_rate": 0.88,
        "pause_before": 0.25,
        "pause_after": 0.35,
        "voice_intensity": "medium",
        "bgm_level": "low",
        "sfx_level": "low",
        "prefer_silence": True,
    },
    "statement": {
        "speech_rate": 1.0,
        "pause_before": 0.05,
        "pause_after": 0.12,
        "voice_intensity": "medium",
        "bgm_level": "low",
        "sfx_level": "low",
        "prefer_silence": False,
    },
    "confrontation": {
        "speech_rate": 1.03,
        "pause_before": 0.02,
        "pause_after": 0.08,
        "voice_intensity": "high",
        "bgm_level": "low",
        "sfx_level": "low",
        "prefer_silence": True,
    },
    "action": {
        "speech_rate": 1.05,
        "pause_before": 0.0,
        "pause_after": 0.06,
        "voice_intensity": "high",
        "bgm_level": "medium",
        "sfx_level": "high",
        "prefer_silence": False,
    },
}


INTENSITY_AUDIO_ADJUSTMENTS = {
    1: {
        "speech_rate_delta": -0.05,
        "pause_before_delta": 0.10,
        "pause_after_delta": 0.10,
        "bgm_push": "up",
    },
    2: {
        "speech_rate_delta": -0.02,
        "pause_before_delta": 0.05,
        "pause_after_delta": 0.05,
        "bgm_push": "flat",
    },
    3: {
        "speech_rate_delta": 0.00,
        "pause_before_delta": 0.00,
        "pause_after_delta": 0.00,
        "bgm_push": "flat",
    },
    4: {
        "speech_rate_delta": 0.03,
        "pause_before_delta": -0.03,
        "pause_after_delta": -0.03,
        "bgm_push": "down",
    },
    5: {
        "speech_rate_delta": 0.05,
        "pause_before_delta": -0.05,
        "pause_after_delta": -0.04,
        "bgm_push": "down",
    },
}


SCENE_KIND_AUDIO_DEFAULTS = {
    "dialogue": {
        "duck_bgm_under_voice": True,
        "prefer_character_voice": True,
    },
    "narration": {
        "duck_bgm_under_voice": False,
        "prefer_character_voice": False,
    },
    "mixed": {
        "duck_bgm_under_voice": True,
        "prefer_character_voice": True,
    },
}


def clamp_float(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def shift_level(level: str, direction: str) -> str:
    order = ["off", "low", "medium", "high"]
    if level not in order:
        return level

    idx = order.index(level)

    if direction == "up":
        idx = min(len(order) - 1, idx + 1)
    elif direction == "down":
        idx = max(0, idx - 1)

    return order[idx]


def infer_music_tag(direction: Dict[str, object]) -> str:
    visual_style = direction.get("visual_style", "")
    function_type = direction.get("function_type", "")
    emotion = direction.get("emotion", "")

    if visual_style == "sumi_e":
        return "minimal_contemplative"
    if visual_style == "datos":
        return "cold_analytical_pulse"
    if visual_style == "tradicion":
        return "warm_human_drama"
    if visual_style == "choque_mundos":
        return "strategic_tension"

    if function_type == "confrontation":
        return "dramatic_tension"
    if function_type == "action":
        return "kinetic_pulse"
    if function_type == "environment":
        return "ambient_space"
    if emotion == "reflection":
        return "introspective_sparse"

    return "neutral_underscore"


def infer_sfx_tags(direction: Dict[str, object]) -> List[str]:
    tags = []

    function_type = direction.get("function_type", "")
    location = direction.get("location", "")
    shot = direction.get("shot", "")
    props = direction.get("props", []) or []

    if location:
        tags.append(f"ambience:{location}")

    if function_type == "action":
        tags.append("movement")
    if function_type == "confrontation":
        tags.append("room_tension")
    if function_type == "environment":
        tags.append("environment_presence")
    if shot == "insert":
        tags.append("object_detail")

    for prop in props[:2]:
        tags.append(f"prop:{prop}")

    return tags


def build_audio_direction(direction: Dict[str, object]) -> Dict[str, object]:
    function_type = direction.get("function_type", "transition")
    scene_kind = direction.get("kind", "narration")
    dramatic_intensity = int(direction.get("dramatic_intensity", 3) or 3)

    base = FUNCTION_AUDIO_PROFILE.get(function_type, FUNCTION_AUDIO_PROFILE["transition"])
    adj = INTENSITY_AUDIO_ADJUSTMENTS.get(dramatic_intensity, INTENSITY_AUDIO_ADJUSTMENTS[3])
    scene_defaults = SCENE_KIND_AUDIO_DEFAULTS.get(scene_kind, SCENE_KIND_AUDIO_DEFAULTS["narration"])

    speech_rate = clamp_float(base["speech_rate"] + adj["speech_rate_delta"], 0.78, 1.12)
    pause_before = clamp_float(base["pause_before"] + adj["pause_before_delta"], 0.0, 0.6)
    pause_after = clamp_float(base["pause_after"] + adj["pause_after_delta"], 0.0, 0.8)

    bgm_level = shift_level(base["bgm_level"], adj["bgm_push"])
    sfx_level = base["sfx_level"]

    if function_type == "confrontation":
        bgm_level = "off"
    if function_type == "reaction" and dramatic_intensity >= 4:
        bgm_level = "off"
    if function_type == "object_emphasis" and direction.get("shot") == "insert":
        sfx_level = "high"

    result = {
        "speech_rate": round(speech_rate, 2),
        "pause_before": round(pause_before, 2),
        "pause_after": round(pause_after, 2),
        "voice_intensity": base["voice_intensity"],
        "duck_bgm_under_voice": scene_defaults["duck_bgm_under_voice"],
        "prefer_character_voice": scene_defaults["prefer_character_voice"],
        "bgm_level": bgm_level,
        "sfx_level": sfx_level,
        "prefer_silence": base["prefer_silence"],
        "music_tag": infer_music_tag(direction),
        "sfx_tags": infer_sfx_tags(direction),
    }

    return result


def apply_audio_drama_to_direction(direction: Dict[str, object]) -> Dict[str, object]:
    d = dict(direction)
    audio_plan = build_audio_direction(d)

    d["audio_plan"] = audio_plan

    # Campos cómodos para el pipeline
    d["speech_rate"] = audio_plan["speech_rate"]
    d["pause_before"] = audio_plan["pause_before"]
    d["pause_after"] = audio_plan["pause_after"]
    d["voice_intensity"] = audio_plan["voice_intensity"]
    d["bgm_level"] = audio_plan["bgm_level"]
    d["sfx_level"] = audio_plan["sfx_level"]
    d["music_tag"] = audio_plan["music_tag"]

    return d


def apply_audio_drama_to_scene(directions: List[Dict[str, object]]) -> List[Dict[str, object]]:
    return [apply_audio_drama_to_direction(d) for d in directions]


if __name__ == "__main__":
    sample = [
        {
            "kind": "narration",
            "function_type": "environment",
            "visual_style": "sumi_e",
            "location": "stadium_exterior",
            "dramatic_intensity": 1,
            "shot": "wide_establishing",
        },
        {
            "kind": "dialogue",
            "function_type": "confrontation",
            "visual_style": "choque_mundos",
            "location": "empty_stands",
            "dramatic_intensity": 5,
            "shot": "close_up",
            "speaker": "Tomas",
        },
    ]

    import json
    print(json.dumps(apply_audio_drama_to_scene(sample), ensure_ascii=False, indent=2))
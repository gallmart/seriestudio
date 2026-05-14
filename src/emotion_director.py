import re
from typing import Dict


# -----------------------------------
# Diccionarios emocionales simples
# -----------------------------------

EMOTION_LEXICON = {
    "tension": [
        "tensión", "tenso", "miró fijamente", "silencio incómodo",
        "amenaza", "peligro", "conflicto", "desafío"
    ],
    "calm": [
        "calma", "tranquilo", "respiró", "sereno", "silencio",
        "observó", "miró alrededor"
    ],
    "conflict": [
        "discutió", "enfrentó", "acusó", "gritó", "enfadado",
        "ira", "rabia", "golpeó", "rechazó"
    ],
    "reflection": [
        "pensó", "recordó", "reflexionó", "dudó", "miró al suelo",
        "suspiró", "meditó"
    ],
    "strategy": [
        "plan", "estrategia", "cálculo", "analizó", "decidió",
        "balance", "probabilidad"
    ]
}


# -----------------------------------
# Detectar emoción dominante
# -----------------------------------

def detect_emotion(text: str) -> str:

    text_lower = text.lower()

    scores = {}

    for emotion, keywords in EMOTION_LEXICON.items():
        score = 0
        for word in keywords:
            if re.search(r"\b" + re.escape(word) + r"\b", text_lower):
                score += 1
        if score:
            scores[emotion] = score

    if not scores:
        return "neutral"

    return max(scores, key=scores.get)


# -----------------------------------
# Aplicar estilo cinematográfico
# -----------------------------------

def apply_emotion_to_direction(direction: Dict, emotion: str) -> Dict:

    # copia para no modificar original
    d = dict(direction)

    # ------------------------------
    # tensión
    # ------------------------------

    if emotion == "tension":

        if d.get("shot") not in ("close_up", "over_the_shoulder"):
            d["shot"] = "close_up"

        d["visual_style"] = "choque_mundos"
        d["camera"] = "static_subtle_push"

    # ------------------------------
    # conflicto
    # ------------------------------

    elif emotion == "conflict":

        d["shot"] = "over_the_shoulder"
        d["camera"] = "tracking_right"
        d["visual_style"] = "tradicion"

    # ------------------------------
    # calma
    # ------------------------------

    elif emotion == "calm":

        if d.get("shot") == "close_up":
            d["shot"] = "medium"

        d["camera"] = "static"
        d["visual_style"] = "tradicion"

    # ------------------------------
    # reflexión
    # ------------------------------

    elif emotion == "reflection":

        d["shot"] = "close_up"
        d["camera"] = "static_subtle_push"
        d["visual_style"] = "sumi_e"

    # ------------------------------
    # estrategia
    # ------------------------------

    elif emotion == "strategy":

        if d.get("shot") not in ("medium", "medium_two_shot"):
            d["shot"] = "medium"

        d["visual_style"] = "datos"

    return d


# -----------------------------------
# Función principal
# -----------------------------------

def apply_emotion_direction(text: str, direction: Dict) -> Dict:

    emotion = detect_emotion(text)

    return apply_emotion_to_direction(direction, emotion)
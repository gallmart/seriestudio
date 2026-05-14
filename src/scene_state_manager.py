from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class SceneState:
    """
    Estado acumulado de una escena para mantener continuidad visual.
    """
    scene_id: str = ""
    block_kind: str = ""
    location: str = ""

    active_characters: List[str] = field(default_factory=list)
    active_props: List[str] = field(default_factory=list)

    previous_focus: str = ""
    previous_speaker: str = ""
    previous_shot: str = ""
    previous_framing: str = ""
    previous_camera: str = ""
    previous_transition: str = ""
    previous_visual_style: str = ""

    # eje visual / orientación simple
    screen_left_character: str = ""
    screen_right_character: str = ""

    # historial corto para evitar repeticiones
    recent_shots: List[str] = field(default_factory=list)
    recent_focuses: List[str] = field(default_factory=list)
    recent_speakers: List[str] = field(default_factory=list)

    beat_index: int = 0


class SceneStateManager:
    """
    Gestiona continuidad dentro de una escena.
    """

    def __init__(self, max_history: int = 5):
        self.max_history = max_history
        self.state = SceneState()

    # -------------------------------------------------
    # Ciclo de escena
    # -------------------------------------------------
    def start_scene(
        self,
        scene_id: str,
        block_kind: str = "",
        location: str = "",
        characters: Optional[List[str]] = None,
        props: Optional[List[str]] = None,
    ) -> SceneState:
        self.state = SceneState(
            scene_id=scene_id or "",
            block_kind=block_kind or "",
            location=location or "",
            active_characters=list(characters or []),
            active_props=list(props or []),
        )

        # si hay dos personajes, fijar eje inicial simple
        if len(self.state.active_characters) >= 2:
            self.state.screen_left_character = self.state.active_characters[0]
            self.state.screen_right_character = self.state.active_characters[1]
        elif len(self.state.active_characters) == 1:
            self.state.screen_left_character = self.state.active_characters[0]

        return self.state

    def get_state(self) -> SceneState:
        return self.state

    # -------------------------------------------------
    # Actualización de entidades
    # -------------------------------------------------
    def merge_characters(self, characters: List[str]):
        for c in characters:
            if c and c not in self.state.active_characters:
                self.state.active_characters.append(c)

        if not self.state.screen_left_character and self.state.active_characters:
            self.state.screen_left_character = self.state.active_characters[0]

        if len(self.state.active_characters) >= 2 and not self.state.screen_right_character:
            self.state.screen_right_character = self.state.active_characters[1]

    def merge_props(self, props: List[str]):
        for p in props:
            if p and p not in self.state.active_props:
                self.state.active_props.append(p)

    # -------------------------------------------------
    # Reglas de continuidad
    # -------------------------------------------------
    def get_conversation_roles(self, current_speaker: str) -> Dict[str, str]:
        """
        Devuelve foreground/background para OTS o contraplano.
        """
        left_char = self.state.screen_left_character
        right_char = self.state.screen_right_character
        previous_speaker = self.state.previous_speaker

        if current_speaker and previous_speaker and current_speaker != previous_speaker:
            return {
                "foreground": previous_speaker,
                "background": current_speaker,
            }

        if current_speaker and left_char and right_char:
            if current_speaker == left_char:
                return {
                    "foreground": right_char,
                    "background": left_char,
                }
            if current_speaker == right_char:
                return {
                    "foreground": left_char,
                    "background": right_char,
                }

        return {
            "foreground": left_char or "",
            "background": current_speaker or right_char or left_char or "",
        }

    def suggest_focus(self, explicit_focus: str = "", current_speaker: str = "", detected_characters: Optional[List[str]] = None, detected_props: Optional[List[str]] = None) -> str:
        if explicit_focus:
            return explicit_focus

        if current_speaker:
            return current_speaker

        detected_characters = detected_characters or []
        detected_props = detected_props or []

        if detected_characters:
            return detected_characters[0]

        if detected_props:
            return detected_props[0]

        if self.state.previous_focus:
            return self.state.previous_focus

        return ""

    def should_avoid_same_closeup(self, current_focus: str, current_shot: str) -> bool:
        return (
            self.state.previous_shot == "close_up"
            and current_shot == "close_up"
            and self.state.previous_focus == current_focus
        )

    def should_force_two_shot_opening(self, beat_index: int, characters: List[str], scene_kind: str) -> bool:
        return beat_index == 0 and scene_kind == "dialogue" and len(characters) >= 2

    def should_force_reaction_shot(self, function_type: str, current_focus: str) -> bool:
        return (
            function_type == "reaction"
            and current_focus
            and current_focus != self.state.previous_focus
        )

    def suggest_transition(
        self,
        current_location: str,
        current_focus: str,
        current_speaker: str,
        scene_kind: str,
    ) -> str:
        if self.state.beat_index == 0:
            return "fade_in"

        if self.state.location and current_location and self.state.location != current_location:
            return "fade"

        if (
            self.state.previous_speaker
            and current_speaker
            and self.state.previous_speaker != current_speaker
            and scene_kind == "dialogue"
        ):
            return "cut"

        if (
            self.state.previous_focus
            and current_focus
            and self.state.previous_focus != current_focus
        ):
            return "match_cut"

        if scene_kind == "narration":
            return "dissolve"

        return "cut"

    # -------------------------------------------------
    # Historial
    # -------------------------------------------------
    def _push_recent(self, arr: List[str], value: str):
        if not value:
            return
        arr.append(value)
        if len(arr) > self.max_history:
            del arr[0]

    def update_after_beat(self, direction: Dict[str, object]):
        """
        Actualiza el estado con el resultado final del beat.
        """
        location = str(direction.get("location", "") or "")
        focus = str(direction.get("focus", "") or "")
        speaker = str(direction.get("speaker", "") or "")
        shot = str(direction.get("shot", "") or "")
        framing = str(direction.get("framing", "") or "")
        camera = str(direction.get("camera", "") or "")
        transition = str(direction.get("transition", "") or "")
        visual_style = str(direction.get("visual_style", "") or "")

        characters = list(direction.get("characters", []) or [])
        props = list(direction.get("props", []) or [])

        if location:
            self.state.location = location

        self.merge_characters(characters)
        self.merge_props(props)

        self.state.previous_focus = focus or self.state.previous_focus
        self.state.previous_speaker = speaker or self.state.previous_speaker
        self.state.previous_shot = shot or self.state.previous_shot
        self.state.previous_framing = framing or self.state.previous_framing
        self.state.previous_camera = camera or self.state.previous_camera
        self.state.previous_transition = transition or self.state.previous_transition
        self.state.previous_visual_style = visual_style or self.state.previous_visual_style

        self._push_recent(self.state.recent_shots, shot)
        self._push_recent(self.state.recent_focuses, focus)
        self._push_recent(self.state.recent_speakers, speaker)

        self.state.beat_index += 1

    # -------------------------------------------------
    # Export simple para integraciones
    # -------------------------------------------------
    def to_history_dict(self, current_focus: str = "", current_speaker: str = "") -> Dict[str, str]:
        roles = self.get_conversation_roles(current_speaker=current_speaker)

        return {
            "current_speaker": current_speaker or "",
            "previous_speaker": self.state.previous_speaker or "",
            "current_focus": current_focus or "",
            "previous_focus": self.state.previous_focus or "",
            "previous_shot": self.state.previous_shot or "",
            "previous_location": self.state.location or "",
            "foreground_character": roles.get("foreground", ""),
            "background_character": roles.get("background", ""),
        }
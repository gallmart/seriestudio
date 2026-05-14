from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class BlockingPosition:
    character: str
    side: str = "center"          # left / right / center / background
    depth: str = "mid"            # foreground / mid / background
    priority: int = 0


@dataclass
class SceneBlockingState:
    scene_id: str = ""
    block_kind: str = ""
    location: str = ""
    positions: Dict[str, BlockingPosition] = field(default_factory=dict)
    dominant_character: str = ""
    last_focus: str = ""
    established_axis: bool = False


class SceneBlockingEngine:
    """
    Mantiene continuidad espacial simple dentro de una escena.
    """

    def __init__(self):
        self.state = SceneBlockingState()

    # -------------------------------------------------
    # Inicio de escena
    # -------------------------------------------------
    def start_scene(
        self,
        scene_id: str,
        block_kind: str = "",
        location: str = "",
        characters: Optional[List[str]] = None,
    ) -> SceneBlockingState:
        self.state = SceneBlockingState(
            scene_id=scene_id or "",
            block_kind=block_kind or "",
            location=location or "",
        )

        characters = list(characters or [])

        if len(characters) == 1:
            self.state.positions[characters[0]] = BlockingPosition(
                character=characters[0],
                side="center",
                depth="mid",
                priority=1,
            )
            self.state.dominant_character = characters[0]
            self.state.established_axis = True

        elif len(characters) >= 2:
            self.state.positions[characters[0]] = BlockingPosition(
                character=characters[0],
                side="left",
                depth="mid",
                priority=1,
            )
            self.state.positions[characters[1]] = BlockingPosition(
                character=characters[1],
                side="right",
                depth="mid",
                priority=1,
            )

            for extra in characters[2:]:
                self.state.positions[extra] = BlockingPosition(
                    character=extra,
                    side="background",
                    depth="background",
                    priority=0,
                )

            self.state.dominant_character = characters[0]
            self.state.established_axis = True

        return self.state

    # -------------------------------------------------
    # Merge de personajes
    # -------------------------------------------------
    def ensure_characters(self, characters: List[str]):
        existing = set(self.state.positions.keys())

        for char in characters:
            if char in existing:
                continue

            # si todavía no hay eje, ocupar huecos básicos
            if not self.state.established_axis:
                if not any(p.side == "left" for p in self.state.positions.values()):
                    self.state.positions[char] = BlockingPosition(char, "left", "mid", 1)
                elif not any(p.side == "right" for p in self.state.positions.values()):
                    self.state.positions[char] = BlockingPosition(char, "right", "mid", 1)
                    self.state.established_axis = True
                else:
                    self.state.positions[char] = BlockingPosition(char, "background", "background", 0)
            else:
                # si ya hay eje, personajes nuevos se quedan al fondo
                self.state.positions[char] = BlockingPosition(char, "background", "background", 0)

    # -------------------------------------------------
    # Consultas de posición
    # -------------------------------------------------
    def get_position(self, character: str) -> BlockingPosition:
        if character not in self.state.positions:
            self.state.positions[character] = BlockingPosition(character, "background", "background", 0)
        return self.state.positions[character]

    def get_left_character(self) -> str:
        for c, pos in self.state.positions.items():
            if pos.side == "left":
                return c
        return ""

    def get_right_character(self) -> str:
        for c, pos in self.state.positions.items():
            if pos.side == "right":
                return c
        return ""

    def get_center_character(self) -> str:
        for c, pos in self.state.positions.items():
            if pos.side == "center":
                return c
        return ""

    # -------------------------------------------------
    # Roles conversacionales
    # -------------------------------------------------
    def get_dialogue_roles(self, current_speaker: str = "", previous_speaker: str = "") -> Dict[str, str]:
        """
        Devuelve foreground/background respetando el eje visual.
        """
        left_char = self.get_left_character()
        right_char = self.get_right_character()
        center_char = self.get_center_character()

        # escena de un solo personaje
        if center_char and not left_char and not right_char:
            return {
                "foreground": "",
                "background": current_speaker or center_char,
            }

        # diálogo con cambio de speaker
        if current_speaker and previous_speaker and current_speaker != previous_speaker:
            return {
                "foreground": previous_speaker,
                "background": current_speaker,
            }

        # diálogo con speaker actual y eje ya fijado
        if current_speaker:
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
            "foreground": left_char,
            "background": current_speaker or right_char or left_char or center_char,
        }

    # -------------------------------------------------
    # Sugerencias por tipo de plano
    # -------------------------------------------------
    def suggest_characters_for_shot(
        self,
        shot: str,
        focus: str = "",
        current_speaker: str = "",
        previous_speaker: str = "",
        detected_characters: Optional[List[str]] = None,
    ) -> List[str]:
        detected_characters = list(detected_characters or [])
        self.ensure_characters(detected_characters)

        if shot == "over_the_shoulder":
            roles = self.get_dialogue_roles(
                current_speaker=current_speaker,
                previous_speaker=previous_speaker,
            )
            result = [roles.get("foreground", ""), roles.get("background", "")]
            return [x for x in result if x]

        if shot == "medium_two_shot":
            left_char = self.get_left_character()
            right_char = self.get_right_character()

            if left_char and right_char:
                return [left_char, right_char]

            if len(detected_characters) >= 2:
                return detected_characters[:2]

            return detected_characters[:1]

        if shot == "close_up":
            if focus:
                return [focus]
            if current_speaker:
                return [current_speaker]
            if detected_characters:
                return [detected_characters[0]]

        if shot == "wide_establishing":
            # para wide, mantener personajes ya establecidos
            left_char = self.get_left_character()
            right_char = self.get_right_character()
            center_char = self.get_center_character()

            result = []
            if left_char:
                result.append(left_char)
            if right_char:
                result.append(right_char)
            if center_char:
                result.append(center_char)

            if not result:
                result = detected_characters[:2]

            return result

        # fallback
        if focus:
            return [focus]

        if detected_characters:
            return detected_characters[:2]

        return []

    # -------------------------------------------------
    # Ajuste fino del eje
    # -------------------------------------------------
    def promote_focus(self, focus: str):
        if not focus:
            return
        self.state.last_focus = focus
        self.state.dominant_character = focus

        if focus in self.state.positions:
            self.state.positions[focus].priority = 2

    def move_to_background(self, character: str):
        if character in self.state.positions:
            self.state.positions[character].side = "background"
            self.state.positions[character].depth = "background"
            self.state.positions[character].priority = 0

    def stabilize_two_shot_axis(self, characters: List[str]):
        self.ensure_characters(characters)

        if len(characters) < 2:
            return

        left_char = self.get_left_character()
        right_char = self.get_right_character()

        # si no hay eje, fijarlo
        if not left_char and not right_char:
            self.state.positions[characters[0]].side = "left"
            self.state.positions[characters[1]].side = "right"
            self.state.positions[characters[0]].depth = "mid"
            self.state.positions[characters[1]].depth = "mid"
            self.state.established_axis = True
            return

        # no tocar si ya están fijados
        for char in characters:
            if char not in self.state.positions:
                self.state.positions[char] = BlockingPosition(char, "background", "background", 0)

    # -------------------------------------------------
    # Export simple
    # -------------------------------------------------
    def export_layout(self) -> Dict[str, Dict[str, str]]:
        return {
            char: {
                "side": pos.side,
                "depth": pos.depth,
                "priority": str(pos.priority),
            }
            for char, pos in self.state.positions.items()
        }
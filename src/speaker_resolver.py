from typing import Dict, List, Optional, Tuple


def normalize(text: str) -> str:
    if not text:
        return ""
    return (
        text.lower()
        .replace("á", "a").replace("é", "e").replace("í", "i")
        .replace("ó", "o").replace("ú", "u").replace("ü", "u")
        .replace("ñ", "n").strip()
    )


def ordered_unique(items: List[str]) -> List[str]:
    seen, out = set(), []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


class SpeakerResolver:
    def __init__(
        self,
        character_aliases: Dict[str, List[str]],
        aliases: Dict[str, str],
        dialogue_entities: Optional[Dict[str, object]] = None,
        dialogue_rules: Optional[Dict[str, object]] = None
    ):
        self.character_aliases = character_aliases or {}
        self.aliases = aliases or {}
        self.dialogue_entities = dialogue_entities or {}
        self.dialogue_rules = dialogue_rules or {}

        self.alias_to_character = self._build_alias_to_character()

        self.vocative_aliases = {
            normalize(k): v
            for k, v in (self.dialogue_entities.get("vocative_aliases", {}) or {}).items()
        }
        self.non_speaker_vocatives = {
            normalize(v)
            for v in (self.dialogue_entities.get("non_speaker_vocatives", []) or [])
        }

        thresholds = self.dialogue_rules.get("confidence_thresholds", {}) or {}
        scoring = self.dialogue_rules.get("scoring", {}) or {}

        self.speaker_min = thresholds.get("speaker_min", 0.45)
        self.explicit_attribution_score = scoring.get("explicit_attribution", 0.70)
        self.focus_bonus = scoring.get("focus_bonus", 0.15)
        self.explicit_mention_bonus = scoring.get("explicit_mention_bonus", 0.08)
        self.vocative_penalty = scoring.get("vocative_penalty", 0.20)
        self.two_character_turn_penalty = scoring.get("two_character_turn_penalty", 0.10)
        self.multi_character_previous_speaker_bonus = scoring.get("multi_character_previous_speaker_bonus", 0.05)

    def _build_alias_to_character(self) -> Dict[str, str]:
        mapping = {}
        for canonical, aliases in self.character_aliases.items():
            mapping[normalize(canonical)] = canonical
            for alias in aliases or []:
                mapping[normalize(alias)] = canonical
        return mapping

    def resolve_entity(self, value: str) -> Optional[str]:
        if not value:
            return None

        raw = value.strip()
        aliased = self.aliases.get(raw, self.aliases.get(raw.strip(), raw)).strip()

        return (
            self.alias_to_character.get(normalize(aliased))
            or self.alias_to_character.get(normalize(raw))
        )

    def extract_vocatives(self, spoken_text: str) -> List[str]:
        if not spoken_text:
            return []

        first_clause = (
            spoken_text
            .split(".")[0]
            .split("!")[0]
            .split("?")[0]
            .strip()
        )

        chunks = [c.strip(" ,:;¡!¿?\"“”'") for c in first_clause.split(",")]
        out = []

        # 1) Resolver chunks completos por aliases normales
        for chunk in chunks[:3]:
            canonical = self.resolve_entity(chunk)
            if canonical:
                out.append(canonical)

        # 2) Resolver por vocative_aliases configurados
        norm_clause = normalize(first_clause)
        for alias_norm, canonical in self.vocative_aliases.items():
            if f" {alias_norm} " in f" {norm_clause} ":
                out.append(canonical)

        # 3) Resolver por aliases generales dentro del primer tramo
        for alias_norm, canonical in self.alias_to_character.items():
            if alias_norm and f" {alias_norm} " in f" {norm_clause} ":
                out.append(canonical)

        return ordered_unique(out)

    def score_candidates(
        self,
        attribution_entity: Optional[str],
        spoken_text: str,
        explicit_characters: List[str],
        focus: Optional[str] = None,
        previous_speaker: Optional[str] = None,
        active_characters: Optional[List[str]] = None
    ) -> Tuple[Optional[str], float, List[str], Optional[str]]:
        candidates = ordered_unique((active_characters or []) + explicit_characters)

        if attribution_entity:
            candidates = ordered_unique([attribution_entity] + candidates)
        if focus:
            candidates = ordered_unique([focus] + candidates)
        if previous_speaker:
            candidates = ordered_unique([previous_speaker] + candidates)

        if not candidates:
            return None, 0.0, ["sin candidatos"], None

        scores = {c: 0.0 for c in candidates}
        reasons = {c: [] for c in candidates}

        if attribution_entity in scores:
            scores[attribution_entity] += self.explicit_attribution_score
            reasons[attribution_entity].append("atribución explícita o alias en inciso")

        if focus in scores:
            scores[focus] += self.focus_bonus
            reasons[focus].append("coincide con el foco actual")

        vocatives = self.extract_vocatives(spoken_text)
        addressee = vocatives[0] if vocatives else None

        for v in vocatives:
            if v in scores:
                scores[v] -= self.vocative_penalty
                reasons[v].append("nombre invocado dentro del diálogo; probable destinatario")

        if previous_speaker in scores and len(candidates) == 2:
            scores[previous_speaker] -= self.two_character_turn_penalty
            reasons[previous_speaker].append("alternancia probable de turno")

        if previous_speaker in scores and len(candidates) > 2:
            scores[previous_speaker] += self.multi_character_previous_speaker_bonus
            reasons[previous_speaker].append("continuidad conversacional reciente")

        for explicit in explicit_characters:
            if explicit in scores:
                scores[explicit] += self.explicit_mention_bonus
                reasons[explicit].append("personaje mencionado en el beat")

        best = max(scores, key=scores.get)
        conf = max(0.0, min(0.99, scores[best]))

        return (
            best if conf >= self.speaker_min else None,
            conf,
            reasons[best] or ["confianza insuficiente"],
            addressee
        )
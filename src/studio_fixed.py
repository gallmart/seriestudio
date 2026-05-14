import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from studio_book import book_to_text, format_attrs, parse_attrs, parse_book, parse_structured_text
from studio_ids import choose_inserted_beat_id, ensure_ids, infer_next_id, renumber_scene_beats
from studio_models import Beat, Block, Episode, Scene
from studio_ops import (
    delete_beat,
    insert_empty_beat,
    move_beat,
    move_block,
    move_scene,
    split_beat_by_marker,
    split_beat_by_paragraph,
    split_beat_manually,
    split_block_from_scene,
    split_scene_from_beat,
)

import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="Project Studio", layout="wide")

DEFAULT_JSON_FILES = [
    "assets_manifest.json",
    "audio_manifest.json",
    "style_rules.json",
    "aliases.json",
    "character_aliases.json",
    "location_keywords.json",
    "function_rules.json",
    "shot_rules.json",
    "transition_rules.json",
    "director_defaults.json",
    "beat_overrides.json",
    "dialogue_rules.json",
    "dialogue_entities.json",
    "metadata_schema.json",
    "segmentation_marks.json",
]

# --------------------------------------------------
# Basic IO
# --------------------------------------------------
def load_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return default


def save_text(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def create_timestamped_backup_if_needed(path: Path) -> Optional[Path]:
    target = Path(path)
    if target.name.lower() != "libro.txt" or not target.exists():
        return None

    backup_dir = target.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"{target.stem}_backup_{timestamp}{target.suffix}"

    counter = 1
    while backup_path.exists():
        backup_path = backup_dir / f"{target.stem}_backup_{timestamp}_{counter:02d}{target.suffix}"
        counter += 1

    shutil.copy2(target, backup_path)
    return backup_path


def ensure_open_backup(path: Path, force: bool = False) -> Optional[Path]:
    target = Path(path)
    if target.name.lower() != "libro.txt" or not target.exists():
        return None
    session_key = f"open_backup_done::{target.resolve()}"
    if force or not st.session_state.get(session_key):
        backup_path = create_timestamped_backup_if_needed(target)
        st.session_state[session_key] = True
        return backup_path
    return None


def save_book_text(path: Path, content: str) -> Optional[Path]:
    backup_path = create_timestamped_backup_if_needed(path)
    save_text(path, content)
    return backup_path


def save_book_text_without_backup(path: Path, content: str):
    save_text(path, content)


def autosave_working_book(root: Path, episodes: List["Episode"]):
    book_path = get_active_book_path(root)
    ensure_ids(episodes)
    serialized = book_to_text(episodes)
    hash_key = f"autosave_book_hash::{book_path}"
    current_hash = hash(serialized)
    if st.session_state.get(hash_key) != current_hash:
        save_book_text_without_backup(book_path, serialized)
        st.session_state[hash_key] = current_hash
    st.session_state["working_book_path"] = str(book_path)


def load_json_file(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json_file(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def json_valid(text: str):
    try:
        return True, json.loads(text), ""
    except Exception as e:
        return False, None, str(e)


# --------------------------------------------------
# Helpers
# --------------------------------------------------
def split_csv(value: str) -> List[str]:
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def normalize(text: str) -> str:
    if not text:
        return ""
    return (
        text.lower()
        .replace("á", "a")
        .replace("é", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ú", "u")
        .replace("ü", "u")
        .replace("ñ", "n")
    )


def contains_whole_phrase(text: str, phrase: str) -> bool:
    return re.search(r"\b" + re.escape(normalize(phrase)) + r"\b", normalize(text)) is not None


def ordered_unique(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


BEAT_FIELD_HELP = {
    "beat.id": "Identificador visible del beat dentro de la estructura. Conviene que sea estable para poder localizarlo y referenciarlo.",
    "Texto del beat": "Unidad mínima de acción, idea o intención dramática. Aquí editas el contenido narrativo exacto de este beat.",
    "beat.kind": "Función dramática del beat. Define si este momento sirve para exponer, confrontar, revelar, transicionar, dialogar o rematar una acción.",
    "beat.shot": "Tipo de plano sugerido para filmar este beat. Orienta la puesta en cámara según la información narrativa que debe percibir el espectador.",
    "beat.framing": "Encuadre del sujeto o de la acción. Sirve para decidir cuánto aire visual dejas y qué jerarquía tiene el elemento principal dentro del plano.",
    "beat.camera": "Movimiento o comportamiento de cámara. Indica si la cámara permanece fija, acompaña, descubre, persigue o enfatiza la acción.",
    "beat.focus": "Elemento dramático al que debe ir la atención principal: personaje, objeto o detalle. Ayuda a priorizar lectura visual y montaje.",
    "beat.location": "Localización específica visible en este beat. Puede heredar la de escena o concretarla si el plano ocurre en un punto distinto dentro de la continuidad.",
    "beat.characters": "Personajes que aparecen o tienen presencia visual/narrativa en este beat. Útil para continuidad, blocking y prompts de generación.",
    "beat.props": "Objetos relevantes que se ven o intervienen en este beat. Sirve para mantener continuidad visual y evitar cambios arbitrarios entre planos.",
    "beat.wardrobe": "Vestuario o rasgos visibles de vestuario relevantes para este beat. Úsalo cuando necesites fijar continuidad de ropa, accesorios o uniformes.",
    "beat.time_of_day_override": "Sobrescribe el momento del día solo para este beat si necesitas una variación puntual respecto a la escena.",
    "beat.continuity_notes": """Notas editoriales de continuidad para este beat: qué debe mantenerse igual entre planos, qué no debe cambiar y qué elementos conviene fijar:
    
                "CONTINUOUS",                 # continuidad total
                "CONT_VISUAL",                # misma imagen/plano
                "CONT_CAMERA",                # misma cámara/movimiento
                "CONT_LOCATION",              # mismo espacio
                "CONT_CHARACTER",             # mismo personaje dominante
                "CONT_EMOTION",               # misma emoción
                "CONT_VOICE",                 # misma voz / tono
                "CONT_AUDIO",                 # mismo ambiente sonoro
                "CONT_TIME",                  # sin salto temporal

                "SHIFT_VISUAL",               # cambio visual
                "SHIFT_CAMERA",               # cambio de plano
                "SHIFT_LOCATION",             # cambio de lugar
                "SHIFT_CHARACTER",            # cambia foco de personaje
                "SHIFT_EMOTION",              # cambia emoción
                "SHIFT_VOICE",                # cambia voz
                "SHIFT_AUDIO",                # cambia sonido
                "TIME_JUMP",                  # elipsis

                "HARD_CUT",                   # corte fuerte
                "SOFT_TRANSITION",            # transición suave""",
    "beat.transition": "Tipo de transición de entrada o salida hacia el siguiente beat. Marca continuidad, elipsis, contraste o golpe de montaje.",
    "beat.duration": "Duración orientativa del beat en segundos. Por defecto se estima con el texto del beat dividido entre 2.5 palabras por segundo, útil para voz, ritmo y planning.",
    "beat.audio": "Tratamiento sonoro predominante del beat: diálogo, ambiente, silencio, efecto o combinación. Define la capa auditiva principal.",
    "beat.video": "Tratamiento visual o recurso de imagen dominante: acción, archivo, apoyo, detalle, transición visual, etc.",
    "beat.visual_style": "Acabado visual del beat: tono fotográfico, textura, energía y forma de representación. Ayuda a mantener coherencia estética.",
    "beat.music_tag": "Etiqueta musical narrativa. Describe la intención de la música: tensión, épica, melancolía, avance, ironía, alivio, etc.",
    "beat.bgm_level": "Nivel de música de fondo. Indica cuánto protagonismo debe tener la base musical frente a la voz y los efectos.",
    "beat.sfx_level": "Nivel de efectos sonoros. Sirve para decidir si los sonidos de acción o ambiente deben sentirse discretos, presentes o muy marcados.",
    "beat.voice_intensity": "Intensidad interpretativa o energética de la voz. Orienta la locución o el diálogo hacia un tono más contenido, neutro o enfático.",
    "beat.speech_rate": "Velocidad de dicción o ritmo verbal. Ajusta la percepción de urgencia, claridad, solemnidad o naturalidad del beat.",
    "beat.pause_before": "Pausa previa en segundos antes de arrancar este beat. Útil para respiración dramática, anticipación o cambio de foco.",
    "beat.pause_after": "Pausa posterior en segundos al terminar este beat. Útil para dejar resonancia, rematar una idea o abrir espacio al siguiente golpe narrativo.",
    "scene.audio": "Diseño sonoro general de la escena. Define la atmósfera acústica sobre la que vivirán los beats.",
    "scene.video": "Tratamiento visual global de la escena. Sirve para mantener continuidad de lenguaje audiovisual entre beats.",
    "scene.shot_style": "Estilo de cobertura dominante en la escena: observacional, clásico, dinámico, íntimo, etc.",
    "scene.visual_style": "Firma estética principal de la escena: luz, textura, contraste y tono emocional de la imagen.",
}


def field_help(label: str) -> Optional[str]:
    return BEAT_FIELD_HELP.get(label)


def safe_select(label: str, options: List[str], value: str, key: str, help_text: str | None = None) -> str:
    opts = [""] + [o for o in options if o != ""]
    idx = opts.index(value) if value in opts else 0
    return st.selectbox(label, opts, index=idx, key=key, help=help_text)

def safe_multiselect(label: str, options: List[str], value_csv: str, key: str, help: Optional[str] = None) -> str:
    current = split_csv(value_csv)
    selected = st.multiselect(
        label,
        options,
        default=[x for x in current if x in options],
        key=key,
        help=help if help is not None else field_help(label),
    )
    return ", ".join(selected)


def metadata_catalogs_path(project_root: Path) -> Path:
    return project_root / "config" / "metadata_catalogs.json"


def save_metadata_catalog_list(project_root: Path, catalog_name: str, values: List[str]):
    data = load_project_jsons(project_root).get("metadata_catalogs", {}) or {}
    data[catalog_name] = ordered_unique(values)
    save_json_file(metadata_catalogs_path(project_root), data)


def save_assets_manifest_location(project_root: Path, location_key: str, location_path: str):
    data = load_project_jsons(project_root).get("assets_manifest", {}) or {}
    data.setdefault("locations", {})[location_key] = location_path
    save_json_file(project_root / "config" / "assets_manifest.json", data)


def catalog_select_or_create(
    project_root: Path,
    label: str,
    options: List[str],
    value: str,
    key: str,
    catalog_name: str,
    help_text: Optional[str] = None,
) -> str:
    ordered = ordered_unique([opt for opt in options if opt])
    has_custom_value = bool(value) and value not in ordered
    selector_options = [""] + ordered + ["✚ New…"]
    selector_value = value if value in ordered else ("✚ New…" if has_custom_value else "")

    selected = st.selectbox(
        label,
        selector_options,
        index=selector_options.index(selector_value) if selector_value in selector_options else 0,
        key=f"{key}__select",
        help=help_text if help_text is not None else field_help(label),
    )

    if selected == "✚ New…":
        default_new_value = value if has_custom_value else ""
        new_value = st.text_input(
            f"New value for {label}",
            value=default_new_value,
            key=f"{key}__new_value",
        ).strip()
        if st.button(f"Save new value for {label}", key=f"{key}__save_new"):
            if new_value:
                save_metadata_catalog_list(project_root, catalog_name, ordered + [new_value])
                st.session_state[f"{key}__select"] = new_value
                st.rerun()
            else:
                st.warning(f"Write a value for {label} before saving.")
        return new_value

    return selected


def location_ref_select_or_create(
    project_root: Path,
    label: str,
    value: str,
    key: str,
    catalogs: Dict[str, List[str]],
) -> str:
    options = catalogs.get("locations", [])
    ordered = ordered_unique([opt for opt in options if opt])
    has_custom_value = bool(value) and value not in ordered
    selector_options = [""] + ordered + ["✚ New location…"]
    selector_value = value if value in ordered else ("✚ New location…" if has_custom_value else "")

    selected = st.selectbox(
        label,
        selector_options,
        index=selector_options.index(selector_value) if selector_value in selector_options else 0,
        key=f"{key}__select",
        help=(field_help(label) or "") + " Select a location key from assets_manifest.json or create a new one.",
    )

    if selected == "✚ New location…":
        default_key = value if has_custom_value else ""
        new_key = st.text_input(
            f"New location key for {label}",
            value=default_key,
            key=f"{key}__new_key",
        ).strip()
        default_path = f"assets/locations/{new_key}.png" if new_key else "assets/locations/"
        new_path = st.text_input(
            f"Asset path for {label}",
            value=st.session_state.get(f"{key}__new_path", default_path),
            key=f"{key}__new_path",
            help="Relative path inside the project, for example assets/locations/stadium_exterior.png",
        ).strip()
        if st.button(f"Save new location for {label}", key=f"{key}__save_new"):
            if new_key and new_path:
                save_assets_manifest_location(project_root, new_key, new_path)
                st.session_state[f"{key}__select"] = new_key
                st.rerun()
            else:
                st.warning(f"Write both a key and a path for {label} before saving.")
        return new_key

    return selected

def voice_options_for_scene(scene: Scene, catalogs: Dict[str, List[str]]) -> List[str]:
    scene_characters = split_csv(scene.attrs.get("characters", ""))
    all_characters = ordered_unique(scene_characters + catalogs.get("characters", []))
    return ["NARRADOR"] + all_characters


def continuity_options_for_scene(scene: Scene) -> List[str]:
    scene_notes = [scene.attrs.get("continuity_notes", "")]
    beat_notes = [bt.attrs.get("continuity_notes", "") for bt in scene.beats]
    return [note for note in ordered_unique(scene_notes + beat_notes) if note]

def estimate_duration_seconds(text: str) -> int:
    words = len((text or "").split())
    estimated = round(words / 2.5) if words > 0 else 2
    return max(1, estimated)


def suggested_duration_value(text: str) -> str:
    words = len((text or "").split())
    seconds = (words / 2.5) if words > 0 else 2.0
    seconds = max(1.0, seconds)
    if abs(seconds - round(seconds)) < 1e-9:
        return str(int(round(seconds)))
    return f"{seconds:.1f}".rstrip("0").rstrip(".")


def match_catalog_option(options: List[str], *candidates: str) -> str:
    if not options:
        return ""
    normalized_map = {normalize(option): option for option in options if option}
    lowered_options = [(normalize(option), option) for option in options if option]
    for candidate in candidates:
        cand = normalize(candidate)
        if not cand:
            continue
        if cand in normalized_map:
            return normalized_map[cand]
        for normalized_option, original_option in lowered_options:
            if cand in normalized_option or normalized_option in cand:
                return original_option
    return ""


def infer_beat_attrs_from_text(text: str, catalogs: Dict[str, List[str]], current_attrs: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    source = (text or "").strip()
    lowered = normalize(source)
    attrs = {}

    if source:
        attrs["duration"] = suggested_duration_value(source)

    sentence_count = max(1, len([x for x in re.split(r"[.!?…]+", source) if x.strip()]))
    dialogue_like = bool(re.search(r'["“”«»]|—|: ', source))
    exclam_count = source.count("!") + source.count("¡")
    question_count = source.count("?") + source.count("¿")
    quote_like = bool(re.search(r'["“”«»]', source))
    upper_ratio = sum(1 for ch in source if ch.isupper()) / max(1, sum(1 for ch in source if ch.isalpha()))

    if dialogue_like:
        attrs["kind"] = match_catalog_option(catalogs.get("beat_kinds", []), "dialogo", "diálogo", "quote", "cita")
        attrs["audio"] = match_catalog_option(catalogs.get("beat_audio", []), "dialogo", "diálogo", "voz", "voice", "speech", "narracion", "narración")
        attrs["video"] = match_catalog_option(catalogs.get("beat_video", []), "dialogo", "diálogo", "personaje", "speaker", "hablante")
        attrs["shot"] = match_catalog_option(catalogs.get("beat_shots", []), "close up", "close-up", "primer plano", "medio corto")
        attrs["framing"] = match_catalog_option(catalogs.get("beat_framings", []), "close", "cerrado", "primer plano", "medio")
        attrs["focus"] = current_attrs.get("focus", "") if current_attrs else ""
    elif sentence_count >= 3:
        attrs["kind"] = match_catalog_option(catalogs.get("beat_kinds", []), "exposicion", "exposición", "info", "narracion", "narración")
        attrs["audio"] = match_catalog_option(catalogs.get("beat_audio", []), "voz", "voice over", "narracion", "narración", "locucion", "locución")
        attrs["shot"] = match_catalog_option(catalogs.get("beat_shots", []), "general", "master", "wide")
        attrs["framing"] = match_catalog_option(catalogs.get("beat_framings", []), "abierto", "wide", "general")
    else:
        attrs["kind"] = match_catalog_option(catalogs.get("beat_kinds", []), "accion", "acción", "reaction", "reaccion", "reacción")
        attrs["audio"] = match_catalog_option(catalogs.get("beat_audio", []), "ambiente", "fx", "sfx", "efectos")
        attrs["video"] = match_catalog_option(catalogs.get("beat_video", []), "accion", "acción", "detalle", "insert")

    if exclam_count >= 1 or upper_ratio > 0.18:
        attrs["voice_intensity"] = match_catalog_option(catalogs.get("beat_voice_intensity", []), "alta", "high", "fuerte", "energica", "enérgica", "intensa")
        attrs["speech_rate"] = "1.08"
        attrs["sfx_level"] = match_catalog_option(catalogs.get("beat_sfx_levels", []), "medio", "alta", "high")
    elif question_count >= 1:
        attrs["voice_intensity"] = match_catalog_option(catalogs.get("beat_voice_intensity", []), "media", "neutral", "curiosa")
        attrs["speech_rate"] = "1.00"
    else:
        attrs["voice_intensity"] = match_catalog_option(catalogs.get("beat_voice_intensity", []), "media", "neutral", "contenida")
        attrs["speech_rate"] = "0.96" if sentence_count >= 3 else "1.00"

    attrs["pause_before"] = "0.10" if source else "0.00"
    attrs["pause_after"] = "0.20" if re.search(r'[.!?…][\'"]?$', source) else "0.10"

    if any(word in lowered for word in ["guerra", "muerte", "ruina", "crisis", "peligro", "arma", "batalla"]):
        attrs["music_tag"] = match_catalog_option(catalogs.get("beat_music_tags", []), "tension", "tensión", "drama", "epica", "épica")
        attrs["bgm_level"] = match_catalog_option(catalogs.get("beat_bgm_levels", []), "medio", "media", "low", "bajo")
        attrs["visual_style"] = match_catalog_option(catalogs.get("beat_visual_styles", []), "dramatico", "dramático", "sobrio", "documental")
    elif quote_like:
        attrs["music_tag"] = match_catalog_option(catalogs.get("beat_music_tags", []), "reflexivo", "solemne", "emocional")
        attrs["bgm_level"] = match_catalog_option(catalogs.get("beat_bgm_levels", []), "bajo", "low", "suave")
        attrs["visual_style"] = match_catalog_option(catalogs.get("beat_visual_styles", []), "sobrio", "elegante", "documental")
    else:
        attrs["music_tag"] = match_catalog_option(catalogs.get("beat_music_tags", []), "neutral", "transicion", "transición", "impulso")
        attrs["bgm_level"] = match_catalog_option(catalogs.get("beat_bgm_levels", []), "bajo", "medio", "low")
        attrs["visual_style"] = match_catalog_option(catalogs.get("beat_visual_styles", []), "naturalista", "documental", "clasico", "clásico")

    attrs["transition"] = match_catalog_option(catalogs.get("beat_transitions", []), "cut", "corte", "directa", "continua")
    attrs["camera"] = match_catalog_option(catalogs.get("beat_cameras", []), "fija", "estatica", "estática", "tripode", "trípode")
    if not attrs.get("shot"):
        attrs["shot"] = match_catalog_option(catalogs.get("beat_shots", []), "medio", "medium", "general")
    if not attrs.get("framing"):
        attrs["framing"] = match_catalog_option(catalogs.get("beat_framings", []), "medio", "centered", "centrado")
    if not attrs.get("video"):
        attrs["video"] = match_catalog_option(catalogs.get("beat_video", []), "accion", "acción", "apoyo", "recurso")
    if not attrs.get("sfx_level"):
        attrs["sfx_level"] = match_catalog_option(catalogs.get("beat_sfx_levels", []), "bajo", "low", "medio")

    cleaned = {}
    for key, value in attrs.items():
        if value not in (None, ""):
            cleaned[key] = value
    return cleaned


def apply_inferred_beat_attrs(beat: "Beat", text: str, catalogs: Dict[str, List[str]]):
    inferred = infer_beat_attrs_from_text(text, catalogs, current_attrs=dict(beat.attrs))
    beat.text = text
    beat.attrs.update(inferred)
    return inferred


def safe_float(value, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


BUTTON_HELP = {
    "Validar JSON": "Comprueba si el contenido JSON es válido.",
    "Guardar JSON": "Guarda el archivo JSON actual en la carpeta config/.",
    "Formatear JSON": "Muestra el JSON con indentación para revisarlo mejor.",
    "Guardar archivo activo": "Guarda el texto actual en el archivo activo.",
    "Generar borrador desde texto plano": "Convierte el texto plano en una estructura inicial de episodios, bloques, escenas y beats.",
    "Borrar todo entre llaves {...}": "Elimina del archivo activo todas las marcas o etiquetas escritas entre llaves.",
    "Generar archivo estructurado desde marcas": "Construye el libro estructurado usando las marcas manuales de episodio, bloque, escena y beat.",
    "Guardar archivo estructurado activo": "Guarda en libro.txt todos los cambios actuales de la estructura y crea una copia de seguridad previa.",
    "Resetear cambios no guardados": "Recarga el libro desde disco y descarta los cambios que todavía no se hayan guardado.",
    "Nuevo episodio después": "Inserta un episodio nuevo justo después del episodio actual.",
    "Insertar beat después": "Inserta un beat vacío justo después del beat actual.",
    "Unir con el anterior": "Fusiona el beat actual con el beat inmediatamente anterior.",
    "Unir con el siguiente": "Fusiona el beat actual con el beat inmediatamente siguiente.",
    "Nueva escena desde este beat": "Parte la escena actual y crea una nueva escena empezando en este beat.",
    "Nuevo bloque desde esta escena": "Parte el bloque actual y crea un bloque nuevo desde esta escena.",
    "Subir beat": "Mueve el beat actual una posición hacia arriba dentro de la escena.",
    "Bajar beat": "Mueve el beat actual una posición hacia abajo dentro de la escena.",
    "Borrar beat": "Elimina el beat actual de la escena.",
    "Aplicar de aquí para adelante": "Propaga el metadato seleccionado desde el punto actual hasta el final del alcance elegido.",
    "E": "Marca o desmarca este segmento como inicio de episodio.",
    "B": "Marca o desmarca este segmento como inicio de bloque.",
    "S": "Marca o desmarca este segmento como inicio de escena.",
    "BT": "Marca o desmarca este segmento como inicio de beat.",
    "↑": "Mueve este beat una posición hacia arriba.",
    "↓": "Mueve este beat una posición hacia abajo.",
    "+": "Inserta un beat vacío después de este beat.",
    "✂": "Parte la escena actual a partir de este beat.",
    "×": "Elimina este beat.",
}

BEAT_META_HELP = {
    "beat.kind": (
        "Función narrativa del beat. "
        "Úsalo para distinguir si este momento es acción, diálogo, narración, reacción o transición. "
        "Marca la intención dramática principal del beat."
    ),
    "beat.shot": (
        "Tipo de plano. Define qué ve el espectador. "
        "wide = plano general para situar espacio; medium = plano medio para acción o conversación; "
        "close_up = primer plano para emoción; insert = detalle de objeto, manos o pantalla."
    ),
    "beat.framing": (
        "Encuadre o composición del plano. "
        "Sirve para decidir cómo colocas al sujeto dentro de la imagen: centrado, descentrado, "
        "regla de tercios, etc. Influye en la estética y en la atención del espectador."
    ),
    "beat.camera": (
        "Movimiento o comportamiento de cámara. "
        "static = cámara fija; pan = giro horizontal; tilt = giro vertical; "
        "tracking = seguimiento del sujeto; dolly = desplazamiento suave; "
        "handheld = cámara en mano, más nerviosa o realista. "
        "Usa tracking o dolly cuando acompañas a un personaje en movimiento."
    ),
    "beat.focus": (
        "Elemento sobre el que debe recaer la atención visual. "
        "Puede ser un personaje o un objeto. "
        "Te ayuda a dejar claro qué debe dominar el plano."
    ),
    "beat.transition": (
        "Forma de enlazar este beat con el siguiente. "
        "cut = corte directo, lo más normal; fade = fundido; dissolve = transición suave. "
        "En montaje clásico, cut suele ser la opción por defecto."
    ),
    "beat.duration": (
        "Duración aproximada del beat en segundos. "
        "Como regla rápida, una narración natural suele rondar palabras / 2.5. "
        "Ajusta este valor si quieres dar más pausa, tensión o rapidez."
    ),
    "beat.audio": (
        "Capa sonora principal del beat. "
        "Por ejemplo: diálogo, voiceover, ambiente, música o silencio. "
        "Define qué manda en el oído del espectador."
    ),
    "beat.video": (
        "Tipo de imagen que acompaña al beat. "
        "Por ejemplo: live_action, broll, archive, animation. "
        "Sirve para distinguir si vemos acción principal, recurso visual o material de apoyo."
    ),
    "beat.visual_style": (
        "Estilo visual dominante del beat. "
        "Debe ser coherente con la escena salvo que quieras marcar un cambio claro. "
        "Úsalo para mantener continuidad estética entre beats."
    ),
    "beat.music_tag": (
        "Etiqueta narrativa de la música. "
        "No describe una canción concreta, sino su función dramática: tensión, emoción, épica, "
        "neutralidad, misterio, etc."
    ),
    "beat.bgm_level": (
        "Nivel de música de fondo. "
        "Sube este valor si la música debe notarse más; bájalo si no debe competir con la voz o con el diálogo."
    ),
    "beat.sfx_level": (
        "Nivel de efectos sonoros. "
        "Úsalo para dar presencia a pasos, golpes, teclados, puertas, ambiente mecánico, etc. "
        "No debería tapar la voz salvo que busques ese efecto."
    ),
    "beat.voice_intensity": (
        "Intensidad emocional o energía de la voz. "
        "Bajo = calmado o íntimo; alto = enfático, tenso o agresivo."
    ),
    "beat.speech_rate": (
        "Velocidad de habla. "
        "1.00 = ritmo normal; menos de 1.00 = más pausado; más de 1.00 = más rápido. "
        "Útil para modular solemnidad, tensión o urgencia."
    ),
    "beat.pause_before": (
        "Pausa antes de empezar el beat. "
        "Sirve para preparar una entrada, dejar respirar el montaje o crear expectativa."
    ),
    "beat.pause_after": (
        "Pausa después del beat. "
        "Sirve para dejar resonar una frase, sostener una emoción o dar espacio antes del siguiente plano."
    ),
}


def tip_button(label: str, key: Optional[str] = None, help: Optional[str] = None, **kwargs):
    tooltip = help if help is not None else BUTTON_HELP.get(label, f"Ejecuta la acción: {label}.")
    return st.button(label, key=key, help=tooltip, **kwargs)


def html_escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def compact_preview(text: str, size: int = 80) -> str:
    clean = " ".join((text or "").split())
    return clean[:size] + ("..." if len(clean) > size else "")


def strip_braced_tags(text: str) -> str:
    cleaned = re.sub(r"\{[^{}]*\}", "", text or "")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def is_structured_book_text(text: str) -> bool:
    source = text or ""
    return "{episode" in source and "{/episode}" in source


def remove_manual_marks_for_file(root: Path, source_file: Path):
    all_marks = load_segmentation_all(root)
    key = str(source_file.resolve())
    if key in all_marks:
        del all_marks[key]
        save_segmentation_all(root, all_marks)


def structured_output_path(source_file: Path) -> Path:
    return source_file.with_name(source_file.stem + "_structured.txt")



# --------------------------------------------------
# Book parsing / serialization
# --------------------------------------------------
def structured_book_to_plain_text(text: str) -> str:
    if not is_structured_book_text(text):
        return text or ""

    episodes = parse_structured_text(text)
    out: List[str] = []
    for idx, ep in enumerate(episodes, start=1):
        title = (ep.attrs.get("title") or f"Capítulo {idx}").strip()
        if title:
            out.append(title)
        for block in ep.blocks:
            for scene in block.scenes:
                for beat in scene.beats:
                    for line in split_source_into_lines(strip_braced_tags(beat.text)):
                        stripped = line.strip()
                        if stripped:
                            out.append(stripped)
        out.append("")
    return "\n".join(out).strip()


# --------------------------------------------------
# Project config
# --------------------------------------------------
def load_project_jsons(project_root: Path):
    config = project_root / "config"
    return {
        "assets_manifest": load_json_file(config / "assets_manifest.json", {"characters": {}, "locations": {}, "props": {}}),
        "aliases": load_json_file(config / "aliases.json", {}),
        "character_aliases": load_json_file(config / "character_aliases.json", {}),
        "location_keywords": load_json_file(config / "location_keywords.json", {}),
        "function_rules": load_json_file(config / "function_rules.json", {}),
        "shot_rules": load_json_file(config / "shot_rules.json", {}),
        "transition_rules": load_json_file(config / "transition_rules.json", {}),
        "director_defaults": load_json_file(config / "director_defaults.json", {}),
        "style_rules": load_json_file(config / "style_rules.json", {}),
        "dialogue_rules": load_json_file(config / "dialogue_rules.json", {}),
        "dialogue_entities": load_json_file(config / "dialogue_entities.json", {}),
        "metadata_schema": load_json_file(config / "metadata_schema.json", {}),
        "metadata_catalogs": load_json_file(config / "metadata_catalogs.json", {}),
    }


def overrides_path(project_root: Path) -> Path:
    return project_root / "config" / "beat_overrides.json"


def load_overrides(project_root: Path) -> Dict[str, Dict[str, str]]:
    return load_json_file(overrides_path(project_root), {})


def save_overrides(project_root: Path, data: Dict[str, Dict[str, str]]):
    save_json_file(overrides_path(project_root), data)


def apply_overrides_to_beat(beat_id: str, beat_attrs: Dict[str, str], overrides: Dict[str, Dict[str, str]]) -> Dict[str, str]:
    merged = dict(beat_attrs)
    beat_override = overrides.get(beat_id, {})
    for k, v in beat_override.items():
        if v != "":
            merged[k] = v
    return merged


def schema_enum(schema: Dict, section: str, field: str) -> List[str]:
    return list((((schema or {}).get(section, {}) or {}).get("enums", {}) or {}).get(field, []))


def build_catalogs(project_root: Path):
    data = load_project_jsons(project_root)
    assets = data["assets_manifest"]
    location_keywords = data["location_keywords"]
    schema = data["metadata_schema"]
    metadata_catalogs = data.get("metadata_catalogs", {}) or {}

    def merged_options(*groups: List[str]) -> List[str]:
        merged: List[str] = []
        for group in groups:
            merged.extend(group or [])
        return ordered_unique(merged)

    return {
        "characters": sorted(list(assets.get("characters", {}).keys())),
        "props": sorted(list(assets.get("props", {}).keys())),
        "locations": sorted(set(list(assets.get("locations", {}).keys()) + list(location_keywords.keys()) + schema_enum(schema, "scene", "location") + (metadata_catalogs.get("scene_location", []) or []))),
        "block_kinds": merged_options(schema_enum(schema, "block", "kind"), metadata_catalogs.get("block_kind", []) or []),
        "block_themes": merged_options(schema_enum(schema, "block", "theme"), metadata_catalogs.get("block_theme", []) or []),
        "scene_kinds": merged_options(schema_enum(schema, "scene", "kind"), metadata_catalogs.get("scene_kind", []) or []),
        "scene_times": merged_options(schema_enum(schema, "scene", "time"), metadata_catalogs.get("scene_time", []) or []),
        "scene_moods": merged_options(schema_enum(schema, "scene", "mood"), metadata_catalogs.get("scene_mood", []) or []),
        "scene_audio": merged_options(schema_enum(schema, "scene", "audio"), metadata_catalogs.get("scene_audio", []) or []),
        "scene_video": merged_options(schema_enum(schema, "scene", "video"), metadata_catalogs.get("scene_video", []) or []),
        "scene_visual_styles": merged_options(schema_enum(schema, "scene", "visual_style"), metadata_catalogs.get("visual_style", []) or []),
        "scene_shot_styles": merged_options(schema_enum(schema, "scene", "shot_style"), metadata_catalogs.get("scene_shot_style", []) or []),
        "beat_shots": merged_options(schema_enum(schema, "beat", "shot"), metadata_catalogs.get("beat_shot", []) or []),
        "beat_framings": merged_options(schema_enum(schema, "beat", "framing"), metadata_catalogs.get("beat_framing", []) or []),
        "beat_cameras": merged_options(schema_enum(schema, "beat", "camera"), metadata_catalogs.get("beat_camera", []) or []),
        "beat_transitions": merged_options(schema_enum(schema, "beat", "transition"), metadata_catalogs.get("beat_transition", []) or []),
        "beat_audio": merged_options(schema_enum(schema, "beat", "audio"), metadata_catalogs.get("scene_audio", []) or []),
        "beat_video": merged_options(schema_enum(schema, "beat", "video"), metadata_catalogs.get("scene_video", []) or []),
        "beat_visual_styles": merged_options(schema_enum(schema, "beat", "visual_style"), metadata_catalogs.get("visual_style", []) or []),
        "beat_kinds": merged_options(schema_enum(schema, "beat", "kind"), metadata_catalogs.get("beat_kind", []) or []),
        "beat_music_tags": merged_options(schema_enum(schema, "beat", "music_tag"), metadata_catalogs.get("music_tag", []) or []),
        "beat_bgm_levels": merged_options(schema_enum(schema, "beat", "bgm_level"), metadata_catalogs.get("level", []) or []),
        "beat_sfx_levels": merged_options(schema_enum(schema, "beat", "sfx_level"), metadata_catalogs.get("level", []) or []),
        "beat_voice_intensity": merged_options(schema_enum(schema, "beat", "voice_intensity"), metadata_catalogs.get("voice_intensity", []) or []),
        "reference_modes": merged_options(metadata_catalogs.get("reference_mode", []) or [], ["AUTO", "START_IMAGE", "START_END_IMAGES"]),
        "visual_transitions": merged_options(metadata_catalogs.get("visual_transition", []) or [], ["NONE", "SOFT_TRANSITION", "HARD_CUT", "MORPH", "DISSOLVE"]),
        "audio_transitions": merged_options(metadata_catalogs.get("audio_transition", []) or [], ["CUT", "FADE", "CROSSFADE"]),
        "continuity_tags": merged_options(metadata_catalogs.get("continuity_notes", []) or [], [
            "CONTINUOUS", "CONT_VISUAL", "CONT_CAMERA", "CONT_LOCATION", "CONT_CHARACTER",
            "CONT_EMOTION", "CONT_VOICE", "CONT_AUDIO", "CONT_TIME",
            "SHIFT_VISUAL", "SHIFT_CAMERA", "SHIFT_LOCATION", "SHIFT_CHARACTER",
            "SHIFT_EMOTION", "SHIFT_VOICE", "SHIFT_AUDIO", "TIME_JUMP",
            "HARD_CUT", "SOFT_TRANSITION",
        ]),
        "schema": schema,
    }


# --------------------------------------------------
# Active source book selector
# --------------------------------------------------
def list_text_files(root: Path) -> List[Path]:
    patterns = ["*.txt", "books/*.txt", "source/*.txt", "input/*.txt", "texts/*.txt"]
    found = []
    for pattern in patterns:
        found.extend(root.glob(pattern))
    return sorted(set([p.resolve() for p in found if p.is_file()]))


def get_active_book_path(root: Path) -> Path:
    default = root / "libro.txt"
    value = st.session_state.get("book_path", str(default))
    return Path(value)


def sidebar_book_selector(root: Path) -> Path:
    st.sidebar.subheader("Archivo de libro activo")
    files = list_text_files(root)
    default = root / "libro.txt"

    options = [str(p) for p in files] if files else [str(default)]
    if "book_path" not in st.session_state:
        st.session_state["book_path"] = str(default if default.exists() else options[0])

    current = st.session_state.get("book_path", options[0])
    index = options.index(current) if current in options else 0

    selected = st.sidebar.selectbox("Archivo .txt", options, index=index, key="sidebar_book_select")
    st.session_state["book_path"] = selected
    ensure_open_backup(Path(selected))

    manual_path = st.sidebar.text_input("Ruta manual del .txt", value=selected, key="sidebar_book_manual_path")
    if st.sidebar.button("Usar ruta manual", help="Activa manualmente la ruta indicada y, si es libro.txt, crea una copia de seguridad con fecha y hora antes de usarla."):
        st.session_state["book_path"] = manual_path
        ensure_open_backup(Path(manual_path), force=True)
        st.rerun()

    st.sidebar.caption(f"Activo: `{st.session_state['book_path']}`")
    return Path(st.session_state["book_path"])


# --------------------------------------------------
# Segmentation marks
# --------------------------------------------------
def segmentation_marks_path(root: Path) -> Path:
    return root / "config" / "segmentation_marks.json"


def load_segmentation_all(root: Path) -> Dict[str, Dict]:
    return load_json_file(segmentation_marks_path(root), {})


def save_segmentation_all(root: Path, data: Dict[str, Dict]):
    save_json_file(segmentation_marks_path(root), data)


def load_segmentation_marks(root: Path, source_file: Path) -> Dict:
    all_marks = load_segmentation_all(root)
    key = str(source_file.resolve())
    base = {
        "source_file": key,
        "episode_starts": [0],
        "block_starts": [0],
        "scene_starts": [0],
        "beat_starts": [0],
    }
    data = all_marks.get(key, {})
    merged = dict(base)
    merged.update(data)
    for field in ["episode_starts", "block_starts", "scene_starts", "beat_starts"]:
        values = merged.get(field, [0]) or [0]
        values = sorted(set([int(v) for v in values if isinstance(v, int) or str(v).isdigit()]))
        if 0 not in values:
            values = [0] + values
        merged[field] = values
    return merged


def save_segmentation_marks(root: Path, source_file: Path, marks: Dict):
    all_marks = load_segmentation_all(root)
    key = str(source_file.resolve())
    all_marks[key] = marks
    save_segmentation_all(root, all_marks)


def split_source_into_paragraphs(text: str) -> List[str]:
    raw = re.split(r"\n\s*\n+", text.strip(), flags=re.DOTALL)
    return [p.strip() for p in raw if p.strip()]


CHAPTER_LINE_RE = re.compile(r"^\s*(cap[ií]tulo|capitulo)\s+(\d+)(?:\s*[:.\-]\s*(.*))?$", flags=re.IGNORECASE)
SUMARIO_LINE_RE = re.compile(r"^\s*(sumario|indice|índice|contents|table of contents)\s*$", flags=re.IGNORECASE)
INDEX_ENTRY_RE = re.compile(r"^\s*(cap[ií]tulo|capitulo)\s+\d+.*?\s+\d+\s*$", flags=re.IGNORECASE)
DIALOGUE_PREFIX_RE = re.compile(r"^\s*—")


def split_source_into_lines(text: str) -> List[str]:
    return [line.rstrip() for line in (text or '').splitlines()]


def is_chapter_heading(line: str) -> bool:
    return CHAPTER_LINE_RE.match((line or '').strip()) is not None


def parse_chapter_heading(line: str) -> tuple[int, str]:
    m = CHAPTER_LINE_RE.match((line or '').strip())
    if not m:
        return 0, ''
    return int(m.group(2)), (m.group(3) or '').strip()


def is_index_like_line(line: str) -> bool:
    s = (line or '').strip()
    return SUMARIO_LINE_RE.match(s) is not None or INDEX_ENTRY_RE.match(s) is not None


def bootstrap_is_dialogue_paragraph(text: str) -> bool:
    s = (text or '').strip()
    return bool(DIALOGUE_PREFIX_RE.match(s) or re.match(r'^\s*[A-ZÁÉÍÓÚÑ0-9_ ]+\s*:\s*.+$', s, flags=re.DOTALL))


def bootstrap_beat_kind(text: str) -> str:
    s = (text or '').strip()
    if not s:
        return 'transition'
    if bootstrap_is_dialogue_paragraph(s):
        return 'dialogue'
    if len(s.split()) <= 8:
        return 'transition'
    return 'narration'


def bootstrap_scene_kind(beats: List[str]) -> str:
    if not beats:
        return 'narration'
    dialogue_count = sum(1 for beat in beats if bootstrap_beat_kind(beat) == 'dialogue')
    if dialogue_count == 0:
        return 'narration'
    if dialogue_count == len(beats):
        return 'dialogue'
    return 'mixed'


def bootstrap_trim_front_matter(lines: List[str]) -> List[str]:
    first_chapter_idx = next((i for i, line in enumerate(lines) if is_chapter_heading(line)), -1)
    return lines[first_chapter_idx:] if first_chapter_idx >= 0 else lines


def bootstrap_remove_index(lines: List[str]) -> List[str]:
    out: List[str] = []
    in_index = False
    for line in lines:
        stripped = (line or '').strip()
        if SUMARIO_LINE_RE.match(stripped):
            in_index = True
            continue
        if in_index:
            if not stripped:
                continue
            if is_index_like_line(stripped):
                continue
            if is_chapter_heading(stripped):
                in_index = False
                out.append(line)
                continue
            if re.search(r'\d+\s*$', stripped):
                continue
            in_index = False
        if stripped:
            out.append(line)
    return out


def get_segmentation_source_text(text: str) -> str:
    source = text or ''
    return structured_book_to_plain_text(source) if is_structured_book_text(source) else source


def split_source_into_segments(text: str) -> List[str]:
    source = get_segmentation_source_text(text)
    lines = split_source_into_lines(source)
    lines = bootstrap_trim_front_matter(lines)
    lines = bootstrap_remove_index(lines)
    segments: List[str] = []
    for line in lines:
        stripped = (line or '').strip()
        if not stripped:
            continue
        if is_index_like_line(stripped):
            continue
        segments.append(stripped)
    return segments


def bootstrap_paragraphs_from_lines(lines: List[str]) -> List[str]:
    return split_source_into_segments("\n".join(lines))


def bootstrap_build_book(text: str) -> List[Episode]:
    segments = split_source_into_segments(text)
    if not segments:
        return []

    episodes: List[Episode] = []
    current_title = 'Capítulo 1'
    current_episode: Episode | None = None
    current_block: Block | None = None
    current_scene: Scene | None = None
    beat_buffer: List[str] = []

    def flush_scene_kind():
        nonlocal current_scene, beat_buffer
        if current_scene is not None:
            current_scene.attrs['kind'] = current_scene.attrs.get('kind') or bootstrap_scene_kind(beat_buffer)

    def start_episode(title: str):
        nonlocal current_episode, current_block, current_scene, beat_buffer
        flush_scene_kind()
        current_episode = Episode(attrs={'id': '', 'title': title}, blocks=[])
        episodes.append(current_episode)
        current_block = Block(attrs={'id': '', 'kind': 'chapter_block', 'theme': ''}, scenes=[])
        current_episode.blocks.append(current_block)
        current_scene = Scene(attrs={'id': '', 'kind': '', 'location': '', 'mood': ''}, beats=[])
        current_block.scenes.append(current_scene)
        beat_buffer = []

    for segment in segments:
        if is_chapter_heading(segment):
            num, subtitle = parse_chapter_heading(segment)
            current_title = f'Capítulo {num}' + (f': {subtitle}' if subtitle else '')
            start_episode(current_title)
            continue
        if current_scene is None:
            start_episode(current_title)
        current_scene.beats.append(Beat(attrs={'id': '', 'kind': bootstrap_beat_kind(segment)}, text=segment.strip()))
        beat_buffer.append(segment.strip())

    flush_scene_kind()
    episodes = [ep for ep in episodes if any(bt.text.strip() for bl in ep.blocks for sc in bl.scenes for bt in sc.beats)]
    if not episodes:
        return []

    ensure_ids(episodes)
    for e_i, ep in enumerate(episodes, start=1):
        ep.attrs['id'] = ep.attrs.get('id') or f'E{e_i:02d}'
        if not ep.attrs.get('title'):
            ep.attrs['title'] = f'Capítulo {e_i}'
        for b_i, block in enumerate(ep.blocks, start=1):
            block.attrs['id'] = block.attrs.get('id') or f"{ep.attrs['id']}_B{b_i:02d}"
            for s_i, scene in enumerate(block.scenes, start=1):
                scene.attrs['id'] = scene.attrs.get('id') or f"{block.attrs['id']}_S{s_i:02d}"
                for bt_i, beat in enumerate(scene.beats, start=1):
                    beat.attrs['id'] = beat.attrs.get('id') or f"{scene.attrs['id']}_BT{bt_i:02d}"
    return episodes


@st.cache_data(show_spinner=False)
def cached_bootstrap_build_book(text: str) -> List[Episode]:
    """Versión cacheada del bootstrap inicial para evitar recomputar el borrador
    estructurado en cada rerun de Streamlit mientras el texto base no cambie.
    """
    return bootstrap_build_book(text)


def toggle_start_mark(marks: Dict, field: str, idx: int):
    values = set(marks.get(field, []))
    if idx == 0:
        values.add(0)
    else:
        if idx in values:
            values.remove(idx)
        else:
            values.add(idx)
    values.add(0)
    marks[field] = sorted(values)


def paragraph_is_marked(marks: Dict, field: str, idx: int) -> bool:
    return idx in set(marks.get(field, []))


def build_structured_book_from_marks(root: Path, source_file: Path) -> List[Episode]:
    text = load_text(source_file, "")
    segments = split_source_into_segments(text)
    if not segments:
        return []

    marks = load_segmentation_marks(root, source_file)

    auto_episode_starts = {idx for idx, segment in enumerate(segments) if is_chapter_heading(segment)}
    episode_starts = sorted(set(marks.get("episode_starts", [0])) | auto_episode_starts)
    block_starts = sorted(set(marks.get("block_starts", [0])))
    scene_starts = sorted(set(marks.get("scene_starts", [0])))
    beat_starts = sorted(set(marks.get("beat_starts", [0])) | set(range(len(segments))))

    if 0 not in episode_starts:
        episode_starts.insert(0, 0)
    if 0 not in block_starts:
        block_starts.insert(0, 0)
    if 0 not in scene_starts:
        scene_starts.insert(0, 0)
    if 0 not in beat_starts:
        beat_starts.insert(0, 0)

    episodes: List[Episode] = []
    current_episode = None
    current_block = None
    current_scene = None
    pending_episode_title = ''

    def new_episode(title: str = ''):
        nonlocal current_episode, current_block, current_scene, pending_episode_title
        current_episode = Episode(attrs={"id": "", "title": title or pending_episode_title}, blocks=[])
        episodes.append(current_episode)
        current_block = None
        current_scene = None
        pending_episode_title = ''

    def new_block():
        nonlocal current_episode, current_block, current_scene
        if current_episode is None:
            new_episode()
        current_block = Block(attrs={"id": "", "kind": "chapter_block", "theme": ""}, scenes=[])
        current_episode.blocks.append(current_block)
        current_scene = None

    def new_scene():
        nonlocal current_block, current_scene
        if current_block is None:
            new_block()
        current_scene = Scene(attrs={"id": "", "kind": "", "location": ""}, beats=[])
        current_block.scenes.append(current_scene)

    for idx, segment in enumerate(segments):
        if is_chapter_heading(segment):
            num, subtitle = parse_chapter_heading(segment)
            pending_episode_title = f'Capítulo {num}' + (f': {subtitle}' if subtitle else '')
            if idx in episode_starts:
                new_episode(pending_episode_title)
                new_block()
                new_scene()
            continue

        if current_episode is None or (idx != 0 and idx in episode_starts):
            new_episode()
            new_block()
            new_scene()
        elif idx != 0 and idx in block_starts:
            new_block()
            new_scene()
        elif idx != 0 and idx in scene_starts:
            new_scene()

        if current_scene is None:
            new_scene()

        current_scene.beats.append(Beat(attrs={"id": "", "kind": bootstrap_beat_kind(segment)}, text=segment))

    for ep in episodes:
        for block in ep.blocks:
            for scene in block.scenes:
                scene.attrs['kind'] = scene.attrs.get('kind') or bootstrap_scene_kind([bt.text for bt in scene.beats])

    episodes = [ep for ep in episodes if any(bt.text.strip() for bl in ep.blocks for sc in bl.scenes for bt in sc.beats)]
    if not episodes:
        return []

    ensure_ids(episodes)

    for e_i, ep in enumerate(episodes, start=1):
        ep.attrs["id"] = ep.attrs.get("id") or f"E{e_i:02d}"
        if not ep.attrs.get("title"):
            ep.attrs["title"] = f"Capítulo {e_i}"

        for b_i, block in enumerate(ep.blocks, start=1):
            block.attrs["id"] = block.attrs.get("id") or f"{ep.attrs['id']}_B{b_i:02d}"

            for s_i, scene in enumerate(block.scenes, start=1):
                scene.attrs["id"] = scene.attrs.get("id") or f"{block.attrs['id']}_S{s_i:02d}"

                for bt_i, beat in enumerate(scene.beats, start=1):
                    beat.attrs["id"] = beat.attrs.get("id") or f"{scene.attrs['id']}_BT{bt_i:02d}"

    return episodes



# --------------------------------------------------
# Working structured book state
# --------------------------------------------------
def get_working_book(root: Path) -> List[Episode]:
    book_path = get_active_book_path(root)
    if "working_book" not in st.session_state or st.session_state.get("working_book_path") != str(book_path):
        st.session_state["working_book"] = parse_book(book_path) if book_path.exists() else []
        st.session_state["working_book_path"] = str(book_path)
        serialized = book_to_text(st.session_state["working_book"]) if st.session_state["working_book"] else ""
        st.session_state[f"autosave_book_hash::{book_path}"] = hash(serialized)
    return st.session_state["working_book"]


def reset_working_book(root: Path):
    book_path = get_active_book_path(root)
    st.session_state["working_book"] = parse_book(book_path) if book_path.exists() else []
    st.session_state["working_book_path"] = str(book_path)
    serialized = book_to_text(st.session_state["working_book"]) if st.session_state["working_book"] else ""
    st.session_state[f"autosave_book_hash::{book_path}"] = hash(serialized)


def ensure_minimum_structure(root: Path):
    episodes = get_working_book(root)
    if not episodes:
        episodes.append(
            Episode(
                attrs={"id": "E01", "title": ""},
                blocks=[
                    Block(
                        attrs={"id": "E01_B01", "kind": "", "theme": ""},
                        scenes=[
                            Scene(
                                attrs={"id": "E01_B01_S01", "kind": "", "location": ""},
                                beats=[Beat(attrs={"id": "E01_B01_S01_BT01"}, text="")]
                            )
                        ],
                    )
                ],
            )
        )


# --------------------------------------------------
# Structural operations
# --------------------------------------------------

def merge_with_previous(scene: Scene, beat_index: int) -> bool:
    if scene is None or beat_index <= 0 or beat_index >= len(scene.beats):
        return False

    prev_beat = scene.beats[beat_index - 1]
    curr_beat = scene.beats[beat_index]

    prev_text = (prev_beat.text or "").rstrip()
    curr_text = (curr_beat.text or "").lstrip()

    if prev_text and curr_text:
        prev_beat.text = prev_text + "\n\n" + curr_text
    else:
        prev_beat.text = prev_text or curr_text

    for key, value in curr_beat.attrs.items():
        if key == "id":
            continue
        if not prev_beat.attrs.get(key):
            prev_beat.attrs[key] = value

    del scene.beats[beat_index]
    return True


def merge_with_next(scene: Scene, beat_index: int) -> bool:
    if scene is None or beat_index < 0 or beat_index >= len(scene.beats) - 1:
        return False

    current_beat = scene.beats[beat_index]
    next_beat = scene.beats[beat_index + 1]

    current_text = (current_beat.text or "").rstrip()
    next_text = (next_beat.text or "").lstrip()

    if current_text and next_text:
        current_beat.text = current_text + "\n\n" + next_text
    else:
        current_beat.text = current_text or next_text

    for key, value in next_beat.attrs.items():
        if key == "id":
            continue
        if not current_beat.attrs.get(key):
            current_beat.attrs[key] = value

    del scene.beats[beat_index + 1]
    return True


def add_scene_after(block: Block, scene_index: int):
    block.scenes.insert(
        scene_index + 1,
        Scene(attrs={"id": "", "kind": "", "location": ""}, beats=[Beat(attrs={"id": ""}, text="")]),
    )


def add_block_after(ep: Episode, block_index: int):
    ep.blocks.insert(
        block_index + 1,
        Block(
            attrs={"id": "", "kind": "", "theme": ""},
            scenes=[Scene(attrs={"id": "", "kind": "", "location": ""}, beats=[Beat(attrs={"id": ""}, text="")])],
        ),
    )


def create_episode_after(episodes: List[Episode], ep_index: int):
    episodes.insert(
        ep_index + 1,
        Episode(
            attrs={"id": "", "title": ""},
            blocks=[
                Block(
                    attrs={"id": "", "kind": "", "theme": ""},
                    scenes=[Scene(attrs={"id": "", "kind": "", "location": ""}, beats=[Beat(attrs={"id": ""}, text="")])],
                )
            ],
        ),
    )


# --------------------------------------------------
# Propagation
# --------------------------------------------------
def _set_attr_value(attrs: Dict[str, str], field: str, value: str, overwrite: bool):
    current = attrs.get(field, "")
    if overwrite or current == "":
        if value == "":
            attrs.pop(field, None)
        else:
            attrs[field] = value


def propagate_beat_attr(
    episodes: List[Episode],
    episode_idx: int,
    block_idx: int,
    scene_idx: int,
    start_idx: int,
    field: str,
    value: str,
    overwrite: bool,
    scope: str,
):
    if scope == "hasta el final de escena":
        target_scenes = [(episodes[episode_idx].blocks[block_idx].scenes[scene_idx], start_idx)]
    elif scope == "hasta el final de bloque":
        target_scenes = []
        block = episodes[episode_idx].blocks[block_idx]
        for s_idx in range(scene_idx, len(block.scenes)):
            target_scenes.append((block.scenes[s_idx], start_idx if s_idx == scene_idx else 0))
    else:
        target_scenes = []
        ep = episodes[episode_idx]
        for b_idx in range(block_idx, len(ep.blocks)):
            block = ep.blocks[b_idx]
            first_scene_idx = scene_idx if b_idx == block_idx else 0
            for s_idx in range(first_scene_idx, len(block.scenes)):
                target_scenes.append((block.scenes[s_idx], start_idx if (b_idx == block_idx and s_idx == scene_idx) else 0))

    for target_scene, beat_start_idx in target_scenes:
        for i in range(beat_start_idx, len(target_scene.beats)):
            _set_attr_value(target_scene.beats[i].attrs, field, value, overwrite)


def propagate_all_beat_attrs(
    episodes: List[Episode],
    episode_idx: int,
    block_idx: int,
    scene_idx: int,
    start_idx: int,
    source_attrs: Dict[str, str],
    overwrite: bool,
    scope: str,
):
    fields = [
        "kind",
        "shot",
        "framing",
        "camera",
        "focus",
        "transition",
        "duration",
        "audio",
        "video",
        "visual_style",
        "music_tag",
        "bgm_level",
        "sfx_level",
        "speech_rate",
        "voice_intensity",
        "pause_before",
        "pause_after",
        "voice_id",
        "continuity_notes",
        "lock_location",
        "lock_props",
        "lock_voice",
        "lock_visual_style",
        "reference_mode",
        "visual_transition",
        "audio_transition",
        "ref_image_start",
        "ref_image_end",
        "video_prompt",
    ]

    for field in fields:
        value = source_attrs.get(field, "")
        propagate_beat_attr(
            episodes,
            episode_idx,
            block_idx,
            scene_idx,
            start_idx,
            field,
            value,
            overwrite,
            scope,
        )



def propagate_scene_attr(
    episodes: List[Episode],
    episode_idx: int,
    block_idx: int,
    start_scene_idx: int,
    field: str,
    value: str,
    overwrite: bool,
    scope: str,
):
    if scope == "hasta el final de bloque":
        target_blocks = [(episodes[episode_idx].blocks[block_idx], start_scene_idx)]
    else:
        target_blocks = []
        ep = episodes[episode_idx]
        for b_idx in range(block_idx, len(ep.blocks)):
            target_blocks.append((ep.blocks[b_idx], start_scene_idx if b_idx == block_idx else 0))

    for target_block, scene_start_idx in target_blocks:
        for i in range(scene_start_idx, len(target_block.scenes)):
            _set_attr_value(target_block.scenes[i].attrs, field, value, overwrite)


def propagate_block_attr(ep: Episode, start_block_idx: int, field: str, value: str, overwrite: bool):
    for i in range(start_block_idx, len(ep.blocks)):
        _set_attr_value(ep.blocks[i].attrs, field, value, overwrite)


# --------------------------------------------------
# Inspector
# --------------------------------------------------
def inspect_beat(project_root: Path, text: str, scene_attrs: Dict[str, str], beat_attrs: Dict[str, str]):
    data = load_project_jsons(project_root)
    assets_manifest = data["assets_manifest"]
    aliases = data["aliases"]
    character_aliases = data["character_aliases"]
    location_keywords = data["location_keywords"]
    function_rules = data["function_rules"]
    shot_rules = data["shot_rules"]
    transition_rules = data["transition_rules"]
    director_defaults = data["director_defaults"]
    style_rules = data["style_rules"]

    characters = list(assets_manifest.get("characters", {}).keys())
    props = list(assets_manifest.get("props", {}).keys())

    found_characters = []
    for canonical_name, alias_list in character_aliases.items():
        for alias in sorted(alias_list, key=lambda x: len(x), reverse=True):
            if contains_whole_phrase(text, alias):
                found_characters.append(canonical_name)
                break
    for canonical_name in characters:
        if canonical_name not in found_characters and contains_whole_phrase(text, canonical_name):
            found_characters.append(canonical_name)

    found_props = []
    for prop in props:
        if contains_whole_phrase(text, prop):
            found_props.append(prop)
    for raw_alias, canonical in aliases.items():
        if canonical in props and contains_whole_phrase(text, raw_alias):
            found_props.append(canonical)
    found_props = list(dict.fromkeys(found_props))

    location_candidates = []
    for loc, kws in location_keywords.items():
        score = 0
        longest = 0
        for kw in kws:
            if contains_whole_phrase(text, kw):
                score += 1
                longest = max(longest, len(normalize(kw)))
        if score:
            location_candidates.append((loc, score, longest))
    location_candidates.sort(key=lambda x: (x[1], x[2], x[0]), reverse=True)
    inferred_location = location_candidates[0][0] if location_candidates else ""
    location = beat_attrs.get("location") or scene_attrs.get("location") or inferred_location or "stadium_exterior"

    dialogue_match = re.match(r'^\s*([A-ZÁÉÍÓÚÑ0-9_ ]+)\s*:\s*(.+)$', text.strip(), flags=re.DOTALL)
    speaker = dialogue_match.group(1).title().strip() if dialogue_match else ""
    spoken = dialogue_match.group(2).strip() if dialogue_match else text.strip()

    focus = beat_attrs.get("focus", "")
    if not focus:
        focus = speaker or (found_characters[0] if found_characters else (found_props[0] if found_props else ""))

    def contains_any(t: str, kws: List[str]) -> bool:
        return any(normalize(k) in normalize(t) for k in kws)

    scores = {k: 0 for k in ["environment", "object_emphasis", "action", "reaction", "statement", "confrontation", "transition"]}
    wc = len(text.split())
    has_dialogue = bool(dialogue_match) or text.strip().startswith("—")

    if has_dialogue:
        scores["statement"] += 4
        if contains_any(text, function_rules.get("confrontation_keywords", [])):
            scores["confrontation"] += 5
        if wc <= 8:
            scores["confrontation"] += 1
        if found_props and contains_any(text, function_rules.get("object_keywords", [])):
            scores["object_emphasis"] += 1
    if found_props:
        scores["object_emphasis"] += 2
        if contains_any(text, function_rules.get("object_keywords", [])):
            scores["object_emphasis"] += 3
        if wc <= 12:
            scores["object_emphasis"] += 1
    if contains_any(text, function_rules.get("action_keywords", [])):
        scores["action"] += 4
        if found_characters:
            scores["action"] += 1
        if wc <= 14:
            scores["action"] += 1
    if contains_any(text, function_rules.get("reaction_keywords", [])):
        scores["reaction"] += 4
        if found_characters:
            scores["reaction"] += 1
    if contains_any(text, function_rules.get("environment_keywords", [])):
        scores["environment"] += 3
        if not found_characters:
            scores["environment"] += 2
        if wc <= 18:
            scores["environment"] += 1
    if found_characters and not has_dialogue:
        scores["reaction"] += 1
        scores["transition"] += 1
    if not found_characters and found_props:
        scores["object_emphasis"] += 1
    if wc <= 10:
        scores["transition"] += 2
    elif wc <= 18:
        scores["transition"] += 1

    priority = ["confrontation", "statement", "action", "object_emphasis", "reaction", "environment", "transition"]
    best = max(scores.values()) if scores else 0
    candidates = [k for k, v in scores.items() if v == best]
    function_type = next((p for p in priority if p in candidates), "transition")

    shot_map = shot_rules.get("function_to_shot", {})
    previous_speaker = scene_attrs.get("_previous_speaker", "")
    if beat_attrs.get("shot"):
        shot = beat_attrs.get("shot")
    elif function_type == "statement":
        if len(found_characters) >= 2 and not previous_speaker:
            shot = shot_rules.get("dialogue_opening_shot", "medium_two_shot")
        elif previous_speaker and speaker and previous_speaker != speaker:
            shot = shot_rules.get("dialogue_reply_shot", "over_the_shoulder")
        else:
            shot = shot_map.get(function_type, shot_rules.get("fallback_shot", "medium"))
    else:
        shot = shot_map.get(function_type, shot_rules.get("fallback_shot", "medium"))

    framing = beat_attrs.get("framing") or director_defaults.get("framing_by_shot", {}).get(shot, "medium")
    camera = beat_attrs.get("camera") or director_defaults.get("camera_by_shot", {}).get(shot, "static")
    scene_kind = beat_attrs.get("kind") or scene_attrs.get("kind") or ("dialogue" if has_dialogue else "narration")

    transition = beat_attrs.get("transition")
    if not transition:
        if scene_attrs.get("_scene_index", 0) == 0 and scene_attrs.get("_beat_index", 0) == 0:
            transition = transition_rules.get("episode_start", "fade_in")
        else:
            transition = transition_rules.get("scene_kind_defaults", {}).get(scene_kind, "cut")

    visual_style = beat_attrs.get("visual_style") or scene_attrs.get("visual_style")
    if not visual_style:
        block_kind = scene_attrs.get("block_kind", "")
        visual_style = style_rules.get("block_kind", {}).get(block_kind, {}).get("visual_style", "tradicion")

    prompt = ", ".join(
        [p for p in [
            shot,
            framing,
            camera,
            f"{scene_attrs.get('mood', 'dramatic')} mood",
            f"{visual_style} visual style",
            f"{scene_kind} scene",
            f"set in {location}",
            f"focus on {focus}" if focus else "",
            f"characters present: {','.join(found_characters)}" if found_characters else "",
            f"visible props: {','.join(found_props)}" if found_props else "",
            f"spoken dialogue by {speaker}: {spoken}" if speaker else f"dramatic action or narration: {spoken}",
        ] if p]
    )

    return {
        "detected_characters": found_characters,
        "detected_props": found_props,
        "detected_location": location,
        "focus": focus,
        "speaker": speaker,
        "function_scores": scores,
        "function_type": function_type,
        "shot": shot,
        "framing": framing,
        "camera": camera,
        "transition": transition,
        "visual_style": visual_style,
        "motion_prompt_preview": prompt,
        "location": location,
        "kind": scene_kind,
        "characters": found_characters,
        "props": found_props,
        "text": text,
    }


# --------------------------------------------------
# Beat selection / labels
# --------------------------------------------------
def get_selected_beat_id(scene: Scene, key_prefix: str) -> str:
    key = f"{key_prefix}_selected_beat_id"
    selected = st.session_state.get(key, "")
    valid_ids = [bt.attrs.get("id", "") for bt in scene.beats]
    if selected not in valid_ids:
        selected = valid_ids[0] if valid_ids else ""
        st.session_state[key] = selected
    return selected


def set_selected_beat(scene: Scene, key_prefix: str, beat_index: int):
    if 0 <= beat_index < len(scene.beats):
        beat_id = scene.beats[beat_index].attrs.get("id", "")
        st.session_state[f"{key_prefix}_selected_beat_id"] = beat_id
        st.session_state[f"{key_prefix}_beat_list_idx"] = beat_index


# --------------------------------------------------
# Sidebar beat toolbar
# --------------------------------------------------
def beat_status_icons(beat: Beat) -> str:
    text = (beat.text or "").strip()
    icons: List[str] = []

    has_dialogue = text.startswith("—") or bool(re.match(r'^\s*([A-ZÁÉÍÓÚÑ0-9_ ]+)\s*:\s*(.+)$', text, flags=re.DOTALL))
    if has_dialogue:
        icons.append("🗣")

    if beat.attrs.get("focus") or beat.attrs.get("kind") or beat.attrs.get("shot"):
        icons.append("🏷")

    if re.match(r'^\s*([A-ZÁÉÍÓÚÑ0-9_ ]+)\s*:\s*(.+)$', text, flags=re.DOTALL):
        icons.append("👤")

    if not text or not beat.attrs.get("kind"):
        icons.append("⚠")

    return " ".join(icons)


def render_scene_beats_sidebar(scene: Scene, block: Block, scene_idx: int, episodes: List[Episode], key_prefix: str):
    st.markdown("### Beats de la escena")
    selected_id = get_selected_beat_id(scene, key_prefix)

    with st.container(height=1000):
        for i, bt in enumerate(scene.beats):
            preview = bt.text.strip().replace("\n", " ")
            preview = preview[:46] + ("..." if len(preview) > 46 else "")
            label = f'{bt.attrs.get("id", f"BT{i+1}")} — {preview or "[vacío]"}'
            icons = beat_status_icons(bt)

            c1, c2, c3, c4, c5, c6, c7 = st.columns([8, 1.1, 1.1, 1.1, 1.1, 1.1, 2.0])

            with c1:
                is_selected = bt.attrs.get("id", "") == selected_id
                if is_selected:
                    st.error(label)
                else:
                    if tip_button(label, key=f"{key_prefix}_select_beat_{scene.attrs.get('id','scene')}_{i}"):
                        set_selected_beat(scene, key_prefix, i)
                        st.rerun()

                if is_selected:
                    if st.button("Seleccionado", key=f"{key_prefix}_selected_dummy_{scene.attrs.get('id','scene')}_{i}", disabled=True):
                        pass

            with c2:
                if tip_button("↑", key=f"{key_prefix}_move_up_{scene.attrs.get('id','scene')}_{i}"):
                    if move_beat(scene, i, -1):
                        ensure_ids(episodes)
                        set_selected_beat(scene, key_prefix, max(0, i - 1))
                        st.rerun()
            with c3:
                if tip_button("↓", key=f"{key_prefix}_move_down_{scene.attrs.get('id','scene')}_{i}"):
                    if move_beat(scene, i, 1):
                        ensure_ids(episodes)
                        set_selected_beat(scene, key_prefix, min(len(scene.beats) - 1, i + 1))
                        st.rerun()
            with c4:
                if tip_button("+", key=f"{key_prefix}_insert_after_{scene.attrs.get('id','scene')}_{i}"):
                    insert_empty_beat(scene, i + 1)
                    ensure_ids(episodes)
                    set_selected_beat(scene, key_prefix, i + 1)
                    st.rerun()
            with c5:
                if tip_button("✂", key=f"{key_prefix}_split_scene_here_{scene.attrs.get('id','scene')}_{i}"):
                    if i > 0:
                        split_scene_from_beat(block, scene_idx, i)
                        ensure_ids(episodes)
                        set_selected_beat(block.scenes[scene_idx + 1] if scene_idx + 1 < len(block.scenes) else scene, key_prefix, 0)
                        st.rerun()
            with c6:
                if tip_button("×", key=f"{key_prefix}_delete_beat_{scene.attrs.get('id','scene')}_{i}"):
                    if delete_beat(scene, i):
                        ensure_ids(episodes)
                        set_selected_beat(scene, key_prefix, max(0, min(i, len(scene.beats) - 1)))
                        st.rerun()
            with c7:
                st.markdown(
                    f"<div style='text-align:center; padding-top:0.35rem;'>{icons or '&nbsp;'}</div>",
                    unsafe_allow_html=True,
                )

    scene_duration_seconds = 0.0
    for bt in scene.beats:
        try:
            scene_duration_seconds += float(bt.attrs.get("duration", 0) or 0)
        except Exception:
            pass

    minutes = int(scene_duration_seconds // 60)
    seconds = scene_duration_seconds - (minutes * 60)
    st.caption(f"Duración total de la escena: {scene_duration_seconds:.1f} s ({minutes:02d}:{seconds:04.1f})")

def synced_beat_text_area(label: str, beat: Beat, beat_id: str, height: int = 220, help: Optional[str] = None, label_visibility: str = "visible") -> str:
    widget_key = f"struct_beat_text_{beat_id}"
    loaded_key = f"{widget_key}__loaded"
    current_text = beat.text or ""

    if st.session_state.get(loaded_key) != current_text:
        st.session_state[widget_key] = current_text
        st.session_state[loaded_key] = current_text

    value = st.text_area(
        label,
        value=st.session_state.get(widget_key, current_text),
        height=height,
        key=widget_key,
        help=help,
        label_visibility=label_visibility,
    )
    st.session_state[loaded_key] = value
    return value


# --------------------------------------------------
# Selection
# --------------------------------------------------
def select_script_entities(root: Path, key_prefix: str = ""):
    episodes = get_working_book(root)
    ensure_ids(episodes)

    if not episodes:
        return None, None, None, None, episodes, 0, 0, 0, 0

    ep_idx = st.selectbox(
        "Episodio",
        range(len(episodes)),
        format_func=lambda i: f'{episodes[i].attrs.get("id","?")} — {episodes[i].attrs.get("title","")}',
        key=f"{key_prefix}_ep",
    )
    ep = episodes[ep_idx]

    if not ep.blocks:
        ep.blocks.append(Block(attrs={"id": ""}, scenes=[Scene(attrs={"id": ""}, beats=[Beat(attrs={"id": ""}, text="")])]))

    block_idx = st.selectbox(
        "Bloque",
        range(len(ep.blocks)),
        format_func=lambda i: f'{ep.blocks[i].attrs.get("id","?")} — {ep.blocks[i].attrs.get("kind","")}',
        key=f"{key_prefix}_block",
    )
    block = ep.blocks[block_idx]

    if not block.scenes:
        block.scenes.append(Scene(attrs={"id": ""}, beats=[Beat(attrs={"id": ""}, text="")]))

    scene_idx = st.selectbox(
        "Escena",
        range(len(block.scenes)),
        format_func=lambda i: f'{block.scenes[i].attrs.get("id","?")} — {block.scenes[i].attrs.get("kind","")}',
        key=f"{key_prefix}_scene",
    )
    scene = block.scenes[scene_idx]

    if not scene.beats:
        scene.beats.append(Beat(attrs={"id": ""}, text=""))

    selector_key = f"{key_prefix}_beat_selector_idx"
    list_key = f"{key_prefix}_beat_list_idx"
    selected_id = get_selected_beat_id(scene, key_prefix)
    desired_idx = next((i for i, bt in enumerate(scene.beats) if bt.attrs.get("id", "") == selected_id), 0)
    desired_idx = max(0, min(desired_idx, len(scene.beats) - 1))

    if selector_key not in st.session_state or st.session_state.get(selector_key) != desired_idx:
        st.session_state[selector_key] = desired_idx

    beat_idx = st.selectbox(
        "Beat",
        range(len(scene.beats)),
        format_func=lambda i: scene.beats[i].attrs.get("id", "?"),
        key=selector_key,
    )

    st.session_state[list_key] = beat_idx
    st.session_state[f"{key_prefix}_selected_beat_id"] = scene.beats[beat_idx].attrs.get("id", "")

    beat = scene.beats[beat_idx]
    return ep, block, scene, beat, episodes, ep_idx, block_idx, scene_idx, beat_idx


# --------------------------------------------------
# Beat map
# --------------------------------------------------
PALETTE = [
    "#4E79A7", "#F28E2B", "#E15759", "#76B7B2", "#59A14F",
    "#EDC948", "#B07AA1", "#FF9DA7", "#9C755F", "#BAB0AC",
    "#1F77B4", "#FF7F0E", "#2CA02C", "#D62728", "#9467BD",
]


def value_color_map(values: List[str]) -> Dict[str, str]:
    uniq = ordered_unique(values)
    mapping = {}
    for i, v in enumerate(uniq):
        mapping[v] = PALETTE[i % len(PALETTE)]
    return mapping


def beat_map_value(meta_field: str, beat: Beat, inspected: Dict[str, str]) -> str:
    if meta_field == "speaker":
        return inspected.get("speaker", "") or "∅"
    value = beat.attrs.get(meta_field, "")
    return value if value else "∅"


def render_beat_color_map(scene: Scene, project_root: Path, selected_field: str):
    inspected_rows = []
    for beat in scene.beats:
        inspected = inspect_beat(project_root, beat.text, dict(scene.attrs), beat.attrs)
        inspected_rows.append(inspected)

    values = [beat_map_value(selected_field, beat, inspected_rows[i]) for i, beat in enumerate(scene.beats)]
    color_by_value = value_color_map(values)

    blocks = []
    for i, beat in enumerate(scene.beats):
        value = values[i]
        color = color_by_value[value]
        label = beat.attrs.get("id", f"BT{i+1}")
        title = f"{label} | {selected_field}={value}"
        blocks.append(
            f"""
            <div title="{html_escape(title)}"
                 style="
                    flex:1;
                    min-width:110px;
                    border-radius:8px;
                    background:{color};
                    color:white;
                    padding:10px 6px;
                    text-align:center;
                    font-size:12px;
                    font-weight:600;
                    box-shadow: inset 0 0 0 1px rgba(255,255,255,0.18);
                 ">
                {html_escape(label)}
            </div>
            """
        )

    legend = []
    for value, color in color_by_value.items():
        legend.append(
            f"""
            <div style="display:flex; align-items:center; gap:8px; margin-right:18px; margin-bottom:8px;">
                <div style="width:14px; height:14px; border-radius:3px; background:{color};"></div>
                <div style="font-size:13px;">{html_escape(value)}</div>
            </div>
            """
        )

    html = f"""
    <div style="font-family: sans-serif;">
        <div style="font-size:18px; font-weight:600; margin-bottom:12px;">Mapa de beats por color</div>

        <div style="
            display:flex;
            gap:8px;
            align-items:stretch;
            margin-bottom:14px;
            overflow-x:auto;
            padding-bottom:4px;
        ">
            {''.join(blocks)}
        </div>

        <div style="
            display:flex;
            flex-wrap:wrap;
            align-items:center;
            margin-top:8px;
        ">
            {''.join(legend)}
        </div>
    </div>
    """
    components.html(html, height=170, scrolling=False)


# --------------------------------------------------
# Sidebar
# --------------------------------------------------
def sidebar_project_root() -> Path:
    st.sidebar.header("Proyecto")
    default_root = st.session_state.get("project_root", str(Path.cwd()))
    root_str = st.sidebar.text_input("Ruta del proyecto", value=default_root)
    root = Path(root_str).expanduser().resolve()
    st.session_state["project_root"] = str(root)
    st.sidebar.write(f"Usando: `{root}`")

    checks = {
        "config/": (root / "config").exists(),
        "assets/": (root / "assets").exists(),
        "output/": (root / "output").exists(),
        "src/series_studio.py": (root / "src" / "series_studio.py").exists(),
        "config/beat_overrides.json": (root / "config" / "beat_overrides.json").exists(),
    }
    st.sidebar.subheader("Comprobación")
    for label, ok in checks.items():
        st.sidebar.write(f"{'✅' if ok else '❌'} {label}")

    st.sidebar.markdown("---")
    return root


# --------------------------------------------------
# Tabs
# --------------------------------------------------
def tab_project(root: Path):
    st.header("Proyecto")
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Config")
        config_dir = root / "config"
        if config_dir.exists():
            for p in sorted(config_dir.glob("*.json")):
                st.write(f"• {p.name}")
        else:
            st.warning("No existe config/")
    with c2:
        st.subheader("Assets")
        for folder in ["characters", "locations", "props", "audio"]:
            st.write(f"• {folder}: {'✅' if (root / 'assets' / folder).exists() else '❌'}")

    st.markdown("---")
    active_book = get_active_book_path(root)
    st.write(f"**Archivo activo:** `{active_book}`")
    if active_book.exists():
        st.caption(f"Tamaño: {active_book.stat().st_size} bytes")


def tab_json_editor(root: Path):
    st.header("Editor de JSON")
    config_dir = root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    json_files = sorted({p.name for p in config_dir.glob("*.json")} | set(DEFAULT_JSON_FILES))
    selected = st.selectbox("Archivo JSON", json_files)
    target = config_dir / selected
    current_text = load_text(target, "{}")
    json_text = st.text_area("Contenido JSON", value=current_text, height=500)
    valid, parsed, error = json_valid(json_text)

    col1, col2, col3 = st.columns(3)
    with col1:
        if tip_button("Validar JSON"):
            st.success("JSON válido") if valid else st.error(error)
    with col2:
        if tip_button("Guardar JSON"):
            if valid:
                save_text(target, json.dumps(parsed, ensure_ascii=False, indent=2))
                st.success(f"Guardado: {target}")
            else:
                st.error(error)
    with col3:
        if tip_button("Formatear JSON"):
            if valid:
                st.code(json.dumps(parsed, ensure_ascii=False, indent=2), language="json")
            else:
                st.error(error)


def tab_segmentation(root: Path):
    st.header("Segmentación")
    source_file = get_active_book_path(root)
    st.write(f"**Archivo activo para segmentar:** `{source_file}`")

    if not source_file.exists():
        st.warning("El archivo activo no existe.")
        raw = st.text_area("Crear contenido del archivo activo", value="", height=300)
        if tip_button("Guardar archivo activo"):
            backup_path = save_book_text(source_file, raw)
            st.success(f"Guardado: {source_file}" + (f" · Backup: {backup_path}" if backup_path else ""))
            st.rerun()
        return

    raw_text = load_text(source_file, "")
    segmentation_text = get_segmentation_source_text(raw_text)
    lines = split_source_into_lines(segmentation_text)
    segments = split_source_into_segments(raw_text)
    marks = load_segmentation_marks(root, source_file)

    if is_structured_book_text(raw_text):
        structured_episodes = parse_structured_text(raw_text)
        chapter_lines = [(ep.attrs.get("title") or f"Capítulo {i}").strip() for i, ep in enumerate(structured_episodes, start=1) if (ep.attrs.get("title") or "").strip()]
    else:
        chapter_lines = [line.strip() for line in lines if is_chapter_heading(line)]
    bootstrap_preview = cached_bootstrap_build_book(segmentation_text)
    bootstrap_beats = sum(len(scene.beats) for ep in bootstrap_preview for block in ep.blocks for scene in block.scenes)

    st.subheader("Bootstrap rápido")
    info1, info2, info3 = st.columns(3)
    with info1:
        st.metric("Capítulos detectados", len(chapter_lines))
    with info2:
        st.metric("Líneas/beats detectados", len(segments))
    with info3:
        st.metric("Beats del borrador", bootstrap_beats)

    b1, b2 = st.columns([1.7, 1.3])
    with b1:
        if tip_button("Generar borrador desde texto plano", key="seg_bootstrap_generate"):
            episodes = cached_bootstrap_build_book(segmentation_text)
            if not episodes:
                st.error("No se pudo generar el borrador inicial.")
            else:
                backup_path = save_book_text(source_file, book_to_text(episodes))
                st.session_state["working_book"] = episodes
                st.session_state["working_book_path"] = str(source_file)
                st.success(f"Borrador estructurado generado en: {source_file}" + (f" · Backup: {backup_path}" if backup_path else ""))
                st.rerun()
    with b2:
        if tip_button("Borrar todo entre llaves {...}", key="seg_strip_braces"):
            cleaned = strip_braced_tags(raw_text)
            backup_path = save_book_text(source_file, cleaned + ("\n" if cleaned and not cleaned.endswith("\n") else ""))
            remove_manual_marks_for_file(root, source_file)
            st.session_state["working_book"] = parse_book(source_file) if is_structured_book_text(cleaned) else []
            st.session_state["working_book_path"] = str(source_file)
            st.success(f"Se eliminó todo lo que estaba entre llaves en: {source_file}" + (f" · Backup: {backup_path}" if backup_path else ""))
            st.rerun()

    st.caption("La pestaña Segmentación siempre lee el archivo activo como texto base útil. Si el archivo ya está estructurado, se proyecta a texto plano para detectar capítulos y beats sin mezclar marcas con contenido.")

    with st.expander("Vista previa del borrador detectado", expanded=False):
        if chapter_lines:
            st.markdown("**Capítulos detectados**")
            for line in chapter_lines[:20]:
                st.write(f"• {line}")
        else:
            st.info("No se han detectado líneas de capítulo. El borrador se generará como un único episodio.")
        if bootstrap_preview:
            st.code(book_to_text(bootstrap_preview), language="text")

    st.markdown("---")
    st.subheader("Marcado manual fino")
    st.caption("Úsalo solo para afinar. El flujo normal debería ser: generar borrador → ir a Estructura → trabajar los beats.")

    manual_top1, manual_top2 = st.columns(2)
    with manual_top1:
        if tip_button("Generar archivo estructurado desde marcas", key="seg_generate_from_marks"):
            episodes = build_structured_book_from_marks(root, source_file)
            if not episodes:
                st.error("No se pudo generar estructura.")
            else:
                backup_path = save_book_text(source_file, book_to_text(episodes))
                st.session_state["working_book"] = episodes
                st.session_state["working_book_path"] = str(source_file)
                st.success(f"Estructura generada en: {source_file}" + (f" · Backup: {backup_path}" if backup_path else ""))
                st.rerun()
    with manual_top2:
        st.caption(f"Líneas disponibles para marcar: {len(segments)}")

    page_size = st.selectbox("Elementos por página", [25, 50, 100, 200], index=1, key="seg_page_size")
    total_pages = max(1, (len(segments) + page_size - 1) // page_size)
    current_page = st.number_input("Página", min_value=1, max_value=total_pages, value=1, step=1, key="seg_current_page")
    start_idx = (current_page - 1) * page_size
    end_idx = min(len(segments), start_idx + page_size)
    st.caption(f"Mostrando segmentos {start_idx}–{max(start_idx, end_idx - 1)} de {len(segments) - 1 if segments else 0}.")

    for idx in range(start_idx, end_idx):
        paragraph = segments[idx]
        with st.container():
            c1, c2, c3, c4, c5 = st.columns([6.2, 0.8, 0.8, 0.8, 0.8])
            with c1:
                badges = []
                if paragraph_is_marked(marks, "episode_starts", idx):
                    badges.append("E")
                if paragraph_is_marked(marks, "block_starts", idx):
                    badges.append("B")
                if paragraph_is_marked(marks, "scene_starts", idx):
                    badges.append("S")
                if paragraph_is_marked(marks, "beat_starts", idx):
                    badges.append("BT")
                st.markdown(f"**[{idx}]** `{ ' · '.join(badges) if badges else '-' }`")
                st.write(paragraph)
            with c2:
                if tip_button("E", key=f"seg_ep_{idx}"):
                    toggle_start_mark(marks, "episode_starts", idx)
                    save_segmentation_marks(root, source_file, marks)
                    st.rerun()
            with c3:
                if tip_button("B", key=f"seg_block_{idx}"):
                    toggle_start_mark(marks, "block_starts", idx)
                    save_segmentation_marks(root, source_file, marks)
                    st.rerun()
            with c4:
                if tip_button("S", key=f"seg_scene_{idx}"):
                    toggle_start_mark(marks, "scene_starts", idx)
                    save_segmentation_marks(root, source_file, marks)
                    st.rerun()
            with c5:
                if tip_button("BT", key=f"seg_beat_{idx}"):
                    toggle_start_mark(marks, "beat_starts", idx)
                    save_segmentation_marks(root, source_file, marks)
                    st.rerun()
            st.markdown("---")

def tab_structure_editor(root: Path):
    st.subheader("Estructura del libro")
    book_path = get_active_book_path(root)
    st.caption(f"Archivo estructurado activo: `{book_path}`")
    st.caption("Los cambios de estructura se guardan automáticamente en `libro.txt`. El botón de guardar fuerza el guardado y crea una copia de seguridad previa.")
    selected = select_script_entities(root, "script_structure")
    if not selected or selected[0] is None:
        st.warning("No se ha podido cargar el libro")
        return

    ep, block, scene, beat, episodes, ep_idx, block_idx, scene_idx, beat_idx = selected
    ensure_ids(episodes)
    catalogs = build_catalogs(root)

    ep_key = f"e{ep_idx}"
    block_key = f"{ep_key}_b{block_idx}"
    scene_key = f"{block_key}_s{scene_idx}"
    beat_key = f"{scene_key}_bt{beat_idx}"

    top1, top2, top3, top4 = st.columns(4)
    with top1:
        if tip_button("Guardar archivo estructurado activo", key="struct_save_book"):
            book_path = get_active_book_path(root)
            ensure_ids(episodes)
            backup_path = save_book_text(book_path, book_to_text(episodes))
            st.success(f"Guardado: {book_path}" + (f" · Backup: {backup_path}" if backup_path else ""))
    with top2:
        if tip_button("Resetear cambios no guardados", key="struct_reset_book"):
            reset_working_book(root)
            st.rerun()
    with top3:
        if tip_button("Nuevo episodio después", key="struct_new_episode_after"):
            create_episode_after(episodes, ep_idx)
            ensure_ids(episodes)
            st.rerun()
    with top4:
        total_beats_episode = sum(len(sc.beats) for bl in ep.blocks for sc in bl.scenes)
        st.info(
            f"Capítulo/Episodio: {ep.attrs.get('id','')} · Beats en episodio: {total_beats_episode} · "
            f"Beats en escena actual: {len(scene.beats)}"
        )

    st.caption(f"Contexto actual: episodio `{ep.attrs.get('id','')}`, bloque `{block.attrs.get('id','')}`, escena `{scene.attrs.get('id','')}`. El foco principal aquí son los beats.")

    st.markdown("#### Herramientas de escena")

    tools_a, tools_b, tools_c = st.columns(3)
    with tools_a:
        if st.button("Dividir beats automáticamente"):
            if auto_split_long_beats(scene):
                ensure_ids(episodes)
                set_selected_beat(scene, "script_structure", min(beat_idx, len(scene.beats) - 1))
                st.success("Beats divididos automáticamente")
                st.rerun()
            else:
                st.info("No se encontraron beats que dividir")
    with tools_b:
        if st.button("🪄 Analizar beats de la escena", key=f"struct_autofill_scene_{scene_key}", use_container_width=True):
            current_widget_key = f"struct_beat_text_{beat.attrs.get('id') or beat_key}"
            if current_widget_key in st.session_state:
                beat.text = st.session_state[current_widget_key]

            analyzed_count = 0
            for scene_beat in scene.beats:
                apply_inferred_beat_attrs(scene_beat, scene_beat.text, catalogs)
                scene_beat_key = scene_beat.attrs.get("id") or beat_key
                st.session_state[f"struct_beat_duration_{scene_beat_key}"] = scene_beat.attrs.get("duration", "")
                for field in ["speech_rate", "pause_before", "pause_after"]:
                    value = scene_beat.attrs.get(field)
                    if value not in (None, ""):
                        try:
                            st.session_state[f"struct_beat_{field}_{scene_beat_key}"] = float(value)
                        except Exception:
                            pass
                analyzed_count += 1
            st.success(f"Metadatos calculados para {analyzed_count} beat(s) de la escena.")
            autosave_working_book(root, episodes)
            st.rerun()
    with tools_c:
        if st.button("Renumerar beats de la escena"):
            renumber_scene_beats(scene)
            ensure_ids(episodes)
            set_selected_beat(scene, "script_structure", min(beat_idx, len(scene.beats) - 1))
            st.success("Etiquetas de beats reconstruidas en la escena actual")
            st.rerun()

    left_panel, right_panel = st.columns([1.5, 2.5])

    with left_panel:
        render_scene_beats_sidebar(scene, block, scene_idx, episodes, "script_structure")

    with right_panel:
        st.subheader("Editor de beats")
        st.caption("Los beats son la unidad principal. Los metadatos estructurales siguen disponibles más abajo.")

        with st.expander("Metadatos estructurales (episodio / bloque / escena)", expanded=False):
            c1, c2, c3 = st.columns(3)
            with c1:
                st.markdown("#### Episode")
                ep.attrs["id"] = st.text_input("episode.id", value=ep.attrs.get("id", ""), key=f"struct_episode_id_{ep_key}")
                ep.attrs["title"] = st.text_input("episode.title", value=ep.attrs.get("title", ""), key=f"struct_episode_title_{ep_key}")

            with c2:
                st.markdown("#### Block")
                block.attrs["id"] = st.text_input("block.id", value=block.attrs.get("id", ""), key=f"struct_block_id_{block_key}")
                block.attrs["kind"] = safe_select("block.kind", catalogs["block_kinds"], block.attrs.get("kind", ""), f"struct_block_kind_{block_key}")
                block.attrs["theme"] = safe_select("block.theme", catalogs["block_themes"], block.attrs.get("theme", ""), f"struct_block_theme_{block_key}")
                block.attrs["source_part"] = st.text_input("block.source_part", value=block.attrs.get("source_part", ""), key=f"struct_block_source_part_{block_key}")

            with c3:
                st.markdown("#### Scene")
                scene.attrs["id"] = st.text_input("scene.id", value=scene.attrs.get("id", ""), key=f"struct_scene_id_{scene_key}")
                scene.attrs["kind"] = safe_select("scene.kind", catalogs["scene_kinds"], scene.attrs.get("kind", ""), f"struct_scene_kind_{scene_key}")
                scene.attrs["location"] = safe_select("scene.location", catalogs["locations"], scene.attrs.get("location", ""), f"struct_scene_location_{scene_key}")
                scene.attrs["time"] = safe_select("scene.time", catalogs["scene_times"], scene.attrs.get("time", ""), f"struct_scene_time_{scene_key}")
                scene.attrs["mood"] = safe_select("scene.mood", catalogs["scene_moods"], scene.attrs.get("mood", ""), f"struct_scene_mood_{scene_key}")

            c4, c5 = st.columns(2)
            with c4:
                scene.attrs["characters"] = safe_multiselect("scene.characters", catalogs["characters"], scene.attrs.get("characters", ""), f"struct_scene_characters_{scene_key}")
                scene.attrs["props"] = safe_multiselect("scene.props", catalogs["props"], scene.attrs.get("props", ""), f"struct_scene_props_{scene_key}")
                scene.attrs["audio"] = safe_select("scene.audio", catalogs["scene_audio"], scene.attrs.get("audio", ""), f"struct_scene_audio_{scene_key}")
                scene.attrs["video"] = safe_select("scene.video", catalogs["scene_video"], scene.attrs.get("video", ""), f"struct_scene_video_{scene_key}")

            with c5:
                scene.attrs["shot_style"] = safe_select("scene.shot_style", catalogs["scene_shot_styles"], scene.attrs.get("shot_style", ""), f"struct_scene_shot_style_{scene_key}")
                scene.attrs["visual_style"] = safe_select("scene.visual_style", catalogs["scene_visual_styles"], scene.attrs.get("visual_style", ""), f"struct_scene_visual_style_{scene_key}")
                scene.attrs["voice_id"] = st.text_input("scene.voice_id", value=scene.attrs.get("voice_id", ""), key=f"struct_scene_voice_id_{scene_key}", help=field_help("scene.voice_id"))
                
                scene_continuity_options = [
                    "CONTINUOUS",
                    "CONT_VISUAL",
                    "CONT_CAMERA",
                    "CONT_LOCATION",
                    "CONT_CHARACTER",
                    "CONT_EMOTION",
                    "CONT_VOICE",
                    "CONT_AUDIO",
                    "CONT_TIME",
                    "SHIFT_VISUAL",
                    "SHIFT_CAMERA",
                    "SHIFT_LOCATION",
                    "SHIFT_CHARACTER",
                    "SHIFT_EMOTION",
                    "SHIFT_VOICE",
                    "SHIFT_AUDIO",
                    "TIME_JUMP",
                    "HARD_CUT",
                    "SOFT_TRANSITION",
                ]

                scene_continuity_value = scene.attrs.get("continuity_notes", "")
                scene_continuity_selected_defaults = [
                    x.strip()
                    for x in scene_continuity_value.split(",")
                    if x.strip() in scene_continuity_options
                ]
                scene_continuity_custom_default = ", ".join(
                    [
                        x.strip()
                        for x in scene_continuity_value.split(",")
                        if x.strip() and x.strip() not in scene_continuity_options
                    ]
                )

                scene_continuity_selected = st.multiselect(
                    "scene.continuity_notes",
                    scene_continuity_options,
                    default=scene_continuity_selected_defaults,
                    key=f"struct_scene_continuity_multi_{scene_key}",
                    help=field_help("scene.continuity_notes"),
                )

                scene_continuity_custom = st.text_area(
                    "Custom scene continuity",
                    value=scene_continuity_custom_default,
                    key=f"struct_scene_continuity_custom_{scene_key}",
                    height=90,
                    help=field_help("scene.continuity_notes"),
                )

                scene_continuity_custom_items = [x.strip() for x in scene_continuity_custom.split(",") if x.strip()]
                scene.attrs["continuity_notes"] = ", ".join(scene_continuity_selected + scene_continuity_custom_items)

        st.subheader("Beat seleccionado")
        st.caption("Desde aquí puedes editar texto, mover, dividir, borrar y aplicar metadatos al beat actual o propagarlos.")

        beat.attrs["id"] = st.text_input("beat.id", value=beat.attrs.get("id", ""), key=f"struct_beat_id_{beat_key}", help=field_help("beat.id"))
        st.session_state["script_structure_selected_beat_id"] = beat.attrs.get("id", "")
        beat_text_key = beat.attrs.get("id") or beat_key

        st.markdown("##### Texto del beat", help=field_help("Texto del beat"))

        beat.text = synced_beat_text_area("Texto del beat", beat, beat_text_key, height=220, help=field_help("Texto del beat"), label_visibility="collapsed")

        bmeta1, bmeta2, bmeta3 = st.columns(3)
        with bmeta1:
            beat.attrs["kind"] = safe_select(
                "beat.kind",
                catalogs["beat_kinds"],
                beat.attrs.get("kind", ""),
                f"struct_beat_kind_{beat_key}",
                help_text=BEAT_META_HELP["beat.kind"],
            )
            beat.attrs["shot"] = safe_select(
                "beat.shot",
                catalogs["beat_shots"],
                beat.attrs.get("shot", ""),
                f"struct_beat_shot_{beat_key}",
                help_text=BEAT_META_HELP["beat.shot"],
            )
            beat.attrs["framing"] = safe_select(
                "beat.framing",
                catalogs["beat_framings"],
                beat.attrs.get("framing", ""),
                f"struct_beat_framing_{beat_key}",
                help_text=BEAT_META_HELP["beat.framing"],
            )
            beat.attrs["camera"] = safe_select(
                "beat.camera",
                catalogs["beat_cameras"],
                beat.attrs.get("camera", ""),
                f"struct_beat_camera_{beat_key}",
                help_text=BEAT_META_HELP["beat.camera"],
            )
            beat.attrs["focus"] = safe_select(
                "beat.focus",
                catalogs["characters"] + catalogs["props"],
                beat.attrs.get("focus", ""),
                f"struct_beat_focus_{beat_key}",
                help_text=BEAT_META_HELP["beat.focus"],
            )

        with bmeta2:
            beat.attrs["transition"] = safe_select(
                "beat.transition",
                catalogs["beat_transitions"],
                beat.attrs.get("transition", ""),
                f"struct_beat_transition_{beat_key}",
                help_text=BEAT_META_HELP["beat.transition"],
            )

            duration_default = str(estimate_duration_seconds(beat.text))
            beat.attrs["duration"] = st.text_input(
                "beat.duration",
                value=beat.attrs.get("duration", duration_default) or duration_default,
                key=f"struct_beat_duration_{beat_key}",
                help=BEAT_META_HELP["beat.duration"],
            )

            beat.attrs["audio"] = safe_select(
                "beat.audio",
                catalogs["beat_audio"],
                beat.attrs.get("audio", ""),
                f"struct_beat_audio_{beat_key}",
                help_text=BEAT_META_HELP["beat.audio"],
            )
            beat.attrs["video"] = safe_select(
                "beat.video",
                catalogs["beat_video"],
                beat.attrs.get("video", ""),
                f"struct_beat_video_{beat_key}",
                help_text=BEAT_META_HELP["beat.video"],
            )
            beat.attrs["visual_style"] = safe_select(
                "beat.visual_style",
                catalogs["beat_visual_styles"],
                beat.attrs.get("visual_style", ""),
                f"struct_beat_visual_style_{beat_key}",
                help_text=BEAT_META_HELP["beat.visual_style"],
            )

        with bmeta3:
            beat.attrs["music_tag"] = safe_select(
                "beat.music_tag",
                catalogs["beat_music_tags"],
                beat.attrs.get("music_tag", ""),
                f"struct_beat_music_tag_{beat_key}",
                help_text=BEAT_META_HELP["beat.music_tag"],
            )
            beat.attrs["bgm_level"] = safe_select(
                "beat.bgm_level",
                catalogs["beat_bgm_levels"],
                beat.attrs.get("bgm_level", ""),
                f"struct_beat_bgm_level_{beat_key}",
                help_text=BEAT_META_HELP["beat.bgm_level"],
            )
            beat.attrs["sfx_level"] = safe_select(
                "beat.sfx_level",
                catalogs["beat_sfx_levels"],
                beat.attrs.get("sfx_level", ""),
                f"struct_beat_sfx_level_{beat_key}",
                help_text=BEAT_META_HELP["beat.sfx_level"],
            )
            beat.attrs["voice_intensity"] = safe_select(
                "beat.voice_intensity",
                catalogs["beat_voice_intensity"],
                beat.attrs.get("voice_intensity", ""),
                f"struct_beat_voice_intensity_{beat_key}",
                help_text=BEAT_META_HELP["beat.voice_intensity"],
            )

            speech_rate_val = safe_float(beat.attrs.get("speech_rate", 1.0), 1.0)
            pause_before_val = safe_float(beat.attrs.get("pause_before", 0.0), 0.0)
            pause_after_val = safe_float(beat.attrs.get("pause_after", 0.0), 0.0)

            beat.attrs["speech_rate"] = f"{st.slider('beat.speech_rate', 0.85, 1.10, speech_rate_val, 0.01, key=f'struct_beat_speech_rate_{beat_key}', help=BEAT_META_HELP['beat.speech_rate']):.2f}"
            beat.attrs["pause_before"] = f"{st.slider('beat.pause_before', 0.0, 0.5, pause_before_val, 0.01, key=f'struct_beat_pause_before_{beat_key}', help=BEAT_META_HELP['beat.pause_before']):.2f}"
            beat.attrs["pause_after"] = f"{st.slider('beat.pause_after', 0.0, 0.5, pause_after_val, 0.01, key=f'struct_beat_pause_after_{beat_key}', help=BEAT_META_HELP['beat.pause_after']):.2f}"

        c_lock1, c_lock2 = st.columns([1.5, 2.5])
        with c_lock1:
            beat.attrs["voice_id"] = st.text_input("beat.voice_id", value=beat.attrs.get("voice_id", scene.attrs.get("voice_id", "")), key=f"struct_beat_voice_id_{beat_key}", help=field_help("beat.voice_id"))
            beat.attrs["lock_location"] = "true" if st.checkbox("beat.lock_location", value=str(beat.attrs.get("lock_location", "true")).lower() == "true", key=f"struct_beat_lock_location_{beat_key}", help=field_help("beat.lock_location")) else "false"
            beat.attrs["lock_props"] = "true" if st.checkbox("beat.lock_props", value=str(beat.attrs.get("lock_props", "true")).lower() == "true", key=f"struct_beat_lock_props_{beat_key}", help=field_help("beat.lock_props")) else "false"
            beat.attrs["lock_voice"] = "true" if st.checkbox("beat.lock_voice", value=str(beat.attrs.get("lock_voice", "true")).lower() == "true", key=f"struct_beat_lock_voice_{beat_key}", help=field_help("beat.lock_voice")) else "false"
            beat.attrs["lock_visual_style"] = "true" if st.checkbox("beat.lock_visual_style", value=str(beat.attrs.get("lock_visual_style", "true")).lower() == "true", key=f"struct_beat_lock_visual_style_{beat_key}", help=field_help("beat.lock_visual_style")) else "false"
        with c_lock2:
            continuity_options = catalogs.get("continuity_tags", [])

            continuity_value = beat.attrs.get("continuity_notes", scene.attrs.get("continuity_notes", ""))
            continuity_selected_defaults = [x.strip() for x in continuity_value.split(",") if x.strip() in continuity_options]
            continuity_has_custom = any(x.strip() and x.strip() not in continuity_options for x in continuity_value.split(","))

            continuity_selected = st.multiselect(
                "beat.continuity_notes",
                continuity_options,
                default=continuity_selected_defaults,
                key=f"struct_beat_continuity_multi_{beat_key}",
                help=field_help("beat.continuity_notes"),
            )

            custom_continuity = ""
            if continuity_has_custom:
                custom_continuity = ", ".join(
                    [x.strip() for x in continuity_value.split(",") if x.strip() and x.strip() not in continuity_options]
                )

            custom_continuity = st.text_area(
                "Custom continuity",
                value=custom_continuity,
                key=f"struct_beat_continuity_custom_{beat_key}",
                height=90,
                help=field_help("beat.continuity_notes"),
            )

            custom_items = [x.strip() for x in custom_continuity.split(",") if x.strip()]
            beat.attrs["continuity_notes"] = ", ".join(continuity_selected + custom_items)
            
        ref1, ref2 = st.columns(2)

        with ref1:
            beat.attrs["reference_mode"] = catalog_select_or_create(
                root,
                "beat.reference_mode",
                catalogs.get("reference_modes", []),
                beat.attrs.get("reference_mode", "AUTO"),
                f"struct_beat_reference_mode_{beat_key}",
                "reference_mode",
                help_text="Controls whether this beat uses no explicit image reference, only a start image, or both start and end images for video generation.",
            )

            beat.attrs["visual_transition"] = catalog_select_or_create(
                root,
                "beat.visual_transition",
                catalogs.get("visual_transitions", []),
                beat.attrs.get("visual_transition", "NONE"),
                f"struct_beat_visual_transition_{beat_key}",
                "visual_transition",
                help_text="Visual transition hint for the video model.",
            )

            beat.attrs["ref_image_start"] = location_ref_select_or_create(
                root,
                "beat.ref_image_start",
                beat.attrs.get("ref_image_start", ""),
                f"struct_beat_ref_image_start_{beat_key}",
                catalogs,
            )

        with ref2:
            beat.attrs["ref_image_end"] = location_ref_select_or_create(
                root,
                "beat.ref_image_end",
                beat.attrs.get("ref_image_end", ""),
                f"struct_beat_ref_image_end_{beat_key}",
                catalogs,
            )

            beat.attrs["video_prompt"] = st.text_area(
                "beat.video_prompt",
                value=beat.attrs.get("video_prompt", ""),
                key=f"struct_beat_video_prompt_{beat_key}",
                height=110,
                help="Optional video prompt override for this beat. If empty, the normal motion prompt is used.",
            )

            beat.attrs["audio_transition"] = catalog_select_or_create(
                root,
                "beat.audio_transition",
                catalogs.get("audio_transitions", []),
                beat.attrs.get("audio_transition", "CUT"),
                f"struct_beat_audio_transition_{beat_key}",
                "audio_transition",
                help_text="Montage hint for audio continuity between beats.",
            )

        b1, b2, b3, b4  = st.columns(4) 
        with b1:
            if tip_button("Insertar beat después", key="struct_insert_beat_after_main"):
                insert_empty_beat(scene, beat_idx + 1)
                ensure_ids(episodes)
                set_selected_beat(scene, "script_structure", beat_idx + 1)
                st.rerun()
        with b2:
            if tip_button("Unir con el anterior", key="struct_merge_prev_main"):
                if merge_with_previous(scene, beat_idx):
                    ensure_ids(episodes)
                    set_selected_beat(scene, "script_structure", max(0, beat_idx - 1))
                    st.rerun()
        with b3:
            if tip_button("Unir con el siguiente", key="struct_merge_next_main"):
                if merge_with_next(scene, beat_idx):
                    ensure_ids(episodes)
                    set_selected_beat(scene, "script_structure", beat_idx)
                    st.rerun()
        with b4:
            if tip_button("Nueva escena desde este beat", key="struct_new_scene_from_beat_main"):
                ok = split_scene_from_beat(block, scene_idx, beat_idx)
                if ok:
                    ensure_ids(episodes)
                    set_selected_beat(scene, "script_structure", 0)
                    st.rerun()
                else:
                    st.warning("No se puede partir la escena desde el primer beat ni desde fuera de rango.")
   
        b5, b6, b7, b8 = st.columns(4)
        with b5:
            if tip_button("Nuevo bloque desde esta escena", key="struct_new_block_from_scene_main"):
                ok = split_block_from_scene(ep, block_idx, scene_idx)
                if ok:
                    ensure_ids(episodes)
                    set_selected_beat(scene, "script_structure", 0)
                    st.rerun()
                else:
                    st.warning("No se puede partir el bloque desde la primera escena ni desde fuera de rango.")
        with b6:
            if tip_button("Subir beat", key="struct_move_beat_up_main"):
                if move_beat(scene, beat_idx, -1):
                    set_selected_beat(scene, "script_structure", max(0, beat_idx - 1))
                    st.rerun()
        with b7:
            if tip_button("Bajar beat", key="struct_move_beat_down_main"):
                if move_beat(scene, beat_idx, 1):
                    set_selected_beat(scene, "script_structure", min(len(scene.beats) - 1, beat_idx + 1))
                    st.rerun()
        with b8:
            if tip_button("Borrar beat", key="struct_delete_beat_main"):
                if delete_beat(scene, beat_idx):
                    ensure_ids(episodes)
                    set_selected_beat(scene, "script_structure", max(0, min(beat_idx, len(scene.beats) - 1)))
                    st.rerun()
                else:
                    st.warning("La escena debe tener al menos un beat.")

        autosave_working_book(root, episodes)

        st.subheader("Propagar metadato")

        prop1, prop2, prop3, prop4 = st.columns([1.2, 1.3, 1.2, 1.1])

        target_level = prop1.selectbox("Nivel", ["beat", "scene", "block"], key=f"struct_propagation_level_{scene_key}")

        if target_level == "beat":
            fields = [
                "[ALL beat.* fields]",
                "kind",
                "shot",
                "framing",
                "camera",
                "focus",
                "transition",
                "duration",
                "audio",
                "video",
                "visual_style",
                "music_tag",
                "bgm_level",
                "sfx_level",
                "speech_rate",
                "voice_intensity",
                "pause_before",
                "pause_after",
                "voice_id",
                "continuity_notes",
                "lock_location",
                "lock_props",
                "lock_voice",
                "lock_visual_style",
                "reference_mode",
                "visual_transition",
                "audio_transition",
                "ref_image_start",
                "ref_image_end",
                "video_prompt",
            ]
            current_value_source = beat.attrs
            scope_options = ["hasta el final de escena", "hasta el final de bloque", "hasta el final de capítulo"]
            
            
        elif target_level == "scene":
            fields = ["kind", "location", "time", "mood", "characters", "props", "audio", "video", "visual_style", "shot_style", "voice_id", "continuity_notes"]
            current_value_source = scene.attrs
            scope_options = ["hasta el final de bloque", "hasta el final de capítulo"]
        else:
            fields = ["kind", "theme", "source_part"]
            current_value_source = block.attrs
            scope_options = ["hasta el final de capítulo"]

        field_to_propagate = prop2.selectbox("Campo", fields, key=f"struct_propagation_field_{scene_key}")
        selected_scope = prop3.selectbox("Alcance", scope_options, key=f"struct_propagation_scope_{scene_key}")
        overwrite_existing = prop4.checkbox("Sobrescribir", value=False, key=f"struct_propagation_overwrite_{scene_key}")

        if target_level == "beat" and field_to_propagate == "[ALL beat.* fields]":
            current_value_to_propagate = "[multiple values from current beat]"
        else:
            current_value_to_propagate = current_value_source.get(field_to_propagate, "")

        st.caption(f"Valor actual a propagar: `{current_value_to_propagate}`")

        if tip_button("Aplicar de aquí para adelante", key="struct_apply_propagation"):
            if target_level == "beat":
                if field_to_propagate == "[ALL beat.* fields]":
                    propagate_all_beat_attrs(
                        episodes,
                        ep_idx,
                        block_idx,
                        scene_idx,
                        beat_idx,
                        beat.attrs,
                        overwrite_existing,
                        selected_scope,
                    )
                else:
                    propagate_beat_attr(
                        episodes,
                        ep_idx,
                        block_idx,
                        scene_idx,
                        beat_idx,
                        field_to_propagate,
                        current_value_to_propagate,
                        overwrite_existing,
                        selected_scope,
                    )
            elif target_level == "scene":
                propagate_scene_attr(
                    episodes,
                    ep_idx,
                    block_idx,
                    scene_idx,
                    field_to_propagate,
                    current_value_to_propagate,
                    overwrite_existing,
                    selected_scope,
                )
            else:
                propagate_block_attr(
                    ep,
                    block_idx,
                    field_to_propagate,
                    current_value_to_propagate,
                    overwrite_existing,
                )
            st.success("Propagación aplicada")
            st.rerun()

        st.subheader("Operaciones de división")

        split_marker = st.text_input("Dividir beat por marcador de texto", value="", key=f"struct_split_marker_{beat_key}")
        s1, s2, s3 = st.columns(3)

        with s1:
            if tip_button("Dividir beat por marcador", key="struct_split_by_marker"):
                if split_beat_by_marker(scene, beat_idx, split_marker):
                    ensure_ids(episodes)
                    set_selected_beat(scene, "script_structure", beat_idx + 1)
                    st.rerun()
                else:
                    st.warning("No se pudo dividir. Comprueba que el marcador exista y deje dos partes con texto.")

        paragraphs = [p for p in beat.text.split("\n") if p.strip()]
        paragraph_labels = [f"{i}: {p[:80]}" for i, p in enumerate(paragraphs)]
        paragraph_split_index = 0
        if len(paragraphs) > 1:
            paragraph_split_index = st.selectbox(
                "Dividir a partir del salto de línea",
                range(len(paragraphs)),
                format_func=lambda i: paragraph_labels[i],
                index=1 if len(paragraphs) > 1 else 0,
                key=f"struct_paragraph_split_index_{beat_key}",
            )

        with s2:
            if tip_button("Dividir beat por salto de línea", key="struct_split_by_paragraph"):
                if split_beat_by_paragraph(scene, beat_idx, paragraph_split_index):
                    ensure_ids(episodes)
                    set_selected_beat(scene, "script_structure", beat_idx + 1)
                    st.rerun()
                else:
                    st.warning("No se pudo dividir por salto de línea.")

        split_manual_first_key = f"struct_split_manual_first_{beat_key}"
        split_manual_second_key = f"struct_split_manual_second_{beat_key}"
        if split_manual_first_key not in st.session_state:
            st.session_state[split_manual_first_key] = beat.text or ""
        if split_manual_second_key not in st.session_state:
            st.session_state[split_manual_second_key] = ""

        st.markdown("#### División manual del beat")
        sm1, sm2 = st.columns(2)
        with sm1:
            first_part = st.text_area(
                "Primera parte",
                value=st.session_state.get(split_manual_first_key, beat.text or ""),
                height=140,
                key=split_manual_first_key,
            )
        with sm2:
            second_part = st.text_area(
                "Segunda parte",
                value=st.session_state.get(split_manual_second_key, ""),
                height=140,
                key=split_manual_second_key,
            )

        if tip_button("Dividir beat manualmente", key="struct_split_manual"):
            if split_beat_manually(scene, beat_idx, first_part, second_part):
                ensure_ids(episodes)
                set_selected_beat(scene, "script_structure", beat_idx + 1)
                st.session_state[split_manual_second_key] = ""
                st.rerun()
            else:
                st.warning("Escribe texto en ambas partes para poder dividir el beat.")

        st.subheader("Mover o borrar niveles superiores")

        mr1, mr2, mr3, mr4 = st.columns(4)
        with mr1:
            if tip_button("Subir escena", key="struct_move_scene_up"):
                if move_scene(block, scene_idx, -1):
                    set_selected_beat(scene, "script_structure", beat_idx)
                    st.rerun()
        with mr2:
            if tip_button("Bajar escena", key="struct_move_scene_down"):
                if move_scene(block, scene_idx, 1):
                    set_selected_beat(scene, "script_structure", beat_idx)
                    st.rerun()
        with mr3:
            if tip_button("Insertar escena", key="struct_insert_empty_scene_after"):
                add_scene_after(block, scene_idx)
                ensure_ids(episodes)
                set_selected_beat(block.scenes[min(scene_idx + 1, len(block.scenes) - 1)], "script_structure", 0)
                st.rerun()
        with mr4:
            if tip_button("Borrar escena", key="struct_delete_scene"):
                if delete_scene(block, scene_idx):
                    ensure_ids(episodes)
                    set_selected_beat(scene, "script_structure", 0)
                    st.rerun()
                else:
                    st.warning("El bloque debe tener al menos una escena.")

        mr5, mr6, mr7, mr8 = st.columns(4)
        with mr5:
            if tip_button("Subir bloque", key="struct_move_block_up"):
                if move_block(ep, block_idx, -1):
                    set_selected_beat(scene, "script_structure", 0)
                    st.rerun()
        with mr6:
            if tip_button("Bajar bloque", key="struct_move_block_down"):
                if move_block(ep, block_idx, 1):
                    set_selected_beat(scene, "script_structure", beat_idx)
                    st.rerun()
        with mr7:
            if tip_button("Insertar bloque", key="struct_insert_empty_block_after"):
                add_block_after(ep, block_idx)
                ensure_ids(episodes)
                new_block = ep.blocks[min(block_idx + 1, len(ep.blocks) - 1)]
                target_scene = new_block.scenes[0] if new_block.scenes else Scene(attrs={"id": ""}, beats=[Beat(attrs={"id": ""}, text="")])
                set_selected_beat(target_scene, "script_structure", 0)
                st.rerun()
        with mr8:
            if tip_button("Borrar bloque", key="struct_delete_block"):
                if delete_block(ep, block_idx):
                    ensure_ids(episodes)
                    set_selected_beat(scene, "script_structure", 0)
                    st.rerun()
                else:
                    st.warning("El episodio debe tener al menos un bloque.")

        with st.expander("Vista previa reconstruida del libro"):
            ensure_ids(episodes)
            st.code(book_to_text(episodes), language="text")


def tab_tag_editor(root: Path):
    st.subheader("Etiquetas y sugerencias")
    selected = select_script_entities(root, "script_tags")
    if not selected or selected[0] is None:
        st.warning("No se ha podido cargar el libro")
        return

    ep, block, scene, beat, episodes, ep_idx, block_idx, scene_idx, beat_idx = selected
    
    tag_beat_key = f"ep{ep_idx}_b{block_idx}_s{scene_idx}_bt{beat_idx}"
    tag_scene_key = f"ep{ep_idx}_b{block_idx}_s{scene_idx}"
    tag_block_key = f"ep{ep_idx}_b{block_idx}"
    
    catalogs = build_catalogs(root)

    suggestion_scene_attrs = dict(scene.attrs)
    suggestion_scene_attrs["block_kind"] = block.attrs.get("kind", "")
    suggestion_scene_attrs["_scene_index"] = scene_idx
    suggestion_scene_attrs["_beat_index"] = beat_idx
    suggestion = inspect_beat(root, beat.text, suggestion_scene_attrs, beat.attrs)

    left, center, right = st.columns([1.2, 1.2, 1.5])

    with left:
        st.markdown("### Texto")
        st.code(beat.text, language="text")
        st.markdown("### Detectado")
        st.json({
            "characters": suggestion.get("detected_characters", []),
            "props": suggestion.get("detected_props", []),
            "location": suggestion.get("detected_location", ""),
            "focus": suggestion.get("focus", ""),
            "speaker": suggestion.get("speaker", ""),
            "function_type": suggestion.get("function_type", ""),
        })

    with center:
        st.markdown("### Sugerencias")
        st.json({
            "beat.kind": suggestion.get("function_type", ""),
            "beat.shot": suggestion.get("shot", ""),
            "beat.framing": suggestion.get("framing", ""),
            "beat.camera": suggestion.get("camera", ""),
            "beat.transition": suggestion.get("transition", ""),
            "beat.focus": suggestion.get("focus", ""),
            "scene.location": suggestion.get("location", ""),
            "scene.kind": suggestion.get("kind", ""),
            "scene.visual_style": suggestion.get("visual_style", ""),
        })

        if tip_button("Aplicar sugerencias al beat", key="tag_apply_suggestions"):
            beat.attrs["kind"] = suggestion.get("function_type", "") or beat.attrs.get("kind", "")
            beat.attrs["shot"] = suggestion.get("shot", "") or beat.attrs.get("shot", "")
            beat.attrs["framing"] = suggestion.get("framing", "") or beat.attrs.get("framing", "")
            beat.attrs["camera"] = suggestion.get("camera", "") or beat.attrs.get("camera", "")
            beat.attrs["focus"] = suggestion.get("focus", "") or beat.attrs.get("focus", "")
            beat.attrs["transition"] = suggestion.get("transition", "") or beat.attrs.get("transition", "")
            if not scene.attrs.get("location"):
                scene.attrs["location"] = suggestion.get("location", "")
            if not scene.attrs.get("kind"):
                scene.attrs["kind"] = suggestion.get("kind", "")
            if not scene.attrs.get("visual_style"):
                scene.attrs["visual_style"] = suggestion.get("visual_style", "")
            st.rerun()

    with right:
        st.markdown("### Editor guiado")

        block.attrs["theme"] = safe_select("block.theme", catalogs["block_themes"], block.attrs.get("theme", ""), f"tag_block_theme_{tag_block_key}")
        scene.attrs["kind"] = safe_select("scene.kind", catalogs["scene_kinds"], scene.attrs.get("kind", ""), f"tag_scene_kind_{tag_scene_key}")
        scene.attrs["location"] = safe_select("scene.location", catalogs["locations"], scene.attrs.get("location", ""), f"tag_scene_location_{tag_scene_key}")
        scene.attrs["time"] = safe_select("scene.time", catalogs["scene_times"], scene.attrs.get("time", ""), f"tag_scene_time_{tag_scene_key}")
        scene.attrs["mood"] = safe_select("scene.mood", catalogs["scene_moods"], scene.attrs.get("mood", ""), f"tag_scene_mood_{tag_scene_key}")
        scene.attrs["audio"] = safe_select("scene.audio", catalogs["scene_audio"], scene.attrs.get("audio", ""), f"tag_scene_audio_{tag_scene_key}")
        scene.attrs["video"] = safe_select("scene.video", catalogs["scene_video"], scene.attrs.get("video", ""), f"tag_scene_video_{tag_scene_key}")
        scene.attrs["shot_style"] = safe_select("scene.shot_style", catalogs["scene_shot_styles"], scene.attrs.get("shot_style", ""), f"tag_scene_shot_style_{tag_scene_key}")
        scene.attrs["visual_style"] = safe_select("scene.visual_style", catalogs["scene_visual_styles"], scene.attrs.get("visual_style", ""), f"tag_scene_visual_style_{tag_scene_key}")
        scene.attrs["characters"] = safe_multiselect("scene.characters", catalogs["characters"], scene.attrs.get("characters", ""), f"tag_scene_characters_{tag_scene_key}")
        scene.attrs["props"] = safe_multiselect("scene.props", catalogs["props"], scene.attrs.get("props", ""), f"tag_scene_props_{tag_scene_key}")

        st.markdown("#### Beat")
        beat.attrs["kind"] = safe_select(
            "beat.kind",    
            catalogs["beat_kinds"],
            beat.attrs.get("kind", ""),
            f"tag_beat_kind_{tag_beat_key}",
            help_text=BEAT_META_HELP["beat.kind"],
        )
        beat.attrs["shot"] = safe_select(
            "beat.shot",
            catalogs["beat_shots"],
            beat.attrs.get("shot", ""),
            f"tag_beat_shot_{tag_beat_key}",
            help_text=BEAT_META_HELP["beat.shot"],
        )
        beat.attrs["framing"] = safe_select(
            "beat.framing",
            catalogs["beat_framings"],
            beat.attrs.get("framing", ""),
            f"tag_beat_framing_{tag_beat_key}",
            help_text=BEAT_META_HELP["beat.framing"],
        )
        beat.attrs["camera"] = safe_select(
            "beat.camera",
            catalogs["beat_cameras"],
            beat.attrs.get("camera", ""),
            f"tag_beat_camera_{tag_beat_key}",
            help_text=BEAT_META_HELP["beat.camera"],
        )
        beat.attrs["focus"] = safe_select(
            "beat.focus",
            catalogs["characters"] + catalogs["props"],
            beat.attrs.get("focus", ""),
            f"tag_beat_focus_{tag_beat_key}",
            help_text=BEAT_META_HELP["beat.focus"],
        )
        beat.attrs["transition"] = safe_select(
            "beat.transition",
            catalogs["beat_transitions"],
            beat.attrs.get("transition", ""),
            f"tag_beat_transition_{tag_beat_key}",
            help_text=BEAT_META_HELP["beat.transition"],
        )
        beat.attrs["audio"] = safe_select(
            "beat.audio",
            catalogs["beat_audio"],
            beat.attrs.get("audio", ""),
            f"tag_beat_audio_{tag_beat_key}",
            help_text=BEAT_META_HELP["beat.audio"],
        )
        beat.attrs["video"] = safe_select(
            "beat.video",
            catalogs["beat_video"],
            beat.attrs.get("video", ""),
            f"tag_beat_video_{tag_beat_key}",
            help_text=BEAT_META_HELP["beat.video"],
        )
        beat.attrs["visual_style"] = safe_select(
            "beat.visual_style",
            catalogs["beat_visual_styles"],
            beat.attrs.get("visual_style", ""),
            f"tag_beat_visual_style_{tag_beat_key}",
            help_text=BEAT_META_HELP["beat.visual_style"],
        )
        beat.attrs["music_tag"] = safe_select(
            "beat.music_tag",
            catalogs["beat_music_tags"],
            beat.attrs.get("music_tag", ""),
            f"tag_beat_music_tag_{tag_beat_key}",
            help_text=BEAT_META_HELP["beat.music_tag"],
        )
        beat.attrs["bgm_level"] = safe_select(
            "beat.bgm_level",
            catalogs["beat_bgm_levels"],
            beat.attrs.get("bgm_level", ""),
            f"tag_beat_bgm_level_{tag_beat_key}",
            help_text=BEAT_META_HELP["beat.bgm_level"],
        )
        beat.attrs["sfx_level"] = safe_select(
            "beat.sfx_level",
            catalogs["beat_sfx_levels"],
            beat.attrs.get("sfx_level", ""),
            f"tag_beat_sfx_level_{tag_beat_key}",
            help_text=BEAT_META_HELP["beat.sfx_level"],
        )
        beat.attrs["voice_intensity"] = safe_select(
            "beat.voice_intensity",
            catalogs["beat_voice_intensity"],
            beat.attrs.get("voice_intensity", ""),
            f"tag_beat_voice_intensity_{tag_beat_key}",
            help_text=BEAT_META_HELP["beat.voice_intensity"],
        )

        duration_default = str(estimate_duration_seconds(beat.text))
        beat.attrs["duration"] = st.text_input(
            "beat.duration",
            value=beat.attrs.get("duration", duration_default) or duration_default,
            key=f"tag_beat_duration_{tag_beat_key}",
            help=BEAT_META_HELP["beat.duration"],
        )

        speech_rate_val = safe_float(beat.attrs.get("speech_rate", 1.0), 1.0)
        pause_before_val = safe_float(beat.attrs.get("pause_before", 0.0), 0.0)
        pause_after_val = safe_float(beat.attrs.get("pause_after", 0.0), 0.0)

        beat.attrs["speech_rate"] = f"{st.slider('beat.speech_rate', 0.85, 1.10, speech_rate_val, 0.01, key=f'tag_beat_speech_rate_{tag_beat_key}', help=BEAT_META_HELP['beat.speech_rate']):.2f}"
        beat.attrs["pause_before"] = f"{st.slider('beat.pause_before', 0.0, 0.5, pause_before_val, 0.01, key=f'tag_beat_pause_before_{tag_beat_key}', help=BEAT_META_HELP['beat.pause_before']):.2f}"
        beat.attrs["pause_after"] = f"{st.slider('beat.pause_after', 0.0, 0.5, pause_after_val, 0.01, key=f'tag_beat_pause_after_{tag_beat_key}', help=BEAT_META_HELP['beat.pause_after']):.2f}"
        
    st.markdown("---")
    g1, g2 = st.columns(2)
    with g1:
        if tip_button("Guardar archivo activo con etiquetas", key="tag_save_book"):
            book_path = get_active_book_path(root)
            ensure_ids(episodes)
            backup_path = save_book_text(book_path, book_to_text(episodes))
            st.success(f"Guardado: {book_path}" + (f" · Backup: {backup_path}" if backup_path else ""))
    with g2:
        if tip_button("Ver preview del beat enriquecido", key="tag_preview_beat"):
            preview_scene = Scene(attrs=dict(scene.attrs), beats=[Beat(attrs=dict(beat.attrs), text=beat.text)])
            preview_block = Block(attrs=dict(block.attrs), scenes=[preview_scene])
            preview_episode = Episode(attrs=dict(ep.attrs), blocks=[preview_block])
            st.code(book_to_text([preview_episode]), language="text")
    autosave_working_book(root, episodes)
    

def tab_script_editor(root: Path):
    st.header("Guion")
    st.markdown(
        """
        <style>
        div.stButton > button {
            padding: 0.14rem 0.30rem;
            min-height: 1.9rem;
            line-height: 1;
            font-size: 0.88rem;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    section = st.radio(
        "Sección del guion",
        ["Segmentación", "Estructura", "Etiquetas"],
        horizontal=True,
        key="script_editor_section",
        label_visibility="collapsed",
    )
    if section == "Segmentación":
        tab_segmentation(root)
    elif section == "Estructura":
        tab_structure_editor(root)
    else:
        tab_tag_editor(root)


def tab_inspector(root: Path):
    st.header("Inspector automático")
    selected = select_script_entities(root, "insp")
    if not selected or selected[0] is None:
        st.warning("No hay archivo estructurado activo")
        return

    ep, block, scene, beat, episodes, ep_idx, block_idx, scene_idx, beat_idx = selected
    overrides = load_overrides(root)
    merged_beat_attrs = apply_overrides_to_beat(beat.attrs.get("id", ""), beat.attrs, overrides)

    scene_attrs = dict(scene.attrs)
    scene_attrs["block_kind"] = block.attrs.get("kind", "")
    scene_attrs["_scene_index"] = 0
    scene_attrs["_beat_index"] = 0

    result = inspect_beat(root, beat.text, scene_attrs, merged_beat_attrs)

    left, right = st.columns(2)
    with left:
        st.subheader("Texto")
        st.code(beat.text, language="text")
        st.subheader("Detección")
        st.json({
            "detected_characters": result["detected_characters"],
            "detected_props": result["detected_props"],
            "detected_location": result["detected_location"],
            "focus": result["focus"],
            "speaker": result["speaker"],
        })
        st.subheader("Función dramática")
        st.write(f"**function_type:** `{result['function_type']}`")
        st.json(result["function_scores"])

    with right:
        st.subheader("Dirección")
        st.json({
            "shot": result["shot"],
            "framing": result["framing"],
            "camera": result["camera"],
            "transition": result["transition"],
            "visual_style": result["visual_style"],
        })
        st.subheader("Prompt de movimiento")
        st.code(result["motion_prompt_preview"], language="text")


def import_shot_planner(project_root: Path):
    src_dir = project_root / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    try:
        from shot_planner import plan_scene_shots
        return plan_scene_shots
    except Exception:
        return None


def build_scene_storyboard(project_root: Path, scene, block, beats, overrides: Dict[str, Dict[str, str]]):
    directions = []
    previous_speaker = ""
    previous_focus = ""
    for i, beat in enumerate(beats):
        scene_attrs = dict(scene.attrs)
        scene_attrs["block_kind"] = block.attrs.get("kind", "")
        scene_attrs["_scene_index"] = 0
        scene_attrs["_beat_index"] = i
        scene_attrs["_previous_speaker"] = previous_speaker
        scene_attrs["_previous_focus"] = previous_focus

        merged_beat_attrs = apply_overrides_to_beat(beat.attrs.get("id", ""), beat.attrs, overrides)
        direction = inspect_beat(project_root=project_root, text=beat.text, scene_attrs=scene_attrs, beat_attrs=merged_beat_attrs)
        direction["beat_id"] = beat.attrs.get("id", "")
        direction["text"] = beat.text
        previous_speaker = direction.get("speaker", "") or previous_speaker
        previous_focus = direction.get("focus", "") or previous_focus
        directions.append(direction)

    plan_scene_shots = import_shot_planner(project_root)
    if plan_scene_shots:
        try:
            directions = plan_scene_shots(directions)
        except Exception:
            pass
    return directions


def storyboard_rows(storyboard: List[Dict[str, str]]):
    rows = []
    for idx, d in enumerate(storyboard, start=1):
        rows.append({
            "beat": idx,
            "id": d.get("beat_id", ""),
            "shot": d.get("shot", ""),
            "framing": d.get("framing", ""),
            "camera": d.get("camera", ""),
            "focus": d.get("focus", ""),
            "speaker": d.get("speaker", ""),
            "transition": d.get("transition", ""),
            "function": d.get("function_type", ""),
            "location": d.get("location", ""),
        })
    return rows


def tab_storyboard(root: Path):
    st.header("Storyboard de escena")
    selected = select_script_entities(root, "story")
    if not selected or selected[0] is None:
        st.warning("No hay archivo estructurado activo")
        return

    ep, block, scene, beat, episodes, ep_idx, block_idx, scene_idx, beat_idx = selected
    overrides = load_overrides(root)
    storyboard = build_scene_storyboard(root, scene, block, scene.beats, overrides)
    rows = storyboard_rows(storyboard)

    st.subheader(f"Escena {scene.attrs.get('id', '')}")
    st.table(rows)

    st.subheader("Timeline visual")
    for r in rows:
        st.markdown(
            f"""**{r["beat"]} — {r["shot"]}**

focus: `{r["focus"]}`  
speaker: `{r["speaker"]}`  
transition: `{r["transition"]}`  
function: `{r["function"]}`  
location: `{r["location"]}`
"""
        )

    st.markdown("---")
    beat_color_field = st.selectbox(
        "Colorear beats por metadato",
        ["kind", "shot", "camera", "focus", "transition", "music_tag", "bgm_level", "speaker"],
        key="story_beat_color_field",
    )
    render_beat_color_map(scene, root, beat_color_field)


def run_render_command(root: Path, episode_id: str, scene_id: str = ""):
    script = root / "src" / "series_studio.py"
    if not script.exists():
        return False, f"No existe {script}"

    book_path = get_active_book_path(root)
    cmd = ["python", str(script), "render", "--book", str(book_path), "--episode", episode_id]
    if scene_id:
        cmd += ["--scene", scene_id]

    try:
        proc = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, timeout=1800)
        output = (proc.stdout or "") + "\n" + (proc.stderr or "")
        return proc.returncode == 0, output.strip()
    except Exception as e:
        return False, str(e)


def run_validate_command(root: Path):
    script = root / "src" / "series_studio.py"
    if not script.exists():
        return False, f"No existe {script}"

    book_path = get_active_book_path(root)
    cmd = ["python", str(script), "validate", "--book", str(book_path)]

    try:
        proc = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, timeout=300)
        output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        return proc.returncode == 0, output
    except Exception as e:
        return False, str(e)


def tab_render(root: Path):
    st.header("Render")
    selected = select_script_entities(root, "render")
    if not selected or selected[0] is None:
        st.warning("No hay archivo estructurado activo")
        return

    ep, block, scene, beat, episodes, ep_idx, block_idx, scene_idx, beat_idx = selected
    book_path = get_active_book_path(root)

    st.write("Puedes validar el libro antes de lanzar el render.")
    validate_preview = f'python src/series_studio.py validate --book "{book_path}"'
    cmd_preview = f'python src/series_studio.py render --book "{book_path}" --episode {ep.attrs.get("id","")} --scene {scene.attrs.get("id","")}'
    st.code(validate_preview + "\n" + cmd_preview, language="bash")

    c1, c2 = st.columns([1, 1])
    with c1:
        if tip_button("Validar libro antes de render", key="validate_book_button"):
            ok, output = run_validate_command(root)
            st.session_state["render_validate_output"] = output
            st.session_state["render_validate_ok"] = ok
    with c2:
        if tip_button("Renderizar escena seleccionada", key="render_scene_button"):
            ok, output = run_render_command(root, ep.attrs.get("id", ""), scene.attrs.get("id", ""))
            st.session_state["render_output"] = output
            st.session_state["render_ok"] = ok

    if "render_validate_output" in st.session_state:
        if st.session_state.get("render_validate_ok"):
            st.success("Validación correcta")
        else:
            st.error("La validación detectó problemas en el libro")
        st.text_area("Salida de validación", value=st.session_state.get("render_validate_output", ""), height=220, key="render_validate_output_area")

    if "render_output" in st.session_state:
        if st.session_state.get("render_ok"):
            st.success("Render ejecutado")
        else:
            st.error("El render devolvió error")
        st.text_area("Salida del render", value=st.session_state.get("render_output", ""), height=250, key="render_output_area")

# --------------------------------------------------
# Main
# --------------------------------------------------
def main():
    root = sidebar_project_root()
    active_book = sidebar_book_selector(root)
    ensure_open_backup(active_book)

    tabs = st.tabs(["Proyecto", "JSON", "Guion", "Inspector", "Storyboard", "Render"])
    with tabs[0]:
        tab_project(root)
    with tabs[1]:
        tab_json_editor(root)
    with tabs[2]:
        tab_script_editor(root)
    with tabs[3]:
        tab_inspector(root)
    with tabs[4]:
        tab_storyboard(root)
    with tabs[5]:
        tab_render(root)


if __name__ == "__main__":
    main()

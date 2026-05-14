from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import streamlit as st

st.set_page_config(page_title="Beat Studio Minimal", layout="wide")


# --------------------------------------------------
# Modelos
# --------------------------------------------------
@dataclass
class Beat:
    attrs: Dict[str, str]
    text: str


@dataclass
class Scene:
    attrs: Dict[str, str]
    beats: List[Beat] = field(default_factory=list)


@dataclass
class Block:
    attrs: Dict[str, str]
    scenes: List[Scene] = field(default_factory=list)


@dataclass
class Episode:
    attrs: Dict[str, str]
    blocks: List[Block] = field(default_factory=list)


# --------------------------------------------------
# IO
# --------------------------------------------------
def load_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return default


def save_text(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# --------------------------------------------------
# Parser
# --------------------------------------------------
def parse_attrs(raw: str) -> Dict[str, str]:
    return {k: v.strip() for k, v in re.findall(r'(\w+)\s*=\s*"([^"]*)"', raw, flags=re.DOTALL)}


def format_attrs(attrs: Dict[str, str]) -> str:
    return " ".join([f'{k}="{v}"' for k, v in attrs.items() if str(v) != ""])


def parse_book(book_path: Path) -> List[Episode]:
    text = load_text(book_path, "")
    ep_pattern = re.compile(r"\{episode(?P<attrs>.*?)\}(?P<body>.*?)\{/episode\}", re.DOTALL)
    block_pattern = re.compile(r"\{block(?P<attrs>.*?)\}(?P<body>.*?)\{/block\}", re.DOTALL)
    scene_pattern = re.compile(r"\{scene(?P<attrs>.*?)\}(?P<body>.*?)\{/scene\}", re.DOTALL)
    beat_pattern = re.compile(r"\{beat(?P<attrs>.*?)\}(?P<body>.*?)\{/beat\}", re.DOTALL)

    episodes: List[Episode] = []
    for ep_match in ep_pattern.finditer(text):
        ep = Episode(parse_attrs(ep_match.group("attrs")))
        for block_match in block_pattern.finditer(ep_match.group("body")):
            block = Block(parse_attrs(block_match.group("attrs")))
            for scene_match in scene_pattern.finditer(block_match.group("body")):
                scene = Scene(parse_attrs(scene_match.group("attrs")))
                for beat_match in beat_pattern.finditer(scene_match.group("body")):
                    scene.beats.append(Beat(parse_attrs(beat_match.group("attrs")), beat_match.group("body").strip()))
                block.scenes.append(scene)
            ep.blocks.append(block)
        episodes.append(ep)
    return episodes


def book_to_text(episodes: List[Episode]) -> str:
    lines: List[str] = []
    for ep in episodes:
        lines.append(f'{{episode {format_attrs(ep.attrs)}}}')
        lines.append("")
        for block in ep.blocks:
            lines.append(f'{{block {format_attrs(block.attrs)}}}')
            lines.append("")
            for scene in block.scenes:
                lines.append(f'{{scene {format_attrs(scene.attrs)}}}')
                lines.append("")
                for beat in scene.beats:
                    lines.append(f'{{beat {format_attrs(beat.attrs)}}}')
                    lines.append(beat.text.strip())
                    lines.append("{/beat}")
                    lines.append("")
                lines.append("{/scene}")
                lines.append("")
            lines.append("{/block}")
            lines.append("")
        lines.append("{/episode}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


# --------------------------------------------------
# Estado de trabajo
# --------------------------------------------------
def get_book_path() -> Path:
    default = Path.cwd() / "libro_borrador.txt"
    path_str = st.session_state.get("book_path", str(default))
    return Path(path_str)


def get_working_book() -> List[Episode]:
    book_path = get_book_path()
    if "working_book" not in st.session_state or st.session_state.get("working_book_path") != str(book_path):
        st.session_state["working_book"] = parse_book(book_path) if book_path.exists() else []
        st.session_state["working_book_path"] = str(book_path)
    return st.session_state["working_book"]


def reset_working_book():
    book_path = get_book_path()
    st.session_state["working_book"] = parse_book(book_path) if book_path.exists() else []
    st.session_state["working_book_path"] = str(book_path)


# --------------------------------------------------
# Utilidades beats
# --------------------------------------------------
def compact(text: str, n: int = 80) -> str:
    clean = " ".join(text.split())
    return clean[:n] + ("..." if len(clean) > n else "")


def ensure_ids(episodes: List[Episode]):
    for e_i, ep in enumerate(episodes, start=1):
        ep.attrs["id"] = ep.attrs.get("id") or f"E{e_i:02d}"
        for b_i, block in enumerate(ep.blocks, start=1):
            block.attrs["id"] = block.attrs.get("id") or f"{ep.attrs['id']}_B{b_i:02d}"
            for s_i, scene in enumerate(block.scenes, start=1):
                scene.attrs["id"] = scene.attrs.get("id") or f"{block.attrs['id']}_S{s_i:02d}"
                for bt_i, beat in enumerate(scene.beats, start=1):
                    beat.attrs["id"] = beat.attrs.get("id") or f"{scene.attrs['id']}_BT{bt_i:02d}"


def flatten_episode_beats(ep: Episode) -> List[Tuple[int, int, int, Beat]]:
    out = []
    for b_i, block in enumerate(ep.blocks):
        for s_i, scene in enumerate(block.scenes):
            for bt_i, beat in enumerate(scene.beats):
                out.append((b_i, s_i, bt_i, beat))
    return out


def get_scene(ep: Episode, block_idx: int, scene_idx: int) -> Scene:
    return ep.blocks[block_idx].scenes[scene_idx]


def split_beat_at_line(scene: Scene, beat_idx: int, line_idx: int) -> bool:
    beat = scene.beats[beat_idx]
    lines = beat.text.splitlines()
    if len(lines) < 2:
        return False
    if line_idx <= 0 or line_idx >= len(lines):
        return False

    left = "\n".join(lines[:line_idx]).strip()
    right = "\n".join(lines[line_idx:]).strip()
    if not left or not right:
        return False

    old_attrs = dict(beat.attrs)
    beat.text = left
    scene.beats.insert(beat_idx + 1, Beat(attrs=dict(old_attrs), text=right))
    scene.beats[beat_idx + 1].attrs["id"] = ""
    return True


def merge_with_previous(scene: Scene, beat_idx: int) -> bool:
    if beat_idx <= 0:
        return False
    prev = scene.beats[beat_idx - 1]
    curr = scene.beats[beat_idx]
    prev.text = (prev.text.rstrip() + "\n\n" + curr.text.lstrip()).strip()
    del scene.beats[beat_idx]
    return True


def merge_with_next(scene: Scene, beat_idx: int) -> bool:
    if beat_idx >= len(scene.beats) - 1:
        return False
    curr = scene.beats[beat_idx]
    nxt = scene.beats[beat_idx + 1]
    curr.text = (curr.text.rstrip() + "\n\n" + nxt.text.lstrip()).strip()
    del scene.beats[beat_idx + 1]
    return True


def move_beat(scene: Scene, beat_idx: int, direction: int) -> bool:
    new_idx = beat_idx + direction
    if 0 <= new_idx < len(scene.beats):
        scene.beats[beat_idx], scene.beats[new_idx] = scene.beats[new_idx], scene.beats[beat_idx]
        return True
    return False


def delete_beat(scene: Scene, beat_idx: int) -> bool:
    if len(scene.beats) <= 1:
        return False
    del scene.beats[beat_idx]
    return True


def insert_empty_beat_after(scene: Scene, beat_idx: int):
    scene.beats.insert(beat_idx + 1, Beat(attrs={"id": ""}, text=""))


# --------------------------------------------------
# UI
# --------------------------------------------------
def sidebar():
    st.sidebar.header("Beat Studio Minimal")
    default = st.session_state.get("book_path", str(Path.cwd() / "libro_borrador.txt"))
    st.session_state["book_path"] = st.sidebar.text_input("Archivo estructurado", value=default)

    if st.sidebar.button("Recargar desde disco"):
        reset_working_book()
        st.rerun()


def select_episode(episodes: List[Episode]) -> Tuple[Optional[Episode], Optional[int]]:
    if not episodes:
        return None, None
    ep_idx = st.selectbox(
        "Episodio",
        range(len(episodes)),
        format_func=lambda i: f'{episodes[i].attrs.get("id","?")} — {episodes[i].attrs.get("title","")}',
        key="episode_select",
    )
    return episodes[ep_idx], ep_idx


def main():
    sidebar()
    book_path = get_book_path()
    episodes = get_working_book()
    ensure_ids(episodes)

    st.title("Beat Studio Minimal")
    st.caption("Interfaz centrada en beats. Bloques y escenas existen, pero no mandan.")

    if not book_path.exists():
        st.warning("El archivo no existe todavía.")
        raw = st.text_area("Crear archivo estructurado manualmente", value="", height=300)
        if st.button("Guardar archivo vacío"):
            save_text(book_path, raw)
            reset_working_book()
            st.rerun()
        return

    ep, ep_idx = select_episode(episodes)
    if ep is None:
        st.warning("No hay episodios en el archivo.")
        return

    flat = flatten_episode_beats(ep)
    if not flat:
        st.warning("No hay beats en este episodio.")
        return

    selected_idx = st.session_state.get("selected_flat_beat_idx", 0)
    selected_idx = max(0, min(selected_idx, len(flat) - 1))

    left, right = st.columns([1.1, 2.2])

    with left:
        st.subheader("Beats")
        for i, (b_i, s_i, bt_i, beat) in enumerate(flat):
            label = f"{beat.attrs.get('id','?')} — {compact(beat.text, 50)}"
            if st.button(label, key=f"beat_list_{i}"):
                st.session_state["selected_flat_beat_idx"] = i
                st.rerun()

    with right:
        b_i, s_i, bt_i, beat = flat[selected_idx]
        scene = get_scene(ep, b_i, s_i)

        st.subheader(beat.attrs.get("id", "Beat"))
        st.caption(f"Bloque {ep.blocks[b_i].attrs.get('id','?')} · Escena {scene.attrs.get('id','?')} · Posición {bt_i + 1}")

        beat.attrs["kind"] = st.text_input("beat.kind", value=beat.attrs.get("kind", ""), key="beat_kind_edit")
        beat.text = st.text_area("Texto del beat", value=beat.text, height=260, key="beat_text_edit")

        c1, c2, c3, c4, c5, c6 = st.columns(6)
        with c1:
            if st.button("↑ Subir", key="move_up"):
                if move_beat(scene, bt_i, -1):
                    ensure_ids(episodes)
                    st.session_state["selected_flat_beat_idx"] = max(0, selected_idx - 1)
                    st.rerun()
        with c2:
            if st.button("↓ Bajar", key="move_down"):
                if move_beat(scene, bt_i, 1):
                    ensure_ids(episodes)
                    st.session_state["selected_flat_beat_idx"] = min(len(flat) - 1, selected_idx + 1)
                    st.rerun()
        with c3:
            if st.button("+ Insertar", key="insert_after"):
                insert_empty_beat_after(scene, bt_i)
                ensure_ids(episodes)
                st.session_state["selected_flat_beat_idx"] = selected_idx + 1
                st.rerun()
        with c4:
            if st.button("Unir ant.", key="merge_prev"):
                if merge_with_previous(scene, bt_i):
                    ensure_ids(episodes)
                    st.session_state["selected_flat_beat_idx"] = max(0, selected_idx - 1)
                    st.rerun()
        with c5:
            if st.button("Unir sig.", key="merge_next"):
                if merge_with_next(scene, bt_i):
                    ensure_ids(episodes)
                    st.rerun()
        with c6:
            if st.button("Borrar", key="delete_beat"):
                if delete_beat(scene, bt_i):
                    ensure_ids(episodes)
                    st.session_state["selected_flat_beat_idx"] = max(0, selected_idx - 1)
                    st.rerun()

        st.markdown("---")
        st.subheader("Partir este beat por línea")
        lines = beat.text.splitlines()

        if len(lines) <= 1:
            st.info("Este beat solo tiene una línea visible. Para partirlo mejor, primero separa líneas en el texto.")
        else:
            for i, line in enumerate(lines):
                c_line, c_btn = st.columns([5, 1.2])
                with c_line:
                    st.code(line if line.strip() else " ", language="text")
                with c_btn:
                    if i > 0 and st.button("Nuevo beat aquí", key=f"split_at_line_{i}"):
                        if split_beat_at_line(scene, bt_i, i):
                            ensure_ids(episodes)
                            st.session_state["selected_flat_beat_idx"] = selected_idx + 1
                            st.rerun()

        st.markdown("---")
        s1, s2 = st.columns(2)
        with s1:
            if st.button("Guardar archivo"):
                ensure_ids(episodes)
                save_text(book_path, book_to_text(episodes))
                st.success(f"Guardado: {book_path}")
        with s2:
            with st.expander("Vista previa del archivo"):
                ensure_ids(episodes)
                st.code(book_to_text(episodes), language="text")


if __name__ == "__main__":
    main()
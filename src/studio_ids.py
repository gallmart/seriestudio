import re
from typing import List, Optional

from studio_models import Episode, Scene

BEAT_ORDER_SCALE = 1000


def infer_next_id(prefix: str, existing_ids: List[str]) -> str:
    max_num = 0
    for eid in existing_ids:
        m = re.search(rf"{re.escape(prefix)}(\d+)", eid or "", flags=re.IGNORECASE)
        if m:
            max_num = max(max_num, int(m.group(1)))
    return f"{prefix}{max_num + 1:02d}"


def _parse_beat_order_value(beat_id: str, scene_id: str) -> Optional[int]:
    pattern = rf"^{re.escape(scene_id)}_BT(\d+)(?:_(\d+))?$"
    m = re.match(pattern, beat_id or "", flags=re.IGNORECASE)
    if not m:
        return None
    base = int(m.group(1)) * BEAT_ORDER_SCALE
    frac_raw = m.group(2)
    if not frac_raw:
        return base
    frac = int((frac_raw + "000")[:3])
    return base + frac


def _format_fraction(frac: int) -> str:
    if frac % 100 == 0:
        return f"{frac // 10:02d}"
    if frac % 10 == 0:
        return str(frac // 10)
    return str(frac)


def format_beat_id(scene_id: str, order_value: int) -> str:
    base = max(0, order_value // BEAT_ORDER_SCALE)
    frac = max(0, order_value % BEAT_ORDER_SCALE)
    if frac == 0:
        return f"{scene_id}_BT{base:02d}"
    return f"{scene_id}_BT{base:02d}_{_format_fraction(frac)}"


def choose_inserted_beat_id(scene: Scene, index: int) -> str:
    scene_id = scene.attrs.get("id", "")
    prev_value = None
    next_value = None

    if index - 1 >= 0:
        prev_value = _parse_beat_order_value(scene.beats[index - 1].attrs.get("id", ""), scene_id)
    if index < len(scene.beats):
        next_value = _parse_beat_order_value(scene.beats[index].attrs.get("id", ""), scene_id)

    if prev_value is not None and next_value is not None and next_value - prev_value > 1:
        return format_beat_id(scene_id, prev_value + (next_value - prev_value) // 2)
    if prev_value is not None:
        return format_beat_id(scene_id, prev_value + BEAT_ORDER_SCALE)
    if next_value is not None and next_value > 1:
        return format_beat_id(scene_id, max(1, next_value // 2))
    return format_beat_id(scene_id, BEAT_ORDER_SCALE)


def _find_next_valid_order(scene: Scene, start_index: int, used_orders: set[int]) -> Optional[int]:
    scene_id = scene.attrs.get("id", "")
    for future in scene.beats[start_index + 1:]:
        value = _parse_beat_order_value(future.attrs.get("id", ""), scene_id)
        if value is not None and value not in used_orders:
            return value
    return None


def _choose_order_between(prev_order: Optional[int], next_order: Optional[int], fallback_index: int) -> int:
    if prev_order is None and next_order is None:
        return max(1, (fallback_index + 1) * BEAT_ORDER_SCALE)
    if prev_order is None:
        return max(1, next_order // 2)
    if next_order is None:
        return prev_order + BEAT_ORDER_SCALE
    if next_order - prev_order > 1:
        return prev_order + (next_order - prev_order) // 2
    return prev_order + 1


def ensure_ids(episodes: List[Episode]):
    ep_ids = [ep.attrs.get("id", "") for ep in episodes]
    for ep in episodes:
        if not ep.attrs.get("id"):
            ep.attrs["id"] = infer_next_id("E", ep_ids)
            ep_ids.append(ep.attrs["id"])

        block_ids = [b.attrs.get("id", "") for b in ep.blocks]
        for block in ep.blocks:
            if not block.attrs.get("id"):
                block.attrs["id"] = infer_next_id(f"{ep.attrs['id']}_B", block_ids)
                block_ids.append(block.attrs["id"])

            scene_ids = [s.attrs.get("id", "") for s in block.scenes]
            for scene in block.scenes:
                if not scene.attrs.get("id"):
                    scene.attrs["id"] = infer_next_id(f"{block.attrs['id']}_S", scene_ids)
                    scene_ids.append(scene.attrs["id"])

                scene_id = scene.attrs["id"]
                used_orders: set[int] = set()
                for idx, beat in enumerate(scene.beats):
                    beat_id = beat.attrs.get("id", "")
                    order_value = _parse_beat_order_value(beat_id, scene_id)
                    if order_value is not None and order_value not in used_orders:
                        used_orders.add(order_value)
                        continue

                    prev_order = max(used_orders) if used_orders else None
                    next_order = _find_next_valid_order(scene, idx, used_orders)
                    new_order = _choose_order_between(prev_order, next_order, idx)
                    while new_order in used_orders:
                        new_order += 1
                    beat.attrs["id"] = format_beat_id(scene_id, new_order)
                    used_orders.add(new_order)


def renumber_scene_beats(scene: Scene):
    scene_id = scene.attrs.get("id", "")
    for idx, beat in enumerate(scene.beats, start=1):
        beat.attrs["id"] = format_beat_id(scene_id, idx * BEAT_ORDER_SCALE)

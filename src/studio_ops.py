from studio_models import Beat, Block, Episode, Scene
from studio_ids import choose_inserted_beat_id


def insert_empty_beat(scene: Scene, index: int):
    scene.beats.insert(index, Beat(attrs={"id": choose_inserted_beat_id(scene, index)}, text=""))


def split_beat_by_marker(scene: Scene, beat_index: int, marker: str) -> bool:
    beat = scene.beats[beat_index]
    if not marker or marker not in beat.text:
        return False
    left, right = beat.text.split(marker, 1)
    left = left.strip()
    right = (marker + right).strip()
    if not left or not right:
        return False
    original_attrs = dict(beat.attrs)
    beat.text = left
    new_beat = Beat(attrs=dict(original_attrs), text=right)
    new_beat.attrs["id"] = choose_inserted_beat_id(scene, beat_index + 1)
    scene.beats.insert(beat_index + 1, new_beat)
    return True


def split_beat_by_paragraph(scene: Scene, beat_index: int, paragraph_index: int) -> bool:
    beat = scene.beats[beat_index]
    paragraphs = [p for p in beat.text.split("\n") if p.strip()]
    if len(paragraphs) < 2 or paragraph_index <= 0 or paragraph_index >= len(paragraphs):
        return False
    left = "\n".join(paragraphs[:paragraph_index]).strip()
    right = "\n".join(paragraphs[paragraph_index:]).strip()
    if not left or not right:
        return False
    original_attrs = dict(beat.attrs)
    beat.text = left
    new_beat = Beat(attrs=dict(original_attrs), text=right)
    new_beat.attrs["id"] = choose_inserted_beat_id(scene, beat_index + 1)
    scene.beats.insert(beat_index + 1, new_beat)
    return True


def split_beat_manually(scene: Scene, beat_index: int, first_text: str, second_text: str) -> bool:
    left = (first_text or "").strip()
    right = (second_text or "").strip()
    if not left or not right:
        return False
    beat = scene.beats[beat_index]
    original_attrs = dict(beat.attrs)
    beat.text = left
    new_beat = Beat(attrs=dict(original_attrs), text=right)
    new_beat.attrs["id"] = choose_inserted_beat_id(scene, beat_index + 1)
    scene.beats.insert(beat_index + 1, new_beat)
    return True


def move_beat(scene: Scene, beat_index: int, direction: int) -> bool:
    new_index = beat_index + direction
    if 0 <= new_index < len(scene.beats):
        scene.beats[beat_index], scene.beats[new_index] = scene.beats[new_index], scene.beats[beat_index]
        return True
    return False


def move_scene(block: Block, scene_index: int, direction: int) -> bool:
    new_index = scene_index + direction
    if 0 <= new_index < len(block.scenes):
        block.scenes[scene_index], block.scenes[new_index] = block.scenes[new_index], block.scenes[scene_index]
        return True
    return False


def move_block(ep: Episode, block_index: int, direction: int) -> bool:
    new_index = block_index + direction
    if 0 <= new_index < len(ep.blocks):
        ep.blocks[block_index], ep.blocks[new_index] = ep.blocks[new_index], ep.blocks[block_index]
        return True
    return False


def delete_beat(scene: Scene, beat_index: int) -> bool:
    if len(scene.beats) <= 1:
        return False
    del scene.beats[beat_index]
    return True


def split_scene_from_beat(block: Block, scene_index: int, beat_index: int) -> bool:
    scene = block.scenes[scene_index]
    if beat_index <= 0 or beat_index >= len(scene.beats):
        return False
    new_scene_beats = scene.beats[beat_index:]
    scene.beats = scene.beats[:beat_index]
    new_scene = Scene(attrs=dict(scene.attrs), beats=new_scene_beats)
    new_scene.attrs["id"] = ""
    block.scenes.insert(scene_index + 1, new_scene)
    return True


def split_block_from_scene(ep: Episode, block_index: int, scene_index: int) -> bool:
    block = ep.blocks[block_index]
    if scene_index <= 0 or scene_index >= len(block.scenes):
        return False
    new_block_scenes = block.scenes[scene_index:]
    block.scenes = block.scenes[:scene_index]
    new_block = Block(attrs=dict(block.attrs), scenes=new_block_scenes)
    new_block.attrs["id"] = ""
    ep.blocks.insert(block_index + 1, new_block)
    return True

from dataclasses import dataclass, field
from typing import Dict, List


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

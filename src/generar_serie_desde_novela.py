import re
from pathlib import Path
import math
import uuid

EPISODES = 13
WORDS_PER_BEAT = 14

BLOCK_STRUCTURE = [
    "sun_tzu_opening",
    "plot_1",
    "interlude_1",
    "plot_2",
    "interlude_2",
    "plot_3",
    "interlude_3",
    "plot_4",
    "sun_tzu_closing"
]

def split_sentences(text):
    return re.split(r'(?<=[.!?]) +', text)

def split_words(sentence):
    words = sentence.split()
    chunks = []
    for i in range(0, len(words), WORDS_PER_BEAT):
        chunks.append(" ".join(words[i:i+WORDS_PER_BEAT]))
    return chunks

def make_id():
    return str(uuid.uuid4())[:8]

def generate_beats(text):

    beats = []

    sentences = split_sentences(text)

    for s in sentences:

        parts = split_words(s)

        for p in parts:

            beat = f'''
{{beat id="{make_id()}" shot="medium"}}
{p.strip()}
{{/beat}}
'''
            beats.append(beat)

    return beats

def split_into_episodes(text):

    words = text.split()
    size = len(words) // EPISODES

    chunks = []

    for i in range(EPISODES):
        part = words[i*size:(i+1)*size]
        chunks.append(" ".join(part))

    return chunks

def build_episode(ep_num, text):

    beats = generate_beats(text)

    beats_per_block = max(1, len(beats) // 9)

    out = []

    out.append(f'\n{{episode id="E{ep_num:02}" title="Episode {ep_num}"}}\n')

    idx = 0

    for b, block_kind in enumerate(BLOCK_STRUCTURE):

        block_id = f"E{ep_num:02}_B{b+1:02}"

        out.append(f'\n{{block id="{block_id}" kind="{block_kind}"}}\n')

        scene_id = f"{block_id}_S01"

        out.append(f'\n{{scene id="{scene_id}" location="stadium_exterior"}}\n')

        for _ in range(beats_per_block):

            if idx >= len(beats):
                break

            out.append(beats[idx])
            idx += 1

        out.append("\n{/scene}\n")
        out.append("\n{/block}\n")

    out.append("\n{/episode}\n")

    return "".join(out)

def generate_series(book_path):

    text = Path(book_path).read_text(encoding="utf8")

    episodes = split_into_episodes(text)

    out = []

    for i, ep_text in enumerate(episodes, start=1):
        out.append(build_episode(i, ep_text))

    return "\n".join(out)

if __name__ == "__main__":

    book = Path("libro.txt")

    result = generate_series(book)

    Path("libro_serie.txt").write_text(result, encoding="utf8")

    print("Serie generada → libro_serie.txt")
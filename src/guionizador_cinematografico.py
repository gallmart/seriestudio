import re
import uuid

MAX_WORDS = 15

camera_patterns = [
    "wide_establishing",
    "medium",
    "close_up",
    "reaction",
    "insert"
]

def split_sentences(text):
    return re.split(r'(?<=[.!?]) +', text)

def split_words(sentence):
    words = sentence.split()
    chunks = []
    for i in range(0, len(words), MAX_WORDS):
        chunk = " ".join(words[i:i+MAX_WORDS])
        chunks.append(chunk)
    return chunks

def detect_shot(text):
    t = text.lower()

    if "estadio" in t or "ciudad" in t:
        return "wide_establishing"

    if "miró" in t or "pensó" in t:
        return "close_up"

    if "balón" in t or "mano" in t:
        return "insert"

    return "medium"

def make_id():
    return str(uuid.uuid4())[:8]

def convert(text):

    beats = []
    sentences = split_sentences(text)

    for s in sentences:

        parts = split_words(s)

        for p in parts:

            shot = detect_shot(p)

            beat = f'''
{{beat id="{make_id()}" shot="{shot}"}}
{p.strip()}
{{/beat}}
'''
            beats.append(beat)

    return "\n".join(beats)

if __name__ == "__main__":

    with open("libro.txt","r",encoding="utf8") as f:
        text = f.read()

    beats = convert(text)

    with open("guion_cinematografico.txt","w",encoding="utf8") as f:
        f.write(beats)

    print("Guion cinematográfico generado")
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Dict, Optional, Tuple, List

from dotenv import load_dotenv
from moviepy import AudioFileClip, CompositeAudioClip, concatenate_audioclips
from moviepy.audio.AudioClip import AudioClip
from moviepy.audio.fx.AudioLoop import AudioLoop
from moviepy.audio.fx.MultiplyVolume import MultiplyVolume

import numpy as np
from moviepy.audio.AudioClip import AudioArrayClip

try:
    from elevenlabs.client import ElevenLabs
    from elevenlabs import VoiceSettings
except Exception:
    ElevenLabs = None
    VoiceSettings = None

try:
    from gtts import gTTS
except Exception:
    gTTS = None

# =========================================================
# PATHS
# =========================================================
ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
ASSETS = ROOT / "assets"
TEMP = ROOT / "temp"
TEMP.mkdir(parents=True, exist_ok=True)

load_dotenv(ROOT / ".env")

ELEVEN_API_KEY = os.getenv("ELEVEN_API_KEY", "")

VOICE_MANIFEST = json.loads((CONFIG / "voice_manifest.json").read_text(encoding="utf-8"))
AUDIO_MANIFEST = json.loads((CONFIG / "audio_manifest.json").read_text(encoding="utf-8"))


# =========================================================
# HELPERS
# =========================================================
def normalize_character_name(name: str) -> str:
    if not name:
        return ""
    aliases = VOICE_MANIFEST.get("aliases", {})
    return aliases.get(name, aliases.get(name.strip(), name)).strip()


def safe_stem(text: str, max_len: int = 32) -> str:
    text = re.sub(r"[^a-zA-Z0-9_-]+", "_", text.strip())
    return text[:max_len] or "audio"


def level_to_volume(level: str) -> float:
    mapping = {
        "off": 0.0,
        "low": 0.10,
        "medium": 0.20,
        "high": 0.35,
    }
    return mapping.get((level or "").lower(), 0.0)


def safe_duration(explicit_duration, text: str) -> float:
    if explicit_duration:
        try:
            return float(explicit_duration)
        except Exception:
            pass

    wc = len((text or "").split())
    if wc <= 5:
        return 3.0
    if wc <= 15:
        return 4.0
    if wc <= 30:
        return 6.0
    return 8.0


def make_silence_clip(duration: float, fps: int = 44100) -> AudioArrayClip:
    duration = max(0.0, float(duration))
    n_samples = max(1, int(duration * fps))
    arr = np.zeros((n_samples, 2), dtype=np.float32)  # estéreo
    return AudioArrayClip(arr, fps=fps)

def audio_cache_key(text: str, speaker: str, beat_attrs: Dict[str, str]) -> str:
    payload = {
        "text": text,
        "speaker": speaker,
        "speech_rate": beat_attrs.get("speech_rate", ""),
        "voice_intensity": beat_attrs.get("voice_intensity", ""),
        "pause_before": beat_attrs.get("pause_before", ""),
        "pause_after": beat_attrs.get("pause_after", ""),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def resolve_voice_id(character_name: str, beat_attrs: Optional[Dict[str, str]] = None, scene_attrs: Optional[Dict[str, str]] = None) -> str:
    beat_attrs = beat_attrs or {}
    scene_attrs = scene_attrs or {}

    explicit_voice_id = (beat_attrs.get("voice_id") or scene_attrs.get("voice_id") or "").strip()
    if explicit_voice_id:
        return explicit_voice_id

    canonical = normalize_character_name(character_name or "")
    voices = VOICE_MANIFEST.get("characters", {})
    defaults = VOICE_MANIFEST.get("defaults", {})
    fallback = defaults.get("fallback_voice", "")
    narrator_name = defaults.get("narrator", "Narrator")

    if not canonical:
        canonical = narrator_name

    return voices.get(canonical, fallback)


def resolve_voice_settings(character_name: str):
    settings_by_voice = VOICE_MANIFEST.get("voice_settings", {})
    canonical = normalize_character_name(character_name or "")
    data = settings_by_voice.get(canonical, settings_by_voice.get("default", {}))

    if VoiceSettings is None:
        return None

    return VoiceSettings(
        stability=float(data.get("stability", 0.45)),
        similarity_boost=float(data.get("similarity_boost", 0.75)),
        style=float(data.get("style", 0.2)),
        use_speaker_boost=bool(data.get("use_speaker_boost", True)),
    )


def resolve_music_path(music_tag: str) -> Optional[Path]:
    rel = AUDIO_MANIFEST.get("music", {}).get(music_tag, "")
    if not rel:
        return None
    p = ROOT / rel
    return p if p.exists() else None


def resolve_ambience_path(location: str) -> Optional[Path]:
    rules = AUDIO_MANIFEST.get("rules", {})
    loc_key = rules.get("location_to_ambience", {}).get(location, "")
    if not loc_key:
        return None
    rel = AUDIO_MANIFEST.get("ambience", {}).get(loc_key, "")
    if not rel:
        return None
    p = ROOT / rel
    return p if p.exists() else None

# =========================================================
# Google gTTS
# =========================================================

def guess_music_tag(beat_attrs: Dict[str, str], scene_attrs: Optional[Dict[str, str]] = None) -> str:
    if beat_attrs.get("music_tag"):
        return beat_attrs["music_tag"]

    mood = beat_attrs.get("mood") or (scene_attrs or {}).get("mood", "")
    rules = AUDIO_MANIFEST.get("rules", {})
    return rules.get("mood_to_music", {}).get(mood, "neutral_underscore")


def safe_loop(clip: AudioFileClip, duration: float):
    if clip.duration >= duration:
        return clip.subclipped(0, duration)
    return clip.with_effects([AudioLoop(duration=duration)])


def fallback_tts_audio(text: str, out_path: Path, lang: str = "es") -> Optional[Path]:
    """
    Fallback hablado con Google TTS.
    """
    if gTTS is None:
        return None

    try:
        out_path = out_path.with_suffix(".mp3")
        out_path.parent.mkdir(parents=True, exist_ok=True)

        tts = gTTS(text=text, lang=lang)
        tts.save(str(out_path))

        return out_path if out_path.exists() else None
    except Exception as e:
        print(f"[gTTS] Error generando fallback hablado: {e}")
        return None

# =========================================================
# ELEVENLABS
# =========================================================
def elevenlabs_tts(text: str, speaker: Optional[str], out_path: Path, beat_attrs: Optional[Dict[str, str]] = None) -> Optional[Path]:
    if not ELEVEN_API_KEY or ElevenLabs is None:
        return None

    beat_attrs = beat_attrs or {}
    voice_id = resolve_voice_id(speaker or "Narrator", beat_attrs=beat_attrs, scene_attrs=None)
    voice_settings = resolve_voice_settings(speaker or "Narrator")
    model_id = VOICE_MANIFEST.get("defaults", {}).get("model_id", "eleven_multilingual_v2")

    client = ElevenLabs(api_key=ELEVEN_API_KEY)

    try:
        audio = client.text_to_speech.convert(
            voice_id=voice_id,
            model_id=model_id,
            text=text,
            voice_settings=voice_settings,
        )

        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "wb") as f:
            for chunk in audio:
                if chunk:
                    f.write(chunk)

        return out_path if out_path.exists() else None
    except Exception as e:
        print(f"[ElevenLabs] Error generando audio: {e}")
        return None


# =========================================================
# FALLBACK
# =========================================================

def fallback_silent_audio(text: str, out_path: Path) -> Optional[Path]:
    """
    Genera un WAV silencioso válido como fallback.
    """
    duration = safe_duration(None, text)
    clip = make_silence_clip(duration)

    out_path = out_path.with_suffix(".wav")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    clip.write_audiofile(
        str(out_path),
        fps=44100,
        nbytes=2,
        logger=None,
    )
    clip.close()

    return out_path if out_path.exists() else None

        
# =========================================================
# AUDIO POSTPROCESS
# =========================================================

def add_leading_trailing_silence(audio_path: Path, pause_before: float, pause_after: float, out_path: Path) -> Path:
    """
    Inserta silencio antes y después del audio.
    Trabaja en WAV para evitar problemas con MP3 intermedio.
    """
    source = AudioFileClip(str(audio_path))

    clips = []
    if pause_before and pause_before > 0:
        clips.append(make_silence_clip(pause_before))
    clips.append(source)
    if pause_after and pause_after > 0:
        clips.append(make_silence_clip(pause_after))

    final = concatenate_audioclips(clips)

    out_path = out_path.with_suffix(".wav")
    final.write_audiofile(
        str(out_path),
        fps=44100,
        nbytes=2,
        logger=None,
    )

    final.close()
    source.close()

    for c in clips:
        try:
            c.close()
        except Exception:
            pass

    return out_path

def apply_pauses_to_audio(audio_path: Path, beat_id: str, beat_attrs: Dict[str, str]) -> Tuple[Path, float]:
    pause_before = float(beat_attrs.get("pause_before", "0") or 0)
    pause_after = float(beat_attrs.get("pause_after", "0") or 0)

    if pause_before <= 0 and pause_after <= 0:
        clip = AudioFileClip(str(audio_path))
        d = clip.duration
        clip.close()
        return audio_path, d

    out_path = TEMP / f"{beat_id}_padded.wav"
    out_path = add_leading_trailing_silence(audio_path, pause_before, pause_after, out_path)

    clip = AudioFileClip(str(out_path))
    d = clip.duration
    clip.close()

    return out_path, d


def mix_audio_layers(
    voice_path: Optional[Path],
    beat_id: str,
    beat_attrs: Dict[str, str],
    scene_attrs: Optional[Dict[str, str]] = None,
    target_duration: float = 0.0,
) -> Tuple[Optional[Path], float]:
    scene_attrs = scene_attrs or {}
    bgm_level = beat_attrs.get("bgm_level", "off")
    sfx_level = beat_attrs.get("sfx_level", "off")
    music_tag = guess_music_tag(beat_attrs, scene_attrs)
    location = beat_attrs.get("location") or scene_attrs.get("location", "")

    voice_clip = None
    music_clip = None
    ambience_clip = None
    layers = []

    try:
        if voice_path and voice_path.exists():
            voice_clip = AudioFileClip(str(voice_path))
            target_duration = max(target_duration, voice_clip.duration)
            layers.append(voice_clip)

        music_path = resolve_music_path(music_tag)
        ambience_path = resolve_ambience_path(location)

        music_volume = level_to_volume(bgm_level)
        ambience_volume = level_to_volume(sfx_level)

        if music_path and music_volume > 0:
            base_music = AudioFileClip(str(music_path))
            music_clip = safe_loop(base_music, target_duration).with_effects([MultiplyVolume(music_volume)])
            if voice_clip:
                music_clip = music_clip.with_effects([MultiplyVolume(0.55)])
            layers.append(music_clip)

        if ambience_path and ambience_volume > 0:
            base_amb = AudioFileClip(str(ambience_path))
            ambience_clip = safe_loop(base_amb, target_duration).with_effects([MultiplyVolume(ambience_volume)])
            layers.append(ambience_clip)

        if not layers:
            return None, target_duration

        final_mix = CompositeAudioClip(layers).with_duration(target_duration)
        out_path = TEMP / f"{beat_id}_mix.wav"
        final_mix.write_audiofile(
            str(out_path),
            fps=44100,
            nbytes=2,
            logger=None,
        )

        final_mix.close()
        for c in [voice_clip, music_clip, ambience_clip]:
            try:
                if c:
                    c.close()
            except Exception:
                pass

        mixed = AudioFileClip(str(out_path))
        final_duration = mixed.duration
        mixed.close()

        return out_path, final_duration

    except Exception as e:
        print(f"[AUDIO MIX] Error: {e}")

        for c in [voice_clip, music_clip, ambience_clip]:
            try:
                if c:
                    c.close()
            except Exception:
                pass

        if voice_path and voice_path.exists():
            clip = AudioFileClip(str(voice_path))
            d = clip.duration
            clip.close()
            return voice_path, d

        return None, target_duration





def is_valid_audio_file(path: Path, min_duration: float = 0.5) -> bool:
    if not path or not path.exists():
        return False

    try:
        clip = AudioFileClip(str(path))
        duration = float(clip.duration or 0)
        clip.close()
        return duration >= min_duration
    except Exception:
        return False


# =========================================================
# MAIN FUNCTION
# =========================================================
def generate_audio(
    text: str,
    speaker: Optional[str],
    beat_id: str,
    beat_attrs: Optional[Dict[str, str]] = None,
    scene_attrs: Optional[Dict[str, str]] = None,
) -> Tuple[Optional[Path], float]:
    """
    Genera audio con:
    1) ElevenLabs si hay key
    2) fallback silencioso WAV si no hay key o falla
    3) pausas dramáticas
    4) mezcla básica con música y ambiente
    """
    beat_attrs = beat_attrs or {}
    scene_attrs = scene_attrs or {}

    canonical_speaker = normalize_character_name(speaker or "Narrator")
    cache_key = audio_cache_key(text, canonical_speaker, beat_attrs)

    raw_path = TEMP / f"{beat_id}_{cache_key}_raw.wav"

    if raw_path.exists() and not is_valid_audio_file(raw_path):
        print(f"[AUDIO CACHE] Archivo inválido, regenerando: {raw_path}")
        raw_path.unlink(missing_ok=True)

    if not raw_path.exists():
        generated = None

        # Solo intentar ElevenLabs si hay key
        if ELEVEN_API_KEY:
            if scene_attrs.get("voice_id") and not beat_attrs.get("voice_id") and str(beat_attrs.get("lock_voice", "true")).lower() == "true":
                beat_attrs = dict(beat_attrs)
                beat_attrs["voice_id"] = scene_attrs.get("voice_id", "")
            generated = elevenlabs_tts(text, canonical_speaker, raw_path, beat_attrs=beat_attrs)

        if not generated or not is_valid_audio_file(generated):
            print(f"[AUDIO GEN] ElevenLabs no disponible o audio inválido para {beat_id}, usando gTTS")
            if generated and Path(generated).exists():
                Path(generated).unlink(missing_ok=True)

            generated = fallback_tts_audio(text, raw_path)

            if not generated or not is_valid_audio_file(generated):
                print(f"[AUDIO GEN] gTTS también falló para {beat_id}, usando silencio")
                if generated and Path(generated).exists():
                    Path(generated).unlink(missing_ok=True)
                generated = fallback_silent_audio(text, raw_path)

        raw_path = generated

    if not raw_path or not raw_path.exists() or not is_valid_audio_file(raw_path):
        return None, safe_duration(beat_attrs.get("duration"), text)

    padded_path, padded_duration = apply_pauses_to_audio(raw_path, beat_id, beat_attrs)

    mixed_path, mixed_duration = mix_audio_layers(
        voice_path=padded_path,
        beat_id=beat_id,
        beat_attrs=beat_attrs,
        scene_attrs=scene_attrs,
        target_duration=padded_duration,
    )

    if mixed_path:
        return mixed_path, mixed_duration

    return padded_path, padded_duration
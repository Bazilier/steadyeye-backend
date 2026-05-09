"""
Deterministic pseudonym generator for anonymous chat users.

Maps a UUID to a stable "Adjective Animal" pseudonym using MD5.
Same UUID always produces the same pseudonym across calls.
"""

import hashlib

ADJECTIVES = [
    "Curious", "Bold", "Quiet", "Bright", "Wise", "Calm",
    "Eager", "Gentle", "Happy", "Kind", "Lively", "Merry",
    "Nimble", "Proud", "Rapid", "Smooth", "Tender", "Vivid",
    "Witty", "Zesty", "Brave", "Cheerful", "Daring", "Elegant",
    "Friendly", "Graceful", "Honest", "Inventive", "Joyful", "Keen",
]

ANIMALS = [
    "Panda", "Otter", "Fox", "Wolf", "Bear", "Lynx",
    "Owl", "Hawk", "Eagle", "Falcon", "Raven", "Crow",
    "Dolphin", "Whale", "Seal", "Octopus", "Crab", "Turtle",
    "Tiger", "Lion", "Cheetah", "Leopard", "Jaguar", "Puma",
    "Rabbit", "Hare", "Deer", "Elk", "Moose", "Bison",
]

LANGUAGE_TO_FLAG = {
    'en-US': '🇺🇸', 'en-GB': '🇬🇧', 'en-CA': '🇨🇦', 'en-AU': '🇦🇺',
    'es-MX': '🇲🇽', 'es-ES': '🇪🇸', 'es-AR': '🇦🇷', 'es-CO': '🇨🇴',
    'es-CL': '🇨🇱', 'es-419': '🌎',
    'pt-BR': '🇧🇷', 'pt-PT': '🇵🇹',
    'ru-RU': '🇷🇺', 'fr-FR': '🇫🇷', 'de-DE': '🇩🇪', 'it-IT': '🇮🇹',
    'ja-JP': '🇯🇵', 'ko-KR': '🇰🇷', 'zh-CN': '🇨🇳', 'zh-TW': '🇹🇼',
}


def uuid_to_pseudonym(uuid: str) -> str:
    """Deterministic 'Adjective Animal' from UUID via MD5 hash."""
    if not uuid:
        return "Anonymous Soul"
    h = hashlib.md5(uuid.encode()).digest()
    adj_idx = h[0] % len(ADJECTIVES)
    animal_idx = h[1] % len(ANIMALS)
    return f"{ADJECTIVES[adj_idx]} {ANIMALS[animal_idx]}"


def language_to_flag(language: str) -> str:
    """Map language code to country emoji flag. Falls back to 🌍."""
    if not language:
        return '🌍'
    if language in LANGUAGE_TO_FLAG:
        return LANGUAGE_TO_FLAG[language]
    base = language.split('-')[0].lower()
    for lang_code, flag in LANGUAGE_TO_FLAG.items():
        if lang_code.lower().startswith(base + '-'):
            return flag
    return '🌍'

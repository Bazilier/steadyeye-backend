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

# Region (country code) → flag. Looked up FIRST in `language_to_flag`
# because the user's region tells us where they are; the language
# component is just a UI preference. Fixes the bug where 'en-AM'
# (English UI in Armenia) would resolve to 🇺🇸 via the en-* fallback.
REGION_TO_FLAG = {
    'US': '🇺🇸', 'GB': '🇬🇧', 'CA': '🇨🇦', 'AU': '🇦🇺', 'NZ': '🇳🇿',
    'IE': '🇮🇪', 'ZA': '🇿🇦', 'IN': '🇮🇳', 'SG': '🇸🇬',
    'MX': '🇲🇽', 'ES': '🇪🇸', 'AR': '🇦🇷', 'CO': '🇨🇴', 'CL': '🇨🇱',
    'PE': '🇵🇪', 'VE': '🇻🇪', 'EC': '🇪🇨', 'GT': '🇬🇹', 'CU': '🇨🇺',
    'BO': '🇧🇴', 'DO': '🇩🇴', 'HN': '🇭🇳', 'PY': '🇵🇾', 'SV': '🇸🇻',
    'NI': '🇳🇮', 'CR': '🇨🇷', 'PA': '🇵🇦', 'UY': '🇺🇾', 'PR': '🇵🇷',
    'BR': '🇧🇷', 'PT': '🇵🇹',
    'RU': '🇷🇺', 'BY': '🇧🇾', 'KZ': '🇰🇿', 'UA': '🇺🇦',
    'AM': '🇦🇲', 'GE': '🇬🇪', 'AZ': '🇦🇿',
    'FR': '🇫🇷', 'DE': '🇩🇪', 'IT': '🇮🇹', 'NL': '🇳🇱', 'BE': '🇧🇪',
    'CH': '🇨🇭', 'AT': '🇦🇹', 'PL': '🇵🇱', 'CZ': '🇨🇿', 'SK': '🇸🇰',
    'HU': '🇭🇺', 'RO': '🇷🇴', 'BG': '🇧🇬', 'GR': '🇬🇷', 'TR': '🇹🇷',
    'SE': '🇸🇪', 'NO': '🇳🇴', 'DK': '🇩🇰', 'FI': '🇫🇮', 'IS': '🇮🇸',
    'EE': '🇪🇪', 'LV': '🇱🇻', 'LT': '🇱🇹',
    'JP': '🇯🇵', 'KR': '🇰🇷', 'CN': '🇨🇳', 'TW': '🇹🇼', 'HK': '🇭🇰',
    'TH': '🇹🇭', 'VN': '🇻🇳', 'ID': '🇮🇩', 'PH': '🇵🇭', 'MY': '🇲🇾',
    'IL': '🇮🇱', 'AE': '🇦🇪', 'SA': '🇸🇦', 'EG': '🇪🇬',
}

# Animal name → matching emoji for the message header. Fallback is 🦁
# (lion) — used when an animal in `ANIMALS` doesn't have an explicit
# entry, which shouldn't happen but keeps the formatter total. Some
# animals share emojis (Eagle/Hawk/Falcon → 🦅, Cheetah/Leopard/Jaguar
# → 🐆); that's fine, they're visually similar in real life.
ANIMAL_EMOJI = {
    "Panda": "🐼", "Otter": "🦦", "Fox": "🦊", "Wolf": "🐺",
    "Bear": "🐻", "Lynx": "🐈", "Owl": "🦉", "Hawk": "🦅",
    "Eagle": "🦅", "Falcon": "🦅", "Raven": "🐦‍⬛", "Crow": "🐦‍⬛",
    "Dolphin": "🐬", "Whale": "🐳", "Seal": "🦭", "Octopus": "🐙",
    "Crab": "🦀", "Turtle": "🐢", "Tiger": "🐯", "Lion": "🦁",
    "Cheetah": "🐆", "Leopard": "🐆", "Jaguar": "🐆", "Puma": "🐈",
    "Rabbit": "🐰", "Hare": "🐰", "Deer": "🦌", "Elk": "🦌",
    "Moose": "🦌", "Bison": "🐃",
}


def uuid_to_pseudonym(uuid: str) -> str:
    """Deterministic 'Adjective Animal' from UUID via MD5 hash."""
    if not uuid:
        return "Anonymous Soul"
    h = hashlib.md5(uuid.encode()).digest()
    adj_idx = h[0] % len(ADJECTIVES)
    animal_idx = h[1] % len(ANIMALS)
    return f"{ADJECTIVES[adj_idx]} {ANIMALS[animal_idx]}"


def pseudonym_with_emoji(uuid: str) -> tuple[str, str]:
    """Returns ('Adjective Animal', emoji) — the emoji matches the animal
    so the Telegram message header reads visually consistent."""
    pseudonym = uuid_to_pseudonym(uuid)
    animal = pseudonym.split()[-1]  # last word is the animal
    emoji = ANIMAL_EMOJI.get(animal, "🦁")
    return pseudonym, emoji


def language_to_flag(language: str) -> str:
    """
    Map a locale string to a country flag emoji.

    Priority:
    1. Region (the part after the dash): `en-AM` → 🇦🇲, `ru-AM` → 🇦🇲.
       The user's *country* drives the flag; the language is a UI
       preference, not a geographic signal.
    2. Apple's `es-419` (Latin America) sentinel → 🌎 globe.
    3. Direct hit in `LANGUAGE_TO_FLAG` for legacy callers passing
       full `lang-REGION` codes we already have mapped.
    4. Language-only fallback: find any locale starting with the
       same base (`ru` → first `ru-*` entry, etc.).
    5. Globe (🌍) when nothing matches.
    """
    if not language:
        return '🌍'

    if '-' in language:
        region = language.split('-', 1)[1].upper()
        if region == '419':  # Apple's "Latin America Spanish" sentinel
            return '🌎'
        if region in REGION_TO_FLAG:
            return REGION_TO_FLAG[region]

    if language in LANGUAGE_TO_FLAG:
        return LANGUAGE_TO_FLAG[language]

    base = language.split('-')[0].lower()
    for lang_code, flag in LANGUAGE_TO_FLAG.items():
        if lang_code.lower().startswith(base + '-'):
            return flag
    return '🌍'

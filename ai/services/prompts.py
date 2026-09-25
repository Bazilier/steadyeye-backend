"""System prompts, copied byte-for-byte from the iOS bundle.

    ai/prompts/optimize.txt  <-  PromptFlow/Shared/Prompts/FormatPrompt.txt
    ai/prompts/split.txt     <-  PromptFlow/Shared/Prompts/SplitPrompt.txt

The iOS `loadPrompt` helper returned the file contents verbatim, trailing
newline included, so these are read without stripping — the bytes sent to
Anthropic must be identical or the model output shifts.
"""

from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / 'prompts'


def _load(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding='utf-8')


# Loaded once at import, mirroring the iOS `static let` prompt properties.
OPTIMIZE_SYSTEM_PROMPT = _load('optimize.txt')
SPLIT_SYSTEM_PROMPT = _load('split.txt')

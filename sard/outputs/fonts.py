"""Deterministic Arabic font discovery and opt-in download support."""

from __future__ import annotations

import hashlib
import os
import urllib.request
from pathlib import Path


FONT_FILENAME = "NotoNaskhArabic-Regular.ttf"
LATIN_FONT_FILENAME = "NotoSans-Regular.ttf"
FONT_REPOSITORY_COMMIT = "ffebf8c1ee449e544955a7e813c54f9b73848eac"
FONT_URL = (
    "https://raw.githubusercontent.com/notofonts/noto-fonts/"
    f"{FONT_REPOSITORY_COMMIT}/hinted/ttf/NotoNaskhArabic/{FONT_FILENAME}"
)
# Filled from the pinned upstream binary. Changing the URL requires reviewing
# the upstream OFL and updating this checksum intentionally.
FONT_SHA256 = "2f4b88e6ee50fa82c617e2d1d4ba18281cb1c6cd71c3af3ec64970c23995db4b"
LATIN_FONT_SHA256 = "b85c38ecea8a7cfb39c24e395a4007474fa5a4fc864f6ee33309eb4948d232d5"
DEFAULT_FONT_PATH = Path(__file__).with_name("assets") / FONT_FILENAME
DEFAULT_LATIN_FONT_PATH = Path(__file__).with_name("assets") / LATIN_FONT_FILENAME


class ArabicFontError(RuntimeError):
    pass


def configured_font_path() -> Path:
    return Path(os.environ.get("SARD_ARABIC_FONT_PATH", DEFAULT_FONT_PATH)).expanduser()


def require_arabic_font(path: Path | None = None) -> Path:
    candidate = (path or configured_font_path()).resolve()
    if not candidate.is_file():
        raise ArabicFontError(
            f"Arabic font not found at {candidate}. Run "
            "`uv run python -m sard.outputs.sample --download-font` or set "
            "SARD_ARABIC_FONT_PATH to a Noto Naskh Arabic Regular TTF file. "
            "PDF rendering never falls back silently."
        )
    if path is None and "SARD_ARABIC_FONT_PATH" not in os.environ:
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if digest != FONT_SHA256:
            raise ArabicFontError(
                f"Bundled Arabic font checksum mismatch: expected {FONT_SHA256}, got {digest}."
            )
    return candidate


def require_latin_font() -> Path:
    """Require the pinned companion font used for readable mixed-script runs."""

    candidate = DEFAULT_LATIN_FONT_PATH.resolve()
    if not candidate.is_file():
        raise ArabicFontError(f"Bundled Latin companion font not found at {candidate}.")
    digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
    if digest != LATIN_FONT_SHA256:
        raise ArabicFontError(
            f"Bundled Latin font checksum mismatch: expected {LATIN_FONT_SHA256}, got {digest}."
        )
    return candidate


def download_pinned_font(destination: Path | None = None) -> Path:
    """Download the pinned SIL OFL 1.1 font and verify its exact checksum."""

    target = (destination or DEFAULT_FONT_PATH).resolve()
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == FONT_SHA256:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".download")
    try:
        urllib.request.urlretrieve(FONT_URL, temporary)
        digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
        if digest != FONT_SHA256:
            raise ArabicFontError(
                f"Downloaded font checksum mismatch: expected {FONT_SHA256}, got {digest}."
            )
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target

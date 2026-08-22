import re


_ERROR_CODE_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{0,127}")


def normalize_transcription_error_code(error_code: str) -> str:
    """Valide un identifiant stable pouvant être persisté sans message brut."""
    if not isinstance(error_code, str):
        raise ValueError("error_code doit être une chaîne canonique non sensible")

    normalized = error_code.strip()
    if _ERROR_CODE_PATTERN.fullmatch(normalized) is None:
        raise ValueError(
            "error_code doit contenir au plus 128 caractères parmi A-Z, 0-9 et _"
        )
    return normalized

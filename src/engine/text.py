"""Placeholder substitution for data-driven text."""


class _KeepMissing(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def fill(template: str, variables: dict[str, str]) -> str:
    """Replace {placeholders}; unknown placeholders are left untouched instead of raising.
    Text whose braces are not placeholders at all (e.g. generated tables) is returned as-is."""
    try:
        return template.format_map(_KeepMissing(variables))
    except (ValueError, IndexError, AttributeError):
        return template

TRANSFORMER_VERSION = "uppercase-v1"


def transform(value: str) -> str:
    """Simulate a deterministic external service."""
    return value.upper()


def interleave(first: list[str], second: list[str]) -> str:
    return ", ".join(
        value for pair in zip(first, second, strict=True) for value in pair
    )

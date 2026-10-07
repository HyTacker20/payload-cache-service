import pytest
from pydantic import ValidationError

from cache_service.generation import interleave, transform
from cache_service.schemas import PayloadInput


def test_sample_output() -> None:
    request = PayloadInput(
        list_1=["first string", "second string", "third string"],
        list_2=["other string", "another string", "last string"],
    )
    first = [transform(value) for value in request.list_1]
    second = [transform(value) for value in request.list_2]

    assert interleave(first, second) == (
        "FIRST STRING, OTHER STRING, SECOND STRING, ANOTHER STRING, "
        "THIRD STRING, LAST STRING"
    )


def test_preserves_order_whitespace_and_empty_strings() -> None:
    assert interleave([" A ", ""], ["B, C", "D"]) == " A , B, C, , D"


def test_empty_lists_produce_empty_output() -> None:
    request = PayloadInput(list_1=[], list_2=[])
    assert interleave(request.list_1, request.list_2) == ""


def test_unicode_transformation() -> None:
    assert transform("привіт straße") == "ПРИВІТ STRASSE"


@pytest.mark.parametrize(
    "data",
    [
        {"list_1": ["a"], "list_2": []},
        {"list_1": [1], "list_2": ["a"]},
        {"list_1": [None], "list_2": ["a"]},
        {"list_1": "abc", "list_2": ["a"]},
        {"list_1": [], "list_2": [], "extra": True},
        {"list_1": []},
        {"list_1": ["\ud800"], "list_2": ["a"]},
    ],
)
def test_rejects_invalid_input(data: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PayloadInput.model_validate(data)


def test_interleave_does_not_silently_truncate() -> None:
    with pytest.raises(ValueError):
        interleave(["A"], [])

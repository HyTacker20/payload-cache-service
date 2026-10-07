from typing import Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class PayloadInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    list_1: list[str]
    list_2: list[str]

    @field_validator("list_1", "list_2")
    @classmethod
    def require_utf8(cls, values: list[str]) -> list[str]:
        try:
            for value in values:
                value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise ValueError("strings must contain valid Unicode") from error
        return values

    @model_validator(mode="after")
    def require_equal_lengths(self) -> Self:
        if len(self.list_1) != len(self.list_2):
            raise ValueError("list_1 and list_2 must have the same length")
        return self


class PayloadOutput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    output: str


class PayloadConfirmation(BaseModel):
    id: str
    created: bool
    message: str

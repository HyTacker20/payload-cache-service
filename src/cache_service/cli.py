import json
import sys
from collections.abc import Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import Self

import httpx
from pydantic import Field, HttpUrl, ValidationError, model_validator
from pydantic_settings import (
    BaseSettings,
    CliApp,
    CliSettingsSource,
    SettingsConfigDict,
    SettingsError,
)

from cache_service.schemas import PayloadConfirmation, PayloadInput, PayloadOutput


class CliSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CACHE_CLI_",
        env_prefix_target="all",
        cli_prog_name="cache-cli",
        cli_hide_none_type=True,
        cli_shortcuts={
            "host": "H",
            "repeat": "r",
            "input": "i",
            "json": "j",
            "output": "o",
        },
    )

    host: HttpUrl = Field(
        default=HttpUrl("http://127.0.0.1:8000"), description="Server URL"
    )
    repeat: int = Field(default=1, gt=0, description="Number of POST/GET iterations")
    input_file: str | None = Field(
        default=None, validation_alias="input", description="JSON file, or - for stdin"
    )
    json_input: str | None = Field(
        default=None, validation_alias="json", description="Inline request JSON"
    )
    output_file: str = Field(
        default="-",
        validation_alias="output",
        description="JSON Lines file, or - for stdout",
    )

    @model_validator(mode="after")
    def validate_options(self) -> Self:
        if self.input_file is not None and self.json_input is not None:
            raise ValueError("--input and --json are mutually exclusive")
        if (
            self.host.query
            or self.host.fragment
            or self.host.username
            or self.host.password
        ):
            raise ValueError("--host must not contain query, fragment, or credentials")
        return self


def read_input(options: CliSettings) -> PayloadInput:
    if options.json_input is not None:
        content = options.json_input
    elif options.input_file is None or options.input_file == "-":
        binary = getattr(sys.stdin, "buffer", None)
        content = (
            sys.stdin.read() if binary is None else binary.read().decode("utf-8-sig")
        )
    else:
        content = Path(options.input_file).read_text(encoding="utf-8-sig")
    return PayloadInput.model_validate_json(content)


def exercise_service(options: CliSettings, payload: PayloadInput) -> None:
    with ExitStack() as stack:
        output = (
            sys.stdout
            if options.output_file == "-"
            else stack.enter_context(
                Path(options.output_file).open("w", encoding="utf-8", newline="\n")
            )
        )
        client = stack.enter_context(
            httpx.Client(
                base_url=str(options.host).rstrip("/") + "/",
                timeout=30,
            )
        )
        previous: tuple[str, str] | None = None
        for _ in range(options.repeat):
            created = client.post("payload", json=payload.model_dump())
            created.raise_for_status()
            confirmation = PayloadConfirmation.model_validate_json(created.content)
            read = client.get(f"payload/{confirmation.id}")
            read.raise_for_status()
            result = PayloadOutput.model_validate_json(read.content)
            current = (confirmation.id, result.output)
            if previous is not None and current != previous:
                raise ValueError("server returned inconsistent payloads across repeats")
            previous = current
            record = {
                "id": confirmation.id,
                "created": confirmation.created,
                "output": result.output,
            }
            output.write(json.dumps(record, ensure_ascii=True) + "\n")


def main(argv: Sequence[str] | None = None) -> int:
    try:
        source = CliSettingsSource[CliSettings](
            CliSettings, case_sensitive=True, cli_exit_on_error=False
        )
        # These options are raw strings: "null" is JSON or a valid filename.
        # NUL cannot occur in OS arguments; set it after help defaults are built.
        source.cli_parse_none_str = "\0"
        options = CliApp.run(
            CliSettings,
            cli_args=None if argv is None else list(argv),
            cli_settings_source=source,
            cli_exit_on_error=False,
        )
        payload = read_input(options)
    except (SettingsError, ValidationError, OSError, UnicodeError) as error:
        print(f"cache-cli: {error}", file=sys.stderr)
        return 2

    try:
        exercise_service(options, payload)
    except (httpx.HTTPError, ValueError, OSError) as error:
        print(f"cache-cli: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from cache_service.schemas import PayloadOutput


class PayloadFiles:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def materialize(self, payload_id: str, output: str) -> PayloadOutput:
        payload = PayloadOutput(output=output)
        content = payload.model_dump_json() + "\n"
        path = self.directory / f"{payload_id}.json"
        try:
            if path.read_text(encoding="utf-8") == content:
                return payload
        except (FileNotFoundError, UnicodeDecodeError):
            pass
        self._atomic_write(path, content)
        return payload

    def _atomic_write(self, destination: Path, content: str) -> None:
        temporary: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=self.directory,
                prefix=".payload-",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

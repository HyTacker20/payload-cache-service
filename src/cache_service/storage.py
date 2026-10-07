import os
import time
from pathlib import Path
from tempfile import NamedTemporaryFile

from cache_service.schemas import PayloadOutput

REPLACE_ATTEMPTS = 10
RETRY_DELAY_SECONDS = 0.01


class PayloadFiles:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def materialize(self, payload_id: str, output: str) -> PayloadOutput:
        payload = PayloadOutput(output=output)
        content = payload.model_dump_json() + "\n"
        path = self.directory / f"{payload_id}.json"
        if not self._matches(path, content):
            self._atomic_write(path, content)
        return payload

    def _matches(self, path: Path, content: str) -> bool:
        try:
            return path.read_text(encoding="utf-8") == content
        except (FileNotFoundError, UnicodeDecodeError, PermissionError):
            # A Windows replacement can briefly deny opening the destination.
            # A genuine storage permission failure still fails the atomic write.
            return False

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
            self._replace(temporary, destination, content)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _replace(self, temporary: Path, destination: Path, content: str) -> None:
        for attempt in range(REPLACE_ATTEMPTS):
            try:
                os.replace(temporary, destination)
                return
            except PermissionError as error:
                # Windows readers temporarily deny replacement. Another repair
                # may already have produced the same file, including in a process.
                if getattr(error, "winerror", None) not in (5, 32, 33):
                    raise
                if self._matches(destination, content):
                    return
                if attempt == REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(RETRY_DELAY_SECONDS)

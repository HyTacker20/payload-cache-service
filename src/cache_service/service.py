from collections.abc import Callable
from hashlib import sha256

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from cache_service.database import Payload, Transformation
from cache_service.generation import TRANSFORMER_VERSION, interleave, transform
from cache_service.schemas import PayloadConfirmation, PayloadInput, PayloadOutput
from cache_service.storage import PayloadFiles


class TransformationError(Exception):
    """The simulated external service could not produce a result."""


class PayloadService:
    def __init__(
        self,
        engine: Engine,
        files: PayloadFiles,
        transformer: Callable[[str], str] = transform,
        *,
        version: str = TRANSFORMER_VERSION,
    ) -> None:
        self.engine = engine
        self.files = files
        self.transformer = transformer
        self.version = version

    def create(self, request: PayloadInput) -> PayloadConfirmation:
        with Session(self.engine) as session, session.begin():
            # Reserve SQLite's writer before reading: a unique key alone cannot
            # prevent two callers from both invoking an expensive transformer.
            session.execute(text("BEGIN IMMEDIATE"))
            cached = self._transform_missing(session, request)
            output = interleave(
                [cached[value] for value in request.list_1],
                [cached[value] for value in request.list_2],
            )
            payload_id = sha256(output.encode("utf-8")).hexdigest()
            existing = session.get(Payload, payload_id)
            created = existing is None
            if existing is not None and existing.output != output:
                raise RuntimeError("payload hash collision")
            if created:
                session.add(Payload(id=payload_id, output=output))
            # Files are derived data. An interrupted DB commit can leave an
            # orphan file, but GET only serves IDs present in committed rows.
            self.files.materialize(payload_id, output)
        return PayloadConfirmation(
            id=payload_id,
            created=created,
            message="Payload created" if created else "Payload already exists",
        )

    def _transform_missing(
        self, session: Session, request: PayloadInput
    ) -> dict[str, str]:
        values = list(dict.fromkeys(request.list_1 + request.list_2))
        cached = self._read_cached(session, values)
        for value in values:
            if value in cached:
                continue
            try:
                transformed = self.transformer(value)
            except Exception as error:
                raise TransformationError("transformer unavailable") from error
            session.add(
                Transformation(
                    version=self.version, original=value, transformed=transformed
                )
            )
            cached[value] = transformed
        return cached

    def _read_cached(self, session: Session, values: list[str]) -> dict[str, str]:
        cached: dict[str, str] = {}
        # Stay below SQLite's bind-parameter limit even with large requests.
        for offset in range(0, len(values), 500):
            rows = session.scalars(
                select(Transformation).where(
                    Transformation.version == self.version,
                    Transformation.original.in_(values[offset : offset + 500]),
                )
            )
            cached.update((row.original, row.transformed) for row in rows)
        return cached

    def read(self, payload_id: str) -> PayloadOutput:
        with Session(self.engine) as session:
            payload = session.get(Payload, payload_id)
            if payload is None:
                raise LookupError("payload not found")
            output = payload.output
        return self.files.materialize(payload_id, output)

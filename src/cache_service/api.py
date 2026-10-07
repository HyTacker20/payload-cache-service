import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Annotated, cast

from fastapi import Depends, FastAPI, HTTPException, Path, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from cache_service.database import create_database
from cache_service.generation import transform
from cache_service.schemas import PayloadConfirmation, PayloadInput, PayloadOutput
from cache_service.service import PayloadService, TransformationError
from cache_service.settings import Settings
from cache_service.storage import PayloadFiles

logger = logging.getLogger(__name__)
PayloadId = Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")]


def get_service(request: Request) -> PayloadService:
    return cast(PayloadService, request.app.state.service)


Service = Annotated[PayloadService, Depends(get_service)]


def create_app(
    settings: Settings | None = None,
    *,
    transformer: Callable[[str], str] = transform,
) -> FastAPI:
    configuration = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_database(configuration.data_dir)
        try:
            files = PayloadFiles(configuration.data_dir / "payloads")
            app.state.service = PayloadService(engine, files, transformer)
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="Cache Service", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request, error: RequestValidationError
    ) -> Response:
        # Arbitrary inputs can contain binary data, non-finite numbers, or invalid
        # Unicode. Return useful error locations without reflecting those values.
        details = [
            {key: detail[key] for key in ("type", "loc", "msg")}
            for detail in error.errors()
        ]
        content = json.dumps({"detail": details}, ensure_ascii=True, allow_nan=False)
        return Response(content, status_code=422, media_type="application/json")

    @app.exception_handler(TransformationError)
    async def transformer_error(
        request: Request, error: TransformationError
    ) -> JSONResponse:
        logger.error("Payload transformation failed", exc_info=error)
        return JSONResponse(
            status_code=502, content={"detail": "Transformer unavailable"}
        )

    @app.exception_handler(SQLAlchemyError)
    @app.exception_handler(OSError)
    async def storage_error(request: Request, error: Exception) -> JSONResponse:
        logger.error("Payload storage failed", exc_info=error)
        return JSONResponse(
            status_code=503,
            content={"detail": "Storage unavailable"},
            headers={"Retry-After": "1"},
        )

    @app.post("/payload", response_model=PayloadConfirmation, status_code=201)
    def create_payload(
        request: PayloadInput, response: Response, service: Service
    ) -> PayloadConfirmation:
        result = service.create(request)
        response.status_code = 201 if result.created else 200
        response.headers["Location"] = f"/payload/{result.id}"
        return result

    @app.get("/payload/{payload_id}", response_model=PayloadOutput)
    def read_payload(payload_id: PayloadId, service: Service) -> PayloadOutput:
        try:
            return service.read(payload_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail="Payload not found") from error

    return app

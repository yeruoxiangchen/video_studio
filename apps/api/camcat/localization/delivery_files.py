"""Use Starlette file delivery and release its work directory even on disconnect."""

from pathlib import Path
from tempfile import TemporaryDirectory

from starlette.responses import FileResponse
from starlette.types import Receive, Scope, Send


class DeliveryFileResponse(FileResponse):
    media_type = "application/zip"

    def __init__(
        self,
        *,
        temporary: TemporaryDirectory[str],
        path: Path,
        filename: str,
        status_code: int = 200,
    ) -> None:
        super().__init__(
            path,
            media_type="application/zip",
            filename=filename,
            headers={"Cache-Control": "no-store"},
            status_code=status_code,
        )
        self.temporary = temporary

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.temporary.cleanup()

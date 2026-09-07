"""Loopback configuration boundary; browser-origin requests are forbidden."""

from typing import Annotated, Any, cast

from fastapi import APIRouter, Body, HTTPException, Request

from quant_engine.configuration import ConfigurationRequest, Platform
from quant_engine.paths import AppPaths
from quant_engine.storage.configuration_repository import execute_configuration

router = APIRouter()


@router.post("/api/workspaces/{platform}/configuration")
def configuration(
    platform: Platform,
    command: Annotated[ConfigurationRequest, Body(discriminator="operation")],
    request: Request,
) -> dict[str, Any]:
    # Main-process fetch has no Origin. Remote pages never receive this capability.
    if (
        request.headers.get("origin") is not None
        or request.headers.get("sec-fetch-site") is not None
    ):
        raise HTTPException(403, "Browser requests are not permitted")
    if platform != command.platform:
        raise HTTPException(422, "Platform mismatch")
    paths = cast(AppPaths, request.app.state.paths)
    try:
        return execute_configuration(paths.database_file, command)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error

"""Reusable OpenAPI response snippets for route decorators."""

ERR_401 = {401: {"description": "Invalid or missing API key"}}
ERR_404 = {404: {"description": "Not found"}}
ERR_400 = {400: {"description": "Bad request"}}
ERR_409 = {409: {"description": "Conflict"}}


def ok(example: dict) -> dict:
    """Wrap a 200 example in the FastAPI responses format."""
    return {200: {"content": {"application/json": {"example": example}}}}


def ok201(example: dict) -> dict:
    """Wrap a 201 example in the FastAPI responses format."""
    return {201: {"content": {"application/json": {"example": example}}}}


def ok202(example: dict) -> dict:
    """Wrap a 202 example in the FastAPI responses format."""
    return {202: {"content": {"application/json": {"example": example}}}}

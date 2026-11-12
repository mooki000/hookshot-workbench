"""Stable failure envelopes, without raw exception messages or local paths."""
from fastmcp.exceptions import ValidationError as FrameworkValidationError, NotFoundError
from fastmcp.server.middleware import Middleware, MiddlewareContext
from pydantic import ValidationError
from src.errors import WorkbenchError, fail


class ErrorMiddleware(Middleware):
    def __init__(self, deps):
        self.deps = deps

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        operation = context.message.name
        try:
            if operation not in self.deps.selected:
                raise fail("not_found", "tool_call")
            return await call_next(context)
        except WorkbenchError:
            raise
        except (FrameworkValidationError, ValidationError):
            raise fail("invalid_argument", operation) from None
        except NotFoundError:
            raise fail("not_found", operation) from None
        except Exception:
            # Framework may already have wrapped a backend exception. Never parse its text.
            raise fail("backend_failed", operation) from None

    async def on_read_resource(self, context: MiddlewareContext, call_next):
        try:
            return await call_next(context)
        except WorkbenchError:
            raise
        except (FrameworkValidationError, ValidationError, ValueError):
            raise fail("invalid_argument", "resource_read") from None
        except NotFoundError:
            raise fail("not_found", "resource_read") from None
        except Exception:
            raise fail("backend_failed", "resource_read") from None

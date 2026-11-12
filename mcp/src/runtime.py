"""CLI operations use the same real registration, validation and error boundary as MCP."""
import asyncio
from fastmcp import Client
from fastmcp.resources.template import match_uri_template
from src.config import Config
from src.deps import Deps
from src.errors import WorkbenchError, fail
from src.main import build_server
from src.models import ErrorEnvelope
from src.policy import Safety, ToolSpec
from src.tools.registry import configure


def tool_spec(config: Config, name: str) -> ToolSpec:
    try:
        deps = Deps(config)
        configure(deps)
    except ValueError:
        raise fail("invalid_argument", "cli_configuration") from None
    if name not in deps.selected:
        raise fail("not_found", "tool_call")
    return deps.specs[name]


def error_reply(exc: Exception, operation: str = "cli") -> dict:
    if isinstance(exc, WorkbenchError):
        return {"error": exc.envelope.model_dump()}
    return {"error": fail("backend_failed", operation).envelope.model_dump()}


async def execute(client: Client, action: str, *, name: str | None = None, arguments: dict | None = None) -> dict:
    try:
        if action == "call":
            result = await client.call_tool(name, arguments or {}, raise_on_error=False)
            if result.is_error:
                try:
                    envelope = ErrorEnvelope.model_validate_json(result.content[0].text)
                    return {"error": envelope.model_dump()}
                except (ValueError, AttributeError, IndexError):
                    return error_reply(fail("backend_failed", name or "tool_call"))
            if result.structured_content is None:
                return error_reply(fail("backend_failed", name or "tool_call"))
            return {"result": result.structured_content}
        if action == "read":
            resources = await client.list_resources()
            templates = await client.list_resource_templates()
            if not isinstance(name, str) or not (any(str(item.uri) == name for item in resources) or
                    any(match_uri_template(name, item.uri_template) is not None for item in templates)):
                return error_reply(fail("not_found", "resource_read"))
            contents = await client.read_resource(name)
            return {"result": {"contents": [item.model_dump(mode="json", by_alias=True) for item in contents]}}
        if action == "prompt":
            prompts = {item.name: item for item in await client.list_prompts()}
            if name not in prompts:
                return error_reply(fail("not_found", "prompt_read"))
            declared = {arg.name: arg for arg in prompts[name].arguments or []}
            supplied = arguments or {}
            if (supplied.keys() - declared.keys() or any(arg.required and key not in supplied for key, arg in declared.items())
                    or any(not isinstance(value, str) for value in supplied.values())):
                return error_reply(fail("invalid_argument", name))
            result = await client.get_prompt(name, arguments or {})
            return {"result": result.model_dump(mode="json", by_alias=True)}
        return error_reply(fail("invalid_argument", "cli"))
    except WorkbenchError as exc:
        return error_reply(exc)
    except Exception as exc:
        # Resource/prompt client errors may carry an already-sanitized envelope.
        try:
            envelope = ErrorEnvelope.model_validate_json(str(exc))
            return {"error": envelope.model_dump()}
        except ValueError:
            return error_reply(exc)


async def one_shot(config: Config, action: str, *, name: str | None = None,
                   arguments: dict | None = None, timeout: float = 60) -> dict:
    """Run observations without a broker. Owned work must use the persistent route."""
    try:
        if not 0 < timeout <= 3600:
            raise fail("invalid_argument", "cli")
        deps = Deps(config)
        server = build_server(deps=deps)
        if action == "call":
            if name not in deps.selected:
                raise fail("not_found", "tool_call")
            if deps.specs[name].requires_broker:
                raise fail("refused_by_policy", "cli_ownership")
        async with Client(server) as client:
            async with asyncio.timeout(timeout):
                if action == "list":
                    tools = await client.list_tools()
                    return {"result": [{"name": tool.name, "group": deps.groups[tool.name],
                        "safety": deps.specs[tool.name].safety.value,
                        "profiles": ["observe", "operate"] if deps.specs[tool.name].safety == Safety.READ else ["operate"],
                        "requires_broker": deps.specs[tool.name].requires_broker,
                        "shell_route": deps.specs[tool.name].shell_route,
                        "description": tool.description, "input_schema": tool.input_schema}
                        for tool in sorted(tools, key=lambda item: item.name)]}
                return await execute(client, action, name=name, arguments=arguments)
    except TimeoutError:
        return error_reply(fail("timeout", "cli", retryable=True))
    except ValueError:
        return error_reply(fail("invalid_argument", "cli_configuration"))
    except Exception as exc:
        return error_reply(exc)

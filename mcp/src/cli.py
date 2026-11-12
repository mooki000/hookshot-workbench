"""Shell access to the registered MCP surface, with broker-owned long work."""
import argparse
import asyncio
from dataclasses import replace
import json
import logging
import math
import sys
from typing import Sequence

from src.config import Config
from src.errors import WorkbenchError, fail


BROKER_STARTED = "hookshot-wb: broker started"


class Parser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, allow_abbrev=False, **kwargs)

    def error(self, message):
        # argparse normally includes the caller's arguments in diagnostics.
        raise fail("invalid_argument", "cli")


def positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError
        return seconds
    except ValueError:
        raise argparse.ArgumentTypeError("Expected positive finite seconds.") from None


def inherited_fd(value: str) -> int:
    try:
        fd = int(value)
        if fd < 0:
            raise ValueError
        return fd
    except ValueError:
        raise argparse.ArgumentTypeError("Expected an inherited descriptor.") from None


def parser() -> Parser:
    def common(target):
        # Suppression lets flags appear before or after a subcommand without
        # an omitted child option overwriting an explicit parent option.
        target.add_argument("--profile", choices=("observe", "operate"), default=argparse.SUPPRESS,
                            help="Select the registered MCP profile.")
        target.add_argument("--timeout", type=positive_seconds, default=argparse.SUPPRESS,
                            help="Bound the RPC wait in seconds (default: 60); never replay it.")

    root = Parser(prog="hookshot-wb", description=__doc__)
    common(root)
    root.set_defaults(profile=None, timeout=60.0)
    commands = root.add_subparsers(dest="command", required=True)

    listing = commands.add_parser("list", help="List the actual registered tools.")
    common(listing)
    listing.add_argument("--json", action="store_true", help="Return full catalogue rows as JSON.")

    call = commands.add_parser("call", help="Call one registered tool; return its result without waiting for a Job.")
    common(call)
    call.add_argument("tool")
    call.add_argument("--args", default="{}", help="A JSON object matching the advertised input schema.")

    resource = commands.add_parser("read", help="Read one registered resource URI, never an arbitrary file.")
    common(resource)
    resource.add_argument("uri")

    prompt = commands.add_parser("prompt", help="Render one registered prompt, without executing its plan.")
    common(prompt)
    prompt.add_argument("name")
    prompt.add_argument("--args", default="{}", help="A JSON object of prompt arguments.")

    broker = commands.add_parser("broker", help="Inspect or stop this state root's broker; never autostart it.")
    common(broker)
    actions = broker.add_subparsers(dest="action", required=True)
    for name in ("status", "stop"):
        common(actions.add_parser(name))

    serve = commands.add_parser("serve", help="Run the broker in the foreground.")
    common(serve)
    serve.add_argument("--idle-seconds", type=positive_seconds,
                       help="Exit after this idle interval, never ending an active owned session.")
    serve.add_argument("--lifetime-seconds", type=positive_seconds,
                       help="Stop owned work and exit at this lifetime deadline.")
    serve.add_argument("--lock-fd", type=inherited_fd, help=argparse.SUPPRESS)
    serve.add_argument("--config-fd", type=inherited_fd, help=argparse.SUPPRESS)
    return root


def arguments_json(value: str) -> dict:
    def reject_constant(_):
        raise ValueError

    def finite_float(text):
        number = float(text)
        if not math.isfinite(number):
            raise ValueError
        return number

    try:
        arguments = json.loads(value, parse_constant=reject_constant, parse_float=finite_float)
        if not isinstance(arguments, dict):
            raise ValueError
        return arguments
    except (ValueError, RecursionError):
        raise fail("invalid_argument", "cli") from None


def configuration(options) -> Config:
    try:
        if options.command == "serve" and options.config_fd is not None:
            from src.broker import config_from_fd
            config = config_from_fd(options.config_fd)
        else:
            config = Config.from_env()
        return replace(config, profile=options.profile) if options.profile is not None else config
    except WorkbenchError:
        raise
    except (OSError, ValueError, TypeError):
        raise fail("invalid_argument", "configuration") from None


async def dispatch(config: Config, options, arguments: dict | None) -> dict | None:
    from src import broker, runtime

    if options.command == "serve":
        await broker.serve(config, idle_seconds=options.idle_seconds,
                           lifetime_seconds=options.lifetime_seconds, lock_fd=options.lock_fd)
        return None
    if options.command == "broker":
        return await broker.request(config, options.action, profile=config.profile,
                                    auto_start=False, timeout=options.timeout)
    if options.command == "call":
        spec = runtime.tool_spec(config, options.tool)
        if spec.requires_broker:
            return await broker.request(config, "call", tool=options.tool, arguments=arguments,
                                        profile=config.profile, auto_start=True, timeout=options.timeout)
        return await runtime.one_shot(config, "call", name=options.tool,
                                      arguments=arguments, timeout=options.timeout)
    if options.command == "read":
        return await runtime.one_shot(config, "read", name=options.uri, timeout=options.timeout)
    if options.command == "prompt":
        return await runtime.one_shot(config, "prompt", name=options.name,
                                      arguments=arguments, timeout=options.timeout)
    return await runtime.one_shot(config, "list", timeout=options.timeout)


def table(rows: list[dict]) -> str:
    columns = [("tool", "group", "safety", "profile", "broker", "replaces")]
    columns.extend((row["name"], row["group"], row["safety"], ",".join(row["profiles"]),
                    "yes" if row["requires_broker"] else "no", row["shell_route"]) for row in rows)
    widths = [max(len(row[index]) for row in columns) for index in range(5)]
    return "\n".join("  ".join(value.ljust(widths[index]) if index < 5 else value
                                for index, value in enumerate(row)) for row in columns)


def emit_json(value, *, error=False):
    print(json.dumps(value, ensure_ascii=True, allow_nan=False), file=sys.stderr if error else sys.stdout)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s", force=True)
    # Framework logs can include raw validation values and exception paths.
    # Shell diagnostics are only the fixed envelope and explicit start notice.
    logging.disable(logging.CRITICAL)
    try:
        options = parser().parse_args(argv)
        arguments = arguments_json(options.args) if options.command in {"call", "prompt"} else None
        config = configuration(options)
        reply = asyncio.run(dispatch(config, options, arguments))
        if options.command == "serve":
            return 0
        if reply.get("started"):
            print(BROKER_STARTED, file=sys.stderr)
        if "error" in reply:
            emit_json(reply["error"], error=True)
            return 1
        if options.command == "list" and not options.json:
            print(table(reply["result"]))
        else:
            emit_json(reply["result"])
        return 0
    except WorkbenchError as exc:
        emit_json(exc.envelope.model_dump(), error=True)
        return 1
    except KeyboardInterrupt:
        emit_json(fail("uncertain_outcome", "cli").envelope.model_dump(), error=True)
        return 130
    except Exception:
        # Backend and configuration details must never become shell diagnostics.
        emit_json(fail("backend_failed", "cli").envelope.model_dump(), error=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Whole-stdout JSON, global argv normalization and safe exception boundary."""

from __future__ import annotations

import asyncio
import io
import json
import sys
import tomllib
from collections.abc import Sequence
from contextlib import redirect_stdout
from typing import Any

import typer
from pydantic import ValidationError
from pydantic_settings import SettingsError
from typer import _click as click
from typer._click.exceptions import UsageError
from typer.core import TyperGroup
from typer.exceptions import Abort

from aimedia.application.single_image import ImageHistoryError
from aimedia.domain.errors import DomainError, ProviderError
from aimedia.logging import finish_active, redact
from aimedia.registry.errors import RegistryError
from aimedia.storage.errors import StorageError

GLOBAL_VALUES = {"--provider", "--config", "--data-dir", "--log-level"}
GLOBAL_FLAGS = {"--json", "--quiet", "--verbose", "--no-color"}
VALUE_OPTIONS = GLOBAL_VALUES | {
    "--prompt",
    "--prompt-file",
    "--image",
    "--model",
    "--resolution",
    "--aspect-ratio",
    "--quality",
    "--format",
    "--max-images",
    "--seed",
    "--out",
    "--name",
    "--concurrency",
    "--limit",
    "--status",
    "--wait-timeout",
    "--poll-interval",
    "--file",
}


def normalize_args(args: Sequence[str]) -> tuple[list[str], list[tuple[str, str]]]:
    globals_: list[str] = []
    rest: list[str] = []
    sources: list[tuple[str, str]] = []
    values: dict[str, str] = {}
    i = 0
    while i < len(args):
        token = args[i]
        if token == "--":
            rest.extend(args[i:])
            break
        key, sep, inline = token.partition("=")
        if key in VALUE_OPTIONS:
            if sep:
                value = inline
                pair = [token]
            else:
                if i + 1 >= len(args):
                    raise UsageError("Значение опции отсутствует")
                i += 1
                value = args[i]
                pair = [token, value]
            if key in GLOBAL_VALUES:
                if key in values and values[key] != value:
                    raise UsageError("Конфликт глобальных опций")
                if key not in values:
                    globals_.extend(pair)
                    values[key] = value
            else:
                rest.extend(pair)
            if key in {"--prompt", "--prompt-file"}:
                sources.append((key, value))
        elif token in GLOBAL_FLAGS:
            if token not in globals_:
                globals_.append(token)
        else:
            rest.append(token)
        i += 1
    return globals_ + rest, sources


def _json_requested(args: Sequence[str]) -> bool:
    i = 0
    while i < len(args):
        token = args[i]
        if token == "--":
            break
        if token == "--json":
            return True
        key, separator, _ = token.partition("=")
        if key in VALUE_OPTIONS and not separator:
            i += 1
        i += 1
    return False


def emit(
    data: object = None,
    *,
    as_json: bool = False,
    ok: bool = True,
    error: dict[str, object] | None = None,
) -> None:
    payload: dict[str, object] = {"ok": ok}
    if data is not None:
        payload["data"] = data
    if error is not None:
        payload["error"] = error
    if as_json:
        typer.echo(json.dumps(redact(payload), ensure_ascii=False, default=str))
    elif error is not None:
        typer.echo(str(error["message"]), err=True)
        if data is not None:
            typer.echo(json.dumps(redact(data), ensure_ascii=False, indent=2, default=str))
    elif isinstance(data, str):
        typer.echo(data)
    else:
        typer.echo(json.dumps(redact(data), ensure_ascii=False, indent=2, default=str))


def job_exit(code: str) -> int:
    if code == "REMOTE_GENERATION_FAILED":
        return 5
    if code in {"JOB_TIMEOUT", "PROVIDER_TIMEOUT"}:
        return 6
    if code == "JOB_WAIT_INTERRUPTED":
        return 130
    if code.startswith("PROVIDER_") or code in {"SUBMIT_UNCERTAIN", "UNKNOWN_PROVIDER"}:
        return 4
    if code in {
        "IMAGE_FINALIZATION_FAILED",
        "INPUT_ARCHIVE_FAILED",
        "OUTPUT_WRITE_FAILED",
        "ARTIFACT_DOWNLOAD_FAILED",
        "INPUT_FILE_NOT_FOUND",
        "UNSUPPORTED_INPUT_FORMAT",
    }:
        return 7
    return 3


class PublicGroup(TyperGroup):
    """The same boundary applies to installed entrypoint and CliRunner."""

    def main(
        self,
        args: Sequence[str] | None = None,
        prog_name: str | None = None,
        complete_var: str | None = None,
        standalone_mode: bool = True,
        windows_expand_args: bool = True,
        **kwargs: Any,
    ) -> Any:
        raw = list(sys.argv[1:] if args is None else args)
        as_json = _json_requested(raw)
        exit_code = 0
        local = None
        try:
            normalized, sources = normalize_args(raw)
            context = kwargs.setdefault("obj", {})
            context.update(sources=sources, as_json=as_json, emitted=False)
            local = context
            kwargs["standalone_mode"] = False
            if as_json and "--help" in normalized:
                captured = io.StringIO()
                with redirect_stdout(captured):
                    result = super().main(
                        args=normalized,
                        prog_name=prog_name,
                        complete_var=complete_var,
                        windows_expand_args=False,
                        **kwargs,
                    )
                emit({"help": captured.getvalue()}, as_json=True)
            else:
                result = super().main(
                    args=normalized,
                    prog_name=prog_name,
                    complete_var=complete_var,
                    windows_expand_args=False,
                    **kwargs,
                )
            if isinstance(result, int):
                exit_code = result
                # Locked Typer converts KeyboardInterrupt into return code 130
                # even in non-standalone mode. Emit the missing public result.
                if result == 130 and not context.get("emitted"):
                    emit(
                        as_json=as_json,
                        ok=False,
                        error={
                            "code": "JOB_WAIT_INTERRUPTED",
                            "message": "Локальное ожидание прервано; remote cancel не отправлен",
                            "details": {},
                        },
                    )
                raise SystemExit(result)
            return result
        except SystemExit as exc:
            exit_code = exc.code if isinstance(exc.code, int) else 1
            raise
        except click.ClickException:
            # Parser values can contain secrets: never echo raw Click messages.
            exit_code, code, message = (
                2,
                "INVALID_ARGUMENT",
                "Неверные аргументы; используйте --help",
            )
        except (KeyboardInterrupt, asyncio.CancelledError, Abort):
            exit_code, code, message = (
                130,
                "JOB_WAIT_INTERRUPTED",
                "Локальное ожидание прервано; remote cancel не отправлен",
            )
        except ProviderError as exc:
            exit_code, code, message = job_exit(exc.error.code), exc.error.code, exc.error.message
        except DomainError as exc:
            error = exc.to_job_error()
            exit_code, code, message = job_exit(error.code), error.code, error.message
        except RegistryError as exc:
            exit_code, code, message = (
                3,
                exc.code,
                "Модель/provider не найдены или каталог невалиден",
            )
        except (StorageError, ImageHistoryError):
            exit_code, code, message = (
                8,
                "DATABASE_ERROR",
                "История не подтверждена; повторный submit запрещён",
            )
        except (ValidationError, SettingsError, tomllib.TOMLDecodeError):
            exit_code, code, message = 4, "CONFIGURATION_ERROR", "Не удалось загрузить настройки"
        except OSError:
            exit_code, code, message = (
                7,
                "OUTPUT_WRITE_FAILED",
                "Локальная файловая операция не выполнена",
            )
        except Exception:
            exit_code, code, message = 1, "INTERNAL_ERROR", "Непредвиденная внутренняя ошибка"
        finally:
            if local and local.get("application"):
                local["application"].close()
            finish_active(exit_code)
        # Click's standalone boundary must not print usage before our JSON.
        # super.main below is always invoked with standalone_mode=False.
        emit(as_json=as_json, ok=False, error={"code": code, "message": message, "details": {}})
        raise SystemExit(exit_code)

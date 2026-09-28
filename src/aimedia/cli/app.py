"""Корневое приложение CLI aimedia.

На E01 приложение содержит только `version` и короткий `--help`; эти команды
работают без API key, базы данных и сети. Полное дерево команд из `04` появится
на E09.

Диагностика (`app_started`, `config_loaded`, `command_finished`) направляется в
stderr. В режиме `--json` stdout содержит единственный JSON-документ и не
загрязняется диагностическими записями.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import typer
from pydantic import ValidationError
from pydantic_settings import SettingsError

from aimedia import __version__
from aimedia.config import Settings
from aimedia.logging import (
    EventLogger,
    activate,
    configure_diagnostics,
    finish_active,
)

# Код возврата для ошибки настроек. Контракт `04`/baseline E00 закрепляет `4` как
# provider/configuration error; непредвиденные ошибки остаются кодом `1`.
CONFIG_ERROR_EXIT_CODE = 4

app = typer.Typer(
    name="aimedia",
    help="Локальный CLI генерации изображений через Polza.",
    add_completion=False,
    no_args_is_help=True,
)


def _emit_version(as_json: bool) -> None:
    if as_json:
        payload = {"ok": True, "data": {"version": __version__}}
        typer.echo(json.dumps(payload, ensure_ascii=False))
    else:
        typer.echo(f"aimedia {__version__}")


def _build_settings(
    config_file: str | None,
    data_dir: str | None,
    log_level: str | None,
) -> Settings:
    overrides: dict[str, object] = {}
    if data_dir is not None:
        overrides["data_dir"] = Path(data_dir)
    if log_level is not None:
        overrides["log_level"] = log_level
    return Settings.load(
        cli_overrides=overrides,
        config_file=Path(config_file) if config_file is not None else None,
    )


def _command_path(ctx: typer.Context) -> str:
    """Нормализованный путь команды без значений опций.

    В `app_started` запрещено писать сырой `sys.argv`: он содержит значения опций,
    которыми может быть ключ (например `--log-level <secret>`). Логируется только
    имя приложения и имя вызванной подкоманды.
    """
    parts = [ctx.command_path]
    if ctx.invoked_subcommand:
        parts.append(ctx.invoked_subcommand)
    return " ".join(parts)


def _safe_settings_message(exc: Exception) -> str:
    """Собрать безопасное сообщение об ошибке настроек без значений.

    Печатается только перечень незаполненных полей и общая подсказка: сами значения
    (в том числе секрет, ошибочно попавший в переменную настройки) в сообщение не
    попадают.
    """
    fields: set[str] = set()
    if isinstance(exc, ValidationError):
        for error in exc.errors():
            location = error.get("loc") or ()
            if location:
                fields.add(str(location[-1]))
    detail = f"поля: {', '.join(sorted(fields))}" if fields else "проверьте значения"
    return (
        f"Не удалось загрузить настройки ({detail}). "
        "Проверьте переменные AIMEDIA_* и файл, указанный в --config."
    )


def _configure_run(settings: Settings, *, command: str) -> EventLogger:
    """Настроить диагностику и записать стартовые события."""
    secret = settings.resolve_api_key()
    logger = configure_diagnostics(
        level=settings.log_level,
        secrets=(secret,) if secret else (),
    )
    activate(logger)
    logger.app_started(command=command, version=__version__)
    logger.config_loaded(settings.sources)
    return logger


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    config: str | None = typer.Option(
        None,
        "--config",
        help="Альтернативный файл настроек TOML.",
    ),
    data_dir: str | None = typer.Option(
        None,
        "--data-dir",
        help="Явный каталог данных вместо системного.",
    ),
    log_level: str | None = typer.Option(
        None,
        "--log-level",
        help="Уровень диагностики (DEBUG/INFO/WARNING/ERROR/CRITICAL).",
    ),
    show_version: bool = typer.Option(
        False,
        "--version",
        help="Показать версию и выйти.",
        is_eager=True,
    ),
) -> None:
    if show_version:
        _emit_version(as_json=False)
        raise typer.Exit(code=0)
    if ctx.invoked_subcommand is None:
        return
    try:
        settings = _build_settings(config, data_dir, log_level)
    except (ValidationError, SettingsError, tomllib.TOMLDecodeError) as exc:
        # Ошибка настроек возникает до логгера; её нельзя пробрасывать сырой —
        # rich напечатал бы трассировку и включённое в неё значение (потенциальный
        # секрет). Вместо этого — короткое сообщение в stderr и код `4`, а stdout
        # остаётся чистым (в `--json` там нет мусора).
        typer.echo(_safe_settings_message(exc), err=True)
        raise typer.Exit(code=CONFIG_ERROR_EXIT_CODE) from None
    _configure_run(settings, command=_command_path(ctx))


@app.command()
def version(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Вывести машинно читаемый JSON.",
    ),
) -> None:
    """Показать версию aimedia."""
    _emit_version(as_json=json_output)


def _exit_code_of(exc: SystemExit) -> int:
    code = exc.code
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    return 1


def main() -> None:
    """Точка входа console script `aimedia`."""
    exit_code = 0
    try:
        app()
    except SystemExit as exc:
        exit_code = _exit_code_of(exc)
        raise
    except BaseException:  # noqa: BLE001 - фиксируем код до проброса исключения
        exit_code = 1
        raise
    finally:
        finish_active(exit_code)

"""Корневое приложение CLI aimedia.

На E01 приложение содержит только `version` и короткий `--help`; эти команды
работают без API key, базы данных и сети. Полное дерево команд из `04` появится
на E09.
"""

from __future__ import annotations

import json

import typer

from aimedia import __version__

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


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
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


def main() -> None:
    """Точка входа console script `aimedia`."""
    app()

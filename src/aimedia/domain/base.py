"""Базовые типы домена aimedia: точные деньги, UTC-время, пути, доменные модели.

Домен не выполняет IO и не зависит от Typer/Rich/httpx/Peewee/YAML
(`docs/plans/02-system-architecture.md`, инвариант 5 в `03-domain-model.md`).

Согласованная сериализация (проверяется `tests/unit/test_serialization.py`):

- `Decimal` → строка без экспоненты (`0.0831` не превращается в float);
- `datetime` → ISO-8601 с суффиксом `Z`, только timezone-aware UTC;
- `Path` → posix-строка (одинакова на Windows и Linux);
- enum → его строковое значение.

`ManagedRelativePath` — относительный путь managed-файла (копия входа, `CN-01`).
Он проверяется **лексически**, без обращения к файловой системе: домен остаётся
IO-free, а решение об абсолютных путях, `..` и символах-разделителях принимается по
одной форме строки на Windows и Linux.

`ExactDecimal` отклоняет `float` и `bool`: деньги не проходят через двоичную
дробь и не смешиваются с булевым флагом.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    PlainSerializer,
    StringConstraints,
)

_ERROR_DECIMAL_TYPE = "денежная сумма должна быть Decimal, строкой или целым числом"
_ERROR_DECIMAL_VALUE = "некорректная десятичная сумма"
_ERROR_DECIMAL_FINITE = "денежная сумма должна быть конечным числом"
_ERROR_TIMESTAMP_NAIVE = "timestamp должен быть timezone-aware; локальное время машины недопустимо"
_ERROR_MANAGED_PATH_ABSOLUTE = (
    "managed_path должен быть относительным путём без абсолютного, drive-relative или UNC-префикса"
)
_ERROR_MANAGED_PATH_ESCAPES_ROOT = (
    "managed_path не должен содержать пустые компоненты, `.` или `..`"
)

_MANAGED_PATH_DRIVE_RE = re.compile(r"[A-Za-z]:")

_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")
_MIME_RE = re.compile(r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+")
_CURRENCY_RE = re.compile(r"[A-Za-z]{3}")


def _to_exact_decimal(value: object) -> object:
    """Привести вход к `Decimal`, запретив float и bool.

    `Decimal` принимается как есть; `int` и `str` конвертируются (TEXT ↔ Decimal
    без промежуточного float, решение baseline D11). Двоичная дробь дала бы
    неточность `0.1 + 0.2`, а `bool` — молчаливую подмену суммы флагом.
    """
    if isinstance(value, Decimal):
        decimal_value = value
    elif isinstance(value, bool):
        raise ValueError(_ERROR_DECIMAL_TYPE)
    elif isinstance(value, float):
        raise ValueError(_ERROR_DECIMAL_TYPE)
    elif isinstance(value, (int, str)):
        try:
            decimal_value = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(_ERROR_DECIMAL_VALUE) from exc
    else:
        raise ValueError(_ERROR_DECIMAL_TYPE)
    if not decimal_value.is_finite():
        raise ValueError(_ERROR_DECIMAL_FINITE)
    return decimal_value


def _decimal_to_text(value: Decimal) -> str:
    """Записать Decimal строкой без экспоненты (`1E+2` → `100`)."""
    return format(value, "f")


def _to_utc(value: datetime) -> datetime:
    """Привести timestamp к UTC, отклонив timezone-naive значение."""
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(_ERROR_TIMESTAMP_NAIVE)
    return value.astimezone(UTC)


def _utc_to_text(value: datetime) -> str:
    """Записать timestamp как ISO-8601 UTC с суффиксом `Z`."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _path_to_text(value: Path) -> str:
    """Записать путь в posix-форме, одинаковой на Windows и Linux."""
    return value.as_posix()


def _to_managed_relative_path(value: object) -> object:
    """Проверить managed-путь лексически и привести его к posix-форме.

    Домен не обращается к файловой системе и не разрешает путей: проверяется только
    форма строки. Абсолютные, drive-relative (`C:rel`) и UNC-пути, компоненты `..`,
    `.`, пустые компоненты и хвостовой разделитель отклоняются, потому что такой
    путь либо покидает managed-root, либо даёт двум записям один и тот же файл под
    разным текстом. Обратный слеш нормализуется в прямой, поэтому значение одинаково
    на Windows и Linux. Наличие ссылки, каталога или файла не проверяется — это
    работа файлового слоя.
    """
    if not isinstance(value, (str, Path)):
        raise ValueError(_ERROR_MANAGED_PATH_ABSOLUTE)
    text = str(value).replace("\\", "/")
    if not text or text.startswith("/") or _MANAGED_PATH_DRIVE_RE.match(text):
        raise ValueError(_ERROR_MANAGED_PATH_ABSOLUTE)
    if any(part in ("", ".", "..") for part in text.split("/")):
        raise ValueError(_ERROR_MANAGED_PATH_ESCAPES_ROOT)
    return text


def _to_sha256(value: object) -> object:
    """Проверить и нормализовать SHA-256 к нижнему регистру."""
    if isinstance(value, str) and _SHA256_RE.fullmatch(value):
        return value.lower()
    raise ValueError("sha256 должен содержать 64 шестнадцатеричных символа")


def _to_mime_type(value: object) -> object:
    """Проверить и нормализовать MIME-тип к нижнему регистру."""
    if isinstance(value, str) and _MIME_RE.fullmatch(value):
        return value.lower()
    raise ValueError("MIME-тип должен иметь форму type/subtype")


def _to_currency(value: object) -> object:
    """Проверить и нормализовать код валюты к верхнему регистру."""
    if isinstance(value, str) and _CURRENCY_RE.fullmatch(value):
        return value.upper()
    raise ValueError("код валюты должен состоять из трёх латинских букв (ISO 4217)")


def _to_non_blank(value: object) -> object:
    """Отклонить строку из одних пробелов, не меняя сам текст.

    Текст prompt может содержать значимые переводы строк и пробелы, поэтому
    проверяется только его непустота: скрытое переформатирование запрещено.
    """
    if isinstance(value, str) and value.strip():
        return value
    raise ValueError("строка не должна быть пустой или состоять из пробелов")


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
NonBlankStr = Annotated[str, BeforeValidator(_to_non_blank)]
ExactDecimal = Annotated[
    Decimal,
    BeforeValidator(_to_exact_decimal),
    PlainSerializer(_decimal_to_text, return_type=str, when_used="json"),
]
UtcDatetime = Annotated[
    datetime,
    AfterValidator(_to_utc),
    PlainSerializer(_utc_to_text, return_type=str, when_used="json"),
]
LocalPath = Annotated[
    Path,
    PlainSerializer(_path_to_text, return_type=str, when_used="json"),
]
ManagedRelativePath = Annotated[
    Path,
    BeforeValidator(_to_managed_relative_path),
    PlainSerializer(_path_to_text, return_type=str, when_used="json"),
]
Sha256Hex = Annotated[str, BeforeValidator(_to_sha256)]
MimeType = Annotated[str, BeforeValidator(_to_mime_type)]
CurrencyCode = Annotated[str, BeforeValidator(_to_currency)]


class DomainModel(BaseModel):
    """Базовая неизменяемая модель домена.

    `frozen=True` защищает value objects от перезаписи после создания, а
    `extra="forbid"` не даёт молча протащить поле, которого нет в контракте.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

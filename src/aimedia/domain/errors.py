"""Доменные ошибки aimedia и их нормализованное представление.

Ошибки домена не являются HTTP-ошибками (`03-domain-model.md`, «Ошибки домена»).
Каждый класс несёт стабильный строковый `code` из `04-cli-contract.md`, чтобы
JSON-конверт ошибки не зависел от текста сообщения, и умеет преобразоваться в
`JobError` для сохранения в истории.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from aimedia.domain.base import DomainModel


class DomainErrorCode(StrEnum):
    """Коды доменных ошибок, возникающих до обращения к provider."""

    UNKNOWN_MODEL = "UNKNOWN_MODEL"
    UNKNOWN_PROVIDER = "UNKNOWN_PROVIDER"
    UNSUPPORTED_PARAMETER = "UNSUPPORTED_PARAMETER"
    INVALID_PARAMETER_VALUE = "INVALID_PARAMETER_VALUE"
    PROMPT_REQUIRED = "PROMPT_REQUIRED"
    INPUT_FILE_NOT_FOUND = "INPUT_FILE_NOT_FOUND"
    UNSUPPORTED_INPUT_FORMAT = "UNSUPPORTED_INPUT_FORMAT"
    TOO_MANY_REFERENCE_IMAGES = "TOO_MANY_REFERENCE_IMAGES"
    UNSUPPORTED_CAPABILITY = "UNSUPPORTED_CAPABILITY"
    INVALID_JOB_STATE = "INVALID_JOB_STATE"


class JobError(DomainModel):
    """Нормализованная информация об ошибке Job.

    `message` — пользовательское сообщение, `provider_message` — сырой ответ
    provider. Обе формы сохраняются отдельно, чтобы не терять ни понятный текст,
    ни диагностический код.
    """

    code: str
    message: str
    provider_code: str | None = None
    provider_message: str | None = None
    retryable: bool | None = None
    details: dict[str, Any] = {}


class DomainError(Exception):
    """Базовая доменная ошибка с машинно различимым кодом."""

    code: DomainErrorCode = DomainErrorCode.INVALID_JOB_STATE

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = dict(details or {})

    def to_job_error(self, *, retryable: bool | None = None) -> JobError:
        """Преобразовать доменную ошибку в сохраняемую запись истории."""
        return JobError(
            code=self.code.value,
            message=self.message,
            retryable=retryable,
            details=dict(self.details),
        )


class UnknownModelError(DomainError):
    """Логическая модель отсутствует в Registry."""

    code = DomainErrorCode.UNKNOWN_MODEL


class ProviderError(Exception):
    """Нормализованный отказ provider, поднимаемый adapter.

    Adapter переводит HTTP/provider-ответ в доменный `JobError` и поднимает его
    вместе с этим исключением: сырой `httpx.Response` не пересекает границу
    провайдера, а application сохраняет одну нормализованную ошибку в истории.
    Этот контракт общий для всех provider/fake-реализаций.
    """

    def __init__(self, error: JobError) -> None:
        super().__init__(error.message)
        self.error = error

    def to_job_error(self) -> JobError:
        """Вернуть сохраняемую запись ошибки."""
        return self.error


class UnknownProviderError(DomainError):
    """Provider не зарегистрирован или не имеет adapter."""

    code = DomainErrorCode.UNKNOWN_PROVIDER


class UnsupportedParameterError(DomainError):
    """Модель не поддерживает переданный параметр."""

    code = DomainErrorCode.UNSUPPORTED_PARAMETER


class InvalidParameterValueError(DomainError):
    """Значение параметра вне допустимого моделью набора."""

    code = DomainErrorCode.INVALID_PARAMETER_VALUE


class PromptRequiredError(DomainError):
    """Пустой финальный prompt недопустим для выбранного режима."""

    code = DomainErrorCode.PROMPT_REQUIRED


class InputFileNotFoundError(DomainError):
    """Входной файл недоступен до обращения к provider."""

    code = DomainErrorCode.INPUT_FILE_NOT_FOUND


class UnsupportedInputFormatError(DomainError):
    """Формат входного ресурса не принимается выбранной моделью."""

    code = DomainErrorCode.UNSUPPORTED_INPUT_FORMAT


class TooManyReferenceImagesError(DomainError):
    """Число reference images превышает ограничение модели."""

    code = DomainErrorCode.TOO_MANY_REFERENCE_IMAGES


class UnsupportedCapabilityError(DomainError):
    """Запрошенный режим не входит в capability модели."""

    code = DomainErrorCode.UNSUPPORTED_CAPABILITY


class InvalidJobStateTransitionError(DomainError):
    """Недопустимый переход статуса Job.

    Сюда попадает в том числе возврат terminal Job в non-terminal и любая попытка
    `failed → completed` без признака recovery того же remote execution.
    """

    code = DomainErrorCode.INVALID_JOB_STATE

"""Статусы Job и допустимые переходы между ними.

`JobStatus` описывает локальное состояние, а не статус provider. Terminal states
— `completed`/`failed`/`cancelled`; возврат terminal Job в non-terminal запрещён.
Единственное исключение — `failed → completed` при recovery **того же** remote
execution без новой генерации (инвариант 13, решение baseline D01). Промежуточный
возврат в `running` запрещён и для этого случая.
"""

from __future__ import annotations

from enum import StrEnum

from aimedia.domain.errors import InvalidJobStateTransitionError

TERMINAL_STATUSES: frozenset[JobStatus]


class JobStatus(StrEnum):
    """Состояние локального Job."""

    CREATED = "created"
    SUBMITTED = "submitted"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = frozenset({JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED})

# Переходы без права на recovery: обычная работа Job.
_STANDARD_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.CREATED: frozenset({JobStatus.SUBMITTED, JobStatus.FAILED, JobStatus.CANCELLED}),
    JobStatus.SUBMITTED: frozenset(
        {JobStatus.RUNNING, JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
    ),
    JobStatus.RUNNING: frozenset({JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}),
    # Единственный контролируемый выход из terminal состояния описан отдельным
    # аргументом `recovery`, а не молчаливым расширением таблицы переходов.
    JobStatus.COMPLETED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}

# Единственное исключение из инварианта terminal states.
_RECOVERY_TRANSITION = (JobStatus.FAILED, JobStatus.COMPLETED)


def is_terminal(status: JobStatus) -> bool:
    """Достиг ли Job терминального состояния."""
    return status in TERMINAL_STATUSES


def can_transition(
    current: JobStatus,
    target: JobStatus,
    *,
    recovery: bool = False,
) -> bool:
    """Допустим ли переход.

    `recovery=True` разрешает ровно один дополнительный переход — `failed →
    completed` для того же remote execution. Всё остальное из terminal состояний
    (включая возврат в `running`) запрещено.
    """
    if target in _STANDARD_TRANSITIONS[current]:
        return True
    return recovery and (current, target) == _RECOVERY_TRANSITION


def ensure_transition(
    current: JobStatus,
    target: JobStatus,
    *,
    recovery: bool = False,
) -> JobStatus:
    """Проверить переход и вернуть целевой статус.

    Вызывается до сохранения нового состояния, поэтому недопустимый переход не
    доходит до истории.
    """
    if not can_transition(current, target, recovery=recovery):
        raise InvalidJobStateTransitionError(
            f"Недопустимый переход статуса Job: {current.value} → {target.value}",
            details={
                "current": current.value,
                "target": target.value,
                "recovery": recovery,
            },
        )
    return target

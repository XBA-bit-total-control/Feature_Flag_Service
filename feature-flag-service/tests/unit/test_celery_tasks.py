from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pymongo.errors import ConnectionFailure
from sqlalchemy.exc import OperationalError

from src.tasks import report_tasks, statistics_tasks


@pytest.mark.parametrize("error", [ConnectionFailure("offline"), OperationalError(None, None, OSError("offline"))])
def test_statistics_task_retries_transient_failures(monkeypatch, error: Exception) -> None:
    """Проверить повтор расчёта после временного сбоя MongoDB или PostgreSQL."""
    outcomes = {str(uuid4()): "calculated"}
    calculate = AsyncMock(side_effect=[error, outcomes])
    monkeypatch.setattr(statistics_tasks, "_calculate_running", calculate)

    result = statistics_tasks.calculate_statistics.apply(throw=False)

    assert result.successful() and result.get() == outcomes, (
        f"После повтора ожидался результат расчёта {outcomes}, получено {result.result}"
    )
    assert calculate.await_count == 2, f"Ожидался один повтор расчёта, вызовов: {calculate.await_count}"


def test_statistics_task_stops_after_three_retries(monkeypatch) -> None:
    """Вернуть ошибку после первой попытки и трёх разрешённых повторов."""
    calculate = AsyncMock(side_effect=ConnectionFailure("offline"))
    monkeypatch.setattr(statistics_tasks, "_calculate_running", calculate)

    result = statistics_tasks.calculate_statistics.apply(throw=False)

    assert result.failed() and isinstance(result.result, ConnectionFailure), (
        f"После исчерпания повторов ожидался сбой MongoDB, получено {result.result}"
    )
    assert calculate.await_count == 4, f"Ожидались четыре попытки расчёта, получено {calculate.await_count}"


def test_report_task_retries_with_same_uuid(monkeypatch) -> None:
    """Проверить преобразование строки в UUID и повтор того же задания."""
    report_id = uuid4()
    run = AsyncMock(side_effect=[OperationalError(None, None, OSError("offline")), None])
    monkeypatch.setattr(report_tasks, "_run_reports", run)

    result = report_tasks.generate_report_task.apply(args=[str(report_id)], throw=False)

    assert result.successful(), f"Задание отчёта не восстановилось после сбоя: {result.result}"
    assert [call.args for call in run.await_args_list] == [(report_id,), (report_id,)], (
        f"При повторе должен использоваться тот же UUID: {run.await_args_list}"
    )


def test_report_task_rejects_invalid_uuid(monkeypatch) -> None:
    """Остановить задачу до доступа к хранилищу, если идентификатор повреждён."""
    run = AsyncMock()
    monkeypatch.setattr(report_tasks, "_run_reports", run)

    result = report_tasks.generate_report_task.apply(args=["invalid"], throw=False)

    assert result.failed() and isinstance(result.result, ValueError), (
        f"Некорректный UUID должен завершить задачу с ValueError, получено {result.result}"
    )
    assert run.await_count == 0, "Задача с повреждённым UUID дошла до обработки отчёта"


def test_pending_reports_task_runs_batch(monkeypatch) -> None:
    """Запустить подбор очереди без UUID конкретного отчёта."""
    run = AsyncMock(return_value=None)
    monkeypatch.setattr(report_tasks, "_run_reports", run)

    result = report_tasks.process_pending_reports.apply(throw=False)

    assert result.successful(), f"Задача подбора отчётов завершилась ошибкой: {result.result}"
    assert run.await_count == 1 and run.await_args.args == (), (
        f"Ожидался один запуск без UUID, получено {run.await_args_list}"
    )

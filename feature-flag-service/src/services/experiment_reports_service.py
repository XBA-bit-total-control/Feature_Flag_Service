import json
from dataclasses import asdict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.experiment import Experiment
from src.models.experiment_report import ExperimentReport
from src.services import report_service
from src.services.experiment_results_service import get_results
from src.services.experiment_service import ExperimentConflictError, ExperimentNotFoundError
from src.services.report_service import ReportContext
from src.services.statistics_service import ComparisonResult, VariantCounts


async def _build_report_context(
        db: AsyncSession, experiment_id: UUID, *, lock: bool = False
) -> ReportContext:
    experiment = await db.get(
        Experiment, experiment_id, with_for_update=lock, populate_existing=True
    )
    if experiment is None:
        raise ExperimentNotFoundError("Эксперимент не найден")
    results = await get_results(db, experiment_id)
    control = next((r for r in results if r.variant_name in ("control", "A")), None)
    if len(results) != 2 or control is None:
        raise ExperimentConflictError("Сначала рассчитайте результаты двух вариантов")
    treatment = next(r for r in results if r is not control)
    interval = None
    if control.confidence_interval_lower is not None and control.confidence_interval_upper is not None:
        interval = (control.confidence_interval_lower, control.confidence_interval_upper)
    counts = [VariantCounts(r.sample_size, r.conversions) for r in (control, treatment)]
    difference = None
    if all(count.sample_size for count in counts):
        difference = treatment.conversion_rate - control.conversion_rate
    return ReportContext(
        experiment.name, experiment.primary_metric,
        control.variant_name, treatment.variant_name,
        *counts,
        ComparisonResult(
            difference, control.z_statistic, control.p_value, interval,
            control.is_significant, control.enough_data,
        ),
        alpha=experiment.alpha,
        min_sample_size=experiment.min_sample_size,
    )


async def build_template_report(db: AsyncSession, experiment_id: UUID) -> str:
    """Сформировать шаблон по сохранённым результатам без записи в историю."""
    context = await _build_report_context(db, experiment_id)
    return report_service.template_report(context)


async def request_llm_report(
        db: AsyncSession, experiment_id: UUID
) -> tuple[ExperimentReport, bool]:
    """Получить ожидающее задание для снимка или создать новое."""
    context = await _build_report_context(db, experiment_id, lock=True)
    snapshot = json.loads(json.dumps(asdict(context)))
    existing = await db.scalar(select(ExperimentReport).where(
        ExperimentReport.experiment_id == experiment_id,
        ExperimentReport.input_snapshot == snapshot,
        ExperimentReport.status == "pending",
    ).order_by(ExperimentReport.created_at, ExperimentReport.id).limit(1))
    if existing is not None:
        return existing, False
    report = ExperimentReport(
        experiment_id=experiment_id, status="pending", input_snapshot=snapshot
    )
    db.add(report)
    await db.flush()
    await db.refresh(report)
    return report, True


async def save_automatic_report(db: AsyncSession, experiment_id: UUID) -> None:
    """Сохранить один автоматический шаблон на снимок метрик."""
    context = await _build_report_context(db, experiment_id, lock=True)
    snapshot = json.loads(json.dumps(asdict(context)))
    existing_id = await db.scalar(select(ExperimentReport.id).where(
        ExperimentReport.experiment_id == experiment_id,
        ExperimentReport.input_snapshot == snapshot,
        ExperimentReport.status == "completed",
        ExperimentReport.report_type == "template",
        ExperimentReport.fallback_reason.is_(None),
    ).limit(1))
    if existing_id is None:
        db.add(ExperimentReport(
            experiment_id=experiment_id,
            status="completed",
            input_snapshot=snapshot,
            report_type="template",
            report_content=report_service.template_report(context),
        ))


async def list_reports(db: AsyncSession, experiment_id: UUID) -> list[ExperimentReport]:
    """Получить историю запросов и готовых отчётов эксперимента."""
    return list(await db.scalars(
        select(ExperimentReport).where(ExperimentReport.experiment_id == experiment_id)
        .order_by(ExperimentReport.created_at.desc(), ExperimentReport.id)
    ))


async def complete_report(db: AsyncSession, report_id: UUID) -> ExperimentReport | None:
    """Сформировать отчёт по сохранённому снимку, пропуская завершённые задачи."""
    report = await db.get(
        ExperimentReport, report_id, with_for_update=True, populate_existing=True
    )
    if report is None or report.status != "pending":
        return report
    if report.input_snapshot is None:
        report.status = "failed"
        report.fallback_reason = "missing_snapshot"
        await db.flush()
        return report
    snapshot = dict(report.input_snapshot)
    context = ReportContext(
        **{k: v for k, v in snapshot.items() if k not in ("control", "treatment", "comparison")},
        control=VariantCounts(**snapshot["control"]),
        treatment=VariantCounts(**snapshot["treatment"]),
        comparison=ComparisonResult(**snapshot["comparison"]),
    )
    generated = await report_service.generate_report(context)
    report.report_content = generated.content
    report.report_type = generated.report_type
    report.llm_model = generated.llm_model
    report.tokens_used = generated.tokens_used
    report.fallback_reason = generated.fallback_reason
    report.status = "completed"
    await db.flush()
    return report

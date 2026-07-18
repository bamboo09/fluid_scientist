"""Project / Study / Variant / SpecVersion / Run 数据模型。

本模块定义 Fluid Scientist V5 的"项目级研究容器"层次结构，补足
``study_spec`` 包在"研究组织"层面的表达能力。与已有的
:class:`~fluid_scientist.study_spec.models.SimulationStudySpec`（单个
仿真的规范）和 :class:`~fluid_scientist.study_spec.versioning.SpecVersion`
（版本元数据）不同，这里的模型描述的是**研究组织结构**：

    Project
      └── Study            （一项独立研究）
            └── Variant    （研究变体，引用一个 SpecVersion）
                  └── Run （一次仿真运行）

设计要点
--------
* 全部使用 Pydantic v2 ``BaseModel``，``model_config = ConfigDict(extra="forbid")``。
* ``Variant.spec_version_id`` 引用一个
  :class:`~fluid_scientist.study_spec.versioning.SpecVersion`，从而把"研究
  变体"与"规范版本"解耦——一个变体可以跨多个规范版本演进。
* 为了避免与 ``study_spec.versioning.SpecVersion`` 同名冲突，这里的规范版本
  类命名为 :class:`SpecVersionSnapshot`，它**携带具体的仿真参数**（``parameters``
  字段），与仅记录版本元数据的 ``versioning.SpecVersion`` 互补。
* 模块同时提供一个线程安全的 :class:`ProjectStore`，供 API router 做内存态
  存储（与 ``cylinder_flow_router`` 中的 ``_spec_store`` 风格一致）。
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from fluid_scientist.compat import UTC, StrEnum

__all__ = [
    "RunStatus",
    "StudyStatus",
    "VariantStatus",
    "Run",
    "SpecVersionSnapshot",
    "Variant",
    "Study",
    "Project",
    "ProjectStore",
]


# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------


class RunStatus(StrEnum):
    """一次仿真运行的生命周期状态。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StudyStatus(StrEnum):
    """一项研究的状态。"""

    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class VariantStatus(StrEnum):
    """一个研究变体的状态。"""

    PROPOSED = "proposed"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


# ---------------------------------------------------------------------------
# Run —— 一次仿真运行
# ---------------------------------------------------------------------------


class Run(BaseModel):
    """单次仿真运行记录。

    Parameters
    ----------
    run_id:
        运行唯一标识。
    variant_id:
        所属变体。
    spec_version_id:
        本次运行所基于的规范版本快照。
    status:
        运行状态。
    job_id:
        底层执行引擎返回的作业 id（可空）。
    started_at / completed_at:
        ISO-8601 时间戳。
    result_summary:
        结果摘要（自由结构，便于前端展示）。
    artifacts:
        产物路径列表。
    error:
        失败时的错误信息。
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    variant_id: str
    spec_version_id: str
    status: RunStatus = RunStatus.PENDING
    job_id: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    result_summary: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[str] = Field(default_factory=list)
    error: str | None = None


# ---------------------------------------------------------------------------
# SpecVersionSnapshot —— 规范版本（包含具体的仿真参数）
# ---------------------------------------------------------------------------


class SpecVersionSnapshot(BaseModel):
    """规范版本快照，携带具体的仿真参数。

    与 :class:`~fluid_scientist.study_spec.versioning.SpecVersion`（仅记录
    版本元数据）不同，本模型**内联**了本次版本对应的仿真参数，使得一个
    研究变体即使脱离外部 spec 存储也能自描述。

    Parameters
    ----------
    spec_version_id:
        规范版本快照唯一标识。
    version:
        版本号（从 1 开始递增）。
    parameters:
        具体的仿真参数（与 ``SimulationStudySpec.model_dump()`` 结构兼容）。
    parent_spec_version_id:
        派生自哪个规范版本（``None`` 表示初始版本）。
    created_at:
        创建时间（ISO-8601）。
    source:
        版本来源说明，例如 ``"initial"``、``"variant"``、``"patch"``。
    """

    model_config = ConfigDict(extra="forbid")

    spec_version_id: str
    version: int = 1
    parameters: dict[str, Any] = Field(default_factory=dict)
    parent_spec_version_id: str | None = None
    created_at: str
    source: str = "initial"


# ---------------------------------------------------------------------------
# Variant —— 研究变体
# ---------------------------------------------------------------------------


class Variant(BaseModel):
    """研究变体，引用一个 :class:`SpecVersionSnapshot`。

    一个变体代表"同一研究目标下的一个具体参数配置"。从当前变体派生新
    变体（CREATE_VARIANT）时，会复制当前规范参数生成新的
    :class:`SpecVersionSnapshot` 与新的 :class:`Variant`。

    Parameters
    ----------
    variant_id:
        变体唯一标识。
    study_id:
        所属研究。
    name:
        变体名称（人类可读）。
    description:
        变体说明。
    spec_version_id:
        当前引用的规范版本快照 id。
    parent_variant_id:
        派生自哪个变体（``None`` 表示根变体）。
    status:
        变体状态。
    created_at:
        创建时间（ISO-8601）。
    """

    model_config = ConfigDict(extra="forbid")

    variant_id: str
    study_id: str
    name: str
    description: str = ""
    spec_version_id: str
    parent_variant_id: str | None = None
    status: VariantStatus = VariantStatus.PROPOSED
    created_at: str


# ---------------------------------------------------------------------------
# Study —— 单个研究
# ---------------------------------------------------------------------------


class Study(BaseModel):
    """单个研究，包含多个 :class:`Variant`。

    Parameters
    ----------
    study_id:
        研究唯一标识。
    project_id:
        所属项目。
    name:
        研究名称。
    objective:
        研究目标（一句话）。
    variants:
        该研究下的所有变体。
    current_variant_id:
        当前活跃变体 id。
    status:
        研究状态。
    created_at:
        创建时间（ISO-8601）。
    metadata:
        自由扩展元数据。
    """

    model_config = ConfigDict(extra="forbid")

    study_id: str
    project_id: str
    name: str
    objective: str = ""
    variants: list[Variant] = Field(default_factory=list)
    current_variant_id: str | None = None
    status: StudyStatus = StudyStatus.DRAFT
    created_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Project —— 项目级别容器
# ---------------------------------------------------------------------------


class Project(BaseModel):
    """项目级别容器，包含多个 :class:`Study`。

    Parameters
    ----------
    project_id:
        项目唯一标识。
    name:
        项目名称。
    description:
        项目描述。
    studies:
        项目下的所有研究。
    current_study_id:
        当前活跃研究 id。
    created_at:
        创建时间（ISO-8601）。
    metadata:
        自由扩展元数据。
    """

    model_config = ConfigDict(extra="forbid")

    project_id: str
    name: str
    description: str = ""
    studies: list[Study] = Field(default_factory=list)
    current_study_id: str | None = None
    created_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# ProjectStore —— 线程安全的内存态存储
# ---------------------------------------------------------------------------


def _new_id(prefix: str) -> str:
    """生成一个短 id，形如 ``prefix_xxxxxxxxxxxx``。"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _now_iso() -> str:
    """当前 UTC 时间的 ISO-8601 字符串。"""
    return datetime.now(UTC).isoformat()


class ProjectStore:
    """线程安全的 Project / Study / Variant / Run 内存存储。

    该存储与 ``cylinder_flow_router`` 中的 ``_spec_store`` 风格保持一致，
    提供 V5 研究 session router 所需的最小 CRUD 能力。生产环境可替换为
    持久化实现（如 SQLite）。
    """

    def __init__(self) -> None:
        self._projects: dict[str, Project] = {}
        self._studies: dict[str, Study] = {}
        self._variants: dict[str, Variant] = {}
        self._spec_versions: dict[str, SpecVersionSnapshot] = {}
        self._runs: dict[str, Run] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Project
    # ------------------------------------------------------------------

    def create_project(
        self,
        name: str,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Project:
        """创建一个新项目。"""
        project = Project(
            project_id=_new_id("proj"),
            name=name,
            description=description,
            created_at=_now_iso(),
            metadata=dict(metadata or {}),
        )
        with self._lock:
            self._projects[project.project_id] = project
        return project

    def get_project(self, project_id: str) -> Project | None:
        with self._lock:
            return self._projects.get(project_id)

    # ------------------------------------------------------------------
    # Study
    # ------------------------------------------------------------------

    def create_study(
        self,
        project_id: str,
        name: str,
        objective: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Study:
        """在项目下创建新研究，并将其设为当前研究。"""
        study = Study(
            study_id=_new_id("study"),
            project_id=project_id,
            name=name,
            objective=objective,
            created_at=_now_iso(),
            metadata=dict(metadata or {}),
        )
        with self._lock:
            self._studies[study.study_id] = study
            project = self._projects.get(project_id)
            if project is not None:
                project.studies.append(study)
                project.current_study_id = study.study_id
        return study

    def get_study(self, study_id: str) -> Study | None:
        with self._lock:
            return self._studies.get(study_id)

    # ------------------------------------------------------------------
    # SpecVersion
    # ------------------------------------------------------------------

    def create_spec_version(
        self,
        parameters: dict[str, Any],
        parent_spec_version_id: str | None = None,
        source: str = "initial",
    ) -> SpecVersionSnapshot:
        """创建一个规范版本快照。

        版本号根据父版本推导：若有父版本则 +1，否则为 1。
        """
        version = 1
        with self._lock:
            if parent_spec_version_id and parent_spec_version_id in self._spec_versions:
                version = self._spec_versions[parent_spec_version_id].version + 1
        snapshot = SpecVersionSnapshot(
            spec_version_id=_new_id("specver"),
            version=version,
            parameters=parameters,
            parent_spec_version_id=parent_spec_version_id,
            created_at=_now_iso(),
            source=source,
        )
        with self._lock:
            self._spec_versions[snapshot.spec_version_id] = snapshot
        return snapshot

    def get_spec_version(self, spec_version_id: str) -> SpecVersionSnapshot | None:
        with self._lock:
            return self._spec_versions.get(spec_version_id)

    # ------------------------------------------------------------------
    # Variant
    # ------------------------------------------------------------------

    def create_variant(
        self,
        study_id: str,
        name: str,
        spec_version_id: str,
        description: str = "",
        parent_variant_id: str | None = None,
    ) -> Variant:
        """在研究下创建新变体，并将其设为当前变体。

        若 ``parent_variant_id`` 提供，旧变体会被标记为
        :attr:`VariantStatus.SUPERSEDED`。
        """
        variant = Variant(
            variant_id=_new_id("variant"),
            study_id=study_id,
            name=name,
            description=description,
            spec_version_id=spec_version_id,
            parent_variant_id=parent_variant_id,
            created_at=_now_iso(),
        )
        with self._lock:
            self._variants[variant.variant_id] = variant
            study = self._studies.get(study_id)
            if study is not None:
                if parent_variant_id:
                    for old in study.variants:
                        if old.variant_id == parent_variant_id:
                            old.status = VariantStatus.SUPERSEDED
                study.variants.append(variant)
                study.current_variant_id = variant.variant_id
        return variant

    def get_variant(self, variant_id: str) -> Variant | None:
        with self._lock:
            return self._variants.get(variant_id)

    def create_variant_from_current(
        self,
        study_id: str,
        name: str,
        description: str = "",
    ) -> tuple[Variant, SpecVersionSnapshot] | None:
        """从当前研究的当前变体派生新变体（CREATE_VARIANT）。

        会复制当前变体所引用的规范参数，生成新的规范版本快照与新变体。
        返回 ``(new_variant, new_spec_version)``，若研究/当前变体不存在则
        返回 ``None``。
        """
        with self._lock:
            study = self._studies.get(study_id)
            if study is None or study.current_variant_id is None:
                return None
            current_variant = self._variants.get(study.current_variant_id)
            if current_variant is None:
                return None
            parent_spec = self._spec_versions.get(current_variant.spec_version_id)
            parameters = (
                dict(parent_spec.parameters) if parent_spec is not None else {}
            )
            parent_variant_id = current_variant.variant_id

        new_spec = self.create_spec_version(
            parameters=parameters,
            parent_spec_version_id=current_variant.spec_version_id,
            source="variant",
        )
        new_variant = self.create_variant(
            study_id=study_id,
            name=name,
            spec_version_id=new_spec.spec_version_id,
            description=description,
            parent_variant_id=parent_variant_id,
        )
        return new_variant, new_spec

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def create_run(
        self,
        variant_id: str,
        spec_version_id: str,
        job_id: str | None = None,
    ) -> Run:
        """记录一次仿真运行。"""
        run = Run(
            run_id=_new_id("run"),
            variant_id=variant_id,
            spec_version_id=spec_version_id,
            job_id=job_id,
        )
        with self._lock:
            self._runs[run.run_id] = run
        return run

    def get_run(self, run_id: str) -> Run | None:
        with self._lock:
            return self._runs.get(run_id)

    def update_run(self, run_id: str, **fields: Any) -> Run | None:
        """更新运行状态等字段。"""
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return None
            for key, value in fields.items():
                if hasattr(run, key):
                    setattr(run, key, value)
            return run

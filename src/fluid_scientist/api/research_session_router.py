"""通用研究 Session Router (Fluid Scientist V5)。

提供与具体仿真族（如 cylinder_flow）解耦的"研究 session"通用入口：

    POST   /api/v5/research-sessions                  — 创建研究 session
    GET    /api/v5/research-sessions/{session_id}      — 获取 session 状态
    POST   /api/v5/research-sessions/{session_id}/turns — 处理用户消息（通用入口）
    POST   /api/v5/research-sessions/{session_id}/studies  — 创建新研究 (CREATE_NEW_STUDY)
    POST   /api/v5/research-sessions/{session_id}/variants — 从当前研究创建变体 (CREATE_VARIANT)

设计要点
--------
* "通用入口"意味着 turns 端点不限定 cylinder_flow：它依据用户消息**意图**
  分发到 CREATE_VARIANT / CREATE_NEW_STUDY / 普通对话三种处理路径。
* 当前"研究方案"以一个通用 spec payload（``dict``）形式保存在 session 中，
  使本 router 与具体 spec 模型解耦——任何场景的 spec 都可以作为 payload
  被 variant / study 流程复制派生。
* 研究组织结构（Project / Study / Variant / SpecVersion）由
  :class:`~fluid_scientist.study_spec.project_models.ProjectStore` 维护。
* 2D/3D 维度冲突在通用入口处也做一次守卫（复用
  :func:`~fluid_scientist.intent.conflict_resolver.detect_dimension_conflict`）。
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from fluid_scientist.compat import UTC, StrEnum
from fluid_scientist.intent.conflict_resolver import detect_dimension_conflict
from fluid_scientist.study_spec.project_models import ProjectStore

__all__ = ["router"]

router = APIRouter(prefix="/api/v5", tags=["v5-research-sessions"])


# ---------------------------------------------------------------------------
# 枚举与状态
# ---------------------------------------------------------------------------


class ResearchSessionStatus(StrEnum):
    """通用研究 session 的状态。"""

    INITIALIZED = "initialized"
    ACTIVE = "active"
    AWAITING_CLARIFICATION = "awaiting_clarification"
    CLOSED = "closed"


class TurnIntent(StrEnum):
    """turns 端点识别出的用户意图。"""

    MESSAGE = "message"
    CREATE_VARIANT = "create_variant"
    CREATE_NEW_STUDY = "create_new_study"
    DIMENSION_CONFLICT = "dimension_conflict"


# ---------------------------------------------------------------------------
# 请求 / 响应模型
# ---------------------------------------------------------------------------


class CreateResearchSessionRequest(BaseModel):
    """创建研究 session 的请求。"""

    model_config = ConfigDict(extra="forbid")

    name: str = ""
    description: str = ""
    # 可选：创建时直接附带首条用户消息与初始 spec payload
    message: str | None = None
    spec_payload: dict[str, Any] | None = None


class CreateResearchSessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    status: ResearchSessionStatus
    current_study_id: str | None = None
    current_variant_id: str | None = None
    current_spec_id: str | None = None
    created_at: str


class TurnRecord(BaseModel):
    """一轮对话记录。"""

    model_config = ConfigDict(extra="forbid")

    turn_id: str
    intent: TurnIntent
    user_message: str
    assistant_reply: str
    original_spec_id: str | None = None
    new_spec_id: str | None = None
    new_study_id: str | None = None
    blocking_issues: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str


class ResearchSessionView(BaseModel):
    """session 状态视图。"""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    status: ResearchSessionStatus
    name: str
    description: str
    current_study_id: str | None = None
    current_variant_id: str | None = None
    current_spec_id: str | None = None
    turns: list[TurnRecord] = Field(default_factory=list)
    created_at: str


class TurnRequest(BaseModel):
    """处理用户消息的请求（通用入口）。"""

    model_config = ConfigDict(extra="forbid")

    message: str
    # 可选：显式指定意图，绕过关键词检测
    intent: TurnIntent | None = None
    # 可选：随消息附带/更新当前 spec payload（用于无 cylinder_flow 的通用场景）
    spec_payload: dict[str, Any] | None = None


class TurnResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    turn_id: str
    intent: TurnIntent
    reply: str
    original_spec_id: str | None = None
    new_spec_id: str | None = None
    new_study_id: str | None = None
    current_spec_id: str | None = None
    blocking_issues: list[dict[str, Any]] = Field(default_factory=list)


class CreateStudyRequest(BaseModel):
    """创建新研究 (CREATE_NEW_STUDY) 的请求。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    objective: str = ""
    # 可选：新研究的初始 spec payload；若未提供则复制当前 session 的 spec
    spec_payload: dict[str, Any] | None = None


class CreateVariantRequest(BaseModel):
    """从当前研究创建变体 (CREATE_VARIANT) 的请求。"""

    model_config = ConfigDict(extra="forbid")

    name: str = ""
    description: str = ""
    # 可选：变体的 spec payload；若未提供则复制当前 spec
    spec_payload: dict[str, Any] | None = None


class CreateStudyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    study_id: str
    variant_id: str
    spec_version_id: str
    spec_id: str
    original_spec_id: str | None = None


class CreateVariantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    variant_id: str
    spec_version_id: str
    spec_id: str
    original_spec_id: str | None = None


# ---------------------------------------------------------------------------
# 内存存储
# ---------------------------------------------------------------------------


class _ResearchSessionState:
    """单个研究 session 的内存状态。"""

    def __init__(
        self,
        session_id: str,
        name: str,
        description: str,
        created_at: str,
    ) -> None:
        self.session_id = session_id
        self.name = name
        self.description = description
        self.status = ResearchSessionStatus.INITIALIZED
        self.current_study_id: str | None = None
        self.current_variant_id: str | None = None
        self.current_spec_id: str | None = None
        # 通用 spec payload（与具体仿真族解耦）
        self.current_spec_payload: dict[str, Any] = {}
        self.turns: list[TurnRecord] = []
        self.created_at = created_at


_project_store = ProjectStore()
_default_project_id: str | None = None
_sessions: dict[str, _ResearchSessionState] = {}
_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _ensure_default_project() -> str:
    global _default_project_id
    if _default_project_id is None:
        project = _project_store.create_project(
            name="research-sessions",
            description="通用研究 session 默认项目",
        )
        _default_project_id = project.project_id
    return _default_project_id


def _get_session(session_id: str) -> _ResearchSessionState:
    with _lock:
        session = _sessions.get(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "SESSION_NOT_FOUND", "session_id": session_id},
        )
    return session


def _to_view(session: _ResearchSessionState) -> ResearchSessionView:
    return ResearchSessionView(
        session_id=session.session_id,
        status=session.status,
        name=session.name,
        description=session.description,
        current_study_id=session.current_study_id,
        current_variant_id=session.current_variant_id,
        current_spec_id=session.current_spec_id,
        turns=list(session.turns),
        created_at=session.created_at,
    )


# ---------------------------------------------------------------------------
# 意图检测（通用，不限定 cylinder_flow）
# ---------------------------------------------------------------------------


def _detect_intent_from_text(message: str) -> TurnIntent:
    """从用户消息关键词推断意图。"""
    if "保留当前方案" in message and ("复制" in message or "对照" in message):
        return TurnIntent.CREATE_VARIANT
    if "保存当前" in message and "新建" in message:
        return TurnIntent.CREATE_NEW_STUDY
    return TurnIntent.MESSAGE


def _seed_initial_study(
    session: _ResearchSessionState,
    spec_payload: dict[str, Any],
    study_name: str = "initial-study",
    objective: str = "",
) -> tuple[str, str, str, str]:
    """为 session 创建首个 Study / Variant / SpecVersion，并更新 session 当前指针。

    返回 ``(study_id, variant_id, spec_version_id, spec_id)``。
    """
    project_id = _ensure_default_project()
    study = _project_store.create_study(
        project_id=project_id,
        name=study_name,
        objective=objective,
    )
    spec_version = _project_store.create_spec_version(
        parameters=spec_payload,
        source="initial",
    )
    variant = _project_store.create_variant(
        study_id=study.study_id,
        name="baseline",
        spec_version_id=spec_version.spec_version_id,
        description="初始变体",
    )
    spec_id = _new_id("spec")

    session.current_study_id = study.study_id
    session.current_variant_id = variant.variant_id
    session.current_spec_id = spec_id
    session.current_spec_payload = dict(spec_payload)
    session.status = ResearchSessionStatus.ACTIVE
    return study.study_id, variant.variant_id, spec_version.spec_version_id, spec_id


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------


@router.post(
    "/research-sessions",
    response_model=CreateResearchSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_research_session(
    request: CreateResearchSessionRequest,
) -> CreateResearchSessionResponse:
    """创建一个通用研究 session。

    若提供了 ``message`` / ``spec_payload``，则同时建立首个 Study/Variant 并
    记录首轮对话。
    """
    session_id = _new_id("rsess")
    session = _ResearchSessionState(
        session_id=session_id,
        name=request.name or f"research-session-{session_id[-6:]}",
        description=request.description,
        created_at=_now_iso(),
    )

    study_id: str | None = None
    variant_id: str | None = None
    spec_id: str | None = None

    if request.spec_payload is not None or request.message:
        payload = request.spec_payload or {}
        _seed_initial_study(
            session,
            spec_payload=payload,
            study_name=f"{session.name}-initial" if session.name else "initial-study",
        )
        study_id = session.current_study_id
        variant_id = session.current_variant_id
        spec_id = session.current_spec_id

        if request.message:
            intent = _detect_intent_from_text(request.message)
            session.turns.append(
                TurnRecord(
                    turn_id=_new_id("turn"),
                    intent=TurnIntent.MESSAGE,
                    user_message=request.message,
                    assistant_reply="研究 session 已创建，首条消息已记录。",
                    created_at=_now_iso(),
                )
            )
            # 记录首条消息的推断意图（不影响状态）
            _ = intent

    with _lock:
        _sessions[session_id] = session

    return CreateResearchSessionResponse(
        session_id=session_id,
        status=session.status,
        current_study_id=study_id,
        current_variant_id=variant_id,
        current_spec_id=spec_id,
        created_at=session.created_at,
    )


@router.get("/research-sessions/{session_id}", response_model=ResearchSessionView)
def get_research_session(session_id: str) -> ResearchSessionView:
    """获取研究 session 的状态。"""
    session = _get_session(session_id)
    return _to_view(session)


@router.post(
    "/research-sessions/{session_id}/turns",
    response_model=TurnResponse,
)
def process_turn(session_id: str, request: TurnRequest) -> TurnResponse:
    """处理用户消息（通用入口，不限定 cylinder_flow）。

    依据用户消息意图分发：

    * **CREATE_VARIANT** — 复制当前 spec payload 派生新变体。
    * **CREATE_NEW_STUDY** — 保存当前方案为新 Study，并新建工作 spec。
    * **DIMENSION_CONFLICT** — 检测到 2D/3D 维度冲突，返回阻塞 issue。
    * **MESSAGE** — 普通对话，记录消息并回执。
    """
    session = _get_session(session_id)

    # 可选：随消息更新当前 spec payload
    if request.spec_payload is not None:
        session.current_spec_payload = dict(request.spec_payload)
        if session.current_spec_id is None:
            _seed_initial_study(
                session,
                spec_payload=session.current_spec_payload,
                study_name=f"{session.name}-initial" if session.name else "initial-study",
            )

    # 意图：显式优先，否则关键词推断
    intent = request.intent or _detect_intent_from_text(request.message)

    # 维度冲突守卫（通用）
    dim_issue = detect_dimension_conflict(request.message)
    if dim_issue is not None:
        intent = TurnIntent.DIMENSION_CONFLICT
        turn = TurnRecord(
            turn_id=_new_id("turn"),
            intent=intent,
            user_message=request.message,
            assistant_reply="检测到 2D/3D 维度冲突，请澄清仿真维度。",
            blocking_issues=[dim_issue],
            created_at=_now_iso(),
        )
        session.turns.append(turn)
        session.status = ResearchSessionStatus.AWAITING_CLARIFICATION
        return TurnResponse(
            session_id=session_id,
            turn_id=turn.turn_id,
            intent=intent,
            reply=turn.assistant_reply,
            blocking_issues=[dim_issue],
            current_spec_id=session.current_spec_id,
        )

    if intent == TurnIntent.CREATE_VARIANT:
        return _handle_create_variant(session, request.message)

    if intent == TurnIntent.CREATE_NEW_STUDY:
        return _handle_create_new_study(session, request.message)

    # 普通对话
    reply = '已收到您的消息。如需保留当前方案并复制对照，请说明「保留当前方案并复制」。'
    turn = TurnRecord(
        turn_id=_new_id("turn"),
        intent=TurnIntent.MESSAGE,
        user_message=request.message,
        assistant_reply=reply,
        created_at=_now_iso(),
    )
    session.turns.append(turn)
    if session.status == ResearchSessionStatus.INITIALIZED:
        session.status = ResearchSessionStatus.ACTIVE
    return TurnResponse(
        session_id=session_id,
        turn_id=turn.turn_id,
        intent=TurnIntent.MESSAGE,
        reply=reply,
        current_spec_id=session.current_spec_id,
    )


@router.post(
    "/research-sessions/{session_id}/studies",
    response_model=CreateStudyResponse,
)
def create_new_study(
    session_id: str,
    request: CreateStudyRequest,
) -> CreateStudyResponse:
    """显式创建新研究 (CREATE_NEW_STUDY)。

    将当前 session 的方案保存为一个新 Study（含首变体与规范版本快照），
    并把 session 的当前指针切换到新 Study。
    """
    session = _get_session(session_id)
    original_spec_id = session.current_spec_id

    payload = request.spec_payload
    if payload is None:
        payload = dict(session.current_spec_payload)

    project_id = _ensure_default_project()
    study = _project_store.create_study(
        project_id=project_id,
        name=request.name,
        objective=request.objective,
    )
    spec_version = _project_store.create_spec_version(
        parameters=payload,
        source="new_study",
    )
    variant = _project_store.create_variant(
        study_id=study.study_id,
        name="baseline",
        spec_version_id=spec_version.spec_version_id,
        description="新研究初始变体",
    )
    new_spec_id = _new_id("spec")

    session.current_study_id = study.study_id
    session.current_variant_id = variant.variant_id
    session.current_spec_id = new_spec_id
    session.current_spec_payload = dict(payload)
    session.status = ResearchSessionStatus.ACTIVE

    session.turns.append(
        TurnRecord(
            turn_id=_new_id("turn"),
            intent=TurnIntent.CREATE_NEW_STUDY,
            user_message=f"[CREATE_NEW_STUDY] {request.name}",
            assistant_reply=f"已创建新研究 {study.study_id}。",
            original_spec_id=original_spec_id,
            new_spec_id=new_spec_id,
            new_study_id=study.study_id,
            created_at=_now_iso(),
        )
    )

    return CreateStudyResponse(
        session_id=session_id,
        study_id=study.study_id,
        variant_id=variant.variant_id,
        spec_version_id=spec_version.spec_version_id,
        spec_id=new_spec_id,
        original_spec_id=original_spec_id,
    )


@router.post(
    "/research-sessions/{session_id}/variants",
    response_model=CreateVariantResponse,
)
def create_variant(
    session_id: str,
    request: CreateVariantRequest,
) -> CreateVariantResponse:
    """显式从当前研究创建变体 (CREATE_VARIANT)。

    复制当前 spec payload 生成新的 SpecVersion 与 Variant，并把 session
    当前指针切换到新变体。
    """
    session = _get_session(session_id)
    if session.current_study_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "NO_CURRENT_STUDY",
                "message": "当前 session 尚无研究，无法创建变体。请先创建 session 或新研究。",
            },
        )
    original_spec_id = session.current_spec_id

    payload = request.spec_payload
    if payload is None:
        payload = dict(session.current_spec_payload)

    parent_spec_version_id: str | None = None
    if session.current_variant_id:
        parent_variant = _project_store.get_variant(session.current_variant_id)
        if parent_variant is not None:
            parent_spec_version_id = parent_variant.spec_version_id

    spec_version = _project_store.create_spec_version(
        parameters=payload,
        parent_spec_version_id=parent_spec_version_id,
        source="variant",
    )
    variant = _project_store.create_variant(
        study_id=session.current_study_id,
        name=request.name or f"variant-{spec_version.spec_version_id[-6:]}",
        spec_version_id=spec_version.spec_version_id,
        description=request.description,
        parent_variant_id=session.current_variant_id,
    )
    new_spec_id = _new_id("spec")

    session.current_variant_id = variant.variant_id
    session.current_spec_id = new_spec_id
    session.current_spec_payload = dict(payload)

    session.turns.append(
        TurnRecord(
            turn_id=_new_id("turn"),
            intent=TurnIntent.CREATE_VARIANT,
            user_message=f"[CREATE_VARIANT] {request.name}",
            assistant_reply=f"已从当前研究创建变体 {variant.variant_id}。",
            original_spec_id=original_spec_id,
            new_spec_id=new_spec_id,
            created_at=_now_iso(),
        )
    )

    return CreateVariantResponse(
        session_id=session_id,
        variant_id=variant.variant_id,
        spec_version_id=spec_version.spec_version_id,
        spec_id=new_spec_id,
        original_spec_id=original_spec_id,
    )


# ---------------------------------------------------------------------------
# turns 端点的意图处理函数
# ---------------------------------------------------------------------------


def _handle_create_variant(
    session: _ResearchSessionState,
    message: str,
) -> TurnResponse:
    """在 turns 通用入口内处理 CREATE_VARIANT 意图。"""
    if session.current_study_id is None:
        reply = "当前尚无研究方案，无法创建变体。请先描述一个研究方案。"
        turn = TurnRecord(
            turn_id=_new_id("turn"),
            intent=TurnIntent.CREATE_VARIANT,
            user_message=message,
            assistant_reply=reply,
            blocking_issues=[{
                "code": "NO_CURRENT_STUDY",
                "message": "当前 session 尚无研究，无法创建变体。",
                "severity": "blocking",
            }],
            created_at=_now_iso(),
        )
        session.turns.append(turn)
        return TurnResponse(
            session_id=session.session_id,
            turn_id=turn.turn_id,
            intent=TurnIntent.CREATE_VARIANT,
            reply=reply,
            blocking_issues=turn.blocking_issues,
            current_spec_id=session.current_spec_id,
        )

    original_spec_id = session.current_spec_id
    payload = dict(session.current_spec_payload)

    parent_spec_version_id: str | None = None
    if session.current_variant_id:
        parent_variant = _project_store.get_variant(session.current_variant_id)
        if parent_variant is not None:
            parent_spec_version_id = parent_variant.spec_version_id

    spec_version = _project_store.create_spec_version(
        parameters=payload,
        parent_spec_version_id=parent_spec_version_id,
        source="variant",
    )
    variant = _project_store.create_variant(
        study_id=session.current_study_id,
        name=f"variant-{spec_version.spec_version_id[-6:]}",
        spec_version_id=spec_version.spec_version_id,
        description="由 turns 通用入口 CREATE_VARIANT 派生",
        parent_variant_id=session.current_variant_id,
    )
    new_spec_id = _new_id("spec")
    session.current_variant_id = variant.variant_id
    session.current_spec_id = new_spec_id

    reply = f"已保留当前方案并复制生成变体 {variant.variant_id}。"
    turn = TurnRecord(
        turn_id=_new_id("turn"),
        intent=TurnIntent.CREATE_VARIANT,
        user_message=message,
        assistant_reply=reply,
        original_spec_id=original_spec_id,
        new_spec_id=new_spec_id,
        created_at=_now_iso(),
    )
    session.turns.append(turn)
    return TurnResponse(
        session_id=session.session_id,
        turn_id=turn.turn_id,
        intent=TurnIntent.CREATE_VARIANT,
        reply=reply,
        original_spec_id=original_spec_id,
        new_spec_id=new_spec_id,
        current_spec_id=session.current_spec_id,
    )


def _handle_create_new_study(
    session: _ResearchSessionState,
    message: str,
) -> TurnResponse:
    """在 turns 通用入口内处理 CREATE_NEW_STUDY 意图。"""
    original_spec_id = session.current_spec_id
    payload = dict(session.current_spec_payload) if session.current_spec_payload else {}

    project_id = _ensure_default_project()
    study = _project_store.create_study(
        project_id=project_id,
        name=f"study-{_new_id('s')[-6:]}",
        objective="由 turns 通用入口 CREATE_NEW_STUDY 创建",
    )
    # 保存当前方案为新 Study 的首变体
    saved_spec_version = _project_store.create_spec_version(
        parameters=payload,
        source="saved_current",
    )
    _project_store.create_variant(
        study_id=study.study_id,
        name="saved-current",
        spec_version_id=saved_spec_version.spec_version_id,
        description="保存的当前方案",
    )
    # 新建工作 spec（复制自当前方案）
    new_spec_version = _project_store.create_spec_version(
        parameters=payload,
        source="new_study",
    )
    new_variant = _project_store.create_variant(
        study_id=study.study_id,
        name="working-copy",
        spec_version_id=new_spec_version.spec_version_id,
        description="新建研究的工作变体",
    )
    new_spec_id = _new_id("spec")

    session.current_study_id = study.study_id
    session.current_variant_id = new_variant.variant_id
    session.current_spec_id = new_spec_id
    session.status = ResearchSessionStatus.ACTIVE

    reply = f"已保存当前方案为新研究 {study.study_id}，并新建工作 spec。"
    turn = TurnRecord(
        turn_id=_new_id("turn"),
        intent=TurnIntent.CREATE_NEW_STUDY,
        user_message=message,
        assistant_reply=reply,
        original_spec_id=original_spec_id,
        new_spec_id=new_spec_id,
        new_study_id=study.study_id,
        created_at=_now_iso(),
    )
    session.turns.append(turn)
    return TurnResponse(
        session_id=session.session_id,
        turn_id=turn.turn_id,
        intent=TurnIntent.CREATE_NEW_STUDY,
        reply=reply,
        original_spec_id=original_spec_id,
        new_spec_id=new_spec_id,
        new_study_id=study.study_id,
        current_spec_id=session.current_spec_id,
    )

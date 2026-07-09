"""Ambiguity detection for structured study intents.

The :class:`AmbiguityDetector` inspects a :class:`StudyIntent` and surfaces
missing, conflicting or ambiguous information as :class:`AmbiguityItem`
objects.  Each item is classified by severity:

* ``blocking_for_case_generation`` — must be resolved before a case can be
  compiled (Level 2).
* ``needs_confirmation`` — affects result quality but does not block the
  draft (Level 1).
* ``non_blocking_assumption`` — a safe default can be recommended (Level 0).
"""

from __future__ import annotations

from fluid_scientist.study_decomposition.models import (
    AmbiguityItem,
    ExtractedParameter,
    StudyIntent,
)


def _param_ids(params: list[ExtractedParameter]) -> set[str]:
    return {p.canonical_id for p in params}


def _has_param(params: list[ExtractedParameter], *ids: str) -> bool:
    ids_set = _param_ids(params)
    return any(i in ids_set for i in ids)


class AmbiguityDetector:
    """Detect ambiguities, conflicts and missing information in a StudyIntent."""

    def detect(self, study: StudyIntent) -> list[AmbiguityItem]:
        """Return all detected ambiguities for *study*, sorted by severity."""
        items: list[AmbiguityItem] = []
        items.extend(self._check_blocking(study))
        items.extend(self._check_needs_confirmation(study))
        items.extend(self._check_non_blocking(study))
        return items

    # ------------------------------------------------------------------ blocking
    def _check_blocking(self, study: StudyIntent) -> list[AmbiguityItem]:
        items: list[AmbiguityItem] = []
        all_params = (
            study.known_parameters
            + study.derived_parameters
            + study.assumed_parameters
            + study.unknown_required_parameters
        )

        # 1. Geometry dimensions completely unknown
        geo_type = study.geometry.get("type", "")
        has_dim = _has_param(
            all_params,
            "cylinder_diameter",
            "cylinder_d",
            "step_height",
            "diameter",
            "characteristic_length",
            "minor_axis",
            "major_axis",
            "pipe_diameter",
        )
        if not has_dim and geo_type in ("cylinder", "elliptic", "pipe", "jet", ""):
            items.append(
                AmbiguityItem(
                    field="characteristic_length",
                    issue="几何特征尺寸完全未知",
                    severity="blocking_for_case_generation",
                    reason="无法生成网格或计算 Re",
                    suggested_question=(
                        f"请确认{geo_type or '研究对象'}"
                        "的特征尺寸（如直径 D、台阶高度 H）"
                    ),
                    recommended_default=None,
                )
            )

        # 2. Heat flux ambiguity
        has_heat_flux_obs = any(
            o.category == "heat_flux" or "heat" in o.observable_id
            for o in study.observables
        )
        has_heat_flux_bc = any(
            "heat" in str(bc.get("type", "")).lower()
            or "thermal" in str(bc.get("type", "")).lower()
            for bc in study.boundary_conditions
        )
        if has_heat_flux_obs or has_heat_flux_bc:
            items.append(
                AmbiguityItem(
                    field="heat_flux_role",
                    issue="壁面热通量是作为输出结果分析，还是作为给定边界条件？",
                    severity="blocking_for_case_generation",
                    reason="热通量的角色决定是否需要求解能量方程和设置热边界条件",
                    suggested_question="壁面热通量是作为输出结果分析，还是作为给定边界条件？",
                    recommended_default=None,
                )
            )

        # 3. Moving body without amplitude/frequency
        is_moving = study.physical_models.get("moving_body", False)
        has_oscillation = _has_param(
            all_params, "oscillation_amplitude", "oscillation_frequency"
        )
        if is_moving and not has_oscillation:
            items.append(
                AmbiguityItem(
                    field="oscillation_parameters",
                    issue="运动边界缺少振荡幅值和频率",
                    severity="blocking_for_case_generation",
                    reason="动态网格或运动边界需要明确的运动参数",
                    suggested_question="请确认垂向振荡的幅值和频率",
                    recommended_default=None,
                )
            )

        # 4. Density stratification formula unknown
        has_strat = study.physical_models.get("density_stratification", False)
        has_rho_formula = any(
            "rho" in str(p.canonical_id).lower()
            and "strat" in str(p.canonical_id).lower()
            for p in all_params
        )
        if has_strat and not has_rho_formula:
            items.append(
                AmbiguityItem(
                    field="density_stratification_formula",
                    issue="密度分层函数 rho(z) 未知",
                    severity="blocking_for_case_generation",
                    reason="分层流需要明确的密度分布公式来初始化和求解",
                    suggested_question=(
                        "是否采用线性密度分层"
                        " rho(z)=rho0+alpha*z？Boussinesq 近似？"
                    ),
                    recommended_default="rho(z) = rho0 + alpha * z (Boussinesq approximation)",
                )
            )

        # 5. Fr definition unknown
        has_fr = _has_param(all_params, "froude_number", "fr")
        if has_fr:
            items.append(
                AmbiguityItem(
                    field="froude_number_definition",
                    issue="Fr 的定义不明确",
                    severity="blocking_for_case_generation",
                    reason="Fr 可以定义为 U/sqrt(gD) 或 U/(N*D) 等，不同定义对应不同物理",
                    suggested_question="Fr=0.2 是否定义为 U/(N·D)？请确认 Fr 的定义",
                    recommended_default=None,
                )
            )

        return items

    # -------------------------------------------------------- needs_confirmation
    def _check_needs_confirmation(self, study: StudyIntent) -> list[AmbiguityItem]:
        items: list[AmbiguityItem] = []
        all_params = (
            study.known_parameters
            + study.derived_parameters
            + study.assumed_parameters
            + study.unknown_required_parameters
        )

        # 1. Domain size not specified
        has_domain = _has_param(
            all_params, "domain_length", "domain_width", "domain_height"
        )
        if not has_domain:
            items.append(
                AmbiguityItem(
                    field="domain_size",
                    issue="计算域尺寸未指定",
                    severity="needs_confirmation",
                    reason="计算域大小影响结果准确性和计算量",
                    suggested_question="是否接受系统按特征长度推荐计算域尺寸？",
                    recommended_default="基于特征长度推荐 (如 20D x 10D)",
                )
            )

        # 2. Mesh resolution not specified
        has_mesh = _has_param(
            all_params, "mesh_resolution", "cell_count", "grid_size"
        )
        if not has_mesh:
            items.append(
                AmbiguityItem(
                    field="mesh_resolution",
                    issue="网格分辨率未指定",
                    severity="needs_confirmation",
                    reason="网格分辨率影响求解精度和计算成本",
                    suggested_question="是否接受系统推荐的网格分辨率？",
                    recommended_default="基于 Re 和几何推荐",
                )
            )

        # 3. Turbulence model not specified
        is_turbulent = study.physical_models.get("turbulent", False)
        has_turb_model = _has_param(all_params, "turbulence_model")
        if is_turbulent and not has_turb_model:
            items.append(
                AmbiguityItem(
                    field="turbulence_model",
                    issue="湍流模型未指定",
                    severity="needs_confirmation",
                    reason="湍流模型选择（LES/RANS/DES）影响求解精度和计算成本",
                    suggested_question="湍流模拟采用 LES、RANS、DES，还是先使用系统推荐方案？",
                    recommended_default="LES (for wake flows) or k-omega SST (RANS)",
                )
            )

        # 4. Reynolds number characteristic length unclear
        has_re = _has_param(all_params, "reynolds_number", "re")
        has_char_length = _has_param(
            all_params,
            "characteristic_length",
            "cylinder_diameter",
            "step_height",
            "pipe_diameter",
        )
        if has_re and not has_char_length:
            items.append(
                AmbiguityItem(
                    field="reynolds_characteristic_length",
                    issue="Re 的特征长度定义不明确",
                    severity="needs_confirmation",
                    reason="Re 基于不同特征长度对应不同物理条件",
                    suggested_question="Re 是基于哪个特征长度定义的（如直径 D、台阶高度 H）？",
                    recommended_default=None,
                )
            )

        # 5. Inlet condition implementation unclear
        has_fully_developed = any(
            "fully_developed" in str(ic.get("type", "")).lower()
            or "充分发展" in str(ic.get("type", ""))
            for ic in study.initial_conditions
        )
        if has_fully_developed:
            items.append(
                AmbiguityItem(
                    field="inlet_implementation",
                    issue="入口充分发展流场的实现方式不明确",
                    severity="needs_confirmation",
                    reason="充分发展入口可用解析剖面、precursor 或 mapped inlet 实现",
                    suggested_question=(
                        "入口充分发展管流希望用解析剖面、"
                        "precursor，还是 mapped inlet？"
                    ),
                    recommended_default="mapped inlet",
                )
            )

        # 6. Outlet boundary mapping unclear
        has_advective = any(
            "advective" in str(bc.get("type", "")).lower()
            or "对流" in str(bc.get("type", ""))
            for bc in study.boundary_conditions
        )
        if has_advective:
            items.append(
                AmbiguityItem(
                    field="outlet_boundary_mapping",
                    issue="对流出口边界需要映射到具体 OpenFOAM 边界条件",
                    severity="needs_confirmation",
                    reason="'对流边界'可映射为 advective、inletOutlet、waveTransmissive 等",
                    suggested_question="出口对流边界是否接受系统推荐使用 advective 边界条件？",
                    recommended_default="advective (OpenFOAM)",
                )
            )

        return items

    # -------------------------------------------------------- non_blocking
    def _check_non_blocking(self, study: StudyIntent) -> list[AmbiguityItem]:
        items: list[AmbiguityItem] = []
        all_params = (
            study.known_parameters
            + study.derived_parameters
            + study.assumed_parameters
            + study.unknown_required_parameters
        )

        if not _has_param(all_params, "solver"):
            items.append(
                AmbiguityItem(
                    field="solver",
                    issue="求解器未指定",
                    severity="non_blocking_assumption",
                    reason="可根据物理模型自动推荐求解器",
                    suggested_question=None,
                    recommended_default="pimpleFoam (transient) / simpleFoam (steady)",
                )
            )

        if not _has_param(all_params, "time_step"):
            items.append(
                AmbiguityItem(
                    field="time_step",
                    issue="时间步长未指定",
                    severity="non_blocking_assumption",
                    reason="可根据 CFL 条件自动推导",
                    suggested_question=None,
                    recommended_default="CFL < 1.0",
                )
            )

        if not _has_param(all_params, "total_time", "end_time"):
            items.append(
                AmbiguityItem(
                    field="total_simulation_time",
                    issue="总模拟时间未指定",
                    severity="non_blocking_assumption",
                    reason="可根据流动特征时间推荐",
                    suggested_question=None,
                    recommended_default="~100 flow-through times",
                )
            )

        if not _has_param(all_params, "numerics_schemes"):
            items.append(
                AmbiguityItem(
                    field="numerics_schemes",
                    issue="数值格式未指定",
                    severity="non_blocking_assumption",
                    reason="可使用系统默认格式",
                    suggested_question=None,
                    recommended_default="2nd-order upwind / central differencing",
                )
            )

        return items


__all__ = ["AmbiguityDetector"]

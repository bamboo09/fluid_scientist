"""Dependency-graph based design closure engine.

Replaces the old hard-coded defaults engine with a generic, rule-based
system that resolves parameter interdependencies: Reynolds number and
viscosity, Courant number and time step, y+ and near-wall spacing,
geometric scales, force reference values, sampling frequency, total
simulation time vs. flow-through times, etc.

The engine operates on a ``ParameterGraph`` where each node is a named
parameter with (value, unit, source, confidence).  Edges encode
formulaic dependencies.  Closure proceeds by iteratively applying rules
until no more values can be derived.  The same engine supports
incremental re-closure after user modifications (only affected nodes
are recomputed).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# ClosedParameter  -- a parameter with fully resolved value and provenance
# ---------------------------------------------------------------------------


class ClosedParameter(BaseModel):
    """A single parameter after closure."""

    name: str
    value: float | int | str | dict[str, Any] | list[Any] | None = None
    unit: str | None = None
    source: str = "SYSTEM_DERIVED"
    reason: str = ""
    confidence: float = 0.8
    modifiable: bool = True
    derivation_trace: list[str] = Field(default_factory=list)


class ClosureResult(BaseModel):
    """Result of running closure over a parameter set."""

    parameters: dict[str, ClosedParameter] = Field(default_factory=dict)
    resolved_values: dict[str, Any] = Field(default_factory=dict)
    derivation_trace: dict[str, list[str]] = Field(default_factory=dict)
    assumptions: list[dict[str, str]] = Field(default_factory=list)
    constraint_violations: list[dict[str, Any]] = Field(default_factory=list)
    recomputation_dependencies: dict[str, list[str]] = Field(default_factory=dict)
    fully_closed: bool = True
    unresolved: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# ClosureRule  -- a single deterministic derivation rule
# ---------------------------------------------------------------------------


@dataclass
class ClosureRule:
    """A rule that derives one or more parameters from prerequisites.

    ``requires`` lists parameter names that must be known.
    ``produces`` lists parameter names this rule can set.
    ``apply`` receives the current parameter dict and returns a dict of
    derived parameter values (name -> ClosedParameter).
    """

    name: str
    requires: list[str]
    produces: list[str]
    apply: Callable[[dict[str, ClosedParameter]], dict[str, ClosedParameter]]
    priority: int = 0  # lower runs first


# ---------------------------------------------------------------------------
# Built-in closure rules
# ---------------------------------------------------------------------------


def _rule_reference_velocity_default(params: dict[str, ClosedParameter]) -> dict[str, ClosedParameter]:
    result: dict[str, ClosedParameter] = {}
    if "U_ref" not in params:
        result["U_ref"] = ClosedParameter(
            name="U_ref",
            value=1.0,
            unit="m/s",
            source="ASSUMED_BASELINE",
            reason="Default reference velocity for non-dimensional setup.",
            confidence=0.7,
            derivation_trace=["U_ref default: 1 m/s for non-dimensionalization"],
        )
    if "Re" not in params:
        result["Re"] = ClosedParameter(
            name="Re",
            value=3900.0,
            source="TEMPLATE_DEFAULT",
            reason="Default Reynolds number for subcritical turbulent flow benchmark.",
            confidence=0.6,
            derivation_trace=["Re default: 3900 (subcritical cylinder flow benchmark)"],
        )
    return result


def _rule_reference_length_default(params: dict[str, ClosedParameter]) -> dict[str, ClosedParameter]:
    if "L_ref" in params or "D" in params:
        return {}
    return {
        "L_ref": ClosedParameter(
            name="L_ref",
            value=1.0,
            unit="m",
            source="ASSUMED_BASELINE",
            reason="Default reference length.",
            confidence=0.7,
            derivation_trace=["L_ref default: 1 m for non-dimensionalization"],
        )
    }


def _rule_density_default(params: dict[str, ClosedParameter]) -> dict[str, ClosedParameter]:
    if "rho" in params:
        return {}
    return {
        "rho": ClosedParameter(
            name="rho",
            value=1.0,
            unit="kg/m^3",
            source="ASSUMED_BASELINE",
            reason="Default density for incompressible non-dimensional setup.",
            confidence=0.9,
            derivation_trace=["rho default: 1 kg/m^3"],
        )
    }


def _rule_nu_from_re(params: dict[str, ClosedParameter]) -> dict[str, ClosedParameter]:
    if "nu" in params:
        return {}
    re = params.get("Re")
    u_ref = params.get("U_ref")
    l_ref = params.get("L_ref") or params.get("D")
    if not (re and u_ref and l_ref):
        return {}
    try:
        re_val = float(re.value)
        u_val = float(u_ref.value)
        l_val = float(l_ref.value)
    except (TypeError, ValueError):
        return {}
    if re_val <= 0:
        return {}
    nu_val = u_val * l_val / re_val
    return {
        "nu": ClosedParameter(
            name="nu",
            value=nu_val,
            unit="m^2/s",
            source="SYSTEM_DERIVED",
            reason="nu = U_ref * L_ref / Re",
            confidence=0.95,
            derivation_trace=[f"nu = {u_val} * {l_val} / {re_val} = {nu_val:.6e}"],
        )
    }


def _rule_re_from_nu(params: dict[str, ClosedParameter]) -> dict[str, ClosedParameter]:
    if "Re" in params:
        return {}
    nu = params.get("nu")
    u_ref = params.get("U_ref")
    l_ref = params.get("L_ref") or params.get("D")
    if not (nu and u_ref and l_ref):
        return {}
    try:
        nu_val = float(nu.value)
        u_val = float(u_ref.value)
        l_val = float(l_ref.value)
    except (TypeError, ValueError):
        return {}
    if nu_val <= 0:
        return {}
    re_val = u_val * l_val / nu_val
    return {
        "Re": ClosedParameter(
            name="Re",
            value=re_val,
            unit="",
            source="SYSTEM_DERIVED",
            reason="Re = U_ref * L_ref / nu",
            confidence=0.95,
            derivation_trace=[f"Re = {u_val} * {l_val} / {nu_val:.6e} = {re_val:.1f}"],
        )
    }


def _rule_courant_timestep(params: dict[str, ClosedParameter]) -> dict[str, ClosedParameter]:
    """Derive delta_t from Co_max, U_ref, and minimum cell size."""
    if "delta_t" in params:
        return {}
    co_max = params.get("Co_max")
    u_ref = params.get("U_ref")
    dx_min = params.get("dx_min") or params.get("delta_x_min")
    if not (co_max and u_ref and dx_min):
        # Fallback: estimate from mesh resolution and U_ref
        if u_ref:
            try:
                u_val = float(u_ref.value)
                # Assume ~100 cells across reference length
                l_ref = params.get("L_ref") or params.get("D")
                if l_ref:
                    l_val = float(l_ref.value)
                    dx_est = l_val / 100.0
                    co_target = 0.5
                    dt = co_target * dx_est / u_val
                    return {
                        "delta_t": ClosedParameter(
                            name="delta_t",
                            value=dt,
                            unit="s",
                            source="SYSTEM_DERIVED",
                            reason="delta_t estimated from Co_max=0.5, U_ref, ~100 cells/L_ref",
                            confidence=0.6,
                            derivation_trace=[f"delta_t ~= 0.5 * (L_ref/100) / U_ref = {dt:.4e}"],
                        )
                    }
            except (TypeError, ValueError):
                pass
        return {}
    try:
        co_val = float(co_max.value)
        u_val = float(u_ref.value)
        dx_val = float(dx_min.value)
    except (TypeError, ValueError):
        return {}
    if u_val <= 0 or dx_val <= 0:
        return {}
    dt = co_val * dx_val / u_val
    return {
        "delta_t": ClosedParameter(
            name="delta_t",
            value=dt,
            unit="s",
            source="SYSTEM_DERIVED",
            reason="delta_t = Co_max * dx_min / U_ref",
            confidence=0.85,
            derivation_trace=[f"delta_t = {co_val} * {dx_val} / {u_val} = {dt:.4e}"],
        )
    }


def _rule_y_plus_first_layer(params: dict[str, ClosedParameter]) -> dict[str, ClosedParameter]:
    """Derive first cell height from target y+ and flow conditions."""
    if "first_layer_height" in params or "wall_layer_thickness" in params:
        return {}
    y_plus_target = params.get("target_y_plus")
    nu = params.get("nu")
    u_ref = params.get("U_ref")
    l_ref = params.get("L_ref") or params.get("D")
    re = params.get("Re")
    if not (y_plus_target and nu and u_ref and l_ref):
        return {}
    try:
        yp_val = float(y_plus_target.value)
        nu_val = float(nu.value)
        u_val = float(u_ref.value)
        l_val = float(l_ref.value)
    except (TypeError, ValueError):
        return {}
    # Flat-plate estimate: cf ~= 0.058 / Re^0.2, u_tau = U_ref * sqrt(cf/2)
    re_val = float(re.value) if re else u_val * l_val / nu_val
    if re_val <= 0:
        return {}
    cf = 0.058 / (re_val ** 0.2)
    u_tau = u_val * math.sqrt(cf / 2.0)
    if u_tau <= 0:
        return {}
    first_h = yp_val * nu_val / u_tau
    return {
        "first_layer_height": ClosedParameter(
            name="first_layer_height",
            value=first_h,
            unit="m",
            source="SYSTEM_DERIVED",
            reason="First layer height from target y+ via flat-plate BL estimate.",
            confidence=0.65,
            derivation_trace=[
                f"Re = {re_val:.1f}",
                f"cf = 0.058 / Re^0.2 = {cf:.5f}",
                f"u_tau = U_ref * sqrt(cf/2) = {u_tau:.4f}",
                f"first_layer = y+ * nu / u_tau = {first_h:.4e}",
            ],
        ),
        "u_tau": ClosedParameter(
            name="u_tau",
            value=u_tau,
            unit="m/s",
            source="SYSTEM_DERIVED",
            reason="Friction velocity from flat-plate estimate.",
            confidence=0.6,
            derivation_trace=[],
        ),
    }


def _rule_end_time_flow_through(params: dict[str, ClosedParameter]) -> dict[str, ClosedParameter]:
    """Derive end_time from flow-through time requirement."""
    if "end_time" in params:
        return {}
    u_ref = params.get("U_ref")
    l_domain = params.get("domain_length") or params.get("L_ref") or params.get("D")
    n_flow_through = params.get("n_flow_through_times")
    if not (u_ref and l_domain):
        return {}
    try:
        u_val = float(u_ref.value)
        l_val = float(l_domain.value)
    except (TypeError, ValueError):
        return {}
    n_ft = float(n_flow_through.value) if n_flow_through else 20.0
    ft_time = l_val / u_val if u_val > 0 else 1.0
    end_time = n_ft * ft_time
    return {
        "end_time": ClosedParameter(
            name="end_time",
            value=end_time,
            unit="s",
            source="SYSTEM_DERIVED",
            reason=f"end_time = {n_ft} flow-through times",
            confidence=0.7,
            derivation_trace=[f"flow_through_time = L_domain/U_ref = {ft_time:.3f}", f"end_time = {n_ft} * FT = {end_time:.2f}"],
        ),
        "flow_through_time": ClosedParameter(
            name="flow_through_time",
            value=ft_time,
            unit="s",
            source="SYSTEM_DERIVED",
            reason="Time for a fluid particle to traverse the domain.",
            confidence=0.8,
            derivation_trace=[],
        ),
    }


def _rule_statistical_start(params: dict[str, ClosedParameter]) -> dict[str, ClosedParameter]:
    """Derive statistics start time (after initial transients)."""
    if "statistics_start_time" in params:
        return {}
    ft = params.get("flow_through_time")
    if not ft:
        return {}
    try:
        ft_val = float(ft.value)
    except (TypeError, ValueError):
        return {}
    start = 5.0 * ft_val
    return {
        "statistics_start_time": ClosedParameter(
            name="statistics_start_time",
            value=start,
            unit="s",
            source="SYSTEM_DERIVED",
            reason="Statistics start after 5 flow-through times (transient washout).",
            confidence=0.65,
            derivation_trace=[f"stats_start = 5 * FT = {start:.2f}"],
        )
    }


def _rule_sampling_frequency(params: dict[str, ClosedParameter]) -> dict[str, ClosedParameter]:
    """Derive sampling frequency from highest expected frequency."""
    if "sampling_frequency" in params:
        return {}
    u_ref = params.get("U_ref")
    l_ref = params.get("L_ref") or params.get("D")
    if not (u_ref and l_ref):
        return {}
    try:
        u_val = float(u_ref.value)
        l_val = float(l_ref.value)
    except (TypeError, ValueError):
        return {}
    # Nyquist: sample at 20x the vortex shedding frequency ~ 0.2*U/D (St~0.2)
    f_shed = 0.2 * u_val / l_val if l_val > 0 else 1.0
    f_sample = 20.0 * f_shed
    return {
        "sampling_frequency": ClosedParameter(
            name="sampling_frequency",
            value=f_sample,
            unit="Hz",
            source="SYSTEM_DERIVED",
            reason="Sampling at 20x expected shedding frequency (Nyquist-safe).",
            confidence=0.6,
            derivation_trace=[f"f_shed ~ 0.2*U/D = {f_shed:.3f} Hz", f"f_sample = 20 * f_shed = {f_sample:.2f} Hz"],
        )
    }


def _rule_solver_selection(params: dict[str, ClosedParameter]) -> dict[str, ClosedParameter]:
    if "solver" in params:
        return {}
    temporal = params.get("temporal_mode")
    compressible = params.get("compressibility")
    temporal_val = temporal.value if temporal else "transient"
    comp_val = compressible.value if compressible else "incompressible"
    # Foundation 13: foamRun -solver <module>
    if comp_val == "incompressible":
        solver_module = "incompressibleFluid"
    else:
        solver_module = "fluid"
    return {
        "solver": ClosedParameter(
            name="solver",
            value=solver_module,
            source="SYSTEM_SELECTED",
            reason=f"Solver module selected for {temporal_val} {comp_val} flow (foamRun application).",
            confidence=0.9,
            derivation_trace=[f"solver_module = {solver_module} (foamRun)"],
        ),
        "application": ClosedParameter(
            name="application",
            value="foamRun",
            source="SYSTEM_SELECTED",
            reason="Foundation 13 uses foamRun as the application.",
            confidence=0.9,
            derivation_trace=["application = foamRun"],
        ),
    }


def _rule_turbulence_model(params: dict[str, ClosedParameter]) -> dict[str, ClosedParameter]:
    if "turbulence_model" in params:
        return {}
    re = params.get("Re")
    temporal = params.get("temporal_mode")
    temporal_val = temporal.value if temporal else "transient"
    re_val = float(re.value) if re else 3900.0
    if re_val < 2000:
        model = "laminar"
        family = "laminar"
    elif temporal_val == "steady" or re_val < 10000:
        model = "kOmegaSST"
        family = "RANS"
    else:
        model = "WALE"
        family = "LES"
    yp = 1.0 if family == "LES" else 30.0
    result = {
        "turbulence_model": ClosedParameter(
            name="turbulence_model",
            value=model,
            source="SYSTEM_SELECTED",
            reason=f"Turbulence model selected for Re={re_val:.0f}, {temporal_val}.",
            confidence=0.8,
            derivation_trace=[f"turbulence_model = {model} ({family})"],
        ),
        "turbulence_family": ClosedParameter(
            name="turbulence_family",
            value=family,
            source="SYSTEM_SELECTED",
            reason="",
            confidence=0.8,
            derivation_trace=[],
        ),
    }
    if "target_y_plus" not in params:
        result["target_y_plus"] = ClosedParameter(
            name="target_y_plus",
            value=yp,
            source="SYSTEM_SELECTED",
            reason=f"y+ target for {family}.",
            confidence=0.8,
            derivation_trace=[f"target_y_plus = {yp}"],
        )
    return result


def _rule_co_default(params: dict[str, ClosedParameter]) -> dict[str, ClosedParameter]:
    if "Co_max" in params:
        return {}
    return {
        "Co_max": ClosedParameter(
            name="Co_max",
            value=0.5,
            source="SYSTEM_SELECTED",
            reason="Default Courant limit for stable transient runs.",
            confidence=0.9,
            derivation_trace=["Co_max = 0.5"],
        )
    }


def _rule_reference_area(params: dict[str, ClosedParameter]) -> dict[str, ClosedParameter]:
    if "reference_area" in params:
        return {}
    d = params.get("D") or params.get("L_ref")
    if not d:
        return {}
    try:
        d_val = float(d.value)
    except (TypeError, ValueError):
        return {}
    area = d_val * d_val
    return {
        "reference_area": ClosedParameter(
            name="reference_area",
            value=area,
            unit="m^2",
            source="SYSTEM_DERIVED",
            reason="Reference area for force coefficients = D^2.",
            confidence=0.9,
            derivation_trace=[f"A_ref = D^2 = {area:.4f}"],
        ),
        "reference_length": ClosedParameter(
            name="reference_length",
            value=d_val,
            unit="m",
            source="SYSTEM_DERIVED",
            reason="Reference length = D.",
            confidence=0.9,
            derivation_trace=[],
        ),
    }


def _rule_write_interval(params: dict[str, ClosedParameter]) -> dict[str, ClosedParameter]:
    if "write_interval" in params:
        return {}
    dt = params.get("delta_t")
    f_sample = params.get("sampling_frequency")
    if dt and f_sample:
        try:
            dt_val = float(dt.value)
            fs_val = float(f_sample.value)
            interval = max(1, int(round(1.0 / (fs_val * dt_val)))) if dt_val > 0 else 100
        except (TypeError, ValueError):
            interval = 100
    else:
        interval = 100
    return {
        "write_interval": ClosedParameter(
            name="write_interval",
            value=interval,
            source="SYSTEM_DERIVED",
            reason="Write interval set from sampling frequency.",
            confidence=0.7,
            derivation_trace=[f"write_interval = {interval} time steps"],
        )
    }


def _rule_parallel_resources(params: dict[str, ClosedParameter]) -> dict[str, ClosedParameter]:
    result = {}
    if "n_cells" not in params:
        result["n_cells"] = ClosedParameter(
            name="n_cells",
            value=500000,
            source="SYSTEM_SELECTED",
            reason="Default 500k cells for LES/DNS-grade resolution.",
            confidence=0.5,
            derivation_trace=[],
        )
    if "parallel_ranks" not in params:
        n_cells = params.get("n_cells")
        try:
            nc = int(float(n_cells.value)) if n_cells else 500000
        except (TypeError, ValueError):
            nc = 500000
        ranks = max(1, min(32, nc // 50000))
        result["parallel_ranks"] = ClosedParameter(
            name="parallel_ranks",
            value=ranks,
            source="SYSTEM_DERIVED",
            reason="~50k cells per core.",
            confidence=0.5,
            derivation_trace=[f"parallel_ranks = {ranks}"],
        )
    return result


# ---------------------------------------------------------------------------
# Build the default rule set
# ---------------------------------------------------------------------------


def _default_rules() -> list[ClosureRule]:
    return [
        ClosureRule("defaults", [], ["U_ref", "L_ref", "rho", "Co_max", "Re"], _rule_reference_velocity_default, priority=-10),
        ClosureRule("length_default", [], ["L_ref"], _rule_reference_length_default, priority=-10),
        ClosureRule("density_default", [], ["rho"], _rule_density_default, priority=-10),
        ClosureRule("co_default", [], ["Co_max"], _rule_co_default, priority=-10),
        ClosureRule("solver", [], ["solver", "application"], _rule_solver_selection, priority=5),
        ClosureRule("turbulence", [], ["turbulence_model", "turbulence_family", "target_y_plus"], _rule_turbulence_model, priority=5),
        ClosureRule("nu_from_re", ["Re", "U_ref"], ["nu"], _rule_nu_from_re, priority=2),
        ClosureRule("re_from_nu", ["nu", "U_ref", "L_ref"], ["Re"], _rule_re_from_nu, priority=2),
        ClosureRule("reference_area", ["D"], ["reference_area", "reference_length"], _rule_reference_area, priority=3),
        ClosureRule("courant_dt", ["Co_max", "U_ref"], ["delta_t"], _rule_courant_timestep, priority=10),
        ClosureRule("y_plus_layer", ["target_y_plus", "nu", "U_ref", "L_ref"], ["first_layer_height", "u_tau"], _rule_y_plus_first_layer, priority=15),
        ClosureRule("end_time", ["U_ref", "L_ref"], ["end_time", "flow_through_time"], _rule_end_time_flow_through, priority=8),
        ClosureRule("stats_start", ["flow_through_time"], ["statistics_start_time"], _rule_statistical_start, priority=12),
        ClosureRule("sampling_freq", ["U_ref", "L_ref"], ["sampling_frequency"], _rule_sampling_frequency, priority=12),
        ClosureRule("write_interval", ["delta_t", "sampling_frequency"], ["write_interval"], _rule_write_interval, priority=20),
        ClosureRule("resources", [], ["n_cells", "parallel_ranks"], _rule_parallel_resources, priority=25),
    ]


# ---------------------------------------------------------------------------
# DesignClosureEngine
# ---------------------------------------------------------------------------


class DesignClosureEngine:
    """Generic dependency-graph closure engine.

    The engine takes a set of known parameters (from user specification
    or previous design stages) and iteratively applies :class:`ClosureRule`
    instances until no more values can be derived or all target parameters
    are resolved.
    """

    def __init__(self, rules: list[ClosureRule] | None = None) -> None:
        self._rules = sorted(rules or _default_rules(), key=lambda r: r.priority)

    def close(
        self,
        known: dict[str, ClosedParameter],
        targets: list[str] | None = None,
        max_iterations: int = 50,
    ) -> ClosureResult:
        """Run closure starting from *known* parameters.

        Returns a :class:`ClosureResult` with resolved parameters,
        assumptions and any constraint violations.
        """
        params: dict[str, ClosedParameter] = dict(known)
        trace: dict[str, list[str]] = {k: list(v.derivation_trace) for k, v in known.items()}
        deps: dict[str, list[str]] = {}
        assumptions: list[dict[str, str]] = []
        violations: list[dict[str, Any]] = []

        for _ in range(max_iterations):
            made_progress = False
            for rule in self._rules:
                # Check if rule can fire
                if not all(req in params for req in rule.requires):
                    continue
                # Check if any of the produces are already derived by a
                # higher-confidence source (USER_SPECIFIED trumps all).
                can_fire = False
                for out in rule.produces:
                    existing = params.get(out)
                    if existing is None:
                        can_fire = True
                        break
                    if existing.source == "USER_SPECIFIED":
                        continue  # never override user
                    # Allow override from SYSTEM_DERIVED over ASSUMED_BASELINE
                    if existing.source in ("ASSUMED_BASELINE", "TEMPLATE_DEFAULT"):
                        can_fire = True
                        break
                if not can_fire:
                    continue
                derived = rule.apply(params)
                for name, cp in derived.items():
                    existing = params.get(name)
                    if existing and existing.source == "USER_SPECIFIED":
                        continue
                    params[name] = cp
                    trace[name] = list(cp.derivation_trace)
                    deps[name] = list(rule.requires)
                    if cp.source in ("ASSUMED_BASELINE", "TEMPLATE_DEFAULT"):
                        assumptions.append({
                            "parameter": name,
                            "value": str(cp.value),
                            "reason": cp.reason,
                        })
                    made_progress = True
            if not made_progress:
                break

        # Check for target resolution
        unresolved: list[str] = []
        if targets:
            for t in targets:
                if t not in params:
                    unresolved.append(t)

        # Constraint checks
        violations.extend(self._check_constraints(params))

        resolved_values = {k: v.value for k, v in params.items()}
        fully_closed = len(unresolved) == 0 and len(violations) == 0

        return ClosureResult(
            parameters=params,
            resolved_values=resolved_values,
            derivation_trace=trace,
            assumptions=assumptions,
            constraint_violations=violations,
            recomputation_dependencies=deps,
            fully_closed=fully_closed,
            unresolved=unresolved,
        )

    def incremental_close(
        self,
        previous: ClosureResult,
        changed_params: dict[str, ClosedParameter],
        targets: list[str] | None = None,
    ) -> ClosureResult:
        """Re-close after changing a subset of parameters.

        Invalidates and recomputes only the subgraph affected by the
        changed values, preserving unrelated parameters.
        """
        # Compute the set of parameters affected by changes (transitive closure)
        affected = set(changed_params.keys())
        for _ in range(20):
            grew = False
            for param_name, dep_list in previous.recomputation_dependencies.items():
                if param_name in affected:
                    continue
                if any(d in affected for d in dep_list):
                    affected.add(param_name)
                    grew = True
            if not grew:
                break

        # Build new known set: previous params minus affected, plus changes
        new_known: dict[str, ClosedParameter] = {}
        for name, cp in previous.parameters.items():
            if name not in affected:
                new_known[name] = cp
        new_known.update(changed_params)

        return self.close(new_known, targets=targets)

    def _check_constraints(self, params: dict[str, ClosedParameter]) -> list[dict[str, Any]]:
        violations: list[dict[str, Any]] = []
        # Re sanity
        re = params.get("Re")
        if re is not None:
            try:
                re_val = float(re.value)
                if re_val < 0:
                    violations.append({"parameter": "Re", "message": "Reynolds number must be positive.", "severity": "error"})
            except (TypeError, ValueError):
                violations.append({"parameter": "Re", "message": "Reynolds number is not numeric.", "severity": "error"})
        # Courant sanity
        co = params.get("Co_max")
        if co is not None:
            try:
                co_val = float(co.value)
                if co_val > 5.0:
                    violations.append({"parameter": "Co_max", "message": f"Co_max={co_val} is too large for stable explicit time integration.", "severity": "warning"})
            except (TypeError, ValueError):
                pass
        # y+ sanity for LES
        yp = params.get("target_y_plus")
        turb_fam = params.get("turbulence_family")
        if yp and turb_fam and turb_fam.value == "LES":
            try:
                yp_val = float(yp.value)
                if yp_val > 5.0:
                    violations.append({"parameter": "target_y_plus", "message": f"y+={yp_val} is too high for wall-resolved LES (target ~1).", "severity": "warning"})
            except (TypeError, ValueError):
                pass
        return violations


__all__ = [
    "ClosedParameter",
    "ClosureResult",
    "ClosureRule",
    "DesignClosureEngine",
]

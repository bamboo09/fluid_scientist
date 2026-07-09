"""Derivation engine for draft parameters.

After user changes are applied to an :class:`ExperimentDraft`, derived
parameters (Reynolds number, Froude number, kinematic viscosity, etc.)
must be recalculated so that the draft remains internally consistent.

Unlike the legacy workbench :class:`~fluid_scientist.workbench.derivation_engine.DerivationEngine`,
which operates on dicts with a ``"parameters"`` key, this engine works
directly with :class:`~fluid_scientist.draft.models.ExperimentDraft` and
its ``control_parameters: list[DraftParameter]``.
"""

from __future__ import annotations

import math

from fluid_scientist.draft.models import (
    DraftParameter,
    ExperimentDraft,
    ParameterSource,
)

# Canonical parameter ID aliases.  The engine recognises both common
# abbreviations (``re``, ``u``, ``nu`` ...) and full names so that
# parameter lookups are tolerant of different naming conventions used
# by the physics extractor and the user.
_PARAM_ALIASES: dict[str, set[str]] = {
    "reynolds_number": {"re", "reynolds_number", "reynolds", "reynoldsnumber"},
    "froude_number": {"fr", "froude_number", "froude", "froudenumber"},
    "velocity": {"u", "velocity", "inlet_velocity", "u_in", "mean_velocity", "u0"},
    "diameter": {"d", "diameter", "cylinder_diameter", "pipe_diameter", "char_length", "d_char"},
    "kinematic_viscosity": {"nu", "kinematic_viscosity", "nu_k"},
    "dynamic_viscosity": {"mu", "dynamic_viscosity", "mu_d"},
    "density": {"rho", "density", "rho_f"},
    "gravity": {"g", "gravity", "g_acc"},
}


def _build_lookup(
    params: list[DraftParameter],
) -> dict[str, DraftParameter]:
    """Build a case-insensitive lookup from canonical id/aliases -> param."""
    lookup: dict[str, DraftParameter] = {}
    for p in params:
        pid_lower = p.parameter_id.lower()
        lookup[pid_lower] = p
        # Also index by any known alias group that contains this id
        for _canonical, aliases in _PARAM_ALIASES.items():
            if pid_lower in aliases:
                for alias in aliases:
                    lookup.setdefault(alias, p)
    return lookup


def _get_value(lookup: dict[str, DraftParameter], *names: str) -> float | None:
    """Return the numeric value of the first parameter found by *names*."""
    for name in names:
        p = lookup.get(name.lower())
        if p is not None and p.value is not None:
            try:
                return float(p.value)
            except (TypeError, ValueError):
                continue
    return None


def _find_param(lookup: dict[str, DraftParameter], *names: str) -> DraftParameter | None:
    """Return the first parameter found by *names*."""
    for name in names:
        p = lookup.get(name.lower())
        if p is not None:
            return p
    return None


class DerivationEngine:
    """Recalculate derived parameters after changes.

    Only parameters whose :attr:`~DraftParameter.source` is
    :attr:`~ParameterSource.DERIVED` are modified.  User-provided values,
    assumptions, system recommendations and unknown-required parameters
    are left untouched.
    """

    def recalculate(self, draft: ExperimentDraft) -> list[DraftParameter]:
        """Recalculate derived parameters based on current control_parameters.

        The ``draft`` is mutated in place; derived parameter ``value``
        fields are updated when sufficient inputs are available.

        Returns:
            The list of :class:`DraftParameter` objects that were
            recalculated (useful for logging / audit).
        """
        recalculated: list[DraftParameter] = []
        lookup = _build_lookup(draft.control_parameters)

        # 1. Kinematic viscosity: nu = mu / rho  (if nu is derived)
        if self._recalc_nu(draft, lookup):
            # Rebuild lookup after nu may have changed
            lookup = _build_lookup(draft.control_parameters)
            p = _find_param(lookup, "nu", "kinematic_viscosity")
            if p is not None:
                recalculated.append(p)

        # 2. Reynolds number: Re = U * D / nu  (if Re is derived)
        if self._recalc_re(draft, lookup):
            lookup = _build_lookup(draft.control_parameters)
            p = _find_param(lookup, "re", "reynolds_number")
            if p is not None:
                recalculated.append(p)

        # 3. Froude number: Fr = U / sqrt(g * D)  (if Fr is derived)
        if self._recalc_fr(draft, lookup):
            lookup = _build_lookup(draft.control_parameters)
            p = _find_param(lookup, "fr", "froude_number")
            if p is not None:
                recalculated.append(p)

        # 4. Velocity: U = Re * nu / D  (if U is derived)
        if self._recalc_u(draft, lookup):
            lookup = _build_lookup(draft.control_parameters)
            p = _find_param(lookup, "u", "velocity", "inlet_velocity")
            if p is not None:
                recalculated.append(p)

        return recalculated

    # ------------------------------------------------------------------
    # Individual derivation rules
    # ------------------------------------------------------------------

    def _recalc_nu(
        self, draft: ExperimentDraft, lookup: dict[str, DraftParameter]
    ) -> bool:
        """Recalculate kinematic viscosity nu = mu / rho when nu is derived."""
        nu_param = _find_param(lookup, "nu", "kinematic_viscosity")
        if nu_param is None or nu_param.source != ParameterSource.DERIVED:
            return False

        mu = _get_value(lookup, "mu", "dynamic_viscosity")
        rho = _get_value(lookup, "rho", "density")
        if mu is None or rho is None or rho == 0:
            return False

        nu_param.value = mu / rho
        nu_param.source_reason = "由 mu/rho 推导"
        return True

    def _recalc_re(
        self, draft: ExperimentDraft, lookup: dict[str, DraftParameter]
    ) -> bool:
        """Recalculate Reynolds number Re = U*D/nu when Re is derived."""
        re_param = _find_param(lookup, "re", "reynolds_number")
        if re_param is None or re_param.source != ParameterSource.DERIVED:
            return False

        u = _get_value(lookup, "u", "velocity", "inlet_velocity", "mean_velocity")
        d = _get_value(lookup, "d", "diameter", "cylinder_diameter", "pipe_diameter")
        nu = _get_value(lookup, "nu", "kinematic_viscosity")
        if u is None or d is None or nu is None or nu == 0:
            return False

        re_param.value = u * d / nu
        re_param.source_reason = "由 U*D/nu 推导"
        return True

    def _recalc_fr(
        self, draft: ExperimentDraft, lookup: dict[str, DraftParameter]
    ) -> bool:
        """Recalculate Froude number Fr = U/sqrt(g*D) when Fr is derived."""
        fr_param = _find_param(lookup, "fr", "froude_number")
        if fr_param is None or fr_param.source != ParameterSource.DERIVED:
            return False

        u = _get_value(lookup, "u", "velocity", "inlet_velocity", "mean_velocity")
        g = _get_value(lookup, "g", "gravity")
        d = _get_value(lookup, "d", "diameter", "cylinder_diameter", "pipe_diameter")
        if u is None or d is None:
            return False
        if g is None:
            g = 9.81  # standard gravity default
        g_d = g * d
        if g_d <= 0:
            return False

        fr_param.value = u / math.sqrt(g_d)
        fr_param.source_reason = "由 U/sqrt(g*D) 推导"
        return True

    def _recalc_u(
        self, draft: ExperimentDraft, lookup: dict[str, DraftParameter]
    ) -> bool:
        """Recalculate velocity U = Re*nu/D when U is derived."""
        u_param = _find_param(lookup, "u", "velocity", "inlet_velocity", "mean_velocity")
        if u_param is None or u_param.source != ParameterSource.DERIVED:
            return False

        re = _get_value(lookup, "re", "reynolds_number")
        d = _get_value(lookup, "d", "diameter", "cylinder_diameter", "pipe_diameter")
        nu = _get_value(lookup, "nu", "kinematic_viscosity")
        if re is None or d is None or nu is None or d == 0:
            return False

        u_param.value = re * nu / d
        u_param.source_reason = "由 Re*nu/D 推导"
        return True


__all__ = ["DerivationEngine"]

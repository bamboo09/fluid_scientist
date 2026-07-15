"""Dimensional consistency validation for RequestedCaseIR.

This validator checks that all physical parameters in the Case IR carry
correct and consistent units.  It parses unit strings into dimensional
exponents (mass, length, time, temperature, etc.) and verifies that
derived constraints and physical relationships are dimensionally
consistent.

The dimensional analysis uses a 7-component SI base vector:
``[M, L, T, theta, I, N, J]`` corresponding to mass, length, time,
temperature, electric current, amount of substance, and luminous
intensity.
"""

from __future__ import annotations

import re
from typing import Any

from fluid_scientist.case_ir.models import ParameterValue, RequestedCaseIR
from fluid_scientist.case_ir.validators.schema_validator import ValidationIssue


# ---------------------------------------------------------------------------
# Unit parser -- converts unit strings to dimensional exponent vectors
# ---------------------------------------------------------------------------

# Dimensional exponents for common physical quantities.
# Format: [M, L, T, theta, I, N, J]
_UNIT_DIMS: dict[str, list[int]] = {
    # Base SI units
    "m": [0, 1, 0, 0, 0, 0, 0],
    "kg": [1, 0, 0, 0, 0, 0, 0],
    "s": [0, 0, 1, 0, 0, 0, 0],
    "K": [0, 0, 0, 1, 0, 0, 0],
    "A": [0, 0, 0, 0, 1, 0, 0],
    "mol": [0, 0, 0, 0, 0, 1, 0],
    "cd": [0, 0, 0, 0, 0, 0, 1],
    # Derived units
    "m/s": [0, 1, -1, 0, 0, 0, 0],
    "m/s^2": [0, 1, -2, 0, 0, 0, 0],
    "m2/s": [0, 2, -1, 0, 0, 0, 0],
    "m^2/s": [0, 2, -1, 0, 0, 0, 0],
    "m2/s2": [0, 2, -2, 0, 0, 0, 0],
    "m^2/s^2": [0, 2, -2, 0, 0, 0, 0],
    "Pa": [1, -1, -2, 0, 0, 0, 0],
    "Pa/s": [1, -1, -3, 0, 0, 0, 0],
    "N": [1, 1, -2, 0, 0, 0, 0],
    "J": [1, 2, -2, 0, 0, 0, 0],
    "W": [1, 2, -3, 0, 0, 0, 0],
    "W/m2": [1, 0, -3, 0, 0, 0, 0],
    "W/m^2": [1, 0, -3, 0, 0, 0, 0],
    "W/(m.K)": [1, 1, -3, -1, 0, 0, 0],
    "W/(m*K)": [1, 1, -3, -1, 0, 0, 0],
    "Hz": [0, 0, -1, 0, 0, 0, 0],
    "1/s": [0, 0, -1, 0, 0, 0, 0],
    "rad": [0, 0, 0, 0, 0, 0, 0],
    "deg": [0, 0, 0, 0, 0, 0, 0],
    "dimensionless": [0, 0, 0, 0, 0, 0, 0],
    "1": [0, 0, 0, 0, 0, 0, 0],
    "kg/m3": [1, -3, 0, 0, 0, 0, 0],
    "kg/m^3": [1, -3, 0, 0, 0, 0, 0],
    "kg/(m.s)": [1, -1, -1, 0, 0, 0, 0],
    "kg/(m*s)": [1, -1, -1, 0, 0, 0, 0],
    "J/(kg.K)": [0, 2, -2, -1, 0, 0, 0],
    "J/(kg*K)": [0, 2, -2, -1, 0, 0, 0],
    "m2/s2K": [0, 2, -2, -1, 0, 0, 0],
    "N/m": [1, 0, -2, 0, 0, 0, 0],
    "C": [0, 0, 1, 0, 1, 0, 0],
}

# Known parameter-name to expected-unit mappings.
_PARAM_UNIT_HINTS: dict[str, set[str]] = {
    "diameter": {"m", "mm", "cm"},
    "radius": {"m", "mm", "cm"},
    "length": {"m", "mm", "cm"},
    "L_ref": {"m", "mm", "cm"},
    "L": {"m", "mm", "cm"},
    "velocity": {"m/s", "m/s^2"},
    "U": {"m/s"},
    "U_ref": {"m/s"},
    "U_inlet": {"m/s"},
    "nu": {"m2/s", "m^2/s"},
    "kinematic_viscosity": {"m2/s", "m^2/s"},
    "viscosity": {"m2/s", "m^2/s", "kg/(m.s)", "kg/(m*s)"},
    "Re": {"dimensionless", "1"},
    "reynolds_number": {"dimensionless", "1"},
    "pressure": {"Pa", "Pa/s"},
    "p": {"Pa", "Pa/s"},
    "p_ref": {"Pa"},
    "rho": {"kg/m3", "kg/m^3"},
    "density": {"kg/m3", "kg/m^3"},
    "temperature": {"K"},
    "T": {"K"},
    "T_ref": {"K"},
    "time": {"s"},
    "frequency": {"Hz", "1/s"},
    "heat_flux": {"W/m2", "W/m^2"},
    "wall_heat_flux": {"W/m2", "W/m^2"},
    "thermal_conductivity": {"W/(m.K)", "W/(m*K)"},
    "cp": {"J/(kg.K)", "J/(kg*K)"},
    "specific_heat": {"J/(kg.K)", "J/(kg*K)"},
    "Aref": {"m2", "m^2"},
    "lRef": {"m"},
    "Cd": {"dimensionless", "1"},
    "Cl": {"dimensionless", "1"},
    "Cp": {"dimensionless", "1"},  # Pressure coefficient
    "Cf": {"dimensionless", "1"},  # Skin friction coefficient
    "y_plus": {"dimensionless", "1"},
    "Courant": {"dimensionless", "1"},
    "angle": {"rad", "deg"},
    "omega": {"rad/s", "1/s", "Hz"},
    "angular_velocity": {"rad/s", "1/s"},
    "flow_rate": {"m3/s", "m^3/s"},
    "mass_flow_rate": {"kg/s"},
}


def _parse_unit(unit_str: str) -> list[int] | None:
    """Parse a unit string into a 7-component dimensional vector.

    Returns ``None`` if the unit cannot be parsed.
    """
    if not unit_str or not unit_str.strip():
        return None

    unit_str = unit_str.strip()

    if unit_str in _UNIT_DIMS:
        return list(_UNIT_DIMS[unit_str])

    # Try normalising: remove spaces around operators
    normalised = unit_str.replace(" ", "")
    if normalised in _UNIT_DIMS:
        return list(_UNIT_DIMS[normalised])

    # Try replacing ^ with power notation
    with_caret = unit_str.replace("^2", "2").replace("^3", "3").replace("^-1", "")
    if with_caret in _UNIT_DIMS:
        return list(_UNIT_DIMS[with_caret])

    return None


def _dims_equal(a: list[int], b: list[int]) -> bool:
    """Check whether two dimensional vectors are equal."""
    if len(a) != len(b):
        return False
    return all(x == y for x, y in zip(a, b))


def _dims_multiply(a: list[int], b: list[int]) -> list[int]:
    """Multiply two dimensional vectors (add exponents)."""
    return [x + y for x, y in zip(a, b)]


def _dims_divide(a: list[int], b: list[int]) -> list[int]:
    """Divide two dimensional vectors (subtract exponents)."""
    return [x - y for x, y in zip(a, b)]


_DIMENSIONLESS = [0, 0, 0, 0, 0, 0, 0]


class DimensionalConsistencyValidator:
    """Validates dimensional consistency of the Case IR.

    Checks performed:

    - All :class:`ParameterValue` objects have a ``unit`` field that is
      either ``"dimensionless"`` or a parseable physical unit.
    - Reynolds number derived constraints (``Re = U*L/nu``) are
      dimensionally consistent.
    - Time and frequency units are inverses of each other.
    - Heat flux units (W/m^2) match temperature gradient * thermal
      conductivity.
    - Pressure and velocity scale relationship (Bernoulli: p ~ rho*U^2).
    - Kinematic viscosity units are m^2/s.
    - Parameter names with known unit hints have matching units.
    """

    def validate(self, case_ir: RequestedCaseIR) -> list[ValidationIssue]:
        """Run all dimensional consistency checks."""
        issues: list[ValidationIssue] = []

        issues.extend(self._check_parameter_units(case_ir))
        issues.extend(self._check_reynolds_dimensional(case_ir))
        issues.extend(self._check_time_frequency(case_ir))
        issues.extend(self._check_heat_flux_dimensional(case_ir))
        issues.extend(self._check_pressure_velocity(case_ir))
        issues.extend(self._check_kinematic_viscosity(case_ir))
        issues.extend(self._check_field_dimensions(case_ir))

        return issues

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_parameter_units(
        self, case_ir: RequestedCaseIR
    ) -> list[ValidationIssue]:
        """Check that all parameters have valid units."""
        issues: list[ValidationIssue] = []

        def _check_params(
            params: dict[str, ParameterValue],
            path_prefix: str,
        ) -> None:
            for pname, pval in params.items():
                path = f"{path_prefix}.{pname}"
                if not pval.unit:
                    issues.append(
                        ValidationIssue(
                            level="warning",
                            code="MISSING_UNIT",
                            path=f"{path}.unit",
                            message=(
                                f"Parameter '{pname}' has no unit specified."
                            ),
                        )
                    )
                elif pval.unit != "dimensionless":
                    dims = _parse_unit(pval.unit)
                    if dims is None:
                        issues.append(
                            ValidationIssue(
                                level="warning",
                                code="UNPARSEABLE_UNIT",
                                path=f"{path}.unit",
                                message=(
                                    f"Parameter '{pname}' has unit "
                                    f"'{pval.unit}' which could not be "
                                    f"parsed."
                                ),
                            )
                        )

                # Check against known unit hints
                if pname in _PARAM_UNIT_HINTS:
                    expected = _PARAM_UNIT_HINTS[pname]
                    if pval.unit not in expected and pval.unit != "dimensionless":
                        # Check dimensional equivalence
                        param_dims = _parse_unit(pval.unit)
                        is_compatible = False
                        for exp_unit in expected:
                            exp_dims = _parse_unit(exp_unit)
                            if param_dims and exp_dims and _dims_equal(param_dims, exp_dims):
                                is_compatible = True
                                break
                        if not is_compatible:
                            issues.append(
                                ValidationIssue(
                                    code="UNIT_MISMATCH",
                                    path=f"{path}.unit",
                                    message=(
                                        f"Parameter '{pname}' has unit "
                                        f"'{pval.unit}' but expected one of "
                                        f"{expected}."
                                    ),
                                )
                            )

        # Check entity parameters
        for i, entity in enumerate(case_ir.entities):
            _check_params(entity.parameters, f"entities[{i}].parameters")

        # Check material properties
        for i, material in enumerate(case_ir.materials):
            _check_params(material.properties, f"materials[{i}].properties")

        # Check boundary intent parameters
        for i, bc in enumerate(case_ir.boundary_intents):
            _check_params(bc.parameters, f"boundary_intents[{i}].parameters")

        # Check initial condition parameters
        for i, ic in enumerate(case_ir.initial_conditions):
            _check_params(ic.parameters, f"initial_conditions[{i}].parameters")

        # Check relation parameters
        for i, rel in enumerate(case_ir.relations):
            _check_params(rel.parameters, f"relations[{i}].parameters")

        # Check mesh intent target_y_plus
        if case_ir.mesh_intent.target_y_plus:
            pval = case_ir.mesh_intent.target_y_plus
            if pval.unit not in {"dimensionless", "1"}:
                issues.append(
                    ValidationIssue(
                        code="UNIT_MISMATCH",
                        path="mesh_intent.target_y_plus.unit",
                        message=(
                            f"target_y_plus has unit '{pval.unit}' but "
                            f"y+ is dimensionless."
                        ),
                    )
                )

        # Check numerical intent
        if case_ir.numerical_intent.max_courant_number:
            pval = case_ir.numerical_intent.max_courant_number
            if pval.unit not in {"dimensionless", "1"}:
                issues.append(
                    ValidationIssue(
                        code="UNIT_MISMATCH",
                        path="numerical_intent.max_courant_number.unit",
                        message=(
                            f"Courant number has unit '{pval.unit}' but "
                            f"Co is dimensionless."
                        ),
                    )
                )

        for pname, pval in case_ir.numerical_intent.tolerances.items():
            if pval.unit not in {"dimensionless", "1"}:
                issues.append(
                    ValidationIssue(
                        level="warning",
                        code="UNIT_MISMATCH",
                        path=f"numerical_intent.tolerances.{pname}.unit",
                        message=(
                            f"Tolerance '{pname}' has unit '{pval.unit}' "
                            f"but tolerances should be dimensionless."
                        ),
                    )
                )

        return issues

    def _check_reynolds_dimensional(
        self, case_ir: RequestedCaseIR
    ) -> list[ValidationIssue]:
        """Verify that Re = U*L/nu is dimensionally consistent.

        ``[U] = m/s``, ``[L] = m``, ``[nu] = m^2/s``.
        ``Re = U*L/nu`` -> ``(m/s * m) / (m^2/s) = m^2/s / (m^2/s) = 1``
        (dimensionless).
        """
        issues: list[ValidationIssue] = []
        for i, dc in enumerate(case_ir.derived_constraints):
            expr_lower = dc.expression.lower().replace(" ", "")
            if "re" not in expr_lower:
                continue

            # Find U, L, nu units from the Case IR
            u_unit = self._find_unit(case_ir, ["U", "U_ref", "velocity"])
            l_unit = self._find_unit(case_ir, ["L", "L_ref", "diameter", "length"])
            nu_unit = self._find_unit(case_ir, ["nu", "kinematic_viscosity", "viscosity"])

            if u_unit and l_unit and nu_unit:
                u_dims = _parse_unit(u_unit)
                l_dims = _parse_unit(l_unit)
                nu_dims = _parse_unit(nu_unit)

                if u_dims and l_dims and nu_dims:
                    # Re = U * L / nu
                    re_dims = _dims_divide(_dims_multiply(u_dims, l_dims), nu_dims)
                    if not _dims_equal(re_dims, _DIMENSIONLESS):
                        issues.append(
                            ValidationIssue(
                                code="DIMENSIONAL_INCONSISTENCY",
                                path=f"derived_constraints[{i}].expression",
                                message=(
                                    f"Derived constraint '{dc.id}': "
                                    f"Re = U*L/nu has dimensions "
                                    f"{re_dims} but Reynolds number should "
                                    f"be dimensionless. Units: U={u_unit}, "
                                    f"L={l_unit}, nu={nu_unit}."
                                ),
                            )
                        )
        return issues

    def _check_time_frequency(
        self, case_ir: RequestedCaseIR
    ) -> list[ValidationIssue]:
        """Verify that time and frequency units are inverses.

        ``[time] = s``, ``[frequency] = 1/s = Hz``.
        """
        issues: list[ValidationIssue] = []

        time_units: set[str] = set()
        freq_units: set[str] = set()

        def _collect(params: dict[str, ParameterValue]) -> None:
            for pname, pval in params.items():
                name_lower = pname.lower()
                if "time" in name_lower or pname in ("t", "deltaT", "endTime", "startTime"):
                    if pval.unit and pval.unit != "dimensionless":
                        time_units.add(pval.unit)
                if "frequency" in name_lower or pname in ("f", "freq", "Strouhal", "St"):
                    if pval.unit and pval.unit != "dimensionless":
                        freq_units.add(pval.unit)

        for entity in case_ir.entities:
            _collect(entity.parameters)
        for material in case_ir.materials:
            _collect(material.properties)
        for bc in case_ir.boundary_intents:
            _collect(bc.parameters)

        s_dims = _parse_unit("s")
        for tu in time_units:
            td = _parse_unit(tu)
            if td and s_dims:
                if not _dims_equal(td, s_dims):
                    issues.append(
                        ValidationIssue(
                            code="DIMENSIONAL_INCONSISTENCY",
                            path="parameters",
                            message=(
                                f"Time parameter has unit '{tu}' but "
                                f"expected 's' (seconds)."
                            ),
                        )
                    )

        hz_dims = _parse_unit("Hz")
        inv_s_dims = _parse_unit("1/s")
        for fu in freq_units:
            fd = _parse_unit(fu)
            if fd and hz_dims:
                if not _dims_equal(fd, hz_dims) and not _dims_equal(fd, inv_s_dims or hz_dims):
                    issues.append(
                        ValidationIssue(
                            code="DIMENSIONAL_INCONSISTENCY",
                            path="parameters",
                            message=(
                                f"Frequency parameter has unit '{fu}' but "
                                f"expected 'Hz' or '1/s'."
                            ),
                        )
                    )

        # Check that time * frequency = dimensionless
        if time_units and freq_units:
            for tu in time_units:
                td = _parse_unit(tu)
                for fu in freq_units:
                    fd = _parse_unit(fu)
                    if td and fd:
                        product = _dims_multiply(td, fd)
                        if not _dims_equal(product, _DIMENSIONLESS):
                            issues.append(
                                ValidationIssue(
                                    level="warning",
                                    code="TIME_FREQUENCY_NOT_INVERSE",
                                    path="parameters",
                                    message=(
                                        f"Time unit '{tu}' and frequency "
                                        f"unit '{fu}' are not inverses "
                                        f"(product = {product}, expected "
                                        f"dimensionless)."
                                    ),
                                )
                            )
        return issues

    def _check_heat_flux_dimensional(
        self, case_ir: RequestedCaseIR
    ) -> list[ValidationIssue]:
        """Verify heat flux = temperature_gradient * thermal_conductivity.

        ``[q''] = W/m^2``, ``[dT/dx] = K/m``, ``[k] = W/(m.K)``.
        ``q'' = k * dT/dx`` -> ``W/(m.K) * K/m = W/m^2``.
        """
        issues: list[ValidationIssue] = []

        k_unit = self._find_unit(case_ir, ["thermal_conductivity", "k"])
        q_unit = self._find_unit(case_ir, ["heat_flux", "wall_heat_flux", "q"])

        if k_unit and q_unit:
            k_dims = _parse_unit(k_unit)
            q_dims = _parse_unit(q_unit)

            if k_dims and q_dims:
                # dT/dx has dimensions [K/m] = [0, -1, 0, 1, 0, 0, 0]
                grad_dims = [0, -1, 0, 1, 0, 0, 0]
                expected_q = _dims_multiply(k_dims, grad_dims)
                if not _dims_equal(expected_q, q_dims):
                    issues.append(
                        ValidationIssue(
                            level="warning",
                            code="HEAT_FLUX_DIMENSIONAL_INCONSISTENCY",
                            path="parameters",
                            message=(
                                f"Heat flux unit '{q_unit}' is not "
                                f"consistent with thermal conductivity "
                                f"unit '{k_unit}' * temperature gradient. "
                                f"Expected {expected_q}, got {q_dims}."
                            ),
                        )
                    )
        return issues

    def _check_pressure_velocity(
        self, case_ir: RequestedCaseIR
    ) -> list[ValidationIssue]:
        """Verify pressure and velocity scale relationship.

        For incompressible flow: ``p / rho ~ U^2`` (kinematic pressure).
        So ``[p_kinematic] = m^2/s^2`` and ``[U] = m/s``.
        ``p_kinematic / U^2`` should be dimensionless.

        For compressible flow: ``[p] = Pa`` and ``[rho*U^2] = Pa``.
        ``p / (rho * U^2)`` should be dimensionless.
        """
        issues: list[ValidationIssue] = []

        p_unit = self._find_unit(case_ir, ["pressure", "p", "p_ref"])
        u_unit = self._find_unit(case_ir, ["U", "U_ref", "velocity"])
        rho_unit = self._find_unit(case_ir, ["rho", "density"])

        if p_unit and u_unit:
            p_dims = _parse_unit(p_unit)
            u_dims = _parse_unit(u_unit)

            if p_dims and u_dims:
                u_squared = _dims_multiply(u_dims, u_dims)

                if case_ir.physics.flow_regime == "incompressible":
                    # Kinematic pressure: p ~ U^2
                    if not _dims_equal(p_dims, u_squared):
                        issues.append(
                            ValidationIssue(
                                level="warning",
                                code="PRESSURE_VELOCITY_SCALE_MISMATCH",
                                path="parameters",
                                message=(
                                    f"For incompressible flow, pressure "
                                    f"unit '{p_unit}' (dims {p_dims}) "
                                    f"should match U^2 (dims {u_squared}). "
                                    f"Kinematic pressure should have "
                                    f"units m^2/s^2."
                                ),
                            )
                        )
                elif rho_unit and case_ir.physics.flow_regime == "compressible":
                    rho_dims = _parse_unit(rho_unit)
                    if rho_dims:
                        dynamic_pressure = _dims_multiply(rho_dims, u_squared)
                        if not _dims_equal(p_dims, dynamic_pressure):
                            issues.append(
                                ValidationIssue(
                                    level="warning",
                                    code="PRESSURE_VELOCITY_SCALE_MISMATCH",
                                    path="parameters",
                                    message=(
                                        f"For compressible flow, pressure "
                                        f"unit '{p_unit}' (dims {p_dims}) "
                                        f"should match rho*U^2 "
                                        f"(dims {dynamic_pressure})."
                                    ),
                                )
                            )
        return issues

    def _check_kinematic_viscosity(
        self, case_ir: RequestedCaseIR
    ) -> list[ValidationIssue]:
        """Verify kinematic viscosity has units m^2/s."""
        issues: list[ValidationIssue] = []

        nu_unit = self._find_unit(case_ir, ["nu", "kinematic_viscosity"])
        if nu_unit:
            nu_dims = _parse_unit(nu_unit)
            expected = _parse_unit("m2/s")
            if nu_dims and expected:
                if not _dims_equal(nu_dims, expected):
                    issues.append(
                        ValidationIssue(
                            code="KINEMATIC_VISCOSITY_UNIT_ERROR",
                            path="parameters",
                            message=(
                                f"Kinematic viscosity has unit '{nu_unit}' "
                                f"(dims {nu_dims}) but expected m^2/s "
                                f"(dims {expected})."
                            ),
                        )
                    )
        return issues

    def _check_field_dimensions(
        self, case_ir: RequestedCaseIR
    ) -> list[ValidationIssue]:
        """Check that field dimension strings are valid and consistent."""
        issues: list[ValidationIssue] = []
        for i, field in enumerate(case_ir.fields):
            if not field.dimensions:
                issues.append(
                    ValidationIssue(
                        code="MISSING_FIELD_DIMENSIONS",
                        path=f"fields[{i}].dimensions",
                        message=f"Field '{field.name}' has no dimensions.",
                    )
                )
                continue

            # Parse OpenFOAM dimension string: [M L T theta I N J]
            dims_str = field.dimensions.strip()
            if dims_str.startswith("[") and dims_str.endswith("]"):
                inner = dims_str[1:-1].strip()
                parts = inner.split()
                if len(parts) != 7:
                    issues.append(
                        ValidationIssue(
                            code="INVALID_FIELD_DIMENSIONS",
                            path=f"fields[{i}].dimensions",
                            message=(
                                f"Field '{field.name}' dimensions '{dims_str}' "
                                f"must have exactly 7 components, got {len(parts)}."
                            ),
                        )
                    )
                else:
                    try:
                        exponents = [int(float(p)) for p in parts]
                        # Check consistency with known fields
                        if field.name == "U":
                            # Velocity: [0 1 -1 0 0 0 0]
                            if exponents != [0, 1, -1, 0, 0, 0, 0]:
                                issues.append(
                                    ValidationIssue(
                                        code="FIELD_DIMENSION_MISMATCH",
                                        path=f"fields[{i}].dimensions",
                                        message=(
                                            f"Field 'U' has dimensions "
                                            f"{exponents} but velocity should "
                                            f"be [0 1 -1 0 0 0 0]."
                                        ),
                                    )
                                )
                        elif field.name == "p":
                            # Pressure (kinematic for incompressible):
                            # [0 2 -2 0 0 0 0]
                            # Pressure (absolute for compressible):
                            # [1 -1 -2 0 0 0 0]
                            if case_ir.physics.flow_regime == "incompressible":
                                if exponents != [0, 2, -2, 0, 0, 0, 0]:
                                    issues.append(
                                        ValidationIssue(
                                            level="warning",
                                            code="FIELD_DIMENSION_MISMATCH",
                                            path=f"fields[{i}].dimensions",
                                            message=(
                                                f"Field 'p' has dimensions "
                                                f"{exponents} but kinematic "
                                                f"pressure should be "
                                                f"[0 2 -2 0 0 0 0] for "
                                                f"incompressible flow."
                                            ),
                                        )
                                    )
                            else:
                                if exponents != [1, -1, -2, 0, 0, 0, 0]:
                                    issues.append(
                                        ValidationIssue(
                                            level="warning",
                                            code="FIELD_DIMENSION_MISMATCH",
                                            path=f"fields[{i}].dimensions",
                                            message=(
                                                f"Field 'p' has dimensions "
                                                f"{exponents} but absolute "
                                                f"pressure should be "
                                                f"[1 -1 -2 0 0 0 0] for "
                                                f"compressible flow."
                                            ),
                                        )
                                    )
                    except (ValueError, TypeError):
                        issues.append(
                            ValidationIssue(
                                code="INVALID_FIELD_DIMENSIONS",
                                path=f"fields[{i}].dimensions",
                                message=(
                                    f"Field '{field.name}' dimensions "
                                    f"'{dims_str}' contain non-numeric values."
                                ),
                            )
                        )
        return issues

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_unit(
        self,
        case_ir: RequestedCaseIR,
        names: list[str],
    ) -> str | None:
        """Find the unit of the first parameter matching any of *names*."""
        name_set = {n.lower() for n in names}

        for entity in case_ir.entities:
            for pname, pval in entity.parameters.items():
                if pname.lower() in name_set and pval.unit:
                    return pval.unit

        for material in case_ir.materials:
            for pname, pval in material.properties.items():
                if pname.lower() in name_set and pval.unit:
                    return pval.unit

        for bc in case_ir.boundary_intents:
            for pname, pval in bc.parameters.items():
                if pname.lower() in name_set and pval.unit:
                    return pval.unit

        return None


__all__ = ["DimensionalConsistencyValidator"]

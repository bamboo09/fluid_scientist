"""Derived-value computation for the dependency engine.

The :class:`DerivedValueComputer` evaluates the formulas declared in
:mod:`fluid_scientist.dependencies.rules`.  Given a *target path* and a
plain-dict representation of the simulation spec, it resolves the
required source values and returns ``(value, formula)``.

Design contract
---------------
* **No silent defaults.**  If any required input is missing the method
  returns ``(None, None)`` — never a zero, never a guessed value.
* The computer can also perform *cascading* derivation: e.g. when asked
  for the Reynolds number it will automatically derive ``nu`` from the
  material if ``kinematic_viscosity`` is not present in the spec.
"""

from __future__ import annotations

import math
from typing import Any

__all__ = ["DerivedValueComputer"]

#: Material property database for known fluids at 20 C.
#: Maps material name (lower-case) -> (density rho, kinematic viscosity nu).
_MATERIAL_PROPERTIES: dict[str, tuple[float, float]] = {
    "air": (1.225, 1.5e-5),
    "water": (998.2, 1.0e-6),
}


class DerivedValueComputer:
    """Evaluate derived spec values from source values.

    All ``compute_*`` helpers are pure functions that take already-resolved
    numeric inputs.  The :meth:`compute` dispatch method resolves inputs
    from a spec dict and delegates to the helpers.
    """

    # ------------------------------------------------------------------
    # Pure computation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def compute_reynolds(
        velocity: float, diameter: float, nu: float
    ) -> float:
        """Return the Reynolds number ``Re = U * D / nu``."""
        return velocity * diameter / nu

    @staticmethod
    def compute_viscosity_from_re(
        re: float, velocity: float, diameter: float
    ) -> float:
        """Return kinematic viscosity ``nu = U * D / Re``."""
        return velocity * diameter / re

    @staticmethod
    def compute_duration(start: float, end: float) -> float:
        """Return ``duration = end - start``."""
        return end - start

    @staticmethod
    def compute_courant(
        delta_t: float, velocity: float, cell_size: float
    ) -> float:
        """Return the Courant number ``Co = U * dt / dx``."""
        return velocity * delta_t / cell_size

    @staticmethod
    def compute_output_count(end_time: float, write_interval: float) -> int:
        """Return the expected number of saved time steps.

        ``count = floor(end_time / write_interval)``.
        """
        return int(math.floor(end_time / write_interval))

    @staticmethod
    def compute_density_from_material(
        material: str,
    ) -> tuple[float | None, float | None]:
        """Return ``(rho, nu)`` for *material*.

        Known materials (at 20 C):

        * ``"air"``   -> ``(1.225, 1.5e-5)``
        * ``"water"`` -> ``(998.2, 1.0e-6)``

        For unknown materials ``(None, None)`` is returned — no guessing.
        """
        entry = _MATERIAL_PROPERTIES.get(material.lower())
        if entry is None:
            return (None, None)
        return entry

    # ------------------------------------------------------------------
    # Path-based dispatch
    # ------------------------------------------------------------------

    def compute(
        self, target_path: str, spec_dict: dict[str, Any]
    ) -> tuple[Any, str | None]:
        """Compute the derived value at *target_path*.

        Parameters
        ----------
        target_path:
            JSON-pointer path of the value to compute, e.g.
            ``"/physics/reynolds_number"``.
        spec_dict:
            Plain-dict representation of the simulation spec.

        Returns
        -------
        ``(value, formula)`` where *formula* is the human-readable formula
        string that was used, or ``None``.  When the value cannot be
        computed (missing inputs, unknown target) returns ``(None, None)``.
        """
        if target_path == "/physics/reynolds_number":
            return self._compute_reynolds(spec_dict)
        if target_path == "/physics/dynamic_viscosity":
            return self._compute_dynamic_viscosity(spec_dict)
        if target_path == "/physics/density":
            return self._compute_density(spec_dict)
        if target_path == "/physics/kinematic_viscosity":
            return self._compute_kinematic_viscosity(spec_dict)
        if target_path == "/numerics/time/duration":
            return self._compute_duration(spec_dict)
        if target_path == "/numerics/time/expected_output_count":
            return self._compute_output_count(spec_dict)
        if target_path == "/numerics/time/courant_number":
            return self._compute_courant(spec_dict)
        # Unknown target — no computation possible.
        return (None, None)

    # ------------------------------------------------------------------
    # Internal: per-target resolvers
    # ------------------------------------------------------------------

    def _compute_reynolds(
        self, spec_dict: dict[str, Any]
    ) -> tuple[Any, str | None]:
        u = self._numeric(spec_dict, "/physics/velocity")
        d = self._numeric(spec_dict, "/physics/characteristic_length")
        nu = self._numeric(spec_dict, "/physics/kinematic_viscosity")
        # Cascading: derive nu from material if not present.
        if nu is None:
            nu = self._nu_from_material(spec_dict)
        if u is None or d is None or nu is None or nu == 0:
            return (None, None)
        return (self.compute_reynolds(u, d, nu), "Re = U * D / nu")

    def _compute_dynamic_viscosity(
        self, spec_dict: dict[str, Any]
    ) -> tuple[Any, str | None]:
        rho = self._numeric(spec_dict, "/physics/density")
        nu = self._numeric(spec_dict, "/physics/kinematic_viscosity")
        # Cascading: derive rho and nu from material if not present.
        if rho is None or nu is None:
            m_rho, m_nu = self._rho_nu_from_material(spec_dict)
            if rho is None:
                rho = m_rho
            if nu is None:
                nu = m_nu
        if rho is None or nu is None:
            return (None, None)
        return (rho * nu, "mu = rho * nu")

    def _compute_density(
        self, spec_dict: dict[str, Any]
    ) -> tuple[Any, str | None]:
        material = self._material_name(spec_dict)
        if material is None:
            return (None, None)
        rho, _nu = self.compute_density_from_material(material)
        if rho is None:
            return (None, None)
        return (rho, "rho = material_property(material, 'density')")

    def _compute_kinematic_viscosity(
        self, spec_dict: dict[str, Any]
    ) -> tuple[Any, str | None]:
        material = self._material_name(spec_dict)
        if material is None:
            return (None, None)
        _rho, nu = self.compute_density_from_material(material)
        if nu is None:
            return (None, None)
        return (nu, "nu = material_property(material, 'kinematic_viscosity')")

    def _compute_duration(
        self, spec_dict: dict[str, Any]
    ) -> tuple[Any, str | None]:
        start = self._numeric(spec_dict, "/numerics/time/start_time")
        end = self._numeric(spec_dict, "/numerics/time/end_time")
        if start is None or end is None:
            return (None, None)
        return (self.compute_duration(start, end), "duration = end_time - start_time")

    def _compute_output_count(
        self, spec_dict: dict[str, Any]
    ) -> tuple[Any, str | None]:
        end = self._numeric(spec_dict, "/numerics/time/end_time")
        wi = self._numeric(spec_dict, "/numerics/time/write_interval")
        if end is None or wi is None or wi == 0:
            return (None, None)
        return (
            self.compute_output_count(end, wi),
            "count = floor(end_time / write_interval)",
        )

    def _compute_courant(
        self, spec_dict: dict[str, Any]
    ) -> tuple[Any, str | None]:
        dt = self._numeric(spec_dict, "/numerics/time/delta_t")
        u = self._numeric(spec_dict, "/physics/velocity")
        dx = self._numeric(spec_dict, "/mesh/resolution")
        if dt is None or u is None or dx is None or dx == 0:
            return (None, None)
        return (self.compute_courant(dt, u, dx), "Co = U * delta_t / dx")

    # ------------------------------------------------------------------
    # Internal: spec-dict helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve(spec_dict: dict[str, Any], path: str) -> Any:
        """Resolve a JSON-pointer *path* inside *spec_dict*.

        Returns ``None`` if any segment is missing.
        """
        parts = [p for p in path.split("/") if p]
        current: Any = spec_dict
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current

    @classmethod
    def _numeric(
        cls, spec_dict: dict[str, Any], path: str
    ) -> float | None:
        """Extract a concrete float from the spec at *path*.

        Handles both raw numeric values and SourcedValue/Quantity dicts
        that wrap the number in a ``"value"`` key.
        """
        resolved = cls._resolve(spec_dict, path)
        if resolved is None:
            return None
        # SourcedValue / Quantity dict?
        if isinstance(resolved, dict):
            v = resolved.get("value")
            if isinstance(v, bool):  # guard: bool is a subclass of int
                return None
            if isinstance(v, int | float):
                return float(v)
            return None
        if isinstance(resolved, bool):
            return None
        if isinstance(resolved, int | float):
            return float(resolved)
        return None

    @classmethod
    def _material_name(
        cls, spec_dict: dict[str, Any]
    ) -> str | None:
        """Extract the material name string from the spec."""
        resolved = cls._resolve(spec_dict, "/physics/material")
        if resolved is None:
            return None
        if isinstance(resolved, dict):
            v = resolved.get("value")
            return str(v) if v is not None else None
        if isinstance(resolved, str):
            return resolved
        return None

    @classmethod
    def _rho_nu_from_material(
        cls, spec_dict: dict[str, Any]
    ) -> tuple[float | None, float | None]:
        """Derive ``(rho, nu)`` from the material name in the spec."""
        material = cls._material_name(spec_dict)
        if material is None:
            return (None, None)
        return cls.compute_density_from_material(material)

    @classmethod
    def _nu_from_material(
        cls, spec_dict: dict[str, Any]
    ) -> float | None:
        """Derive ``nu`` from the material name in the spec."""
        _rho, nu = cls._rho_nu_from_material(spec_dict)
        return nu

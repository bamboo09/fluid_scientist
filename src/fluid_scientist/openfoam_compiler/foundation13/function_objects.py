"""Compiler for OpenFOAM function objects derived from observations.

Each observation metric maps to one or more OpenFOAM function objects.
The function returns a list of plain ``dict`` objects (each carrying a
``"name"`` key) so that the :mod:`control_dict` module can render them
into the ``functions`` sub-dictionary.
"""

from __future__ import annotations

from fluid_scientist.study_spec.observations import ObservationDefinition

__all__ = ["compile_function_objects"]


def compile_function_objects(
    observations: ObservationDefinition,
) -> list[dict]:
    """Map observation targets / probes / post-processing to function objects.

    Deduplication rules
    -------------------
    * ``cd``, ``cl`` and ``strouhal`` all share a single ``forceCoeffs``
      object (Cd and Cl come from the same forces computation).
    * ``point_velocity`` and the explicit ``probes`` list share a single
      ``probes`` object.
    * ``vorticity`` from either a target *or* the post-processing list
      produces a single ``vorticity`` object.
    """
    fos: list[dict] = []

    has_force_coeffs = False
    has_probes = False
    has_vorticity = False
    has_wall_shear = False
    has_y_plus = False
    has_field_average = False

    for target in observations.targets:
        metric = target.metric
        params = target.parameters

        if metric in ("cd", "cl", "strouhal"):
            if not has_force_coeffs:
                fos.append(_make_force_coeffs(params))
                has_force_coeffs = True

        elif metric == "point_velocity":
            if not has_probes:
                fos.append(_make_probes(observations, params))
                has_probes = True

        elif metric == "section_mean_velocity":
            fos.append(_make_surface_field_value(params))

        elif metric == "wall_shear":
            if not has_wall_shear:
                fos.append(_make_wall_shear_stress(params))
                has_wall_shear = True

        elif metric == "y_plus":
            if not has_y_plus:
                fos.append(_make_y_plus(params))
                has_y_plus = True

        elif metric == "vorticity":
            if not has_vorticity:
                fos.append(_make_vorticity(params))
                has_vorticity = True

    # Explicit probe list — merge into a single probes object.
    if observations.probes and not has_probes:
        fos.append(_make_probes(observations, {}))
        has_probes = True

    # Post-processing list (e.g. "vorticity", "time_average").
    for pp in observations.postprocessing:
        if pp == "time_average" and not has_field_average:
            fos.append(_make_field_average())
            has_field_average = True
        elif pp == "vorticity" and not has_vorticity:
            fos.append(_make_vorticity({}))
            has_vorticity = True
        elif pp == "wall_shear" and not has_wall_shear:
            fos.append(_make_wall_shear_stress({}))
            has_wall_shear = True

    return fos


# ---------------------------------------------------------------------------
# Individual function-object builders
# ---------------------------------------------------------------------------


def _make_force_coeffs(params: dict) -> dict:
    """Build a ``forceCoeffs`` function object dict."""
    patches = params.get("patches", [])
    fo: dict = {
        "name": "forceCoeffs1",
        "type": "forceCoeffs",
        "libs": ['"libforces.so"'],
    }
    if patches:
        fo["patches"] = list(patches)
    fo["magUInf"] = params.get("magUInf", 1.0)
    fo["lRef"] = params.get("lRef", 1.0)
    fo["Aref"] = params.get("Aref", 1.0)
    fo["rhoInf"] = params.get("rhoInf", 1.0)
    fo["liftDir"] = params.get("liftDir", [0, 1, 0])
    fo["dragDir"] = params.get("dragDir", [1, 0, 0])
    fo["pitchAxis"] = params.get("pitchAxis", [0, 0, 1])
    return fo


def _make_probes(observations: ObservationDefinition, params: dict) -> dict:
    """Build a ``probes`` function object dict.

    Probe locations come from the explicit ``probes`` list and/or the
    ``probe`` / ``location`` key in *params* (used by ``point_velocity``
    and ``strouhal`` targets).
    """
    locations: list[list[float]] = []
    for probe in observations.probes:
        loc = probe.location
        locations.append(
            [float(loc.get("x", 0.0)), float(loc.get("y", 0.0)), float(loc.get("z", 0.0))]
        )
    loc_param = params.get("probe", params.get("location"))
    if isinstance(loc_param, (list, tuple)):
        locations.append([float(v) for v in loc_param])

    fo: dict = {
        "name": "probes1",
        "type": "probes",
        "libs": ['"libsampling.so"'],
        "fields": params.get("fields", ["U"]),
    }
    if locations:
        fo["probeLocations"] = locations
    return fo


def _make_surface_field_value(params: dict) -> dict:
    """Build a ``surfaceFieldValue`` function object dict."""
    fo: dict = {
        "name": "surfaceFieldValue1",
        "type": "surfaceFieldValue",
        "libs": ['"libfieldFunctionObjects.so"'],
        "operation": params.get("operation", "areaAverage"),
    }
    surface = params.get("surface", params.get("patches", []))
    if surface:
        fo["surface"] = list(surface)
    fields = params.get("fields", ["U"])
    if fields:
        fo["fields"] = list(fields)
    return fo


def _make_wall_shear_stress(params: dict) -> dict:
    """Build a ``wallShearStress`` function object dict."""
    fo: dict = {
        "name": "wallShearStress1",
        "type": "wallShearStress",
        "libs": ['"libfieldFunctionObjects.so"'],
    }
    patches = params.get("patches", [])
    if patches:
        fo["patches"] = list(patches)
    return fo


def _make_y_plus(params: dict) -> dict:
    """Build a ``yPlus`` function object dict."""
    fo: dict = {
        "name": "yPlus1",
        "type": "yPlus",
        "libs": ['"libfieldFunctionObjects.so"'],
    }
    patches = params.get("patches", [])
    if patches:
        fo["patches"] = list(patches)
    return fo


def _make_vorticity(params: dict) -> dict:
    """Build a ``vorticity`` function object dict."""
    fo: dict = {
        "name": "vorticity1",
        "type": "vorticity",
        "libs": ['"libfieldFunctionObjects.so"'],
    }
    return fo


def _make_field_average() -> dict:
    """Build a ``fieldAverage`` function object dict."""
    return {
        "name": "fieldAverage1",
        "type": "fieldAverage",
        "libs": ['"libfieldFunctionObjects.so"'],
        "fields": ["U", "p"],
    }

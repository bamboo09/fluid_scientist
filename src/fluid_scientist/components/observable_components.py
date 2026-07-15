"""Observable components for the OpenFOAM 13 component system.

A :class:`ObservableComponent` maps a scientific measurement intent
(e.g. ``"drag_coefficient"``) to a Foundation 13 OpenFOAM function
object configuration.  Each component carries the function-object type
name, the fields it requires, a parameter schema, and a template for the
``functions`` sub-dictionary in ``system/controlDict``.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from fluid_scientist.components.base_packs import _ComponentBase


class ObservableComponent(_ComponentBase):
    """A reusable observable / measurement target.

    Attributes:
        component_id: Unique identifier.
        description: Human-readable description.
        semantic_type: Scientific type used in Case IR observables.
        function_object_type: OpenFOAM function object type name
            (e.g. ``"forces"``, ``"probes"``).
        required_fields: Field names needed for this observable.
        parameters: Parameter schema.
        foundation13_config_template: Template lines for the
            ``functions`` sub-dictionary entry.
    """

    component_id: str
    description: str
    semantic_type: str = ""
    function_object_type: str = ""
    required_fields: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    foundation13_config_template: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Force-based observables
# ---------------------------------------------------------------------------

FORCES = ObservableComponent(
    component_id="obs-forces",
    description="Total forces (lift, drag) on specified patches",
    semantic_type="forces",
    function_object_type="forces",
    required_fields=["U", "p"],
    parameters={
        "patches": {"type": "list", "default": ["cylinder"], "unit": "patch_name"},
        "rho_inf": {"type": "float", "default": 1.0, "unit": "kg/m3"},
        "write_control": {"type": "string", "default": "timeStep", "unit": "dimensionless"},
        "write_interval": {"type": "int", "default": 1, "unit": "dimensionless"},
    },
    foundation13_config_template={
        "type": "forces;",
        "libs": '("libforces.so");',
        "patches": "(cylinder);",
        "rhoInf": "rhoInf [1 -3 0 0 0 0 0] 1.0;",
        "writeControl": "timeStep;",
        "writeInterval": "1;",
    },
)

FORCE_COEFFICIENTS = ObservableComponent(
    component_id="obs-force-coefficients",
    description="Force coefficients (Cd, Cl, Cm) with reference geometry",
    semantic_type="force_coefficients",
    function_object_type="forceCoeffs",
    required_fields=["U", "p"],
    parameters={
        "patches": {"type": "list", "default": ["cylinder"], "unit": "patch_name"},
        "rho_inf": {"type": "float", "default": 1.0, "unit": "kg/m3"},
        "mag_u_inf": {"type": "float", "default": 1.0, "unit": "m/s"},
        "l_ref": {"type": "float", "default": 0.01, "unit": "m"},
        "a_ref": {"type": "float", "default": 0.0001, "unit": "m2"},
        "drag_direction": {"type": "vector", "default": "(1 0 0)", "unit": "dimensionless"},
        "lift_direction": {"type": "vector", "default": "(0 1 0)", "unit": "dimensionless"},
    },
    foundation13_config_template={
        "type": "forceCoeffs;",
        "libs": '("libforces.so");',
        "patches": "(cylinder);",
        "rhoInf": "rhoInf [1 -3 0 0 0 0 0] 1.0;",
        "magUInf": "magUInf [0 1 -1 0 0 0 0] 1.0;",
        "lRef": "lRef [0 1 0 0 0 0 0] 0.01;",
        "Aref": "Aref [0 2 0 0 0 0 0] 0.0001;",
        "dragDir": "dragDir [0 0 0 0 0 0 0] (1 0 0);",
        "liftDir": "liftDir [0 0 0 0 0 0 0] (0 1 0);",
        "writeControl": "timeStep;",
        "writeInterval": "1;",
    },
)


# ---------------------------------------------------------------------------
# Field-based observables
# ---------------------------------------------------------------------------

PRESSURE_COEFFICIENT = ObservableComponent(
    component_id="obs-pressure-coefficient",
    description="Pressure coefficient Cp distribution on patch surfaces",
    semantic_type="pressure_coefficient",
    function_object_type="surfaceFieldValue",
    required_fields=["p", "U"],
    parameters={
        "patches": {"type": "list", "default": ["cylinder"], "unit": "patch_name"},
        "rho_inf": {"type": "float", "default": 1.0, "unit": "kg/m3"},
        "u_inf": {"type": "float", "default": 1.0, "unit": "m/s"},
    },
    foundation13_config_template={
        "type": "surfaceFieldValue;",
        "libs": '("libfieldFunctionObjects.so");',
        "operation": "areaAverage;",
        "fields": "(p);",
        "patches": "(cylinder);",
        "writeControl": "timeStep;",
        "writeInterval": "1;",
    },
)

WALL_SHEAR_STRESS = ObservableComponent(
    component_id="obs-wall-shear-stress",
    description="Wall shear stress vector field on wall patches",
    semantic_type="wall_shear_stress",
    function_object_type="wallShearStress",
    required_fields=["U", "nut"],
    parameters={
        "patches": {"type": "list", "default": ["wall"], "unit": "patch_name"},
    },
    foundation13_config_template={
        "type": "wallShearStress;",
        "libs": '("libfieldFunctionObjects.so");',
        "patches": "(wall);",
        "writeControl": "timeStep;",
        "writeInterval": "1;",
    },
)


# ---------------------------------------------------------------------------
# Probing and sampling
# ---------------------------------------------------------------------------

PROBES = ObservableComponent(
    component_id="obs-probes",
    description="Point probes for field values at specified locations",
    semantic_type="probes",
    function_object_type="probes",
    required_fields=["U", "p"],
    parameters={
        "fields": {"type": "list", "default": ["U", "p"], "unit": "field_name"},
        "probe_locations": {"type": "list", "default": ["(0.05 0 0)", "(0.1 0 0)"], "unit": "m"},
        "write_control": {"type": "string", "default": "timeStep", "unit": "dimensionless"},
        "write_interval": {"type": "int", "default": 1, "unit": "dimensionless"},
    },
    foundation13_config_template={
        "type": "probes;",
        "libs": '("libsampling.so");',
        "fields": "(U p);",
        "probeLocations": "( (0.05 0 0) (0.1 0 0) );",
        "writeControl": "timeStep;",
        "writeInterval": "1;",
    },
)

FIELD_AVERAGE = ObservableComponent(
    component_id="obs-field-average",
    description="Time-averaged field statistics (mean, RMS)",
    semantic_type="field_average",
    function_object_type="fieldAverage",
    required_fields=["U", "p"],
    parameters={
        "fields": {"type": "list", "default": ["U", "p"], "unit": "field_name"},
        "window": {"type": "float", "default": 1.0, "unit": "s"},
        "restart_on_restart": {"type": "bool", "default": True, "unit": "dimensionless"},
    },
    foundation13_config_template={
        "type": "fieldAverage;",
        "libs": '("libfieldFunctionObjects.so");',
        "fields": "( U { mean on; prime2Mean on; } p { mean on; prime2Mean on; } );",
        "writeControl": "timeStep;",
        "writeInterval": "10;",
    },
)

SURFACE_AVERAGE = ObservableComponent(
    component_id="obs-surface-average",
    description="Area-weighted surface average of specified fields on a patch",
    semantic_type="surface_average",
    function_object_type="surfaceFieldValue",
    required_fields=["U", "p"],
    parameters={
        "fields": {"type": "list", "default": ["p"], "unit": "field_name"},
        "patches": {"type": "list", "default": ["outlet"], "unit": "patch_name"},
        "operation": {"type": "string", "default": "areaAverage", "unit": "dimensionless"},
    },
    foundation13_config_template={
        "type": "surfaceFieldValue;",
        "libs": '("libfieldFunctionObjects.so");',
        "operation": "areaAverage;",
        "fields": "(p);",
        "patches": "(outlet);",
        "writeControl": "timeStep;",
        "writeInterval": "1;",
    },
)


# ---------------------------------------------------------------------------
# Frequency and wake observables
# ---------------------------------------------------------------------------

FREQUENCY_SPECTRUM = ObservableComponent(
    component_id="obs-frequency-spectrum",
    description="FFT frequency spectrum of a probe signal (e.g. lift coefficient)",
    semantic_type="frequency_spectrum",
    function_object_type="probes",
    required_fields=["U", "p"],
    parameters={
        "probe_locations": {"type": "list", "default": ["(0.05 0 0)"], "unit": "m"},
        "fields": {"type": "list", "default": ["U"], "unit": "field_name"},
        "sampling_frequency": {"type": "float", "default": 1000.0, "unit": "Hz"},
        "window_size": {"type": "int", "default": 1024, "unit": "dimensionless"},
    },
    foundation13_config_template={
        "type": "probes;",
        "libs": '("libsampling.so");',
        "fields": "(U);",
        "probeLocations": "( (0.05 0 0) );",
        "writeControl": "timeStep;",
        "writeInterval": "1;",
    },
)

WAKE_DEFLECTION = ObservableComponent(
    component_id="obs-wake-deflection",
    description="Wake deflection angle measured from velocity profile at downstream plane",
    semantic_type="wake_deflection",
    function_object_type="surfaceFieldValue",
    required_fields=["U"],
    parameters={
        "downstream_distance": {"type": "float", "default": 0.1, "unit": "m"},
        "probe_height": {"type": "float", "default": 0.05, "unit": "m"},
        "reference_velocity": {"type": "float", "default": 1.0, "unit": "m/s"},
    },
    foundation13_config_template={
        "type": "surfaceFieldValue;",
        "libs": '("libfieldFunctionObjects.so");',
        "operation": "areaAverage;",
        "fields": "(U);",
        "patches": "(wake_plane);",
        "writeControl": "timeStep;",
        "writeInterval": "1;",
    },
)

VORTEX_IDENTIFICATION = ObservableComponent(
    component_id="obs-vortex-identification",
    description="Q-criterion or lambda2 vortex identification field",
    semantic_type="vortex_identification",
    function_object_type="Q",
    required_fields=["U"],
    parameters={
        "method": {"type": "string", "default": "Q", "unit": "dimensionless"},
        "write_control": {"type": "string", "default": "timeStep", "unit": "dimensionless"},
        "write_interval": {"type": "int", "default": 10, "unit": "dimensionless"},
    },
    foundation13_config_template={
        "type": "Q;",
        "libs": '("libfieldFunctionObjects.so");',
        "writeControl": "timeStep;",
        "writeInterval": "10;",
    },
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

OBSERVABLE_COMPONENTS: dict[str, ObservableComponent] = {
    c.component_id: c
    for c in [
        FORCES,
        FORCE_COEFFICIENTS,
        PRESSURE_COEFFICIENT,
        WALL_SHEAR_STRESS,
        PROBES,
        FIELD_AVERAGE,
        SURFACE_AVERAGE,
        FREQUENCY_SPECTRUM,
        WAKE_DEFLECTION,
        VORTEX_IDENTIFICATION,
    ]
}


__all__ = [
    "FIELD_AVERAGE",
    "FORCE_COEFFICIENTS",
    "FORCES",
    "FREQUENCY_SPECTRUM",
    "OBSERVABLE_COMPONENTS",
    "ObservableComponent",
    "PRESSURE_COEFFICIENT",
    "PROBES",
    "SURFACE_AVERAGE",
    "VORTEX_IDENTIFICATION",
    "WAKE_DEFLECTION",
    "WALL_SHEAR_STRESS",
]

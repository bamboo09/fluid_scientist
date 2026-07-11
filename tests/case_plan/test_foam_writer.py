"""Tests for the OpenFOAM dictionary text writer."""

from __future__ import annotations

import os
import tempfile

from fluid_scientist.case_plan.foam_writer import (
    compile_to_files,
    dict_to_foam_text,
    field_to_foam_text,
)
from fluid_scientist.case_plan.compiler import NativeCaseCompiler
from fluid_scientist.case_plan.models import CasePlan


def _make_compilable_case_plan() -> CasePlan:
    """Create a CasePlan with all required parameters for compilation."""
    return CasePlan(
        case_plan_id="cp_test",
        draft_id="draft_test",
        draft_version=1,
        case_type="cylinder_cross_flow",
        solver="pimpleFoam",
        dimensions="3D",
        geometry_plan={"length": 10.0, "height": 5.0, "width": 2.0},
        mesh_plan={"cells_x": 80, "cells_y": 40, "cells_z": 10},
        boundary_condition_plan={
            "inlet": {"type": "inlet", "velocity": 1.0},
            "outlet": {"type": "outlet", "pressure": 0.0},
            "wall": {"type": "no_slip"},
            "top": {"type": "free_slip"},
            "front": {"type": "periodic"},
            "back": {"type": "periodic"},
        },
        initial_condition_plan={
            "velocity": {"value": [0.0, 0.0, 0.0]},
            "pressure": {"value": 0.0},
        },
        physical_model_plan={
            "nu": 0.01,
            "rho": 1.0,
            "turbulent": False,
        },
        numerics_plan={
            "endTime": 100,
            "deltaT": 0.01,
            "writeInterval": 100,
        },
        can_compile=True,
        blocking_reasons=[],
    )


class TestFoamWriter:
    def test_dict_to_foam_text_has_header(self) -> None:
        data = {"application": "pimpleFoam", "endTime": 100}
        text = dict_to_foam_text("controlDict", data, location="system")
        assert "FoamFile" in text
        assert "version     2.0;" in text
        assert "format      ascii;" in text
        assert 'object      controlDict;' in text
        assert 'location    "system";' in text
        assert "application" in text
        assert "pimpleFoam" in text

    def test_field_to_foam_text_has_boundary_field(self) -> None:
        data = {
            "dimensions": "[0 1 -1 0 0 0 0]",
            "internalField": {"uniform": [0.0, 0.0, 0.0]},
            "boundaryField": {
                "inlet": {"type": "fixedValue", "value": {"uniform": [1.0, 0.0, 0.0]}},
                "outlet": {"type": "zeroGradient"},
            },
        }
        text = field_to_foam_text("U", data, location="0", field_class="volVectorField")
        assert "FoamFile" in text
        assert "volVectorField" in text
        assert "dimensions" in text
        assert "internalField" in text
        assert "boundaryField" in text
        assert "inlet" in text
        assert "fixedValue" in text

    def test_compile_to_files_produces_all_required_files(self) -> None:
        compiler = NativeCaseCompiler()
        case_plan = _make_compilable_case_plan()
        compiled = compiler.compile(case_plan)
        files = compile_to_files(compiled)

        # Must produce all standard OpenFOAM files
        assert "system/controlDict" in files
        assert "system/fvSchemes" in files
        assert "system/fvSolution" in files
        assert "system/blockMeshDict" in files
        assert "constant/transportProperties" in files
        assert "constant/turbulenceProperties" in files
        assert "0/U" in files
        assert "0/p" in files

    def test_compiled_files_have_foamfile_header(self) -> None:
        compiler = NativeCaseCompiler()
        case_plan = _make_compilable_case_plan()
        compiled = compiler.compile(case_plan)
        files = compile_to_files(compiled)

        for path, content in files.items():
            assert "FoamFile" in content, f"Missing FoamFile header in {path}"
            assert "version     2.0;" in content, f"Missing version in {path}"

    def test_control_dict_has_solver_and_timestep(self) -> None:
        compiler = NativeCaseCompiler()
        case_plan = _make_compilable_case_plan()
        compiled = compiler.compile(case_plan)
        files = compile_to_files(compiled)

        control_dict = files["system/controlDict"]
        assert "pimpleFoam" in control_dict
        assert "endTime" in control_dict
        assert "deltaT" in control_dict

    def test_block_mesh_dict_has_vertices_and_blocks(self) -> None:
        compiler = NativeCaseCompiler()
        case_plan = _make_compilable_case_plan()
        compiled = compiler.compile(case_plan)
        files = compile_to_files(compiled)

        block_mesh = files["system/blockMeshDict"]
        assert "vertices" in block_mesh
        assert "blocks" in block_mesh
        assert "hex" in block_mesh
        assert "boundary" in block_mesh

    def test_transport_properties_has_viscosity(self) -> None:
        compiler = NativeCaseCompiler()
        case_plan = _make_compilable_case_plan()
        compiled = compiler.compile(case_plan)
        files = compile_to_files(compiled)

        transport = files["constant/transportProperties"]
        assert "transportModel" in transport
        assert "Newtonian" in transport
        assert "nu" in transport

    def test_turbulence_properties_laminar(self) -> None:
        compiler = NativeCaseCompiler()
        case_plan = _make_compilable_case_plan()
        compiled = compiler.compile(case_plan)
        files = compile_to_files(compiled)

        turb = files["constant/turbulenceProperties"]
        assert "laminar" in turb

    def test_velocity_field_has_boundary_conditions(self) -> None:
        compiler = NativeCaseCompiler()
        case_plan = _make_compilable_case_plan()
        compiled = compiler.compile(case_plan)
        files = compile_to_files(compiled)

        u_field = files["0/U"]
        assert "volVectorField" in u_field
        assert "internalField" in u_field
        assert "boundaryField" in u_field
        assert "inlet" in u_field
        assert "fixedValue" in u_field
        assert "wall" in u_field
        assert "noSlip" in u_field

    def test_pressure_field_has_boundary_conditions(self) -> None:
        compiler = NativeCaseCompiler()
        case_plan = _make_compilable_case_plan()
        compiled = compiler.compile(case_plan)
        files = compile_to_files(compiled)

        p_field = files["0/p"]
        assert "volScalarField" in p_field
        assert "internalField" in p_field
        assert "boundaryField" in p_field
        assert "outlet" in p_field
        assert "fixedValue" in p_field

    def test_files_can_be_written_to_disk(self) -> None:
        """Verify that the generated text files can be written to disk
        and have non-trivial content."""
        compiler = NativeCaseCompiler()
        case_plan = _make_compilable_case_plan()
        compiled = compiler.compile(case_plan)
        files = compile_to_files(compiled)

        with tempfile.TemporaryDirectory() as tmp_dir:
            for rel_path, content in files.items():
                full_path = os.path.join(tmp_dir, rel_path)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(content)
                # Verify file exists and has content
                assert os.path.getsize(full_path) > 50, f"{rel_path} too small"
                # Verify we can read it back
                with open(full_path, "r", encoding="utf-8") as f:
                    read_back = f.read()
                assert read_back == content

    def test_directory_structure_mirrors_openfoam(self) -> None:
        """Verify the file paths follow the standard OpenFOAM layout."""
        compiler = NativeCaseCompiler()
        case_plan = _make_compilable_case_plan()
        compiled = compiler.compile(case_plan)
        files = compile_to_files(compiled)

        for path in files:
            parts = path.split("/")
            assert len(parts) == 2, f"Unexpected path structure: {path}"
            section = parts[0]
            assert section in ("system", "constant", "0"), f"Unexpected section: {section}"

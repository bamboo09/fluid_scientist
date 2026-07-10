"""Real OpenFOAM case writer.

Serializes an in-memory OpenFOAM case dict (as produced by
NativeCaseCompiler) into an actual directory layout on disk, with
proper FoamFile headers, correct OpenFOAM dictionary syntax, and a
manifest.json describing what was generated.

This is the critical bridge between "structured JSON design" and
"runnable OpenFOAM case".  The writer does NOT mock or skip any step.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Foam dictionary serialization
# ---------------------------------------------------------------------------


def _foam_header(class_name: str, object_name: str, location: str = "") -> str:
    """Return a standard OpenFOAM FoamFile header block."""
    loc_str = f'location "{location}";' if location else 'location "";'
    return (
        "FoamFile\n"
        "{\n"
        "    version     2.0;\n"
        "    format      ascii;\n"
        "    class       " + class_name + ";\n"
        "    " + loc_str + "\n"
        "    object      " + object_name + ";\n"
        "}\n"
        "// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //\n\n"
    )


def _serialize_value(value: Any, indent: int = 4) -> str:
    """Serialize a Python value to OpenFOAM syntax."""
    pad = " " * indent
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        if isinstance(value, float):
            return _format_float(value)
        return str(value)
    if isinstance(value, str):
        # Quoted strings (libs, patch names in certain contexts) are passed with quotes
        if value.startswith('"') and value.endswith('"'):
            return value
        if value.startswith("[") and value.endswith("]"):
            return value  # dimension set
        return value
    if isinstance(value, list):
        # Check if uniform vector
        if len(value) == 3 and all(isinstance(v, (int, float)) for v in value):
            return f"uniform ({_format_float(value[0])} {_format_float(value[1])} {_format_float(value[2])})"
        if len(value) > 0 and all(isinstance(v, (int, float)) for v in value):
            return "( " + " ".join(_format_float(v) for v in value) + " )"
        # List of strings (face list, patch names)
        items = " ".join(str(v) for v in value)
        return f"( {items} )"
    if isinstance(value, dict):
        return _serialize_dict(value, indent=indent)
    return str(value)


def _format_float(v: float | int) -> str:
    if isinstance(v, int):
        return str(v)
    if v == 0.0:
        return "0"
    s = f"{v:.6g}"
    return s


def _serialize_dict(d: dict, indent: int = 4) -> str:
    """Serialize a dict as an OpenFOAM sub-dictionary block."""
    pad = " " * indent
    lines: list[str] = []
    for key, val in d.items():
        if isinstance(val, dict):
            if key in ("vertices", "blocks", "boundary", "faces"):
                # blockMeshDict structures
                lines.append(f"{pad}{key}")
                lines.append(f"{pad}(")
                lines.append(_serialize_blockmesh_list(val, key, indent + 4))
                lines.append(f"{pad});")
            elif key == "functions":
                lines.append(f"{pad}functions")
                lines.append(f"{pad}{{")
                for fo_name, fo_val in val.items():
                    lines.append(_serialize_function_object(fo_name, fo_val, indent + 4))
                lines.append(f"{pad}}}")
            elif key in ("fields", "relaxationFactors"):
                lines.append(f"{pad}{key}")
                lines.append(f"{pad}{{")
                for k2, v2 in val.items():
                    if isinstance(v2, dict):
                        lines.append(f"{pad}    {k2}")
                        lines.append(f"{pad}    {{")
                        for k3, v3 in v2.items():
                            lines.append(f"{pad}        {k3}  {_serialize_value(v3, indent+8)};")
                        lines.append(f"{pad}    }}")
                    else:
                        lines.append(f"{pad}    {k2}  {_serialize_value(v2, indent+4)};")
                lines.append(f"{pad}}}")
            else:
                lines.append(f"{pad}{key}")
                lines.append(f"{pad}{{")
                for subkey, subval in val.items():
                    if isinstance(subval, dict):
                        lines.append(f"{pad}    {subkey}")
                        lines.append(f"{pad}    {{")
                        for k3, v3 in subval.items():
                            lines.append(f"{pad}        {k3}  {_serialize_value(v3, indent+8)};")
                        lines.append(f"{pad}    }}")
                    elif isinstance(subval, list) and subval and isinstance(subval[0], dict):
                        # e.g. list of field dicts
                        lines.append(f"{pad}    {subkey}")
                        lines.append(f"{pad}    (")
                        for item in subval:
                            lines.append(f"{pad}        {{")
                            for k3, v3 in item.items():
                                lines.append(f"{pad}            {k3}  {_serialize_value(v3, indent+12)};")
                            lines.append(f"{pad}        }}")
                        lines.append(f"{pad}    );")
                    else:
                        lines.append(f"{pad}    {subkey}  {_serialize_value(subval, indent+4)};")
                lines.append(f"{pad}}}")
        elif isinstance(val, list) and val and isinstance(val[0], dict):
            lines.append(f"{pad}{key}")
            lines.append(f"{pad}(")
            for item in val:
                lines.append(f"{pad}    {{")
                for k2, v2 in item.items():
                    lines.append(f"{pad}        {k2}  {_serialize_value(v2, indent+8)};")
                lines.append(f"{pad}    }}")
            lines.append(f"{pad});")
        else:
            lines.append(f"{pad}{key}  {_serialize_value(val, indent)};")
    return "\n".join(lines)


def _serialize_blockmesh_list(val: dict, key: str, indent: int) -> str:
    """Special serialization for blockMesh vertices/blocks/boundary."""
    pad = " " * indent
    lines: list[str] = []
    if key == "vertices":
        for v in val.get("vertices", []):
            lines.append(f"{pad}({ ' '.join(_format_float(c) for c in v) })")
        return "\n".join(lines)
    if key == "blocks":
        for b in val.get("blocks", []):
            hex_str = "hex " + "( " + " ".join(str(i) for i in b.get("hex", [])) + " )"
            cells = "( " + " ".join(str(c) for c in b.get("cells", [])) + " )"
            grading = b.get("grading", "simpleGrading")
            ratios = "( " + " ".join(str(r) for r in b.get("ratios", [1,1,1])) + " )"
            lines.append(f"{pad}{hex_str}  {cells}  {grading}  {ratios}")
        return "\n".join(lines)
    if key == "boundary":
        for name, bdata in val.get("boundary", {}).items():
            lines.append(f"{pad}{name}")
            lines.append(f"{pad}{{")
            lines.append(f"{pad}    type  {bdata.get('type', 'patch')};")
            faces = bdata.get("faces", [[]])[0] if bdata.get("faces") else []
            lines.append(f"{pad}    faces")
            lines.append(f"{pad}    (")
            if faces:
                lines.append(f"{pad}        ( {' '.join(str(f) for f in faces)} )")
            lines.append(f"{pad}    );")
            lines.append(f"{pad}}}")
        return "\n".join(lines)
    return ""


def _serialize_function_object(name: str, fo: dict, indent: int) -> str:
    """Serialize a single function object entry."""
    pad = " " * indent
    lines: list[str] = [f"{pad}{name}", f"{pad}{{"]
    lines.append(f"{pad}    type  {fo.get('type', 'residuals')};")
    for key, val in fo.items():
        if key == "type":
            continue
        if key == "libs":
            if val:
                lines.append(f"{pad}    libs  ( {' '.join(str(v) for v in val)} );")
        elif key == "patches":
            if val:
                lines.append(f"{pad}    patches  ( {' '.join(str(v) for v in val)} );")
        elif key == "fields":
            if isinstance(val, list) and val and isinstance(val[0], dict):
                # fieldAverage-style: list of sub-dicts
                lines.append(f"{pad}    fields")
                lines.append(f"{pad}    (")
                for item in val:
                    lines.append(f"{pad}        {item.get('field', 'U')}")
                    lines.append(f"{pad}        {{")
                    for k2, v2 in item.items():
                        if k2 == "field":
                            continue
                        lines.append(f"{pad}            {k2}  {_serialize_value(v2, indent+12)};")
                    lines.append(f"{pad}        }}")
                lines.append(f"{pad}    );")
            elif val:
                lines.append(f"{pad}    fields  ( {' '.join(str(v) for v in val)} );")
        elif isinstance(val, dict):
            lines.append(f"{pad}    {key}")
            lines.append(f"{pad}    {{")
            for subkey, subval in val.items():
                if isinstance(subval, dict):
                    lines.append(f"{pad}        {subkey}")
                    lines.append(f"{pad}        {{")
                    for k3, v3 in subval.items():
                        lines.append(f"{pad}            {k3}  {_serialize_value(v3, indent+12)};")
                    lines.append(f"{pad}        }}")
                elif isinstance(subval, list) and subval and isinstance(subval[0], dict):
                    lines.append(f"{pad}        {subkey}")
                    lines.append(f"{pad}        (")
                    for item in subval:
                        lines.append(f"{pad}            {{")
                        for k3, v3 in item.items():
                            lines.append(f"{pad}                {k3}  {_serialize_value(v3, indent+16)};")
                        lines.append(f"{pad}            }}")
                    lines.append(f"{pad}        );")
                else:
                    lines.append(f"{pad}        {subkey}  {_serialize_value(subval, indent+8)};")
            lines.append(f"{pad}    }}")
        elif isinstance(val, list) and val and isinstance(val[0], list):
            # list of vectors (e.g. probeLocations)
            lines.append(f"{pad}    {key}")
            lines.append(f"{pad}    (")
            for vec in val:
                lines.append(f"{pad}        ( {' '.join(_format_float(c) for c in vec)} )")
            lines.append(f"{pad}    );")
        elif isinstance(val, list) and len(val) == 3 and all(isinstance(v, (int, float)) for v in val):
            lines.append(f"{pad}    {key}  ( {' '.join(_format_float(v) for v in val)} );")
        else:
            lines.append(f"{pad}    {key}  {_serialize_value(val, indent+4)};")
    lines.append(f"{pad}}}")
    return "\n".join(lines)


def _serialize_boundary_field(boundary_field: dict, indent: int = 4) -> str:
    """Serialize boundaryField for 0/U and 0/p."""
    pad = " " * indent
    lines: list[str] = []
    for patch, bc in boundary_field.items():
        lines.append(f"{pad}{patch}")
        lines.append(f"{pad}{{")
        lines.append(f"{pad}    type  {bc.get('type', 'zeroGradient')};")
        if "value" in bc:
            val = bc["value"]
            if isinstance(val, dict) and "uniform" in val:
                uv = val["uniform"]
                if isinstance(uv, list):
                    lines.append(f"{pad}    value  uniform ({ ' '.join(_format_float(c) for c in uv) });")
                else:
                    lines.append(f"{pad}    value  uniform {_format_float(uv)};")
            else:
                lines.append(f"{pad}    value  {_serialize_value(val, indent+4)};")
        for key, val in bc.items():
            if key in ("type", "value"):
                continue
            lines.append(f"{pad}    {key}  {_serialize_value(val, indent+4)};")
        lines.append(f"{pad}}}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Case manifest
# ---------------------------------------------------------------------------


class CaseManifest(BaseModel):
    session_id: str
    study_id: str = ""
    draft_id: str = ""
    draft_version: int = 1
    compiler_version: str = "v5-compile-ready-1.0"
    capability_versions: dict[str, str] = Field(default_factory=dict)
    openfoam_version: str = "openfoam13"
    generated_at: str = ""
    generated_files: list[str] = Field(default_factory=list)
    validation_results: dict[str, Any] = Field(default_factory=dict)
    assumptions: list[dict[str, Any]] = Field(default_factory=list)
    input_hashes: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# OpenFOAMCaseWriter
# ---------------------------------------------------------------------------


class OpenFOAMCaseWriter:
    """Write an in-memory case dict to a real OpenFOAM directory tree."""

    def write(
        self,
        case_dict: dict[str, Any],
        output_dir: str | Path,
        session_id: str = "",
        draft_id: str = "",
        draft_version: int = 1,
        assumptions: list[dict] | None = None,
    ) -> CaseManifest:
        """Write the case to disk and return a manifest."""
        out = Path(output_dir)
        # Clean directory if it exists (except scripts/ and postProcessing/)
        if out.exists():
            for item in ["0", "constant", "system", "manifest.json"]:
                target = out / item
                if target.exists():
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
        for sub in ["0", "constant", "system", "postProcessing", "scripts"]:
            (out / sub).mkdir(parents=True, exist_ok=True)

        generated_files: list[str] = []

        # --- write system/ files ---
        sys_dict = case_dict.get("system", {})
        for fname, content in sys_dict.items():
            fpath = out / "system" / fname
            self._write_system_file(fpath, fname, content)
            generated_files.append(f"system/{fname}")

        # --- write constant/ files ---
        const_dict = case_dict.get("constant", {})
        for fname, content in const_dict.items():
            fpath = out / "constant" / fname
            self._write_constant_file(fpath, fname, content)
            generated_files.append(f"constant/{fname}")

        # --- write 0/ files ---
        zero_dict = case_dict.get("0", {})
        for fname, content in zero_dict.items():
            fpath = out / "0" / fname
            self._write_field_file(fpath, fname, content)
            generated_files.append(f"0/{fname}")

        # --- write manifest ---
        manifest = CaseManifest(
            session_id=session_id,
            draft_id=draft_id,
            draft_version=draft_version,
            generated_at=datetime.utcnow().isoformat(),
            generated_files=generated_files,
            assumptions=assumptions or [],
        )
        with open(out / "manifest.json", "w", encoding="utf-8") as f:
            f.write(manifest.model_dump_json(indent=2))
        generated_files.append("manifest.json")

        return manifest

    def _write_system_file(self, path: Path, name: str, content: dict) -> None:
        header = _foam_header("dictionary", name, "system")
        body = self._serialize_system_content(name, content)
        path.write_text(header + body + "\n// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //\n", encoding="utf-8")

    def _write_constant_file(self, path: Path, name: str, content: dict) -> None:
        header = _foam_header("dictionary", name, "constant")
        body = _serialize_dict(content, indent=4)
        path.write_text(header + body + "\n// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //\n", encoding="utf-8")

    def _write_field_file(self, path: Path, name: str, content: dict) -> None:
        class_name = "volVectorField" if name == "U" else "volScalarField"
        header = _foam_header(class_name, name, "0")
        dims = content.get("dimensions", "[0 0 0 0 0 0 0]")
        internal = content.get("internalField", {"uniform": [0, 0, 0]})
        boundary = content.get("boundaryField", {})

        lines: list[str] = []
        lines.append(f"dimensions      {dims};")
        lines.append("")
        if isinstance(internal, dict) and "uniform" in internal:
            uv = internal["uniform"]
            if isinstance(uv, list):
                lines.append(f"internalField   uniform ({ ' '.join(_format_float(c) for c in uv) });")
            else:
                lines.append(f"internalField   uniform {_format_float(uv)};")
        else:
            lines.append(f"internalField   {_serialize_value(internal)};")
        lines.append("")
        lines.append("boundaryField")
        lines.append("{")
        lines.append(_serialize_boundary_field(boundary, indent=4))
        lines.append("}")
        lines.append("")

        body = "\n".join(lines)
        path.write_text(header + body + "// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //\n", encoding="utf-8")

    def _serialize_system_content(self, name: str, content: dict) -> str:
        if name == "blockMeshDict":
            return self._serialize_blockmeshdict(content)
        if name in ("controlDict", "fvSchemes", "fvSolution"):
            return _serialize_dict(content, indent=4)
        return _serialize_dict(content, indent=4)

    def _serialize_blockmeshdict(self, content: dict) -> str:
        lines: list[str] = []
        # convertToMeters
        lines.append(f"convertToMeters {content.get('convertToMeters', 1)};")
        lines.append("")
        # vertices
        lines.append("vertices")
        lines.append("(")
        for v in content.get("vertices", []):
            lines.append(f"    ({ ' '.join(_format_float(c) for c in v) })")
        lines.append(");")
        lines.append("")
        # blocks
        lines.append("blocks")
        lines.append("(")
        for b in content.get("blocks", []):
            hex_str = "hex ( " + " ".join(str(i) for i in b.get("hex", [])) + " )"
            cells = "( " + " ".join(str(c) for c in b.get("cells", [])) + " )"
            grading = b.get("grading", "simpleGrading")
            ratios = "( " + " ".join(str(r) for r in b.get("ratios", [1,1,1])) + " )"
            lines.append(f"    {hex_str}  {cells}  {grading}  {ratios}")
        lines.append(");")
        lines.append("")
        # edges (empty for pure hex blocks)
        lines.append("edges")
        lines.append("(")
        lines.append(");")
        lines.append("")
        # boundary
        lines.append("boundary")
        lines.append("(")
        for bname, bdata in content.get("boundary", {}).items():
            lines.append(f"    {bname}")
            lines.append("    {")
            lines.append(f"        type  {bdata.get('type', 'patch')};")
            faces = bdata.get("faces", [])
            lines.append("        faces")
            lines.append("        (")
            for face in faces:
                lines.append(f"            ( {' '.join(str(f) for f in face)} )")
            lines.append("        );")
            lines.append("    }")
        lines.append(");")
        lines.append("")
        # mergePatchPairs
        lines.append("mergePatchPairs")
        lines.append("(")
        lines.append(");")
        lines.append("")
        return "\n".join(lines)


__all__ = ["CaseManifest", "OpenFOAMCaseWriter"]

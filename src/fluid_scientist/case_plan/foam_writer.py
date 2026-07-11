"""Convert Python dicts to valid OpenFOAM dictionary text.

OpenFOAM uses a C-like dictionary format with a ``FoamFile`` header.
This module provides utilities to serialise the dict output of
:class:`NativeCaseCompiler` into proper OpenFOAM text files that can
be read by OpenFOAM utilities (``blockMesh``, ``checkMesh``, solvers).

Example output::

    FoamFile
    {
        version     2.0;
        format      ascii;
        class       dictionary;
        object      controlDict;
    }
    application     pimpleFoam;
    startFrom       latestTime;
    ...
"""

from __future__ import annotations

from typing import Any


def _foam_header(
    object_name: str,
    *,
    field_class: str = "dictionary",
    location: str = "",
) -> str:
    """Generate the standard FoamFile header."""
    location_line = f'    location    "{location}";\n' if location else ""
    return (
        "FoamFile\n"
        "{\n"
        "    version     2.0;\n"
        "    format      ascii;\n"
        f"    class       {field_class};\n"
        f"{location_line}"
        f"    object      {object_name};\n"
        "}\n"
    )


def _format_value(value: Any) -> str:
    """Format a Python value as an OpenFOAM dictionary token."""
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value == int(value) and abs(value) < 1e15:
            return f"{value:.1f}"
        return str(value)
    if isinstance(value, str):
        # Quote strings that contain spaces or special chars, or that are
        # known OpenFOAM string values (e.g. "latestTime").
        if value.startswith('"') or _needs_quoting(value):
            return value if value.startswith('"') else f'"{value}"'
        return value
    if isinstance(value, list):
        return f"( {' '.join(_format_value(v) for v in value)} )"
    if isinstance(value, dict):
        # Check for "uniform" shorthand: {"uniform": [0,0,0]} -> uniform (0 0 0)
        if len(value) == 1 and "uniform" in value:
            return f"uniform {_format_value(value['uniform'])}"
        # Otherwise it's a sub-dictionary
        return _format_dict(value)
    return str(value)


def _needs_quoting(s: str) -> bool:
    """Check if a string needs to be quoted in OpenFOAM syntax."""
    if not s:
        return True
    # Strings that look like identifiers don't need quoting
    if s.replace("-", "").replace("_", "").isalnum():
        return False
    return any(c in s for c in " \t(){};")


def _format_dict(d: dict[str, Any], indent: int = 0) -> str:
    """Format a dict as an OpenFOAM sub-dictionary block."""
    pad = "    " * indent
    lines: list[str] = []
    for key, value in d.items():
        if isinstance(value, dict):
            # Check for uniform shorthand
            if len(value) == 1 and "uniform" in value:
                lines.append(f"{pad}{key:<20s} uniform {_format_value(value['uniform'])};")
            else:
                lines.append(f"{pad}{key}")
                lines.append(f"{pad}{{")
                lines.append(_format_dict(value, indent + 1))
                lines.append(f"{pad}}}")
        elif isinstance(value, list):
            lines.append(f"{pad}{key:<20s} {_format_value(value)};")
        else:
            formatted = _format_value(value)
            lines.append(f"{pad}{key:<20s} {formatted};")
    return "\n".join(lines)


def dict_to_foam_text(
    object_name: str,
    data: dict[str, Any],
    *,
    field_class: str = "dictionary",
    location: str = "",
) -> str:
    """Convert a dict to a complete OpenFOAM dictionary file text.

    Args:
        object_name: The ``object`` field in the FoamFile header
            (e.g. ``"controlDict"``).
        data: The dictionary content as a Python dict.
        field_class: The ``class`` field in the header (e.g.
            ``"volVectorField"`` for field files).
        location: The ``location`` field in the header (e.g.
            ``"0"`` or ``"system"``).

    Returns:
        A string containing the complete OpenFOAM dictionary file,
        including the FoamFile header.
    """
    header = _foam_header(
        object_name, field_class=field_class, location=location
    )
    body = _format_dict(data, indent=0)
    return header + "\n" + body + "\n"


def field_to_foam_text(
    object_name: str,
    data: dict[str, Any],
    location: str,
    field_class: str,
) -> str:
    """Convert a field dict (0/U, 0/p) to OpenFOAM text.

    Field files have a specific structure with ``internalField`` and
    ``boundaryField`` sections.
    """
    header = _foam_header(
        object_name, field_class=field_class, location=location
    )
    lines: list[str] = [header]

    # Dimensions
    dims = data.get("dimensions", "[0 0 0 0 0 0 0]")
    lines.append(f"dimensions        {dims};")
    lines.append("")

    # Internal field
    internal = data.get("internalField", {"uniform": 0})
    if isinstance(internal, dict) and "uniform" in internal:
        lines.append(f"internalField     uniform {_format_value(internal['uniform'])};")
    else:
        lines.append(f"internalField     {_format_value(internal)};")
    lines.append("")

    # Boundary field
    boundary = data.get("boundaryField", {})
    lines.append("boundaryField")
    lines.append("{")
    for patch_name, patch_bc in boundary.items():
        lines.append(f"    {patch_name}")
        lines.append("    {")
        for bc_key, bc_val in patch_bc.items():
            if isinstance(bc_val, dict):
                if "uniform" in bc_val and len(bc_val) == 1:
                    lines.append(
                        f"        {bc_key:<16s} uniform {_format_value(bc_val['uniform'])};"
                    )
                else:
                    lines.append(f"        {bc_key}")
                    lines.append("        {")
                    for sub_key, sub_val in bc_val.items():
                        formatted = _format_value(sub_val)
                        lines.append(f"            {sub_key:<14s} {formatted};")
                    lines.append("        }")
            else:
                formatted = _format_value(bc_val)
                lines.append(f"        {bc_key:<16s} {formatted};")
        lines.append("    }")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def compile_to_files(
    compiled: dict[str, Any],
) -> dict[str, str]:
    """Convert the NativeCaseCompiler output to a flat dict of files.

    The returned dict maps relative file paths (e.g. ``"system/controlDict"``)
    to their OpenFOAM dictionary text content.

    Args:
        compiled: The dict output from :meth:`NativeCaseCompiler.compile`.

    Returns:
        A flat dict mapping file paths to file contents.
    """
    files: dict[str, str] = {}

    # system/controlDict
    files["system/controlDict"] = dict_to_foam_text(
        "controlDict",
        compiled["system"]["controlDict"],
        location="system",
    )

    # system/fvSchemes
    files["system/fvSchemes"] = dict_to_foam_text(
        "fvSchemes",
        compiled["system"]["fvSchemes"],
        location="system",
    )

    # system/fvSolution
    files["system/fvSolution"] = dict_to_foam_text(
        "fvSolution",
        compiled["system"]["fvSolution"],
        location="system",
    )

    # system/blockMeshDict
    block_mesh_data = compiled["system"]["blockMeshDict"]
    files["system/blockMeshDict"] = _format_block_mesh_dict(block_mesh_data)

    # constant/transportProperties
    files["constant/transportProperties"] = dict_to_foam_text(
        "transportProperties",
        compiled["constant"]["transportProperties"],
        location="constant",
    )

    # constant/turbulenceProperties
    files["constant/turbulenceProperties"] = dict_to_foam_text(
        "turbulenceProperties",
        compiled["constant"]["turbulenceProperties"],
        location="constant",
    )

    # 0/U
    u_data = compiled["0"]["U"]
    files["0/U"] = field_to_foam_text(
        "U",
        u_data,
        location="0",
        field_class="volVectorField",
    )

    # 0/p
    p_data = compiled["0"]["p"]
    files["0/p"] = field_to_foam_text(
        "p",
        p_data,
        location="0",
        field_class="volScalarField",
    )

    return files


def _format_block_mesh_dict(data: dict[str, Any]) -> str:
    """Format the blockMeshDict in proper OpenFOAM text format."""
    header = _foam_header("blockMeshDict", location="system")
    lines: list[str] = [header]

    # Vertices
    vertices = data.get("vertices", [])
    lines.append("vertices")
    lines.append("(")
    for v in vertices:
        formatted = " ".join(_format_value(c) for c in v)
        lines.append(f"    ({formatted})")
    lines.append(")")
    lines.append("")

    # Blocks
    blocks = data.get("blocks", [])
    lines.append("blocks")
    lines.append("(")
    for b in blocks:
        hex_verts = " ".join(str(i) for i in b.get("hex", []))
        cells = b.get("cells", [1, 1, 1])
        cells_str = " ".join(str(c) for c in cells)
        grading = b.get("grading", "simpleGrading")
        ratios = b.get("ratios", [1, 1, 1])
        ratios_str = " ".join(_format_value(r) for r in ratios)
        lines.append(
            f"    hex ({hex_verts}) ({cells_str}) {grading} ({ratios_str})"
        )
    lines.append(")")
    lines.append("")

    # Boundary
    boundary = data.get("boundary", {})
    lines.append("boundary")
    lines.append("(")
    for patch_name, patch_data in boundary.items():
        ptype = patch_data.get("type", "patch")
        faces = patch_data.get("faces", [])
        lines.append(f"    {patch_name}")
        lines.append("    {")
        lines.append(f"        type {ptype};")
        if faces:
            lines.append("        faces")
            lines.append("        (")
            for face in faces:
                if isinstance(face, list):
                    face_str = " ".join(str(i) for i in face)
                    lines.append(f"            ({face_str})")
                else:
                    lines.append(f"            {face}")
            lines.append("        );")
        lines.append("    }")
    lines.append(")")
    lines.append("")

    return "\n".join(lines)


__all__ = [
    "dict_to_foam_text",
    "field_to_foam_text",
    "compile_to_files",
]

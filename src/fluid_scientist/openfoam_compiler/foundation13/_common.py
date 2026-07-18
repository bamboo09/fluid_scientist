"""Shared formatting helpers for the Foundation 13 compiler.

These helpers produce valid OpenFOAM dictionary syntax (not JSON) and
extract concrete numeric values from :class:`Quantity` /
:class:`SourcedValue` wrappers in a duck-typed fashion so that no
circular imports are introduced.
"""

from __future__ import annotations

__all__ = [
    "foam_file_header",
    "fmt_num",
    "foam_value",
    "foam_dict_block",
    "quantity_value",
    "sourced_numeric",
    "sourced_raw",
]


def foam_file_header(class_name: str, object_name: str) -> str:
    """Return a standard OpenFOAM ``FoamFile`` header block."""
    return (
        "FoamFile\n"
        "{\n"
        "    version     2.0;\n"
        "    format      ascii;\n"
        f"    class       {class_name};\n"
        f"    object      {object_name};\n"
        "}\n"
    )


def fmt_num(v: float | int) -> str:
    """Format a number for OpenFOAM output.

    Whole numbers are rendered as integers (``15`` rather than ``15.0``)
    so that the acceptance criterion — *endTime is 15* — is satisfied
    textually.
    """
    fv = float(v)
    if fv == int(fv):
        return str(int(fv))
    return repr(fv)


def foam_value(val: object) -> str:
    """Render a Python value as an OpenFOAM literal.

    * ``bool`` → ``yes`` / ``no``
    * ``int`` / ``float`` → formatted via :func:`fmt_num`
    * ``str`` → returned as-is (callers pre-quote strings that need
      quoting, e.g. ``'"libforces.so"'``)
    * ``list`` → ``(item1 item2 ...)`` — vectors and word-lists alike
    """
    if isinstance(val, bool):
        return "yes" if val else "no"
    if isinstance(val, (int, float)):
        return fmt_num(val)
    if isinstance(val, str):
        return val
    if isinstance(val, list):
        return "(" + " ".join(foam_value(v) for v in val) + ")"
    return str(val)


def foam_dict_block(name: str, entries: dict, indent: int = 0) -> str:
    """Render a named OpenFOAM sub-dictionary block.

    Nested ``dict`` values are rendered recursively.
    """
    pad = "    " * indent
    inner = "    " * (indent + 1)
    lines = [f"{pad}{name}", f"{pad}{{"]
    for key, val in entries.items():
        if isinstance(val, dict):
            lines.append(foam_dict_block(key, val, indent + 1))
        else:
            lines.append(f"{inner}{key:<16} {foam_value(val)};")
    lines.append(f"{pad}}}")
    return "\n".join(lines)


def quantity_value(q: object) -> float | None:
    """Extract a concrete ``float`` from a *Quantity*-like object.

    Works duck-typed: any object with a ``value`` attribute that holds
    an ``int`` or ``float`` is accepted.  Bare ``int`` / ``float``
    arguments are also accepted.
    """
    if q is None:
        return None
    if isinstance(q, bool):
        return None
    if isinstance(q, (int, float)):
        return float(q)
    v = getattr(q, "value", None)
    if isinstance(v, (int, float)):
        return float(v)
    return None


def sourced_numeric(sv: object) -> float | None:
    """Extract a concrete ``float`` from a *SourcedValue*-like object."""
    if sv is None:
        return None
    v = getattr(sv, "value", None)
    if isinstance(v, (int, float)):
        return float(v)
    return None


def sourced_raw(sv: object) -> object:
    """Return the raw ``value`` attribute from a *SourcedValue*-like object."""
    if sv is None:
        return None
    return getattr(sv, "value", None)

"""OpenFOAM field data reader — parses ASCII FoamFile format.

Reads mesh geometry (points, faces, owner, neighbour) and field data
(p, U) from OpenFOAM case directories to enable visualization.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CellMesh:
    """Minimal mesh representation for visualization."""

    n_cells: int = 0
    cell_centers: list[tuple[float, float]] = field(default_factory=list)
    # For 2D cases, only (x, y) coordinates


def _strip_foam_header(text: str) -> str:
    """Remove FoamFile header and return body content."""
    # Remove everything before the first '{' that starts the FoamFile dict
    # Actually, field files have structure:
    # FoamFile { ... } \n body...
    # We need to skip the FoamFile block
    idx = text.find("FoamFile")
    if idx == -1:
        return text
    # Find the closing brace of FoamFile
    brace_start = text.find("{", idx)
    if brace_start == -1:
        return text
    depth = 0
    i = brace_start
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[i + 1 :]
        i += 1
    return text


def _parse_internal_field(body: str) -> tuple[list[Any], str]:
    """Parse internalField from field file body.

    Returns:
        (values, field_type) where field_type is 'scalar' or 'vector'
    """
    # Find internalField
    idx = body.find("internalField")
    if idx == -1:
        return [], "scalar"

    rest = body[idx + len("internalField") :].lstrip()

    # Check for uniform
    if rest.startswith("uniform"):
        rest = rest[len("uniform") :].lstrip()
        # uniform value or uniform (vx vy vz)
        if rest.startswith("("):
            close = rest.find(")")
            vec_str = rest[1:close].split()
            return ([float(v) for v in vec_str], "vector")
        else:
            # scalar value
            match = re.match(r"([\d.eE+-]+)", rest)
            if match:
                return ([float(match.group(1))], "scalar")
            return ([], "scalar")

    # nonuniform list
    if rest.startswith("nonuniform"):
        rest = rest[len("nonuniform") :].lstrip()
        # Next is the number of entries
        match = re.match(r"(\d+)", rest)
        if not match:
            return [], "scalar"
        n = int(match.group(1))
        rest = rest[match.end() :].lstrip()

        # Find opening paren
        if rest.startswith("List<"):
            # Skip List<scalar> or List<vector>
            close = rest.find(">")
            rest = rest[close + 1 :].lstrip()

        if rest.startswith("("):
            # Parse list
            close = rest.find(")")
            list_content = rest[1:close]

            # Check if vector (contains parenthesized groups)
            if "(" in list_content.split("\n")[0] if list_content else False:
                # Vector field
                values = []
                for line in list_content.split("\n"):
                    line = line.strip().strip("()")
                    if line:
                        parts = line.split()
                        if len(parts) >= 3:
                            values.append(
                                (float(parts[0]), float(parts[1]), float(parts[2]))
                            )
                return (values, "vector")
            else:
                # Scalar field
                values = []
                for line in list_content.split("\n"):
                    line = line.strip()
                    if line and not line.startswith("//"):
                        try:
                            values.append(float(line))
                        except ValueError:
                            pass
                return (values, "scalar")

    return [], "scalar"


def _parse_points(points_text: str) -> list[tuple[float, float, float]]:
    """Parse constant/polyMesh/points file."""
    body = _strip_foam_header(points_text)

    # Find the points list
    idx = body.find("(")
    if idx == -1:
        return []

    # First number after internalField is the count
    before_paren = body[:idx].strip()
    match = re.search(r"(\d+)\s*$", before_paren)
    if match:
        n_points = int(match.group(1))
    else:
        n_points = 0

    # Find matching close paren
    close = body.find(")", idx)
    if close == -1:
        return []

    list_content = body[idx + 1 : close]
    points = []
    for line in list_content.split("\n"):
        line = line.strip().strip("()")
        if line:
            parts = line.split()
            if len(parts) >= 3:
                points.append((float(parts[0]), float(parts[1]), float(parts[2])))

    return points


def _parse_faces(faces_text: str) -> list[list[int]]:
    """Parse constant/polyMesh/faces file."""
    body = _strip_foam_header(faces_text)

    idx = body.find("(")
    if idx == -1:
        return []

    close = body.find(")", idx)
    if close == -1:
        return []

    list_content = body[idx + 1 : close]
    faces = []
    for line in list_content.split("\n"):
        line = line.strip()
        if line.startswith("("):
            # Format: (n v1 v2 v3 v4)  or  n(v1 v2 v3 v4)
            close_face = line.find(")")
            inner = line[1:close_face] if close_face > 0 else line[1:]
            parts = inner.split()
            # First element is count, rest are vertex IDs
            if len(parts) >= 2:
                try:
                    n = int(parts[0])
                    face_verts = [int(v) for v in parts[1 : 1 + n]]
                    faces.append(face_verts)
                except (ValueError, IndexError):
                    pass

    return faces


def _parse_owner_neighbour(text: str) -> tuple[list[int], list[int]]:
    """Parse owner and neighbour files."""
    body = _strip_foam_header(text)

    idx = body.find("(")
    if idx == -1:
        return [], []

    close = body.find(")", idx)
    if close == -1:
        return [], []

    list_content = body[idx + 1 : close]
    values = []
    for line in list_content.split("\n"):
        line = line.strip()
        if line:
            try:
                values.append(int(line))
            except ValueError:
                pass

    return values, []


def compute_cell_centers(
    points: list[tuple[float, float, float]],
    faces: list[list[int]],
    owner: list[int],
) -> list[tuple[float, float]]:
    """Compute approximate cell centers from mesh data.

    For 2D cases, returns (x, y) coordinates.
    """
    if not faces or not owner:
        return []

    # Compute face centers
    face_centers = []
    for face_verts in faces:
        if not face_verts:
            face_centers.append((0.0, 0.0))
            continue
        xs = [points[v][0] for v in face_verts if v < len(points)]
        ys = [points[v][1] for v in face_verts if v < len(points)]
        if xs and ys:
            face_centers.append((sum(xs) / len(xs), sum(ys) / len(ys)))
        else:
            face_centers.append((0.0, 0.0))

    # Assign face centers to owner cells (approximation: average of face centers per cell)
    max_cell = max(owner) + 1 if owner else 0
    cell_face_sums_x = [0.0] * max_cell
    cell_face_sums_y = [0.0] * max_cell
    cell_face_counts = [0] * max_cell

    for i, cell_id in enumerate(owner):
        if i < len(face_centers):
            cell_face_sums_x[cell_id] += face_centers[i][0]
            cell_face_sums_y[cell_id] += face_centers[i][1]
            cell_face_counts[cell_id] += 1

    cell_centers = []
    for i in range(max_cell):
        if cell_face_counts[i] > 0:
            cx = cell_face_sums_x[i] / cell_face_counts[i]
            cy = cell_face_sums_y[i] / cell_face_counts[i]
            cell_centers.append((cx, cy))
        else:
            cell_centers.append((0.0, 0.0))

    return cell_centers


class FoamFieldReader:
    """Reads OpenFOAM ASCII field and mesh files for visualization."""

    def __init__(self, case_path: str | Path):
        self.case_path = Path(case_path)

    def read_mesh(self) -> CellMesh:
        """Read mesh geometry and compute cell centers."""
        poly_mesh_dir = self.case_path / "constant" / "polyMesh"

        points_file = poly_mesh_dir / "points"
        faces_file = poly_mesh_dir / "faces"
        owner_file = poly_mesh_dir / "owner"

        if not points_file.exists() or not faces_file.exists():
            return CellMesh()

        try:
            points = _parse_points(points_file.read_text())
            faces = _parse_faces(faces_file.read_text())
            owner, _ = _parse_owner_neighbour(owner_file.read_text())
            cell_centers = compute_cell_centers(points, faces, owner)
            return CellMesh(
                n_cells=len(cell_centers), cell_centers=cell_centers
            )
        except Exception:
            return CellMesh()

    def read_field(self, time_dir: str, field_name: str) -> tuple[list[Any], str]:
        """Read a field from a time directory.

        Args:
            time_dir: Time directory name (e.g. "5", "10", "0")
            field_name: Field name (e.g. "p", "U")

        Returns:
            (values, field_type) where field_type is 'scalar' or 'vector'
        """
        field_path = self.case_path / time_dir / field_name
        if not field_path.exists():
            # Try without leading zeros
            for d in (self.case_path / time_dir).parent.iterdir():
                if d.name == time_dir.lstrip("0") or d.name == time_dir:
                    field_path = d / field_name
                    if field_path.exists():
                        break
            else:
                return [], "scalar"

        try:
            text = field_path.read_text()
            body = _strip_foam_header(text)
            return _parse_internal_field(body)
        except Exception:
            return [], "scalar"

    def list_time_dirs(self) -> list[str]:
        """List all time directories (excluding 0 and constant)."""
        time_dirs = []
        for d in self.case_path.iterdir():
            if d.is_dir() and d.name not in ("0", "constant", "system", "postProcessing"):
                try:
                    float(d.name)
                    time_dirs.append(d.name)
                except ValueError:
                    pass
        time_dirs.sort(key=lambda x: float(x))
        return time_dirs

    def compute_vorticity_z(
        self, time_dir: str, mesh: CellMesh
    ) -> list[float]:
        """Compute z-component of vorticity from velocity field.

        For 2D cases, vorticity_z = du_y/dx - du_x/dy

        Uses nearest-neighbor finite differences on cell centers.
        """
        u_values, u_type = self.read_field(time_dir, "U")
        if u_type != "vector" or len(u_values) != mesh.n_cells:
            return []

        # Extract Ux, Uy components and cell center coordinates
        uxs = [v[0] if isinstance(v, (tuple, list)) else 0.0 for v in u_values]
        uys = [v[1] if isinstance(v, (tuple, list)) else 0.0 for v in u_values]
        coords = mesh.cell_centers

        if not coords or len(coords) != len(uxs):
            return []

        # Simple nearest-neighbor based vorticity
        # For each cell, find neighbors and compute du_y/dx - du_x/dy
        import numpy as np

        coords_arr = np.array(coords)
        uxs_arr = np.array(uxs)
        uys_arr = np.array(uys)

        vorticity = np.zeros(len(coords))
        n = len(coords)

        # For efficiency, only use a subset of neighbors
        sample_size = min(n, 5000)
        if n > sample_size:
            indices = np.random.choice(n, sample_size, replace=False)
        else:
            indices = np.arange(n)

        for i in indices:
            dx = coords_arr[:, 0] - coords_arr[i, 0]
            dy = coords_arr[:, 1] - coords_arr[i, 1]
            dist = np.sqrt(dx**2 + dy**2)

            # Find nearest neighbors (exclude self)
            dist[i] = np.inf
            nearest = np.argsort(dist)[:8]

            if len(nearest) < 4:
                continue

            # Fit linear: ux = a0 + a1*x + a2*y, uy = b0 + b1*x + b2*y
            A = np.column_stack(
                [np.ones(len(nearest)), coords_arr[nearest, 0], coords_arr[nearest, 1]]
            )
            try:
                # Solve for ux gradients
                coeffs_ux = np.linalg.lstsq(A, uxs_arr[nearest], rcond=None)[0]
                coeffs_uy = np.linalg.lstsq(A, uys_arr[nearest], rcond=None)[0]
                # vorticity_z = duy/dx - dux/dy
                vorticity[i] = coeffs_uy[1] - coeffs_ux[2]
            except Exception:
                pass

        return vorticity.tolist()


__all__ = ["CellMesh", "FoamFieldReader"]

"""Component registry for the OpenFOAM 13 component system.

The :class:`ComponentRegistry` holds all registered base packs and
components and provides lookup by id, by category, and by semantic role.
"""

from __future__ import annotations

from typing import Union

from fluid_scientist.components.base_packs import BASE_PACKS, BasePack
from fluid_scientist.components.boundary_components import BOUNDARY_COMPONENTS, BoundaryComponent
from fluid_scientist.components.geometry_components import GEOMETRY_COMPONENTS, GeometryComponent
from fluid_scientist.components.mesh_components import MESH_COMPONENTS, MeshComponent
from fluid_scientist.components.observable_components import (
    OBSERVABLE_COMPONENTS,
    ObservableComponent,
)

AnyComponent = Union[
    BasePack,
    GeometryComponent,
    BoundaryComponent,
    MeshComponent,
    ObservableComponent,
]


class ComponentRegistry:
    """Central registry for all OpenFOAM 13 components.

    Provides unified lookup across base packs, geometry, boundary,
    mesh, and observable components.

    Attributes:
        base_packs: Mapping of pack id to :class:`BasePack`.
        geometry: Mapping of component id to :class:`GeometryComponent`.
        boundary: Mapping of component id to :class:`BoundaryComponent`.
        mesh: Mapping of component id to :class:`MeshComponent`.
        observables: Mapping of component id to :class:`ObservableComponent`.
    """

    def __init__(self) -> None:
        self.base_packs: dict[str, BasePack] = dict(BASE_PACKS)
        self.geometry: dict[str, GeometryComponent] = dict(GEOMETRY_COMPONENTS)
        self.boundary: dict[str, BoundaryComponent] = dict(BOUNDARY_COMPONENTS)
        self.mesh: dict[str, MeshComponent] = dict(MESH_COMPONENTS)
        self.observables: dict[str, ObservableComponent] = dict(OBSERVABLE_COMPONENTS)

    # ------------------------------------------------------------------
    # Lookup by id
    # ------------------------------------------------------------------

    def get_base_pack(self, pack_id: str) -> BasePack | None:
        """Look up a base pack by id."""
        return self.base_packs.get(pack_id)

    def get_geometry(self, component_id: str) -> GeometryComponent | None:
        """Look up a geometry component by id."""
        return self.geometry.get(component_id)

    def get_boundary(self, component_id: str) -> BoundaryComponent | None:
        """Look up a boundary component by id."""
        return self.boundary.get(component_id)

    def get_mesh(self, component_id: str) -> MeshComponent | None:
        """Look up a mesh component by id."""
        return self.mesh.get(component_id)

    def get_observable(self, component_id: str) -> ObservableComponent | None:
        """Look up an observable component by id."""
        return self.observables.get(component_id)

    def get(self, component_id: str) -> AnyComponent | None:
        """Look up any component by id across all categories."""
        for store in (
            self.base_packs,
            self.geometry,
            self.boundary,
            self.mesh,
            self.observables,
        ):
            if component_id in store:
                return store[component_id]
        return None

    # ------------------------------------------------------------------
    # Lookup by category
    # ------------------------------------------------------------------

    def list_by_category(self, category: str) -> list[AnyComponent]:
        """List all components in a category.

        Supported categories: ``"base_pack"``, ``"geometry"``,
        ``"boundary"``, ``"mesh"``, ``"observable"``.
        """
        category_map: dict[str, dict[str, AnyComponent]] = {
            "base_pack": self.base_packs,
            "geometry": self.geometry,
            "boundary": self.boundary,
            "mesh": self.mesh,
            "observable": self.observables,
        }
        store = category_map.get(category)
        if store is None:
            raise ValueError(f"unknown category: {category}")
        return list(store.values())

    def all_ids(self) -> list[str]:
        """Return all registered component ids."""
        return list(self.base_packs) + list(self.geometry) + list(self.boundary) + list(self.mesh) + list(self.observables)

    # ------------------------------------------------------------------
    # Lookup by semantic role
    # ------------------------------------------------------------------

    def find_boundary_by_role(self, semantic_role: str) -> BoundaryComponent | None:
        """Find a boundary component by its semantic role."""
        for bc in self.boundary.values():
            if bc.semantic_role == semantic_role:
                return bc
        return None

    def find_observable_by_type(self, semantic_type: str) -> ObservableComponent | None:
        """Find an observable component by its semantic type."""
        for obs in self.observables.values():
            if obs.semantic_type == semantic_type:
                return obs
        return None

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def validate_composition(
        self,
        base_pack_id: str = "",
        geometry_ids: list[str] | None = None,
        boundary_ids: list[str] | None = None,
        mesh_ids: list[str] | None = None,
        observable_ids: list[str] | None = None,
    ) -> list[str]:
        """Validate a composition plan for missing dependencies and conflicts.

        Returns a list of error strings; an empty list means valid.
        """
        errors: list[str] = []

        geometry_ids = geometry_ids or []
        boundary_ids = boundary_ids or []
        mesh_ids = mesh_ids or []
        observable_ids = observable_ids or []

        all_selected = set(geometry_ids) | set(boundary_ids) | set(mesh_ids) | set(observable_ids)

        # Check that all selected ids exist
        for gid in geometry_ids:
            if gid not in self.geometry:
                errors.append(f"geometry component not found: {gid}")
        for bid in boundary_ids:
            if bid not in self.boundary:
                errors.append(f"boundary component not found: {bid}")
        for mid in mesh_ids:
            if mid not in self.mesh:
                errors.append(f"mesh component not found: {mid}")
        for oid in observable_ids:
            if oid not in self.observables:
                errors.append(f"observable component not found: {oid}")

        # Check dependencies
        for gid in geometry_ids:
            comp = self.geometry.get(gid)
            if comp:
                for dep in comp.depends_on:
                    if dep not in all_selected:
                        errors.append(f"geometry '{gid}' depends on '{dep}' which is not selected")

        for bid in boundary_ids:
            comp = self.boundary.get(bid)
            if comp:
                for dep in comp.dependencies:
                    if dep not in all_selected:
                        errors.append(f"boundary '{bid}' depends on '{dep}' which is not selected")

        for mid in mesh_ids:
            comp = self.mesh.get(mid)
            if comp:
                for dep in comp.depends_on:
                    if dep not in all_selected:
                        errors.append(f"mesh '{mid}' depends on '{dep}' which is not selected")

        # Check conflicts
        for gid in geometry_ids:
            comp = self.geometry.get(gid)
            if comp:
                for conflict in comp.conflicts_with:
                    if conflict in all_selected:
                        errors.append(f"geometry '{gid}' conflicts with '{conflict}'")

        for bid in boundary_ids:
            comp = self.boundary.get(bid)
            if comp:
                for conflict in comp.conflicts:
                    if conflict in all_selected:
                        errors.append(f"boundary '{bid}' conflicts with '{conflict}'")

        return errors


__all__ = [
    "AnyComponent",
    "ComponentRegistry",
]

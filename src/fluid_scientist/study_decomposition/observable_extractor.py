"""Observable (measurement target) extraction from CFD study descriptions.

The :class:`ObservableExtractor` identifies requested observables such as
drag coefficient, lift, Strouhal number, pressure distribution, reattachment
length, vortex structures, and wake characteristics from bilingual free-text.
"""

from __future__ import annotations

from fluid_scientist.study_decomposition.models import ObservableSpec


class ObservableExtractor:
    """Extract requested observables (metrics/outputs) from study text.

    Each keyword group maps to a canonical observable ID, display name, and
    category.  Matching is performed against a lower-cased copy of the
    input so that English keywords are case-insensitive.
    """

    # -- observable map: (keywords, observable_id, display_name, category) --
    _OBSERVABLE_MAP: list[tuple[tuple[str, ...], str, str, str]] = [
        (("阻力", "drag"), "drag", "Drag", "force"),
        (("升力", "lift"), "lift", "Lift", "force"),
        (("斯特劳哈尔", "strouhal"), "strouhal_number", "Strouhal Number", "spectral"),
        (("频谱", "spectral", "spectrum"), "spectrum", "Spectrum", "spectral"),
        (("压力", "pressure"), "pressure", "Pressure", "pressure"),
        (("热通量", "heat flux"), "heat_flux", "Heat Flux", "heat_flux"),
        (("再附", "reattachment"), "reattachment", "Reattachment", "reattachment"),
        (("回流区", "recirculation"), "recirculation", "Recirculation", "vortex_structure"),
        (("涡", "vortex"), "vortex_structure", "Vortex Structure", "vortex_structure"),
        (("尾迹", "wake"), "wake", "Wake", "wake_deflection"),
        (("混合层", "mixing layer"), "mixing_layer", "Mixing Layer", "mixing"),
        (("内波", "internal wave"), "internal_wave", "Internal Wave", "internal_wave"),
        (
            ("雷诺应力", "reynolds stress"),
            "reynolds_stress",
            "Reynolds Stress",
            "turbulence_statistics",
        ),
    ]

    def extract(
        self, text: str, study_type: str
    ) -> tuple[list[ObservableSpec], list[dict]]:
        """Extract observables from *text*.

        Returns a tuple ``(observables, ambiguities)``.

        * ``observables`` is a de-duplicated list of
          :class:`ObservableSpec` instances for each detected observable.
        * ``ambiguities`` is a list of dicts describing unclear or missing
          observable specifications (currently empty; reserved for future
          enrichment).

        The *study_type* argument is accepted for context-aware extraction
        but does not yet change behaviour.
        """
        text_lower = text.lower()
        observables: list[ObservableSpec] = []
        seen: set[str] = set()

        for keywords, obs_id, display_name, category in self._OBSERVABLE_MAP:
            if obs_id in seen:
                continue
            if any(kw in text_lower for kw in keywords):
                observables.append(
                    ObservableSpec(
                        observable_id=obs_id,
                        display_name=display_name,
                        category=category,  # type: ignore[arg-type]
                    )
                )
                seen.add(obs_id)

        ambiguities: list[dict] = []
        return observables, ambiguities


__all__ = ["ObservableExtractor"]

"""Model registry managing model configs by role.

:class:`ModelRegistry` is the single source of truth for which model is
bound to which :class:`~fluid_scientist.model_runtime.models.ModelRole`.
Its central guarantee is that a
:attr:`~fluid_scientist.model_runtime.models.ModelRole.PRIMARY_REASONER`
cannot be registered unless it has passed the capability admission
thresholds - a failing primary reasoner is rejected by raising
:class:`~fluid_scientist.model_runtime.errors.ModelInvocationError`
rather than being silently admitted.
"""
from __future__ import annotations

from fluid_scientist.model_runtime.capability_eval import (
    CapabilityEvalResult,
    evaluate_model,
)
from fluid_scientist.model_runtime.errors import ModelInvocationError
from fluid_scientist.model_runtime.models import ModelConfig, ModelHealthStatus, ModelRole

__all__ = ["ModelRegistry"]


class ModelRegistry:
    """Manage :class:`ModelConfig` instances keyed by :class:`ModelRole`.

    The registry stores at most one config per role.  Registering a
    :attr:`ModelRole.PRIMARY_REASONER` requires a passing
    :class:`CapabilityEvalResult`; any other case raises
    :class:`ModelInvocationError` with code
    ``MODEL_CAPABILITY_INSUFFICIENT``.
    """

    def __init__(self) -> None:
        self._configs: dict[ModelRole, ModelConfig] = {}
        self._evals: dict[ModelRole, CapabilityEvalResult] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------
    def register(
        self,
        role: ModelRole,
        config: ModelConfig,
        *,
        capability_eval: CapabilityEvalResult | None = None,
    ) -> ModelConfig:
        """Bind *config* to *role*.

        Args:
            role: The role to bind the config to.  Must equal
                ``config.role``.
            config: The model configuration to register.
            capability_eval: Required (and must pass admission
                thresholds) when *role* is
                :attr:`ModelRole.PRIMARY_REASONER`.  Optional for other
                roles.

        Returns:
            The registered :class:`ModelConfig`.

        Raises:
            ValueError: If ``config.role`` does not match *role*.
            ModelInvocationError: If *role* is ``PRIMARY_REASONER`` and
                no capability eval was supplied or it failed admission.
        """
        if config.role != role:
            raise ValueError(
                f"config.role ({config.role}) does not match requested role ({role})"
            )

        if role is ModelRole.PRIMARY_REASONER:
            if capability_eval is None:
                raise ModelInvocationError(
                    code="MODEL_CAPABILITY_INSUFFICIENT",
                    provider=config.provider,
                    configured_model=config.model_name,
                    retryable=False,
                    message=(
                        "registering PRIMARY_REASONER requires a CapabilityEvalResult "
                        "that passes admission thresholds"
                    ),
                )
            if not evaluate_model(capability_eval):
                raise ModelInvocationError(
                    code="MODEL_CAPABILITY_INSUFFICIENT",
                    provider=config.provider,
                    configured_model=config.model_name,
                    retryable=False,
                    message=(
                        "PRIMARY_REASONER failed capability admission thresholds "
                        f"(model_id={capability_eval.model_id})"
                    ),
                )
            self._evals[role] = capability_eval
        elif capability_eval is not None:
            # Non-primary roles may carry an eval for informational purposes.
            self._evals[role] = capability_eval

        self._configs[role] = config
        return config

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------
    def get(self, role: ModelRole) -> ModelConfig:
        """Return the config bound to *role*.

        Raises:
            KeyError: If no config is registered for *role*.
        """
        if role not in self._configs:
            raise KeyError(f"no model registered for role {role!r}")
        return self._configs[role]

    def has(self, role: ModelRole) -> bool:
        """Return ``True`` if a config is registered for *role*."""
        return role in self._configs

    def capability_eval(self, role: ModelRole) -> CapabilityEvalResult | None:
        """Return the stored capability eval for *role*, if any."""
        return self._evals.get(role)

    def list_roles(self) -> list[ModelRole]:
        """Return the roles that currently have a registered config."""
        return list(self._configs.keys())

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------
    def health_check(self, role: ModelRole) -> ModelHealthStatus:
        """Return a metadata-level :class:`ModelHealthStatus` for *role*.

        This is a *non-network* probe: it reports whether the role is
        registered and, for ``PRIMARY_REASONER``, whether it passed
        capability admission.  It performs no external API calls so the
        runtime stays importable and testable offline.

        Raises:
            KeyError: If no config is registered for *role*.
        """
        config = self.get(role)
        eval_result = self._evals.get(role)

        if role is ModelRole.PRIMARY_REASONER:
            passed = eval_result is not None and evaluate_model(eval_result)
        else:
            passed = True

        return ModelHealthStatus(
            role=config.role,
            provider=config.provider,
            configured_model=config.model_name,
            actual_returned_model=config.model_name,
            structured_output_support=config.structured_output_enabled,
            reasoning_mode=config.reasoning_effort,
            capability_eval_version=eval_result.eval_version if eval_result else None,
            pass_fail="pass" if passed else "fail",
        )

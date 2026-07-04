from fluid_scientist.execution_targets.base import ExecutionTargetCapability
from fluid_scientist.services.target_capabilities import TargetCapabilityCache


class Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


class CountingTarget:
    target_id = "workstation-openfoam"
    kind = "workstation_openfoam"

    def __init__(self, *, available: bool) -> None:
        self.available = available
        self.calls = 0

    def doctor(self) -> ExecutionTargetCapability:
        self.calls += 1
        return ExecutionTargetCapability(
            target_id=self.target_id,
            kind=self.kind,
            available=self.available,
            reason=None if self.available else "offline",
        )


def test_cache_reuses_available_result_until_ttl_expires() -> None:
    clock = Clock()
    target = CountingTarget(available=True)
    cache = TargetCapabilityCache(monotonic=clock, ttl_seconds=30)

    first = cache.get(target)
    clock.value += 20
    cached = cache.get(target)
    clock.value += 11
    refreshed = cache.get(target)

    assert target.calls == 2
    assert first.cached is False
    assert cached.cached is True
    assert cached.age_seconds == 20
    assert cached.checked_at == first.checked_at
    assert refreshed.cached is False


def test_cache_also_reuses_unavailable_results() -> None:
    clock = Clock()
    target = CountingTarget(available=False)
    cache = TargetCapabilityCache(monotonic=clock, ttl_seconds=30)

    first = cache.get(target)
    clock.value += 29
    second = cache.get(target)

    assert target.calls == 1
    assert first.available is False
    assert second.available is False
    assert second.cached is True


def test_doctor_exception_is_sanitized_and_cached() -> None:
    class FailingTarget:
        target_id = "private-target"
        kind = "workstation_openfoam"
        calls = 0

        def doctor(self):
            self.calls += 1
            raise OSError("ssh://secret-host/private/key")

    target = FailingTarget()
    cache = TargetCapabilityCache()

    first = cache.get(target)
    second = cache.get(target)

    assert target.calls == 1
    assert first.available is False
    assert first.reason == "execution target capability check failed"
    assert "secret-host" not in first.model_dump_json()
    assert second.cached is True

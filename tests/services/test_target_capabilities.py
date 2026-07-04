from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Event

from fluid_scientist.compat import UTC
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


def test_successful_doctor_results_are_sanitized_for_both_health_states() -> None:
    class SecretTarget:
        target_id = "public-target"
        kind = "workstation_openfoam"

        def __init__(self, available: bool) -> None:
            self.available = available

        def doctor(self):
            return ExecutionTargetCapability(
                target_id=self.target_id,
                kind=self.kind,
                available=self.available,
                selected_candidate="ssh://secret-host/private/key",
                foam_version="OpenFOAM-13",
                cpu_count=32,
                memory_gb=64,
                disk_free_gb=100,
                commands=("/private/bin/secret-command",),
                worker_protocol=1,
                reason="private host secret-host failed with /private/key",
            )

    for available in (True, False):
        status = TargetCapabilityCache().get(SecretTarget(available))
        encoded = status.model_dump_json()
        assert status.selected_candidate is None
        assert status.commands == ()
        assert status.reason == (None if available else "execution target is unavailable")
        assert status.foam_version == "OpenFOAM-13"
        assert status.cpu_count == 32
        assert "secret-host" not in encoded
        assert "/private" not in encoded


def test_only_recognized_openfoam_version_formats_are_exposed() -> None:
    class VersionTarget(CountingTarget):
        def __init__(self, version: str) -> None:
            super().__init__(available=True)
            self.version = version

        def doctor(self):
            return ExecutionTargetCapability(
                target_id=self.target_id,
                kind=self.kind,
                available=True,
                foam_version=self.version,
            )

    for version in ("OpenFOAM-13", "OpenFOAM-v2312", "OpenFOAM-12.1"):
        status = TargetCapabilityCache().get(VersionTarget(version))
        assert status.foam_version == version

    for private_value in (
        "secret-host.internal",
        "node01 private key",
        "ssh://secret-host/private/OpenFOAM-13",
        "/opt/openfoam13",
    ):
        status = TargetCapabilityCache().get(VersionTarget(private_value))
        assert status.foam_version is None
        assert private_value not in status.model_dump_json()


def test_slow_target_does_not_block_another_target_refresh() -> None:
    entered = Event()
    release = Event()

    class BlockingTarget(CountingTarget):
        target_id = "target-a"

        def doctor(self):
            entered.set()
            assert release.wait(timeout=2)
            return super().doctor()

    class FastTarget(CountingTarget):
        target_id = "target-b"

    cache = TargetCapabilityCache()
    slow = BlockingTarget(available=True)
    fast = FastTarget(available=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        slow_future = pool.submit(cache.get, slow)
        assert entered.wait(timeout=1)
        fast_future = pool.submit(cache.get, fast)
        try:
            fast_result = fast_future.result(timeout=0.5)
        finally:
            release.set()
        slow_future.result(timeout=1)

    assert fast_result.target_id == "target-b"
    assert fast.calls == 1


def test_checked_at_and_age_start_at_doctor_completion() -> None:
    monotonic_clock = Clock()
    wall_time = [datetime(2026, 7, 4, 8, 0, tzinfo=UTC)]

    class CompletingTarget(CountingTarget):
        def doctor(self):
            monotonic_clock.value += 5
            wall_time[0] = datetime(2026, 7, 4, 8, 1, tzinfo=UTC)
            return super().doctor()

    cache = TargetCapabilityCache(
        monotonic=monotonic_clock,
        wall_clock=lambda: wall_time[0],
    )
    target = CompletingTarget(available=True)

    completed = cache.get(target)
    monotonic_clock.value += 4
    cached = cache.get(target)

    assert completed.checked_at == datetime(2026, 7, 4, 8, 1, tzinfo=UTC)
    assert completed.age_seconds == 0
    assert cached.age_seconds == 4

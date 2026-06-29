from fluid_scientist.adapters.fakes import build_demo_service


def test_demo_research_produces_evidence_linked_report() -> None:
    result = build_demo_service().run_approved_demo(
        "How do curvature and Reynolds number affect bend pressure loss?"
    )

    assert result.workflow_state == "REPORTED"
    assert result.validation.mass_conservation_passed is True
    assert result.analysis.metrics["pressure_drop_pa_mean"] == 107.5
    assert result.report.claims
    assert all(claim.evidence_ids for claim in result.report.claims)


def test_demo_preserves_external_job_ids_and_audit_chain() -> None:
    result = build_demo_service().run_approved_demo(
        "How do curvature and Reynolds number affect bend pressure loss?"
    )

    assert len(result.external_jobs) == 3
    assert set(result.external_jobs) == {"bend-coarse", "bend-medium", "bend-fine"}
    assert result.audit_event_count >= 10


def test_results_analyst_claims_use_analysis_and_simulation_evidence() -> None:
    result = build_demo_service().run_approved_demo(
        "How do curvature and Reynolds number affect bend pressure loss?"
    )

    evidence_ids = {item for claim in result.report.claims for item in claim.evidence_ids}
    assert "analysis:pressure_drop_pa_mean" in evidence_ids
    assert "simulation:bend-fine" in evidence_ids

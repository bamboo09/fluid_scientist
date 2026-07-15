"""Extended test suite — covers scenarios NOT in test_comprehensive.py."""

import sys, os, math

sys.path.insert(0, "d:\\desktop\\AI FOR SCIENCE\\src")
sys.path.insert(0, "d:\\desktop\\AI FOR SCIENCE")

from fluid_scientist.cylinder_flow_2d.pipeline import CylinderFlow2DV1Pipeline
from fluid_scientist.cylinder_flow_2d.models import (
    CylinderFlow2DExperimentSpecV1,
    FieldSource,
    FieldStatus,
    ProvenanceField,
    BoundarySpec,
    SemanticBoundaryType,
    BumpProfileType,
    ObservableType,
)
from fluid_scientist.cylinder_flow_2d.physics_dependency import PhysicsDependencyResolver
from fluid_scientist.cylinder_flow_2d.ambiguity_audit import AmbiguityAndConflictAuditor

pipeline = CylinderFlow2DV1Pipeline()
resolver = PhysicsDependencyResolver()
auditor = AmbiguityAndConflictAuditor()

passed = 0
failed = 0
failures = []

def check(test_id, description, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
    else:
        failed += 1
        failures.append(f"{test_id}: {description} - {detail}")
    print(f"  [{'PASS' if condition else 'FAIL'}] {test_id}: {description}")
    if detail:
        print(f"         {detail}")

def section(title):
    print(f"\n{'='*70}\n  {title}\n{'='*70}")


section("J. Full Derivation Chain")
text_j1 = "二维圆柱绕流，圆柱半径R=0.05m，来流速度1.0m/s，雷诺数200，左速度入口，右压力出口，上下无滑移。"
r_j1 = pipeline.run(text_j1)
d_j1 = r_j1.spec.get_cylinder_diameter()
nu_j1 = r_j1.spec.fluid.kinematic_viscosity_m2_s.value
check("J1", "R=0.05 -> D=0.1 -> nu=0.0005",
      d_j1 is not None and abs(d_j1 - 0.1) < 1e-6 and nu_j1 is not None and abs(nu_j1 - 0.0005) < 1e-6,
      f"D={d_j1}, nu={nu_j1}")

text_j2 = "二维圆柱绕流，圆柱直径0.1m，来流速度1.0m/s，Re=200。"
r_j2 = pipeline.run(text_j2)
check("J2", "D=0.1 -> R=0.05",
      r_j2.spec.get_cylinder_radius() is not None and abs(r_j2.spec.get_cylinder_radius() - 0.05) < 1e-6,
      f"R={r_j2.spec.get_cylinder_radius()}")

spec_j3 = CylinderFlow2DExperimentSpecV1()
spec_j3.cylinder.diameter_m = ProvenanceField(value=0.2, source=FieldSource.FORMULA_DERIVED, status=FieldStatus.RESOLVED, confidence=1.0)
spec_j3.boundaries.left.semantic_type = SemanticBoundaryType.UNIFORM_VELOCITY_INLET
spec_j3.boundaries.left.inlet_velocity = 1.0
spec_j3.fluid.kinematic_viscosity_m2_s = ProvenanceField(value=0.001, source=FieldSource.USER_EXPLICIT, status=FieldStatus.RESOLVED, confidence=1.0)
resolver.resolve(spec_j3)
re_j3 = spec_j3.estimate_reynolds()
check("J3", "nu=0.001, U=1, D=0.2 -> Re=200",
      re_j3 is not None and abs(re_j3 - 200.0) < 1.0, f"Re={re_j3}")

spec_j4 = CylinderFlow2DExperimentSpecV1()
spec_j4.cylinder.diameter_m = ProvenanceField(value=0.1, source=FieldSource.USER_EXPLICIT, status=FieldStatus.RESOLVED, confidence=1.0)
spec_j4.boundaries.left.semantic_type = SemanticBoundaryType.UNIFORM_VELOCITY_INLET
spec_j4.fluid.kinematic_viscosity_m2_s = ProvenanceField(value=0.0005, source=FieldSource.USER_EXPLICIT, status=FieldStatus.RESOLVED, confidence=1.0)
spec_j4.user_input_text = "Re=200"
resolver.resolve(spec_j4)
u_j4 = spec_j4.boundaries.left.inlet_velocity
check("J4", "Re=200, nu=0.0005, D=0.1 -> U=1.0",
      u_j4 is not None and abs(u_j4 - 1.0) < 0.01, f"U={u_j4}")


section("K. Reynolds Boundary Values")
text_k1 = "二维圆柱绕流，圆柱直径0.1m，来流速度0.001m/s，Re=1。"
r_k1 = pipeline.run(text_k1)
check("K1", "Re=1 -> nu=0.0001",
      r_k1.spec.fluid.kinematic_viscosity_m2_s.value is not None and abs(r_k1.spec.fluid.kinematic_viscosity_m2_s.value - 0.0001) < 1e-6,
      f"nu={r_k1.spec.fluid.kinematic_viscosity_m2_s.value}")

text_k2 = "二维圆柱绕流，圆柱直径0.1m，来流速度10m/s，Re=100000。"
r_k2 = pipeline.run(text_k2)
check("K2", "Re=100000 -> nu=0.00001",
      r_k2.spec.fluid.kinematic_viscosity_m2_s.value is not None and abs(r_k2.spec.fluid.kinematic_viscosity_m2_s.value - 0.00001) < 1e-6,
      f"nu={r_k2.spec.fluid.kinematic_viscosity_m2_s.value}")

re_k3 = pipeline._extract_reynolds("Re 200")
check("K3", "'Re 200' -> 200", re_k3 is not None and abs(re_k3 - 200.0) < 1e-6, f"Re={re_k3}")


section("L. delta_t CFL Derivation")
spec_l1 = CylinderFlow2DExperimentSpecV1()
spec_l1.cylinder.diameter_m = ProvenanceField(value=0.1, source=FieldSource.USER_EXPLICIT, status=FieldStatus.RESOLVED, confidence=1.0)
spec_l1.boundaries.left.semantic_type = SemanticBoundaryType.UNIFORM_VELOCITY_INLET
spec_l1.boundaries.left.inlet_velocity = 1.0
spec_l1.fluid.kinematic_viscosity_m2_s = ProvenanceField(value=0.0005, source=FieldSource.FORMULA_DERIVED, status=FieldStatus.RESOLVED, confidence=1.0)
spec_l1.user_input_text = "Re=200"
res_l1 = resolver.resolve(spec_l1)
dt_found = any(d.target_field == "time_step" for d in res_l1.derivations)
check("L1", "delta_t derived from U, D, CFL", dt_found,
      f"derivations: {[d.target_field for d in res_l1.derivations]}")

dt_rec_l2 = next((d for d in res_l1.derivations if d.target_field == "time_step"), None)
check("L2", "delta_t formula contains CFL",
      dt_rec_l2 is not None and "CFL" in dt_rec_l2.formula,
      f"formula: {dt_rec_l2.formula if dt_rec_l2 else 'N/A'}")


section("M. Formula Format Validation")
text_m1 = "二维圆柱绕流，圆柱直径0.1m，来流速度1.0m/s，Re=200。"
r_m1 = pipeline.run(text_m1)
nu_formula = next((d for d in r_m1.decision_summary.derived_values if "viscosity" in d.lower() or "nu" in d.lower() or "粘度" in d), None)
check("M1", "nu formula exists in derived_values", nu_formula is not None,
      f"derived: {r_m1.decision_summary.derived_values[:3]}")

ref_len = next((d for d in r_m1.decision_summary.derived_values if "reference_length" in d.lower() or "参考长度" in d), None)
check("M2", "reference_length derived", ref_len is not None, f"found: {ref_len}")


section("N. X-Position Conflict")
text_n1 = "二维流场，长10米，宽5米，圆柱直径0.1m，圆心(15,2.5)。"
r_n1 = pipeline.run(text_n1)
ar_n1 = auditor.audit(r_n1.spec, text_n1, resolver.resolve(r_n1.spec))
check("N1", "Cylinder x=15 outside domain length=10 -> blocking",
      any(i.blocks for i in ar_n1.issues),
      f"blocking: {[i.code for i in ar_n1.issues if i.blocks]}")

text_n2 = "二维流场，长10米，宽5米，圆柱直径0.1m，圆心(0.05,2.5)。"
r_n2 = pipeline.run(text_n2)
ar_n2 = auditor.audit(r_n2.spec, text_n2, resolver.resolve(r_n2.spec))
check("N2", "Cylinder at x=0.05 -> no false block",
      not any(i.code == "CYLINDER_OUTSIDE_DOMAIN" for i in ar_n2.issues if i.blocks),
      f"blocking: {[i.code for i in ar_n2.issues if i.blocks]}")


section("O. Multiple Conflicts")
text_o1 = "二维流场，长10米，宽5米，圆柱直径0.1m，圆心(15,6)。"
r_o1 = pipeline.run(text_o1)
ar_o1 = auditor.audit(r_o1.spec, text_o1, resolver.resolve(r_o1.spec))
blocking_codes = [i.code for i in ar_o1.issues if i.blocks]
check("O1", "Multiple conflicts: x outside + y outside", len(blocking_codes) >= 2,
      f"blocking: {blocking_codes}")


section("P. Triangle and Boundary Conditions")
text_p1 = "二维流场，长10米，宽5米，圆柱半径0.1m。下壁面三角障碍物宽0.1m。"
r_p1 = pipeline.run(text_p1)
check("P1", "Triangle with only width: height derived from D",
      r_p1.spec.triangle.enabled and r_p1.spec.triangle.height_m.value is not None,
      f"enabled={r_p1.spec.triangle.enabled}, height={r_p1.spec.triangle.height_m.value}")

text_p2 = "二维流场，长10米，宽5米，圆柱半径0.1m。下壁面三角障碍物高0.05m宽0.1m尖端向下。"
r_p2 = pipeline.run(text_p2)
check("P2", "Triangle apex direction 'down'",
      r_p2.spec.triangle.apex_direction == "down", f"apex={r_p2.spec.triangle.apex_direction}")

text_p3 = "二维流场，长10米，宽5米，圆柱直径0.1m。三角障碍物底宽0.3m高0.15m。"
r_p3 = pipeline.run(text_p3)
check("P3", "Triangle full dimensions: bw=0.3, h=0.15",
      r_p3.spec.triangle.base_width_m.value is not None and abs(r_p3.spec.triangle.base_width_m.value - 0.3) < 1e-6
      and r_p3.spec.triangle.height_m.value is not None and abs(r_p3.spec.triangle.height_m.value - 0.15) < 1e-6,
      f"bw={r_p3.spec.triangle.base_width_m.value}, h={r_p3.spec.triangle.height_m.value}")

text_p4 = "二维流场，长10米，宽5米，圆柱直径0.1m，圆心(5,2.5)。底部三角凸起。"
r_p4 = pipeline.run(text_p4)
check("P4", "Triangle center_x derived from cylinder center_x=5",
      r_p4.spec.triangle.center_x_m.value is not None and abs(r_p4.spec.triangle.center_x_m.value - 5.0) < 1e-6,
      f"center_x={r_p4.spec.triangle.center_x_m.value}")

check("P5", "has_triangle=True after derivation",
      r_p4.spec.has_triangle, f"has_triangle={r_p4.spec.has_triangle}")

text_p6 = "二维流场，长10米，宽5米。底部三角凸起。"
r_p6 = pipeline.run(text_p6)
check("P6", "Triangle no cylinder: fallback D=0.2 -> bw=0.4, h=0.2",
      r_p6.spec.triangle.base_width_m.value is not None and abs(r_p6.spec.triangle.base_width_m.value - 0.4) < 1e-6
      and r_p6.spec.triangle.height_m.value is not None and abs(r_p6.spec.triangle.height_m.value - 0.2) < 1e-6,
      f"bw={r_p6.spec.triangle.base_width_m.value}, h={r_p6.spec.triangle.height_m.value}")

text_p7 = "二维流场，长10米，宽5米，圆柱直径0.1m。左右周期边界，上下无滑移。"
r_p7 = pipeline.run(text_p7)
check("P7", "Periodic left+right -> is_periodic",
      r_p7.spec.is_periodic, f"left={r_p7.spec.boundaries.left.semantic_type}")

text_p8 = "二维流场，长10米，宽5米，圆柱直径0.1m。上对称，下无滑移。"
r_p8 = pipeline.run(text_p8)
check("P8", "Symmetry top -> top=SYMMETRY",
      r_p8.spec.boundaries.top.semantic_type == SemanticBoundaryType.SYMMETRY,
      f"top={r_p8.spec.boundaries.top.semantic_type}")


section("Q. Geometry Feasibility")
text_q1 = "二维流场，长10米，宽5米，圆柱直径0.1m，圆心(5,2.5)，来流速度1.0m/s。"
r_q1 = pipeline.run(text_q1)
ar_q1 = auditor.audit(r_q1.spec, text_q1, resolver.resolve(r_q1.spec))
check("Q1", "Valid geometry: no blocking",
      len([i for i in ar_q1.issues if i.blocks]) == 0,
      f"blocking: {[i.code for i in ar_q1.issues if i.blocks]}")

text_q3 = "二维圆柱绕流，圆柱直径0.1m，圆心(5,2.5)，来流速度1.0m/s，Re=200，水，左速度入口，右压力出口，上下无滑移。"
r_q3 = pipeline.run(text_q3)
ar_q3 = auditor.audit(r_q3.spec, text_q3, resolver.resolve(r_q3.spec))
check("Q3", "Standard case: no blocking",
      len([i for i in ar_q3.issues if i.blocks]) == 0,
      f"blocking: {[i.code for i in ar_q3.issues if i.blocks]}")

check("Q4", "Full spec confidence > 0.8",
      r_q3.decision_summary.confidence > 0.8, f"confidence={r_q3.decision_summary.confidence}")

re_q5 = pipeline._extract_reynolds("雷诺数200")
check("Q5", "'雷诺数200' -> 200", re_q5 is not None and abs(re_q5 - 200.0) < 1e-6, f"Re={re_q5}")

re_q6 = pipeline._extract_reynolds("Re=200")
check("Q6", "'Re=200' -> 200", re_q6 is not None and abs(re_q6 - 200.0) < 1e-6, f"Re={re_q6}")

re_q7 = pipeline._extract_reynolds("Reynolds number 200")
check("Q7", "'Reynolds number 200' -> 200", re_q7 is not None and abs(re_q7 - 200.0) < 1e-6, f"Re={re_q7}")

re_q8 = pipeline._extract_reynolds("Re=0")
check("Q8", "'Re=0' rejected", re_q8 is None, f"Re={re_q8}")


section("R. Additional Edge Cases")
r_r1 = pipeline.run("")
check("R1", "Empty text: no crash, low confidence",
      r_r1.decision_summary.confidence < 0.7, f"confidence={r_r1.decision_summary.confidence}")

text_r2 = "二维流场，长10米，宽5米。"
r_r2 = pipeline.run(text_r2)
check("R2", "Only domain: has_cylinder=False", not r_r2.spec.has_cylinder, f"has_cylinder={r_r2.spec.has_cylinder}")

text_r3 = "二维圆柱绕流，直径0.2m，来流速度1.0m/s，Re=500。"
r_r3 = pipeline.run(text_r3)
check("R3", "D=0.2, U=1, Re=500 -> nu=0.0004",
      r_r3.spec.fluid.kinematic_viscosity_m2_s.value is not None and abs(r_r3.spec.fluid.kinematic_viscosity_m2_s.value - 0.0004) < 1e-6,
      f"nu={r_r3.spec.fluid.kinematic_viscosity_m2_s.value}")

spec_dict = r_q3.spec.model_dump()
check("R4", "Spec serialization: no crash",
      "cylinder" in spec_dict and "fluid" in spec_dict, f"keys: {list(spec_dict.keys())[:5]}")

re_r5 = r_q3.spec.estimate_reynolds()
check("R5", "estimate_reynolds() returns 200",
      re_r5 is not None and abs(re_r5 - 200.0) < 1.0, f"Re={re_r5}")

spec_r6 = CylinderFlow2DExperimentSpecV1()
spec_r6.triangle.enabled = True
check("R6", "Triangle enabled but no base_width -> has_triangle=False",
      not spec_r6.has_triangle, f"has_triangle={spec_r6.has_triangle}")

spec_r6.triangle.base_width_m = ProvenanceField(value=0.2, source=FieldSource.USER_EXPLICIT, status=FieldStatus.RESOLVED, confidence=1.0)
check("R7", "Triangle with base_width -> has_triangle=True",
      spec_r6.has_triangle, f"has_triangle={spec_r6.has_triangle}")

res_r8 = resolver.resolve(r_q3.spec)
check("R8", "Multiple derivations for full spec",
      len(res_r8.derivations) >= 5, f"count: {len(res_r8.derivations)}")


section("S. Spec Methods and Properties")
spec_s1 = CylinderFlow2DExperimentSpecV1()
spec_s1.cylinder.radius_m = ProvenanceField(value=0.05, source=FieldSource.USER_EXPLICIT, status=FieldStatus.RESOLVED, confidence=1.0)
check("S1", "get_cylinder_diameter from R=0.05 -> 0.1",
      spec_s1.get_cylinder_diameter() is not None and abs(spec_s1.get_cylinder_diameter() - 0.1) < 1e-6,
      f"D={spec_s1.get_cylinder_diameter()}")

spec_s2 = CylinderFlow2DExperimentSpecV1()
spec_s2.cylinder.diameter_m = ProvenanceField(value=0.2, source=FieldSource.USER_EXPLICIT, status=FieldStatus.RESOLVED, confidence=1.0)
check("S2", "get_cylinder_radius from D=0.2 -> 0.1",
      spec_s2.get_cylinder_radius() is not None and abs(spec_s2.get_cylinder_radius() - 0.1) < 1e-6,
      f"R={spec_s2.get_cylinder_radius()}")

spec_s3 = CylinderFlow2DExperimentSpecV1()
spec_s3.bottom_profile.enabled = True
spec_s3.bottom_profile.profile_type = BumpProfileType.COSINE_BELL
check("S3", "has_bottom_profile=True with cosine_bell", spec_s3.has_bottom_profile, f"has_bottom_profile={spec_s3.has_bottom_profile}")

spec_s4 = CylinderFlow2DExperimentSpecV1()
spec_s4.bottom_profile.enabled = True
spec_s4.bottom_profile.profile_type = BumpProfileType.FLAT
check("S4", "FLAT profile -> has_bottom_profile=False", not spec_s4.has_bottom_profile, f"has_bottom_profile={spec_s4.has_bottom_profile}")

spec_s5 = CylinderFlow2DExperimentSpecV1()
spec_s5.boundaries.left.semantic_type = SemanticBoundaryType.PERIODIC
spec_s5.boundaries.right.semantic_type = SemanticBoundaryType.PERIODIC
check("S5", "Periodic left+right -> is_periodic=True", spec_s5.is_periodic, f"is_periodic={spec_s5.is_periodic}")

spec_s6 = CylinderFlow2DExperimentSpecV1()
spec_s6.boundaries.left.semantic_type = SemanticBoundaryType.PERIODIC
spec_s6.boundaries.right.semantic_type = SemanticBoundaryType.PRESSURE_OUTLET
check("S6", "Only left periodic -> is_periodic=False", not spec_s6.is_periodic, f"is_periodic={spec_s6.is_periodic}")


section("T. Pipeline Extraction Edge Cases")
d_t1 = pipeline._extract_diameter("圆柱直径=0.1m")
check("T1", "'直径=0.1m' -> 0.1", d_t1 is not None and abs(d_t1 - 0.1) < 1e-6, f"D={d_t1}")

d_t2 = pipeline._extract_diameter("直径0.1m")
check("T2", "'直径0.1m' (no =) -> 0.1", d_t2 is not None and abs(d_t2 - 0.1) < 1e-6, f"D={d_t2}")

r_t3 = pipeline._extract_radius("半径R=0.05m")
check("T3", "'半径R=0.05m' -> 0.05", r_t3 is not None and abs(r_t3 - 0.05) < 1e-6, f"R={r_t3}")

u_t4 = pipeline._extract_inlet_velocity("来流速度1.0m/s")
check("T4", "'来流速度1.0m/s' -> 1.0", u_t4 is not None and abs(u_t4 - 1.0) < 1e-6, f"U={u_t4}")

u_t5 = pipeline._extract_inlet_velocity("inlet velocity 1.0m/s")
check("T5", "English 'inlet velocity 1.0m/s' -> 1.0", u_t5 is not None and abs(u_t5 - 1.0) < 1e-6, f"U={u_t5}")

dom_t6 = pipeline._extract_domain("长10米，宽5米")
check("T6", "'长10米，宽5米' -> (10, 5)",
      dom_t6 is not None and dom_t6.get("length") is not None and dom_t6.get("height") is not None and abs(dom_t6["length"] - 10) < 1e-6 and abs(dom_t6["height"] - 5) < 1e-6,
      f"domain={dom_t6}")

pos_t7 = pipeline._extract_cylinder_position("圆心(5,2.5)")
cx_t7, cy_t7 = (pos_t7.get("x"), pos_t7.get("y")) if pos_t7 else (None, None)
check("T7", "'圆心(5,2.5)' -> (5, 2.5)",
      cx_t7 is not None and cy_t7 is not None and abs(cx_t7 - 5) < 1e-6 and abs(cy_t7 - 2.5) < 1e-6,
      f"center=({cx_t7}, {cy_t7})")

tri_t8 = pipeline._extract_triangle("三角障碍物底宽0.3m高0.15m")
check("T8", "Triangle extraction: bw=0.3, h=0.15",
      tri_t8 is not None and tri_t8.get("base_width") is not None and abs(tri_t8["base_width"] - 0.3) < 1e-6
      and tri_t8.get("height") is not None and abs(tri_t8["height"] - 0.15) < 1e-6,
      f"tri={tri_t8}")


section("SUMMARY")
print(f"\n  Total: {passed + failed} tests")
print(f"  Passed: {passed}")
print(f"  Failed: {failed}")
if failures:
    print(f"\n  Failures:")
    for f in failures:
        print(f"    -> {f}")
print(f"\n  Result: {'ALL PASS' if failed == 0 else 'HAS FAILURES'}")
sys.exit(0 if failed == 0 else 1)

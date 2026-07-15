"""LLM Diagnoser — uses LLM to diagnose OpenFOAM errors and suggest fixes.

Sends error context to LLM with a structured prompt, receives:
- Root cause analysis
- Specific fix instructions
- Which files to modify
- What changes to make
"""

from __future__ import annotations

import json
from typing import Any

from fluid_scientist.repair.error_classifier import ClassifiedError

_DIAGNOSIS_SYSTEM_PROMPT = """你是一个OpenFOAM错误诊断专家。你的任务是分析仿真错误日志，找出根本原因，并提出具体的修复方案。

## 诊断规则

1. **基于错误日志诊断** — 只根据日志中的实际错误信息提出修复，不得猜测
2. **具体到文件和行** — 修复方案必须指明修改哪个文件的哪个参数
3. **不改变用户意图** — 修复不得改变用户原始描述的物理设置（几何类型、边界条件语义等）
4. **最小化修改** — 只修改导致错误的最小必要内容
5. **给出修复后的完整文件内容** — 如果需要修改文件，给出修复后的完整文件内容

## 常见错误模式

- **CFL过高**: 减小deltaT或启用adjustTimeStep
- **边界条件不匹配**: 检查0/文件夹中的patch名与constant/polyMesh/boundary一致
- **NaN**: 检查物理参数（nu是否为0）、网格质量、边界条件类型
- **网格错误**: 检查blockMeshDict的vertices和blocks定义
- **字典语法错误**: 检查分号、大括号、关键词拼写

## 输出JSON Schema

```json
{
  "root_cause": "根本原因描述",
  "error_category": "mesh_error|boundary_condition_error|solver_error|physics_error|syntax_error|file_error",
  "fix_strategy": "config_only|dictionary_syntax|partial_regeneration",
  "fixes": [
    {
      "file": "system/controlDict",
      "parameter": "deltaT",
      "old_value": "0.01",
      "new_value": "0.001",
      "reason": "CFL number too high, reduce time step"
    }
  ],
  "regenerate_files": ["文件列表，仅当strategy=partial_regeneration时"],
  "confidence": 0.0-1.0,
  "warnings": ["需要注意的警告"]
}
```

## 严格禁止

- 不得建议将triangle改为cosine_bell或其他几何类型
- 不得建议改变用户明确指定的边界条件类型
- 不得建议删除用户明确要求的观测指标
- 不得返回空修复方案（如果没有修复建议，说明原因）
"""


class LLMDiagnoser:
    """Uses LLM to diagnose OpenFOAM errors."""

    def __init__(self, llm_client: Any | None = None) -> None:
        self._llm_client = llm_client

    def diagnose(
        self,
        context: dict[str, Any],
        llm_client: Any | None = None,
    ) -> dict[str, Any]:
        """Diagnose an error using LLM.

        Args:
            context: Repair context from RepairContextBuilder
            llm_client: Optional LLM client (uses init client if not provided)

        Returns:
            Diagnosis result dict with root_cause, fixes, etc.
        """
        client = llm_client or self._llm_client
        if client is None:
            return {
                "root_cause": "LLM client not available",
                "error_category": "unknown",
                "fix_strategy": "none",
                "fixes": [],
                "confidence": 0.0,
                "warnings": ["LLM client not configured — cannot diagnose"],
            }

        # Build the user message from context
        user_message = self._build_diagnosis_message(context)

        output_schema = {
            "type": "object",
            "properties": {
                "root_cause": {"type": "string"},
                "error_category": {"type": "string"},
                "fix_strategy": {"type": "string"},
                "fixes": {"type": "array"},
                "regenerate_files": {"type": "array"},
                "confidence": {"type": "number"},
                "warnings": {"type": "array"},
            },
            "required": ["root_cause", "fix_strategy", "fixes"],
        }

        try:
            parsed, record = client.call(
                purpose="explanation",
                prompt_name="of_error_diagnosis",
                system_prompt=_DIAGNOSIS_SYSTEM_PROMPT,
                user_message=user_message,
                output_schema=output_schema,
                session_id=context.get("session_id", ""),
                prompt_version="repair-diag-v1",
            )

            if not record.success:
                return {
                    "root_cause": f"LLM call failed: {record.error}",
                    "error_category": "unknown",
                    "fix_strategy": "none",
                    "fixes": [],
                    "confidence": 0.0,
                    "warnings": ["LLM diagnosis failed"],
                }

            return parsed

        except Exception as e:
            return {
                "root_cause": f"Diagnosis exception: {e}",
                "error_category": "unknown",
                "fix_strategy": "none",
                "fixes": [],
                "confidence": 0.0,
                "warnings": [f"Exception during diagnosis: {e}"],
            }

    def _build_diagnosis_message(self, context: dict[str, Any]) -> str:
        """Build the user message for LLM diagnosis."""
        parts: list[str] = []

        error = context.get("error", {})
        parts.append(f"## 错误信息\n```\n{error.get('error_message', 'Unknown error')}\n```")
        parts.append(f"错误类别: {error.get('category', 'unknown')}")
        parts.append(f"失败阶段: {context.get('stage', 'unknown')}")

        if error.get("raw_log"):
            parts.append(f"\n## 错误日志（最后500字符）\n```\n{error['raw_log'][:500]}\n```")

        spec = context.get("spec_summary", {})
        if spec:
            parts.append(f"\n## 当前Spec摘要\n```json\n{json.dumps(spec, ensure_ascii=False, indent=2)}\n```")

        files = context.get("files", {})
        if files:
            parts.append("\n## 相关文件内容")
            for name, content in files.items():
                parts.append(f"\n### {name}\n```\n{content}\n```")

        prev = context.get("previous_attempts", [])
        if prev:
            parts.append("\n## 之前的修复尝试（避免重复）")
            for i, attempt in enumerate(prev):
                parts.append(f"\n尝试{i+1}: {attempt.get('fix_applied', 'unknown')} — 结果: {'成功' if attempt.get('retry_passed') else '失败'}")

        parts.append("\n## 用户原始输入")
        parts.append(context.get("user_original_input", "（未提供）"))

        return "\n".join(parts)

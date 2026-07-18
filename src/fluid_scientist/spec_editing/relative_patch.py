"""相对 Patch 表达式 (RelativePatchExpression)。

本模块提供一种**相对值修改**能力：当用户希望"把时间步长减半"、"把来流
速度加倍"时，模型无需自己做算术（容易出错），而是发出一个相对表达式，
由本模块依据**当前 spec 的实际取值**计算出新值。

支持的操作
-----------
``multiply`` / ``divide`` / ``add`` / ``subtract``。

表达式格式
----------

1. 直接表达式（推荐）::

       {"operator": "multiply", "path": "/numerics/time/delta_t", "factor": 0.5}

2. 包装表达式（与 :class:`~fluid_scientist.spec_editing.quantity_resolver.QuantityResolver`
   的嵌套 Quantity 风格一致）::

       {"expression": {"operator": "multiply", "path": "/numerics/time/delta_t", "factor": 0.5}}

与 ``QuantityResolver`` 的关系
------------------------------
:class:`~fluid_scientist.spec_editing.quantity_resolver.QuantityResolver` 是
**PatchEngine 内部**使用的相对表达式解析器，它把表达式嵌入到
:class:`PatchOperation.value` 中随 patch 流转。

本模块则提供一个**独立、可复用**的 Pydantic 模型 + 函数式入口
``apply()``，供不需要走完整 PatchEngine 的场景（例如
``cylinder_flow_router.modify`` 端点）直接调用。两者复用同一套 JSON Pointer
读取与数值规整逻辑，行为保持一致。

设计原则
--------
* **永不静默回退**：表达式无法解析（路径缺失、当前值非数值、缺少操作数、
  除零）时抛出 :class:`RelativePatchError`，由调用方决定如何提示用户。
* **不修改输入 spec**：``apply`` 仅返回新值，不就地改动 spec；写回 spec
  是调用方的职责。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fluid_scientist.compat import StrEnum

__all__ = [
    "RelativeOperator",
    "RelativePatchError",
    "RelativePatchExpression",
    "apply",
]


class RelativeOperator(StrEnum):
    """相对表达式支持的算术操作。"""

    MULTIPLY = "multiply"
    DIVIDE = "divide"
    ADD = "add"
    SUBTRACT = "subtract"


class RelativePatchError(ValueError):
    """相对表达式无法解析时抛出。"""


class RelativePatchExpression(BaseModel):
    """一个相对值修改表达式。

    根据当前 spec 中 ``path`` 处的取值，应用 ``operator`` 计算**新值**。

    Parameters
    ----------
    operator:
        算术操作，见 :class:`RelativeOperator`。
    path:
        JSON Pointer 路径，指向当前 spec 中被参照的字段，例如
        ``"/numerics/time/delta_t"``。新值 = 该路径当前值 op 操作数。
    factor:
        ``multiply`` / ``divide`` 的操作数。二者至少提供一个。
    addend:
        ``add`` / ``subtract`` 的操作数。二者至少提供一个。
    value:
        通用操作数别名：当 ``factor`` / ``addend`` 均缺失时回退使用。

    Examples
    --------
    >>> expr = RelativePatchExpression(
    ...     operator="multiply", path="/numerics/time/delta_t", factor=0.5
    ... )
    >>> expr.apply({"numerics": {"time": {"delta_t": 0.02}}})
    0.01
    """

    model_config = ConfigDict(extra="forbid")

    operator: RelativeOperator
    path: str
    factor: float | None = None
    addend: float | None = None
    value: float | None = None

    @model_validator(mode="after")
    def _ensure_operand(self) -> "RelativePatchExpression":
        """校验：至少提供一个可用操作数。"""
        if self.operator in (RelativeOperator.MULTIPLY, RelativeOperator.DIVIDE):
            if self.factor is None and self.value is None:
                raise RelativePatchError(
                    f"operator '{self.operator}' requires 'factor' "
                    f"(or 'value') for path '{self.path}'."
                )
        else:  # add / subtract
            if self.addend is None and self.value is None:
                raise RelativePatchError(
                    f"operator '{self.operator}' requires 'addend' "
                    f"(or 'value') for path '{self.path}'."
                )
        return self

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def apply(self, current_spec: dict[str, Any]) -> float:
        """依据 *current_spec* 中 ``path`` 处的当前值计算新值。

        Parameters
        ----------
        current_spec:
            当前 spec 的字典形式（例如 ``spec.model_dump()``）。

        Returns
        -------
        计算后的新值（``float``）。

        Raises
        ------
        RelativePatchError
            若路径不存在、当前值非数值、或除零。
        """
        return _compute(self, current_spec, target_path=self.path)


# ---------------------------------------------------------------------------
# 模块级函数式入口
# ---------------------------------------------------------------------------


def apply(current_spec: dict[str, Any], expression: Any) -> float:
    """根据 *expression* 在 *current_spec* 上计算相对新值。

    函数式入口，兼容三种 *expression* 形态：

    1. :class:`RelativePatchExpression` 实例；
    2. 直接表达式 dict，例如
       ``{"operator": "multiply", "path": "...", "factor": 0.5}``；
    3. 包装表达式 dict，例如
       ``{"expression": {"operator": "multiply", ...}}``。

    Parameters
    ----------
    current_spec:
        当前 spec 的字典形式。
    expression:
        相对表达式（实例或 dict）。

    Returns
    -------
    计算后的新值（``float``）。

    Raises
    ------
    RelativePatchError
        表达式非法或无法解析时抛出。
    """
    expr = _coerce_expression(expression)
    return _compute(expr, current_spec, target_path=expr.path)


# ---------------------------------------------------------------------------
# 内部实现
# ---------------------------------------------------------------------------


def _coerce_expression(expression: Any) -> RelativePatchExpression:
    """把任意形态的表达式规整为 :class:`RelativePatchExpression`。"""
    if isinstance(expression, RelativePatchExpression):
        return expression

    if not isinstance(expression, dict):
        raise RelativePatchError(
            f"expression must be a RelativePatchExpression or dict, "
            f"got {type(expression).__name__}."
        )

    # 包装形态：{"expression": {...}}
    if "expression" in expression and isinstance(expression["expression"], dict):
        inner = expression["expression"]
    else:
        inner = expression

    try:
        return RelativePatchExpression.model_validate(inner)
    except Exception as exc:  # pydantic ValidationError 等
        raise RelativePatchError(f"invalid relative patch expression: {exc}") from exc


def _compute(
    expr: RelativePatchExpression,
    current_spec: dict[str, Any],
    target_path: str,
) -> float:
    """读取当前值并应用算术操作。"""
    current_value = _read_value_at_path(current_spec, expr.path)
    if current_value is None:
        raise RelativePatchError(
            f"cannot resolve expression for '{target_path}': source path "
            f"'{expr.path}' has no value in the current spec."
        )

    numeric_current = _to_number(current_value)
    if numeric_current is None:
        raise RelativePatchError(
            f"cannot resolve expression for '{target_path}': source path "
            f"'{expr.path}' contains a non-numeric value ({current_value!r})."
        )

    if expr.operator in (RelativeOperator.MULTIPLY, RelativeOperator.DIVIDE):
        operand = expr.factor if expr.factor is not None else expr.value
    else:  # add / subtract
        operand = expr.addend if expr.addend is not None else expr.value

    if operand is None:
        # 理论上 _ensure_operand 已挡住，这里做防御性检查。
        raise RelativePatchError(
            f"expression for path '{target_path}' with operator "
            f"'{expr.operator}' is missing the operand."
        )

    if expr.operator == RelativeOperator.ADD:
        return float(numeric_current + operand)
    if expr.operator == RelativeOperator.SUBTRACT:
        return float(numeric_current - operand)
    if expr.operator == RelativeOperator.MULTIPLY:
        return float(numeric_current * operand)
    # divide
    if operand == 0:
        raise RelativePatchError(
            f"expression for path '{target_path}' attempts to divide by zero."
        )
    return float(numeric_current / operand)


def _read_value_at_path(spec: dict[str, Any], json_pointer: str) -> Any:
    """按 JSON Pointer 读取 spec 中的值。

    与 ``QuantityResolver._read_value_at_path`` 行为一致：支持 ``-`` 哨兵
    （取数组最后一个元素）。
    """
    if not json_pointer or json_pointer == "/":
        return spec

    parts = json_pointer.lstrip("/").split("/")
    current: Any = spec
    for part in parts:
        if part == "-":
            if isinstance(current, list) and current:
                current = current[-1]
            else:
                return None
            continue
        if isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
        elif isinstance(current, list):
            try:
                idx = int(part)
            except ValueError:
                return None
            if idx < 0 or idx >= len(current):
                return None
            current = current[idx]
        else:
            return None
    return current


def _to_number(value: Any) -> float | None:
    """把值规整为 ``float``，处理裸数值与 Quantity/SourcedValue 字典。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, dict):
        inner = value.get("value")
        if isinstance(inner, bool):
            return None
        if isinstance(inner, int | float):
            return float(inner)
    return None

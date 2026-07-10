"""AST validator for the minimal CodeAct sandbox."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field


class CodeValidationError(ValueError):
    """Raised when user code violates sandbox rules."""


@dataclass
class ASTCodeValidator(ast.NodeVisitor):
    """Validate a small, side-effect-limited Python subset."""

    max_nodes: int = 256
    max_for_loops: int = 8
    allowed_call_names: set[str] = field(
        default_factory=lambda: {
            "float",
            "int",
            "len",
            "max",
            "min",
            "print",
            "range",
            "sorted",
            "str",
            "sum",
        }
    )
    allowed_math_attributes: set[str] = field(
        default_factory=lambda: {
            "ceil",
            "cos",
            "e",
            "exp",
            "fabs",
            "floor",
            "log",
            "log10",
            "pi",
            "sin",
            "sqrt",
            "tan",
        }
    )
    allowed_math_call_attributes: set[str] = field(
        default_factory=lambda: {
            "ceil",
            "cos",
            "exp",
            "fabs",
            "floor",
            "log",
            "log10",
            "sin",
            "sqrt",
            "tan",
        }
    )
    forbidden_call_names: set[str] = field(
        default_factory=lambda: {
            "__import__",
            "compile",
            "eval",
            "exec",
            "open",
        }
    )
    _for_loop_count: int = 0

    def validate(self, code: str) -> ast.Module:
        if not isinstance(code, str) or not code.strip():
            raise CodeValidationError("code must be a non-empty string")
        try:
            tree = ast.parse(code, mode="exec")
        except SyntaxError as exc:
            raise CodeValidationError(f"syntax error: {exc.msg}") from exc

        if sum(1 for _ in ast.walk(tree)) > self.max_nodes:
            raise CodeValidationError("code is too complex")

        self._for_loop_count = 0
        self.visit(tree)
        return tree

    def generic_visit(self, node: ast.AST) -> None:
        forbidden_nodes = (
            ast.AsyncFor,
            ast.AsyncFunctionDef,
            ast.AsyncWith,
            ast.Await,
            ast.ClassDef,
            ast.Delete,
            ast.FunctionDef,
            ast.GeneratorExp,
            ast.Global,
            ast.Import,
            ast.ImportFrom,
            ast.Lambda,
            ast.ListComp,
            ast.Match,
            ast.NamedExpr,
            ast.Nonlocal,
            ast.Raise,
            ast.SetComp,
            ast.Try,
            ast.While,
            ast.With,
            ast.Yield,
            ast.YieldFrom,
        )
        if isinstance(node, forbidden_nodes):
            raise CodeValidationError(f"{type(node).__name__} is not allowed")
        super().generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("__"):
            raise CodeValidationError("dunder attribute access is not allowed")
        if not isinstance(node.value, ast.Name) or node.value.id != "math":
            raise CodeValidationError("only preloaded math attributes are allowed")
        if node.attr not in self.allowed_math_attributes:
            raise CodeValidationError(f"math.{node.attr} is not allowed")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name):
            if func.id in self.forbidden_call_names:
                raise CodeValidationError(f"{func.id} is not allowed")
            if func.id not in self.allowed_call_names:
                raise CodeValidationError(f"call to {func.id} is not allowed")
        elif isinstance(func, ast.Attribute):
            if (
                not isinstance(func.value, ast.Name)
                or func.value.id != "math"
                or func.attr not in self.allowed_math_call_attributes
            ):
                raise CodeValidationError("only selected math calls are allowed")
        else:
            raise CodeValidationError("dynamic call targets are not allowed")
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self._for_loop_count += 1
        if self._for_loop_count > self.max_for_loops:
            raise CodeValidationError("too many for loops")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id.startswith("__"):
            raise CodeValidationError("dunder names are not allowed")
        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert) -> None:
        raise CodeValidationError("assert is not allowed")

    def visit_ListComp(self, node: ast.ListComp) -> None:
        raise CodeValidationError("comprehensions are not allowed")

    def visit_DictComp(self, node: ast.DictComp) -> None:
        raise CodeValidationError("comprehensions are not allowed")

    def visit_SetComp(self, node: ast.SetComp) -> None:
        raise CodeValidationError("comprehensions are not allowed")

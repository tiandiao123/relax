from __future__ import annotations

import inspect
from typing import TypeVar, Generic, Union
from io import StringIO

import tvm
from tvm.relay.base import Id
from tvm.relax import expr, op
from tvm.ir import diagnostics
from tvm import tir

import numpy as np

import synr
from synr import ast, Transformer
from synr.diagnostic_context import DiagnosticContext

from .compile import Compiler

def print_ty(ty):
    if isinstance(ty, expr.Dim):
        return "Dim"
    elif isinstance(ty, expr.Tensor):
        return "Tensor"
    else:
        return "UNKNOWN"

def print_fn(func):
    buffer = StringIO("")
    param_str = ""
    for param in func.params:
        param_str += f"{param.id.name_hint}: {print_ty(param.ty)}, "

    buffer.write(f"fn {func.name}({param_str}) {{\n")
    buffer.write(f"{func.body}\n")
    buffer.write("}")
    return buffer.getvalue()

expr.Function.__str__ = print_fn

class R2Transformer(Transformer):
    def __init__(self, definition_scope, diag_ctx):
        self.definition_scope = definition_scope
        self.diag_ctx = diag_ctx
        self.str_to_var = {}
        self.blocks = []
        self.module = {}
        super().__init__()

    def span_to_span(self, span):
        src_name = self.diag_ctx.str_to_source_name[span.filename]
        tvm_span = tvm.ir.Span(src_name, span.start_line, span.end_line, span.start_column, span.end_column)
        return tvm_span

    def decl_var(self, name, ty, span=None):
        identifier = Id(name)
        var = expr.Var(identifier, ty, span)
        self.str_to_var[name] = var
        return var

    def to_type(self, ty):
        if ty is None:
            return None

        if isinstance(ty, ast.TypeVar):
            if ty.id.name == "Tensor":
                span = self.span_to_span(ty.span)
                return expr.Tensor(None, None, span)

        if isinstance(ty, ast.TypeApply):
            if ty.id.name == "Tensor":
                dims = []
                # TODO(@jroesch): add support for dtype
                for param in ty.params:
                    if isinstance(param, ast.TypeConstant):
                        dim = expr.TIRExpr(tir.IntImm("int32", param.value), None)
                        dims.append(dim)

                return expr.Tensor(expr.Tuple(dims, span=None), None, None)

        import pdb; pdb.set_trace()


        self._diagnostic_context.emit('error', "invalid type", ty.span)
        self._diagnostic_context.render()

    def transform_module(self, mod: ast.Module) -> M:
        for func_name in mod.funcs:
            func = mod.funcs[func_name]
            self.module[func_name] = self.transform_function(func)
        return self.module

    def transform_function(self, func: ast.Function) -> F:
        params = []
        for param in func.params:
            ty = self.to_type(param.ty)
            param = self.decl_var(param.name, ty, None)
            params.append(param)
        new_body = self.transform_block(func.body)
        return expr.Function(func.name, params, new_body, None, None)

    def transform_stmt(self, stmt: ast.Stmt) -> S:
        if isinstance(stmt, ast.Assign):
            assert isinstance(stmt.lhs, ast.Var)
            lhs = self.decl_var(stmt.lhs.id.name, None, None)
            rhs = self.transform_expr(stmt.rhs)
            self.blocks[-1].append(expr.Binding(lhs, rhs))
            return None
        elif isinstance(stmt, ast.Return):
            return self.transform_expr(stmt.value)
        else:
            self._diagnostic_context.emit('error', "only variable left-hand sides are supported in Relay", stmt.span)
            self._diagnostic_context.render()

    def transform_expr(self, exp: ast.Expr) -> E:
        if isinstance(exp, ast.Call):
            if isinstance(exp.func_name, ast.Var):
                params = []
                for arg in exp.params:
                    params.append(self.transform_expr(arg))

                if exp.func_name.id.name == "broadcast_shape":
                    if len(params) != 2:
                        self._diagnostic_context.emit('error', f"broadcast_shape only takes 2 arguments {params.len()}", exp.span)
                        self._diagnostic_context.render()
                    return expr.BroadcastShape(params[0], params[1], span=None)
                elif exp.func_name.id.name == "compute":
                    if len(params) != 2:
                        self._diagnostic_context.emit('error', f"compute only takes 2 arguments {params.len()}", exp.span)
                        self._diagnostic_context.render()
                    return expr.Compute(params[0], params[1], span=None)
                else:
                    if exp.func_name.id.name in self.str_to_var:
                        return self.str_to_var[exp.func_name.id.name]
                    else:
                        name = exp.func_name.id.name
                        relax_fn = getattr(self.definition_scope, name, None)
                        # builtin operator
                        if relax_fn is None:
                            return expr.Call(op.Op.get(name), params, None)
                        else:
                            self.module[name] = relax_fn.module[name]
                            # todo: globalvar equality? use global str -> id map?
                            ident = Id(exp.func_name.id.name)
                            return expr.Call(expr.GlobalVar(ident, None, None), params, None)

                    self._diagnostic_context.emit('error', f"unknown functionc all {len(params)}", exp.span)
                    self._diagnostic_context.render()
            elif isinstance(exp.func_name, ast.Op):
                if exp.func_name.name == ast.BuiltinOp.Subscript:
                    tensor = self.transform_expr(exp.params[0])
                    indicies = []
                    for index in exp.params[1].values:
                        indicies.append(self.transform_expr(index))
                    return expr.TensorSlice(tensor, indicies, None)
                elif exp.func_name.name == ast.BuiltinOp.Add:
                    params = []
                    for arg in exp.params:
                        params.append(self.transform_expr(arg))
                    return expr.Add(params[0], params[1], None)

            self._diagnostic_context.emit('error', "unsupported function", exp.span)
            self._diagnostic_context.render()
        elif isinstance(exp, ast.Attr):
            field_name = exp.field.name
            tensor = self.transform_expr(exp.object)

            if field_name == "shape":
                return expr.ShapeOf(tensor, None)
            else:
                self._diagnostic_context.emit('error', "unsupported function", exp.span)
                self._diagnostic_context.render()
        elif isinstance(exp, ast.Function):
            print(exp)
            return self.transform_function(exp)
        elif isinstance(exp, ast.Tuple):
            assert False
        elif isinstance(exp, ast.Var):
            return self.str_to_var[exp.id.name]
        else:
            self._diagnostic_context.emit('error', f"don't support this construct {type(exp)}", exp.span)
            self._diagnostic_context.render()

    def enter_block(self):
        self.blocks.append([])

    def exit_block(self):
        back = self.blocks[-1]
        self.blocks.pop()
        return back

    def transform_block(self, block: ast.Block) -> B:
        self.enter_block()

        for stmt in block.stmts[:-1]:
            assert self.transform_stmt(stmt) is None

        ret_expr = self.transform_stmt(block.stmts[-1])
        # assert ret_expr is not None

        bindings = self.exit_block()
        return expr.Let(bindings, ret_expr, span=None)

    def transform_parameter(self, expr: ast.Parameter) -> P:
        pass

    def transform_type(self, ty: ast.Type) -> T:
        pass

class TVMDiagnosticContext(synr.DiagnosticContext):
    def __init__(self, tvm_diag_ctx):
        self.tvm_diag_ctx = tvm_diag_ctx
        self.str_to_source_name = {}

    def add_source(self, name: str, source: str) -> None:
        """Add a file with source code to the context. This will be called
        before any call to :py:func:`emit` that contains a span in this
        file.
        """
        src_name = self.tvm_diag_ctx.module.source_map.add(name, source)
        self.str_to_source_name[name] = src_name

    def emit(self, level: str, message: str, span: Span) -> None:
        """Called when an error has occured."""

        if level == "error":
            level = diagnostics.DiagnosticLevel.ERROR
        elif level == "bug":
            level = diagnostics.DiagnosticLevel.BUG
        elif level == "warning":
            level = diagnostics.DiagnosticLevel.WARNING
        else:
            level = "error"

        assert span, "Span must not be null"

        diag = diagnostics.Diagnostic(level, span, message)

        self.tvm_diag_ctx.emit(diag)

    def render(self) -> Optional[Any]:
        """Render out all error messages. Can either return a value or raise
        and execption.
        """
        self.tvm_diag_ctx.render()

class RelaxDecoratedFn:
    def __init__(self, fn_name, relax_module, diag_ctx):
        self.fn_name = fn_name
        self.module = relax_module
        self.diag_ctx = diag_ctx

    def __call__(self, *args):
        compiler = Compiler(self.diag_ctx, self.module, self.fn_name)
        compiled_f = compiler.compile(execute=True)
        # Actually compute needed buffer sizes.
        out = tvm.nd.array(np.random.rand(10).astype('float32'))
        import pdb; pdb.set_trace()
        compiled_f(*(list(args) + [out]))
        return out

def r2(f):
    ir_module = tvm.IRModule({})
    diag_ctx = diagnostics.DiagnosticContext(ir_module, diagnostics.get_renderer())
    diag_ctx = TVMDiagnosticContext(diag_ctx)
    ast = synr.to_ast(f, diag_ctx)
    definition_scope = inspect.getmodule(f)
    # Why have diag context at transform time? TK?
    module = R2Transformer(definition_scope, diag_ctx).do_transform(ast, diag_ctx)
    return RelaxDecoratedFn(f.__name__, module, diag_ctx)
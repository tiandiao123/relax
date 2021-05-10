# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
# pylint: disable=unused-argument,invalid-name
"""The base node types for the Relay language."""
import tvm._ffi
import tvm.ir
from tvm.driver import lower, build
from tvm.target import get_native_generic_func, GenericFunc
from tvm.runtime import Object
from .. import expr
from . import _ffi_api

@tvm._ffi.register_object("relax.Op")
class Op(expr.Expr):
    """Primitive operator in the IR."""

    def __init__(self):
        raise RuntimeError("Cannot create op, use get instead")

    @staticmethod
    def register(op_name):
        return _ffi_api.Register(op_name)

    @staticmethod
    def get(op_name):
        """Get the Op for a given name

        Parameters
        ----------
        op_name : str
            The operator name

        Returns
        -------
        op : Op
            The op of the corresponding name
        """
        return _ffi_api.GetOp(op_name)

    def get_attr(self, attr_name):
        """Get additional attribute about the operator.

        Parameters
        ----------
        attr_name : str
            The attribute name.

        Returns
        -------
        value : object
            The attribute value
        """
        return _ffi_api.OpGetAttr(self, attr_name)

    def set_attr(self, attr_name, value, plevel=10):
        """Set attribute about the operator.

        Parameters
        ----------
        attr_name : str
            The attribute name

        value : object
            The attribute value

        plevel : int
            The priority level
        """
        _ffi_api.OpSetAttr(self, attr_name, value, plevel)

    def reset_attr(self, attr_name):
        """Reset attribute about the operator.

        Parameters
        ----------
        attr_name : str
            The attribute name
        """
        _ffi_api.OpResetAttr(self, attr_name)


def register_op_attr(op_name, attr_key, value=None, level=10):
    """Register an operator property of an operator by name.

    Parameters
    ----------
    op_name : str
        The name of operator

    attr_key : str
        The attribute name.

    value : object, optional
        The value to set

    level : int, optional
        The priority level

    Returns
    -------
    fregister : function
        Register function if value is not specified.
    """

    def _register(v):
        """internal register function"""
        _ffi_api.RegisterOpAttr(op_name, attr_key, v, level)
        return v

    return _register(value) if value is not None else _register

def get(op_name):
    """Get the Op for a given name

    Parameters
    ----------
    op_name : str
        The operator name

    Returns
    -------
    op : Op
        The op of the corresponding name
    """
    return tvm.ir.Op.get(op_name)


class OpPattern(object):
    """Operator generic patterns

    See Also
    --------
    top.tag : Contains explanation of the tag type.
    """

    # Elementwise operator
    ELEMWISE = 0
    # Broadcast operator
    BROADCAST = 1
    # Injective mapping
    INJECTIVE = 2
    # Communication
    COMM_REDUCE = 3
    # Complex op, can still fuse ewise into it
    OUT_ELEMWISE_FUSABLE = 4
    # Represents tuple node
    TUPLE = 7
    # Not fusable opaque op
    OPAQUE = 8


# @tvm._ffi.register_object("relax.OpImplementation")
# class OpImplementation(Object):
#     """Operator implementation"""

#     def compute(self, attrs, inputs, out_type):
#         """Call compute function.

#         Parameters
#         ----------
#         attrs : Attrs
#             Op attributes.

#         inputs : list[te.tensor.Tensor]
#             The input tensors.

#         out_type : relax.Type
#             The output type.

#         Returns
#         -------
#         outs : list[te.tensor.Tensor]
#             The output tensors.
#         """
#         return _OpImplementationCompute(self, attrs, inputs, out_type)

#     def schedule(self, attrs, outs, target):
#         """Call schedule function.

#         Parameters
#         ----------
#         attrs : Attrs
#             Op attributes.

#         outs : list[te.tensor.Tensor]
#             The output tensors.

#         target : tvm.target.Target
#             The target to schedule the op.

#         Returns
#         -------
#         schedule : tvm.te.Schedule
#             The schedule.
#         """
#         return _OpImplementationSchedule(self, attrs, outs, target)


# @tvm._ffi.register_object("relax.OpSpecialization")
# class OpSpecialization(Object):
#     """Operator specialization"""


# @tvm._ffi.register_object("relax.OpStrategy")
# class OpStrategy(Object):
#     """Operator strategy"""

#     def __init__(self):
#         self.__init_handle_by_constructor__(_make.OpStrategy)

#     def add_implementation(self, compute, schedule, name="default", plevel=10):
#         """Add an implementation to the strategy

#         Parameters
#         ----------
#         compute : function (attrs: Attrs, inputs: List[Tensor], out_type: Type)
#                            -> List[Tensor]
#             The compute function.

#         schedule : function (attrs: Attrs, outs: List[Tensor], target:Target) -> Schedule
#             The schedule function.

#         name : str
#             The name of implementation.

#         plevel : int
#             The priority level of implementation.
#         """
#         _OpStrategyAddImplementation(self, compute, schedule, name, plevel)


def _wrap_default_fstrategy(compute, schedule, name):
    def _fstrategy(attrs, inputs, out_type, target):
        strategy = OpStrategy()
        strategy.add_implementation(compute, schedule, name=name)
        return strategy

    return _fstrategy


def _create_fstrategy_from_schedule(op_name, schedule):
    assert hasattr(schedule, "dispatch_dict")
    compute = get(op_name).get_attr("FTVMCompute")
    assert compute is not None, "FTVMCompute is not registered for op %s" % op_name
    fstrategy = get_native_generic_func("{}_strategy".format(op_name))
    name_pfx = schedule.__name__
    name_pfx = name_pfx[name_pfx.index("_") + 1 :]
    fstrategy.set_default(
        _wrap_default_fstrategy(compute, schedule.fdefault, "%s.generic" % name_pfx)
    )
    for key, sch in schedule.dispatch_dict.items():
        fstrategy.register(_wrap_default_fstrategy(compute, sch, "%s.%s" % (name_pfx, key)), [key])
    return fstrategy


def register_compute(op_name, compute=None, level=10):
    """Register compute function for an op.

    Parameters
    ----------
    op_name : str
        The name of the op.

    compute : function (attrs: Attrs, inputs: List[Tensor], out_type: Type)
                       -> List[Tensor]
        The compute function.

    level : int
        The priority level
    """
    return tvm.ir.register_op_attr(op_name, "FTVMCompute", compute, level)


def register_strategy(op_name, fstrategy=None, level=10):
    """Register strategy function for an op.

    Parameters
    ----------
    op_name : str
        The name of the op.

    fstrategy : function (attrs: Attrs, inputs: List[Tensor], out_type: Type,
                          target:Target) -> OpStrategy
        The strategy function. Need to be native GenericFunc.

    level : int
        The priority level
    """
    if not isinstance(fstrategy, GenericFunc):
        assert hasattr(fstrategy, "generic_func_node")
        fstrategy = fstrategy.generic_func_node
    return tvm.ir.register_op_attr(op_name, "FTVMStrategy", fstrategy, level)


def register_schedule(op_name, schedule, level=10):
    """Register schedule function for an op.

    This is used when compute function is the same for all targets and only
    schedule is different. It requires FTVMCompute is already registered to
    the op.

    Parameters
    ----------
    op_name : str
        The name of the op.

    schedule : function (attrs: Attrs, outs: List[Tensor], target:Target) -> Schedule
        The schedule function. Need to be target.generic_func.

    level : int
        The priority level
    """
    fstrategy = _create_fstrategy_from_schedule(op_name, schedule)
    return register_strategy(op_name, fstrategy, level)


def register_injective_schedule(op_name, level=10):
    """Register injective schedule function for an op.

    Parameters
    ----------
    op_name : str
        The name of the op.

    level : int
        The priority level
    """
    return register_schedule(op_name, _schedule_injective, level)


def register_broadcast_schedule(op_name, level=10):
    """Register broadcast schedule function for an op.

    Parameters
    ----------
    op_name : str
        The name of the op.

    level : int
        The priority level
    """
    return register_schedule(op_name, _schedule_injective, level)


def register_reduce_schedule(op_name, level=10):
    """Register reduce schedule function for an op.

    Parameters
    ----------
    op_name : str
        The name of the op.

    level : int
        The priority level
    """
    return register_schedule(op_name, _schedule_reduce, level)


def register_alter_op_layout(op_name, alter_layout=None, level=10):
    """Register alter op layout function for an op

    Parameters
    ----------
    op_name : str
        The name of the operator

    alter_layout: function (attrs: Attrs, inputs: List[Expr]) -> new_expr: Expr
        The function for changing the layout or replacing the operator

    level : int
        The priority level
    """
    return tvm.ir.register_op_attr(op_name, "FTVMAlterOpLayout", alter_layout, level)


def register_convert_op_layout(op_name, convert_layout=None, level=10):
    """Register convert op layout function for an op

    Parameters
    ----------
    op_name : str
        The name of the operator

    convert_layout: function (attrs: Attrs, inputs: List[Expr]) -> new_expr: Expr
        The function for changing the layout or replacing the operator

    level : int
        The priority level
    """
    return tvm.ir.register_op_attr(op_name, "FTVMConvertOpLayout", convert_layout, level)


def register_legalize(op_name, legal_op=None, level=10):
    """Register legal transformation function for an op

    Parameters
    ----------
    op_name : str
        The name of the operator

    legal_op: function (attrs: Attrs, inputs: List[Expr]) -> new_expr: Expr
        The function for transforming an expr to another expr.

    level : int
        The priority level
    """
    return tvm.ir.register_op_attr(op_name, "FTVMLegalize", legal_op, level)


def register_pattern(op_name, pattern, level=10):
    """Register operator pattern for an op.

    Parameters
    ----------
    op_name : str
        The name of the op.

    pattern : int
        The pattern being used.

    level : int
        The priority level
    """
    return tvm.ir.register_op_attr(op_name, "TOpPattern", pattern, level)


def register_gradient(op_name, fgradient=None, level=10):
    """Register operator pattern for an op.

    Parameters
    ----------
    op_name : str
        The name of the op.

    fgradient : function (orig_expr : Expr, output_grad : Expr) -> new_expr : Expr
        The gradient being used.

    level : int
        The priority level
    """
    return tvm.ir.register_op_attr(op_name, "FPrimalGradient", fgradient, level)


def register_shape_func(op_name, data_dependent, shape_func=None, level=10):
    """Register operator shape function for an op.

    Parameters
    ----------
    op_name : str
        The name of the op.

    data_dependent : bool or list of bool
        Whether the shape function depends on input data. If this is a list of bool,
        the length of the list must be the same as the number of arguments of this op.
        The list specifies per-input data dependence of the op.

    shape_func : function (attrs: Attrs, inputs: List[Tensor], out_ndims: List[IndexExpr])
                 -> shape_tensors: List<Tensor>
        The function for computing the dynamic output shapes

    level : int
        The priority level
    """
    if not isinstance(data_dependent, list):
        data_dependent = [data_dependent]
    get(op_name).set_attr("TShapeDataDependent", data_dependent, level)
    return tvm.ir.register_op_attr(op_name, "FShapeFunc", shape_func, level)


def register_external_compiler(op_name, fexternal=None, level=10):
    """Register the external compiler for an op.

    Parameters
    ----------
    op_name : str
        The name of the operator.

    fexternal : function (attrs: Attrs, args: List[Expr], compiler: str)
              -> new_expr: Expr
        The function for wrapping a call expr with compiler_begin and
        compiler_end.

    level : int
        The priority level
    """
    return tvm.ir.register_op_attr(op_name, "FTVMExternalCompiler", fexternal, level)
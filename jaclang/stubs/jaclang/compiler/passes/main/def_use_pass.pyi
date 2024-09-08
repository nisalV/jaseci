import jaclang.compiler.absyntree as ast
from jaclang.compiler.passes import Pass as Pass

class DefUsePass(Pass):
    def after_pass(self) -> None: ...
    def enter_architype(self, node: ast.Architype) -> None: ...
    def enter_enum(self, node: ast.Enum) -> None: ...
    def enter_arch_ref(self, node: ast.ArchRef) -> None: ...
    def enter_arch_ref_chain(self, node: ast.ArchRefChain) -> None: ...
    def enter_param_var(self, node: ast.ParamVar) -> None: ...
    def enter_has_var(self, node: ast.HasVar) -> None: ...
    def enter_assignment(self, node: ast.Assignment) -> None: ...
    def enter_inner_compr(self, node: ast.InnerCompr) -> None: ...
    def enter_atom_trailer(self, node: ast.AtomTrailer) -> None: ...
    def enter_func_call(self, node: ast.FuncCall) -> None: ...
    def enter_index_slice(self, node: ast.IndexSlice) -> None: ...
    def enter_special_var_ref(self, node: ast.SpecialVarRef) -> None: ...
    def enter_edge_op_ref(self, node: ast.EdgeOpRef) -> None: ...
    def enter_disconnect_op(self, node: ast.DisconnectOp) -> None: ...
    def enter_connect_op(self, node: ast.ConnectOp) -> None: ...
    def enter_filter_compr(self, node: ast.FilterCompr) -> None: ...
    def enter_token(self, node: ast.Token) -> None: ...
    def enter_float(self, node: ast.Float) -> None: ...
    def enter_int(self, node: ast.Int) -> None: ...
    def enter_string(self, node: ast.String) -> None: ...
    def enter_bool(self, node: ast.Bool) -> None: ...
    def enter_builtin_type(self, node: ast.BuiltinType) -> None: ...
    def enter_name(self, node: ast.Name) -> None: ...
    def enter_in_for_stmt(self, node: ast.InForStmt) -> None: ...
    def enter_delete_stmt(self, node: ast.DeleteStmt) -> None: ...
    def enter_expr_as_item(self, node: ast.ExprAsItem) -> None: ...
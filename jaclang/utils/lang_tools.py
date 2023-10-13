"""Language tools for the Jaclang project."""

import inspect
import os
import sys
from typing import List, Optional

import jaclang.jac.absyntree as ast
from jaclang.jac.parser import JacLexer
from jaclang.jac.passes.blue.schedules import SymbolTablePrinterPass, sym_tab_print
from jaclang.jac.passes.blue.schedules import ASTPrinterPass, full_ast_print
from jaclang.jac.passes.blue.schedules import DotGraphPass, full_ast_dot_gen
from jaclang.jac.transpiler import jac_file_to_pass
from jaclang.utils.helpers import pascal_to_snake


class AstKidInfo:
    """Information about a kid."""

    def __init__(self, name: str, typ: str, default: Optional[str] = None) -> None:
        """Initialize."""
        self.name = name
        self.typ = typ
        self.default = default


class AstNodeInfo:
    """Meta data about AST nodes."""

    type_map: dict[str, type] = {}

    def __init__(self, cls: type) -> None:
        """Initialize."""
        self.cls = cls
        self.process(cls)

    def process(self, cls: type) -> None:
        """Process AstNode class."""
        self.name = cls.__name__
        self.doc = cls.__doc__
        AstNodeInfo.type_map[self.name] = cls
        self.class_name_snake = pascal_to_snake(cls.__name__)
        self.init_sig = inspect.signature(cls.__init__)
        self.kids: list[AstKidInfo] = []
        for param_name, param in self.init_sig.parameters.items():
            if param_name not in [
                "self",
                "parent",
                "kid",
                "line",
                "mod_link",
                "sym_tab",
            ]:
                param_type = (
                    param.annotation
                    if param.annotation != inspect.Parameter.empty
                    else "Any"
                )
                param_default = (
                    param.default if param.default != inspect.Parameter.empty else None
                )
                self.kids.append(AstKidInfo(param_name, param_type, param_default))


class AstTool:
    """Ast tools."""

    def __init__(self) -> None:
        """Initialize."""
        module = sys.modules[ast.__name__]
        source_code = inspect.getsource(module)
        classes = inspect.getmembers(module, inspect.isclass)
        ast_node_classes = [
            AstNodeInfo(cls)
            for _, cls in classes
            if issubclass(cls, ast.AstNode)
            and cls.__name__ not in ["AstNode", "OOPAccessNode", "WalkerStmtOnlyNode"]
        ]
        self.ast_classes = sorted(
            ast_node_classes,
            key=lambda cls: source_code.find(f"class {cls.name}"),
        )

    def pass_template(self, *args: List[str]) -> str:
        """Generate pass template."""
        output = "import jaclang.jac.absyntree as ast\nfrom jaclang.jac.passes import Pass\n\nclass SomePass(Pass):\n"

        def emit(to_append: str) -> None:
            """Emit to output."""
            nonlocal output
            output += "\n    " + to_append

        for cls in self.ast_classes:
            emit(
                f"def exit_{cls.class_name_snake}(self, node: ast.{cls.name}) -> None:\n"
            )
            emit('    """Sub objects.\n')

            for kid in cls.kids:
                emit(
                    f"    {kid.name}: {kid.typ}{' ='+kid.default if kid.default else ''},"
                )

            emit('    """\n')
        output = (
            output.replace("jaclang.jac.absyntree.", "")
            .replace("typing.", "")
            .replace("<enum '", "")
            .replace("'>", "")
            .replace("<class '", "")
            .replace("ForwardRef('", "")
            .replace("')", "")
        )
        return output

    def jac_keywords(self, *args: List[str]) -> str:
        """Get all Jac keywords as an or string."""
        ret = ""
        for k in JacLexer._remapping["NAME"].keys():
            ret += f"{k}|"
        return ret[:-1]

    def md_doc(self, *args: List[str]) -> str:
        """Generate mermaid markdown doc."""
        output = ""
        for cls in self.ast_classes:
            if not len(cls.kids):
                continue
            output += f"## {cls.name}\n"
            output += "```mermaid\nflowchart LR\n"
            for kid in cls.kids:
                if "_end" in kid.name:
                    kid.name = kid.name.replace("_end", "_end_")
                arrow = "-.->" if "Optional" in kid.typ else "-->"
                typ = (
                    kid.typ.replace("Optional[", "")
                    .replace("]", "")
                    .replace("|", ",")
                    .replace("list[", "list - ")
                )
                output += f"{cls.name} {arrow}|{typ}| {kid.name}\n"
            output += "```\n\n"
            output += f"{cls.doc} \n\n"
        return output

    def gen_dotfile(self, *args: List[str]) -> str:
        """Generate a dot file for AST."""
        args = args[0]
        if len(args) == 0:
            return "Usage: gen_dotfile <file_path> [<output_path>]"

        file_name: str = args[0]
        DotGraphPass.OUTPUT_FILE_PATH = args[1] if len(args) == 2 else None

        if not os.path.isfile(file_name):
            return f"Error: {file_name} not found"

        if file_name.endswith(".jac"):
            [base, mod] = os.path.split(file_name)
            base = './' if not base else base
            jac_file_to_pass(file_name, base, DotGraphPass, full_ast_dot_gen)
            if DotGraphPass.OUTPUT_FILE_PATH:
                return f"Dot file generated at {DotGraphPass.OUTPUT_FILE_PATH}"
            else:
                return ""
        else:
            return "Not a .jac file."

    def print(self, *args: List[str]) -> str:
        """Generate a dot file for AST."""
        args = args[0]
        if len(args) == 0:
            return "Usage: print <file_path>"

        file_name: str = args[0]

        if not os.path.isfile(file_name):
            return f"Error: {file_name} not found"

        if file_name.endswith(".jac"):
            [base, mod] = os.path.split(file_name)
            base = './' if not base else base
            jac_file_to_pass(file_name, base, ASTPrinterPass, full_ast_print)
            return ""
        else:
            return "Not a .jac file."

    def symtab_print(self, *args: List[str]) -> str:
        """Generate a dot file for AST."""
        args = args[0]
        if len(args) == 0:
            return "Usage: print <file_path>"

        file_name: str = args[0]

        if not os.path.isfile(file_name):
            return f"Error: {file_name} not found"

        if file_name.endswith(".jac"):
            [base, mod] = os.path.split(file_name)
            base = './' if not base else base
            jac_file_to_pass(file_name, base, SymbolTablePrinterPass, sym_tab_print)
            return ""
        else:
            return "Not a .jac file."

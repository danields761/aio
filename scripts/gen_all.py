import ast
from pathlib import Path

import click


class ImportCollector(ast.NodeVisitor):
    def __init__(self):
        self.names = []

    def visit_Import(self, node):
        self.names.extend(alias.asname or alias.name for alias in node.names)

    visit_ImportFrom = visit_Import


class RewriteAll(ast.NodeTransformer):
    def __init__(self, all_names):
        self.all_names = all_names

    def visit_Assign(self, node):
        match node.targets:
            case [ast.Name(id="__all__", ctx=ctx)]:
                return ast.copy_location(
                    ast.Assign(
                        [ast.Name("__all__", ctx)],
                        ast.List(
                            elts=[ast.Constant(name) for name in self.all_names],
                        ),
                    ),
                    node,
                )
        return None


def collect_import_names(file_path):
    with open(file_path, "r", encoding="utf-8") as file:
        text = file.read()

        parsed = ast.parse(text, filename=str(file_path))
        collector = ImportCollector()
        collector.visit(parsed)
        return parsed, collector.names


def rewrite_all(file_path, parsed, names):
    with open(file_path, "w", encoding="utf-8") as file:
        transformer = RewriteAll(names)
        transformer.visit(parsed)

        file.seek(0)
        file.truncate()
        file.write(ast.unparse(parsed))


def process(file_path):
    parsed, names = collect_import_names(file_path)
    rewrite_all(file_path, parsed, names)


@click.command()
@click.argument("path", type=click.Path(exists=True, dir_okay=True, file_okay=True))
def gen_all(path):
    root = Path(path)

    if root.is_dir():
        for init_py in root.rglob("__init__.py"):
            print(init_py, collect_import_names(init_py))
            process(init_py)
    else:
        process(path)


if __name__ == "__main__":
    gen_all()

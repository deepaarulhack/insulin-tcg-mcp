import os
import ast

SRC_DIR = "src"

def inspect_file(path):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        try:
            tree = ast.parse(f.read(), filename=path)
        except SyntaxError:
            return []
    items = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            items.append(("function", node.name, path))
        elif isinstance(node, ast.ClassDef):
            items.append(("class", node.name, path))
    return items

def main():
    all_items = []
    for root, _, files in os.walk(SRC_DIR):
        for f in files:
            if f.endswith(".py"):
                path = os.path.join(root, f)
                all_items.extend(inspect_file(path))
    for kind, name, path in all_items:
        print(f"{kind:8} {name:20} {path}")

if __name__ == "__main__":
    main()


"""Extrai os módulos embutidos no notebook V2 e gera uma cópia sem saídas.

Uso:
    python scripts/import_notebook_source.py caminho/do/notebook.ipynb

O script existe apenas para preservar a proveniência do primeiro commit. Depois da
importação, ``src/`` passa a ser a fonte canônica da aplicação.
"""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def embedded_modules(source: str) -> dict[str, str]:
    modules: dict[str, str] = {}
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not isinstance(call.func, ast.Attribute) or call.func.attr != "write_text":
            continue
        if not call.args or not isinstance(call.args[0], ast.Constant):
            continue
        target = call.func.value
        if not isinstance(target, ast.Call) or not target.args:
            continue
        path_arg = target.args[0]
        if not isinstance(path_arg, ast.Constant):
            continue
        name = Path(str(path_arg.value)).name
        if name in {"engine.py", "universe_b3.py"}:
            modules[name] = str(call.args[0].value)
    return modules


def clean_notebook(notebook: dict, source_sha256: str) -> dict:
    notebook = json.loads(json.dumps(notebook))
    bootstrap = """# Carrega o motor versionado no repositório.\nfrom pathlib import Path\nimport sys\n\nROOT = Path.cwd()\nif not (ROOT / 'src').exists():\n    raise RuntimeError('Abra o notebook a partir da raiz do repositório clonado.')\nif str(ROOT) not in sys.path:\n    sys.path.insert(0, str(ROOT))\n\nfrom src.engine import *\nfrom src.universe_b3 import *\nprint('Motor B3 versionado carregado')\n"""
    notebook["cells"][3]["source"] = bootstrap.splitlines(keepends=True)
    for cell in notebook.get("cells", []):
        if cell.get("cell_type") == "code":
            cell["execution_count"] = None
            cell["outputs"] = []
        cell.get("metadata", {}).pop("execution", None)
    notebook.setdefault("metadata", {})["alphalab_provenance"] = {
        "source_notebook_sha256": source_sha256,
        "note": "Saídas removidas; módulos embutidos extraídos para src/.",
    }
    return notebook


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Informe o caminho do notebook de origem.")
    source = Path(sys.argv[1]).resolve()
    raw = source.read_bytes()
    notebook = json.loads(raw.decode("utf-8"))
    modules = embedded_modules("".join(notebook["cells"][3]["source"]))
    if set(modules) != {"engine.py", "universe_b3.py"}:
        raise RuntimeError(f"Módulos esperados não encontrados: {sorted(modules)}")
    for name, content in modules.items():
        (ROOT / "src" / name).write_text(content, encoding="utf-8")
    cleaned = clean_notebook(notebook, hashlib.sha256(raw).hexdigest())
    (ROOT / "notebooks" / "Carteira_B3_Automatica_V2.ipynb").write_text(
        json.dumps(cleaned, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()

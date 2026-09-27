"""Grep test (07 §1): only the adapter can talk to Afero.

- Only `conductor/drivers/afero.py` imports aioafero.
- Only `conductor/drivers/afero*.py` import aiohttp, construct a `ClientSession`, or call `.put(` / `.post(` /
  `.patch(` / `.delete(` on anything that looks like an HTTP session.

The scan parses every module (AST, so comments and docstrings don't count) and the checker is itself tested on
snippets, so a broken scanner can't pass silently.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CONDUCTOR = ROOT / "conductor"
AIOAFERO_ALLOWED = {"conductor/drivers/afero.py"}
AIOHTTP_ALLOWED = {"conductor/drivers/afero.py", "conductor/drivers/afero_transport.py"}
WRITE_METHODS = {"put", "post", "patch", "delete"}
SESSIONISH = ("session", "aiohttp", "http_client", "httpclient")


def _root_module(name: str | None) -> str:
    return (name or "").split(".")[0]


def _receiver_text(node: ast.AST) -> str:
    try:
        return ast.unparse(node).lower()
    except Exception:  # noqa: BLE001 - unparse failure means we can't classify; treat as suspicious text
        return "session"


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    return func.attr if isinstance(func, ast.Attribute) else ""


def violations(source: str, rel: str) -> list[str]:
    """Problems in one module's source, as 'rel:line: what'."""
    tree = ast.parse(source)
    out: list[str] = []
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [_root_module(a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = [_root_module(node.module)]
        elif (
            isinstance(node, ast.Call)
            and _call_name(node) in ("__import__", "import_module")
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            modules = [_root_module(str(node.args[0].value))]
        for mod in modules:
            if mod == "aioafero" and rel not in AIOAFERO_ALLOWED:
                out.append(f"{rel}:{node.lineno}: imports aioafero")
            if mod == "aiohttp" and rel not in AIOHTTP_ALLOWED:
                out.append(f"{rel}:{node.lineno}: imports aiohttp")
        if isinstance(node, ast.Call) and rel not in AIOHTTP_ALLOWED:
            func = node.func
            name = _call_name(node)
            if name in ("ClientSession", "GatedClientSession", "TCPConnector"):
                out.append(f"{rel}:{node.lineno}: constructs {name}")
            if (
                isinstance(func, ast.Attribute)
                and func.attr in WRITE_METHODS
                and any(k in _receiver_text(func.value) for k in SESSIONISH)
            ):
                out.append(f"{rel}:{node.lineno}: calls .{func.attr}( on {ast.unparse(func.value)}")
    return out


def test_nothing_outside_the_adapter_imports_aioafero_or_writes_with_aiohttp():
    files = sorted(CONDUCTOR.rglob("*.py"))
    assert any(p.name == "afero.py" for p in files), "the scan must see the adapter itself"
    problems = []
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        problems += violations(path.read_text(encoding="utf-8"), rel)
    assert problems == [], "\n".join(problems)


def test_the_adapter_is_where_aioafero_actually_lives():
    tree = ast.parse((CONDUCTOR / "drivers/afero.py").read_text(encoding="utf-8"))
    imported = {
        _root_module(n.module) for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level == 0
    }
    assert "aioafero" in imported
    transport = (CONDUCTOR / "drivers/afero_transport.py").read_text(encoding="utf-8")
    assert violations(transport, "conductor/drivers/afero_transport.py") == []
    assert not any(
        _root_module(n.module if isinstance(n, ast.ImportFrom) else n.names[0].name) == "aioafero"
        for n in ast.walk(ast.parse(transport))
        if isinstance(n, (ast.Import, ast.ImportFrom))
    )


@pytest.mark.parametrize(
    "snippet",
    [
        "import aioafero",
        "from aioafero.v1 import AferoBridgeV1",
        "import aioafero.v1.auth as a",
        "import aiohttp",
        "from aiohttp import ClientSession",
        "__import__('aioafero')",
        "import importlib\nimportlib.import_module('aioafero.v1')",
        "s = ClientSession()",
        "async def f(session):\n    await session.put('https://api2.afero.net/x')",
        "async def f(self):\n    await self._session.post('u')",
        "async def f(http_client):\n    await http_client.delete('u')",
        "async def f(a):\n    await a.aiohttp_session.patch('u')",
    ],
)
def test_the_scanner_catches_each_forbidden_pattern(snippet):
    assert violations(snippet, "conductor/somewhere.py")


@pytest.mark.parametrize(
    "snippet",
    [
        "@router.post('/api/x')\ndef f(): pass",
        "queue.put(1)",
        "d = {}\nd.pop('x')",
        "# import aioafero in a comment is fine",
        '"""docs may mention aiohttp and aioafero"""',
    ],
)
def test_the_scanner_ignores_ordinary_code(snippet):
    assert violations(snippet, "conductor/somewhere.py") == []

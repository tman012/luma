"""Every Python file must parse on the interpreter the image runs.

The image is python:3.11-slim (Dockerfile). 82a1404 crash-looped on :5002
because app/file_management.py had a backslash inside an f-string
expression, which 3.12 accepts (PEP 701) and 3.11 rejects at import.

On the target interpreter (or older) compile() is the real check. On a
newer one, ast.parse(feature_version=...) catches grammar such as `type`
statements and generic `def f[T]`, but it does NOT catch PEP 701 f-strings,
so the tokenizer pass below checks those: no backslash, comment, reused
quote or (in a single-quoted f-string) line break inside a replacement
field.
"""

import ast
import io
import re
import sys
import token
import tokenize
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DIRS = ('app', 'tests', 'scripts')


def target_version():
    m = re.search(r'^FROM python:(\d+)\.(\d+)', (ROOT / 'Dockerfile').read_text(), re.M)
    assert m, 'Dockerfile has no FROM python:X.Y line'
    return int(m.group(1)), int(m.group(2))


TARGET = target_version()


def py_files():
    return sorted(p for d in DIRS if (ROOT / d).is_dir() for p in (ROOT / d).rglob('*.py')
                  if '__pycache__' not in p.parts and 'node_modules' not in p.parts)


def _quote(tok_string):
    body = tok_string.lstrip('rRbBuUfF')
    return body[:3] if body[:3] in ('"""', "'''") else body[:1]


def pep701_problems(src):
    """(line, what) for each f-string feature that needs 3.12+. Needs a 3.12+
    tokenizer (FSTRING_START tokens)."""
    out = []
    stack = []   # [quote, brace depth] per open f-string, innermost last
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        line = tok.start[0]
        in_expr = bool(stack) and stack[-1][1] > 0
        nested = any(depth > 0 for _q, depth in stack[:-1])
        if tok.type == token.FSTRING_START:
            if in_expr:
                if _quote(tok.string)[0] == stack[-1][0][0] and len(stack[-1][0]) == 1 \
                        or _quote(tok.string) == stack[-1][0]:
                    out.append((line, 'reused quote in nested f-string'))
            stack.append([_quote(tok.string), 0])
            continue
        if not stack:
            continue
        if tok.type == token.FSTRING_END:
            stack.pop()
            continue
        if tok.type == token.FSTRING_MIDDLE:
            if nested and '\\' in tok.string:
                out.append((line, 'backslash inside f-string expression'))
            continue
        if tok.type == token.OP and tok.string == '{':
            stack[-1][1] += 1
            continue
        if tok.type == token.OP and tok.string == '}':
            stack[-1][1] -= 1
            continue
        if not in_expr:
            continue
        if tok.type == token.COMMENT:
            out.append((line, 'comment inside f-string expression'))
        elif tok.type in (token.NL, token.NEWLINE) and len(stack[-1][0]) == 1:
            out.append((line, 'line break inside single-quoted f-string expression'))
        elif tok.type == token.STRING:
            if '\\' in tok.string:
                out.append((line, 'backslash inside f-string expression'))
            outer, q = stack[-1][0], _quote(tok.string)
            if (len(outer) == 1 and q[0] == outer) or q == outer:
                out.append((line, 'reused quote inside f-string expression'))
    return out


def problems(src, name='<src>'):
    """What keeps *src* from compiling on TARGET, as a list of strings."""
    if sys.version_info[:2] <= TARGET:
        try:
            compile(src, name, 'exec', dont_inherit=True)
        except SyntaxError as e:
            return [f'{e.lineno}: {e.msg}']
        return []
    try:
        ast.parse(src, name, feature_version=TARGET)
    except SyntaxError as e:
        return [f'{e.lineno}: {e.msg}']
    return [f'{line}: {what}' for line, what in pep701_problems(src)]


BAD = [
    'x = f" {re.sub(r\'[._()\\[\\]]+\', \' \', name)} "\n',   # the 82a1404 line
    "x = f\"{'\\n'.join(a)}\"\n",
    'x = f"{a["k"]}"\n',
    'x = f"{f"{a}"}"\n',
    'x = f"{a # note\n}"\n',
    'x = f"{a +\n b}"\n',
    'type Alias = int\n',
    'def first[T](xs: list[T]) -> T:\n    return xs[0]\n',
]
GOOD = [
    "x = f\"{a['k']}\"\n",
    'x = f"{x!r:>{width}}"\n',
    'x = f"{{literal}} {a}"\n',
    'x = rf"\\d{{2}}{n}\\b"\n',
    'x = f"""{"a"} {\'b\'}"""\n',
    'x = f"""{a +\n b}"""\n',
    "x = f'{a:%Y-%m-%d}'\n",
    "x = f\"{f'{a}'}\"\n",
    'x = "\\n".join(f"{a}" for a in b)\n',
]


@pytest.mark.parametrize('src', BAD)
def test_checker_flags_syntax_the_target_rejects(src):
    assert problems(src), src


@pytest.mark.parametrize('src', GOOD)
def test_checker_passes_syntax_the_target_accepts(src):
    assert problems(src) == [], src


def test_target_is_the_image_python():
    assert TARGET == (3, 11)


def test_every_python_file_parses_on_the_target():
    files = py_files()
    assert any(p.name == 'main.py' for p in files) and len(files) > 50
    bad = {str(p.relative_to(ROOT)): found for p in files
           if (found := problems(p.read_text(encoding='utf-8'), str(p)))}
    assert bad == {}, f'needs Python > {TARGET[0]}.{TARGET[1]}: {bad}'

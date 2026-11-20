"""Espressioni sugli attributi: un linguaggio piccolo per guardie, assegnazioni e invarianti, interpretato qui
(nessun eval, nessun codice dell'utente eseguito: solo i nodi ammessi sotto).

Sintassi:
  - confronti  ==  !=  <  <=  >  >=   (anche in catena: 0 <= x <= 3)
  - logica     and  or  not   (anche e / o / non)
  - aritmetica +  -  *   e il meno unario
  - costanti   numeri interi, true / false (vero / falso), testi tra apici: 'APERTO'
  - riferimenti: attributo (della propria entità), Entità.attributo, [Nome con spazi].attributo

parse() ritorna l'albero e i riferimenti [(entità o None, attributo)]; build() lo trasforma in una funzione dello
stato, dato come risolvere ogni riferimento nella chiave della variabile.
"""
import ast
import operator
import re

_BRACKET = re.compile(r"\[([^\]]+)\]\s*\.\s*([A-Za-z_À-ÿ][\wÀ-ÿ]*)")
_WORDS = {"vero": "True", "falso": "False", "true": "True", "false": "False", "e": "and", "o": "or", "non": "not"}
_CMP = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le,
        ast.Gt: operator.gt, ast.GtE: operator.ge}
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul}


class ExprError(ValueError):
    pass


def parse(text: str):
    """Albero dell'espressione e riferimenti agli attributi. ExprError se non è un'espressione ammessa."""
    if not (text or "").strip():
        raise ExprError("vuota")
    names = []

    def bracket(m):
        names.append((m.group(1).strip(), m.group(2)))
        return f"__ref{len(names) - 1}__"
    src = _BRACKET.sub(bracket, text.strip())
    # parole italiane e minuscole per vero/falso, fuori dai testi tra apici
    parts = re.split(r"('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")", src)
    src = "".join(p if i % 2 else re.sub(r"\b(vero|falso|true|false|e|o|non)\b", lambda w: _WORDS[w.group(1)], p)
                  for i, p in enumerate(parts))
    try:
        tree = ast.parse(src, mode="eval").body
    except SyntaxError as exc:
        raise ExprError(f"sintassi: {exc.msg}") from exc
    refs = []

    def check(n):
        if isinstance(n, ast.BoolOp) and isinstance(n.op, (ast.And, ast.Or)):
            return [check(v) for v in n.values]
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.Not, ast.USub)):
            return check(n.operand)
        if isinstance(n, ast.BinOp) and type(n.op) in _BIN:
            return check(n.left), check(n.right)
        if isinstance(n, ast.Compare) and all(type(o) in _CMP for o in n.ops):
            return check(n.left), [check(c) for c in n.comparators]
        if isinstance(n, ast.Constant) and isinstance(n.value, (bool, int, str)):
            return None
        if isinstance(n, ast.Name):
            m = re.fullmatch(r"__ref(\d+)__", n.id)
            refs.append(names[int(m.group(1))] if m else (None, n.id))
            return None
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and not n.value.id.startswith("__ref"):
            refs.append((n.value.id, n.attr))
            return None
        raise ExprError(f"non ammesso: {type(n).__name__}")
    check(tree)
    return (tree, names), list(dict.fromkeys(refs))


def build(parsed, resolve):
    """Funzione stato -> valore. resolve((entità o None, attributo)) dà la chiave della variabile nello stato."""
    tree, names = parsed

    def ref_of(n):
        if isinstance(n, ast.Name):
            m = re.fullmatch(r"__ref(\d+)__", n.id)
            return names[int(m.group(1))] if m else (None, n.id)
        return (n.value.id, n.attr)

    def comp(n):
        if isinstance(n, ast.BoolOp):
            fs = [comp(v) for v in n.values]
            return (lambda s: all(f(s) for f in fs)) if isinstance(n.op, ast.And) else (lambda s: any(f(s) for f in fs))
        if isinstance(n, ast.UnaryOp):
            f = comp(n.operand)
            return (lambda s: not f(s)) if isinstance(n.op, ast.Not) else (lambda s: -f(s))
        if isinstance(n, ast.BinOp):
            a, b, op = comp(n.left), comp(n.right), _BIN[type(n.op)]
            return lambda s: op(a(s), b(s))
        if isinstance(n, ast.Compare):
            first, rest = comp(n.left), [(_CMP[type(o)], comp(c)) for o, c in zip(n.ops, n.comparators)]

            def cmp(s):
                left = first(s)
                for op, f in rest:
                    right = f(s)
                    try:
                        if not op(left, right):
                            return False
                    except TypeError:  # es. testo < numero: la condizione non vale
                        return False
                    left = right
                return True
            return cmp
        if isinstance(n, ast.Constant):
            v = n.value
            return lambda s: v
        key = resolve(ref_of(n))
        return lambda s: s[key]
    return comp(tree)


def domain_of(a: dict) -> list | None:
    """Valori che l'attributo può assumere nell'analisi, oppure None se non partecipa (attributo solo descrittivo)."""
    if not a.get("verify"):
        return None
    d = a.get("domain") or {}
    if a.get("type") == "bool":
        return [False, True]
    if a.get("type") == "enum":
        return list(d.get("values") or []) or None
    if a.get("type") in ("int", "decimal"):
        lo, hi = int(d.get("min", 0)), int(d.get("max", 0))
        return list(range(lo, hi + 1)) if hi >= lo and hi - lo <= 1000 else None
    return None


def initial_of(a: dict, values: list):
    v = a.get("initial")
    return v if v in values else values[0]

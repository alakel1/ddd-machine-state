"""Confronto tra BFS completa e BFS con riduzione per simmetria sullo stesso modello.

Usato dai test e, a mano, sui domini del database:  python -m tests.equivalence <id dominio> ...
"""
import collections
import copy
import sys
import time

from app import engine
from app.engine import Engine


def analyze(m: dict, symmetry: bool, lang: str = "en"):
    engine.SYMMETRY = symmetry
    engine._EXPLORE_CACHE.clear()
    try:
        t0 = time.time()
        eng = Engine(copy.deepcopy(m), lang)
        return eng, eng.analyze(), time.time() - t0
    finally:
        engine.SYMMETRY = True


def observations(r: dict) -> dict:
    """Quello che l'utente vede: segnalazioni (con le istanze nel titolo), lunghezza del percorso più breve di
    ciascuna, impronte, numero di stati raggiungibili."""
    depth = {}
    for i in r["issues"]:
        if i.get("trace") is not None:
            k = (i["category"], i["title"])
            depth[k] = min(depth.get(k, 10 ** 9), len(i["trace"]))
    return {"issues": collections.Counter((i["category"], i["title"]) for i in r["issues"]), "depth": depth,
            "fingerprints": {i["fingerprint"] for i in r["issues"]}, "reachable": r["stats"]["reachable"]}


def unreplayable(eng: Engine, r: dict) -> list[str]:
    """Segnalazioni il cui percorso, rigiocato sul modello espanso, non arriva esattamente allo stato mostrato."""
    dyn, _ = eng.dynamic()
    bad = []
    for i in r["issues"]:
        if i.get("trace") is None or i.get("state") is None:
            continue
        st = dyn.initial_state()
        for s in i["trace"]:
            res = dyn.apply(st, s["transition_id"])
            if not res["ok"]:
                bad.append(i["title"])
                break
            st = res["state"]
        else:
            norm = lambda s: {(k if "." in str(k) else int(k)): v for k, v in s.items()}  # chiavi JSON → id e attributi
            if norm(st) != norm(i["state"]):
                bad.append(i["title"])
    return bad


def compare(m: dict) -> dict:
    full_eng, full, t_full = analyze(m, False)
    red_eng, red, t_red = analyze(m, True)
    a, b = observations(full), observations(red)
    return {"equal": {k: a[k] == b[k] for k in a}, "full": a, "reduced": b, "unreplayable": unreplayable(red_eng, red),
            "explored": (full["stats"]["explored"], red["stats"]["explored"]), "symmetry": red["stats"]["symmetry"],
            "seconds": (round(t_full, 2), round(t_red, 2))}


if __name__ == "__main__":
    from app import db
    conn = db.connect()
    for d in map(int, sys.argv[1:]):
        c = compare(db.load_model(conn, d))
        print(f"dominio {d}: uguali {c['equal']}, non riproducibili {len(c['unreplayable'])}, "
              f"stati esplorati {c['explored'][0]} → {c['explored'][1]}, secondi {c['seconds'][0]} → {c['seconds'][1]}, "
              f"simmetria {c['symmetry']}")

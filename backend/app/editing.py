"""Modifiche al modello che toccano più righe: ognuna gira in una sola transazione (quella di db.tx()).

Il CRUD generico di main.py resta per i campi semplici; qui le operazioni con regole di dominio:
valori di partenza, normalizzazioni, pulizia dei riferimenti.
"""
import re

from . import db
from .relations import suggest_liveness

# palette delle entità (frontend: colors.js), assegnata a rotazione alle nuove entità
COLORS = ["indigo", "emerald", "amber", "rose", "sky", "violet", "teal", "orange", "fuchsia", "slate"]


def domain_of_entity(conn, entity_id: int) -> int | None:
    r = conn.execute("SELECT domain_id FROM entities WHERE id=?", (entity_id,)).fetchone()
    return r and r["domain_id"]


def domain_of_state(conn, state_id: int) -> int | None:
    r = conn.execute("SELECT e.domain_id FROM states s JOIN entities e ON e.id=s.entity_id WHERE s.id=?", (state_id,)).fetchone()
    return r and r["domain_id"]


# ---------------------------------------------------------------- entità
def create_entity(conn, data: dict) -> int:
    """Nuova entità in fondo all'elenco, con il colore successivo della palette."""
    n = conn.execute("SELECT COUNT(*) FROM entities WHERE domain_id=?", (data["domain_id"],)).fetchone()[0]
    return db.insert(conn, "entities", {"pos": n, "color": COLORS[n % len(COLORS)], **data})


# ---------------------------------------------------------------- stati
def state_name(name: str) -> str:
    """Nomi degli stati in MAIUSCOLO_CON_UNDERSCORE: "in lavorazione" → IN_LAVORAZIONE."""
    return re.sub(r"\s+", "_", (name or "").strip()).upper()


def create_state(conn, data: dict) -> int:
    """Nuovo stato in fondo; il primo dell'entità è l'iniziale; vita proposta dal nome se non indicata."""
    name = state_name(data["name"])
    n = conn.execute("SELECT COUNT(*) FROM states WHERE entity_id=?", (data["entity_id"],)).fetchone()[0]
    return db.insert(conn, "states", {"pos": n, "is_initial": int(n == 0), "liveness": suggest_liveness(name),
                                      **data, "name": name})


def reorder_states(conn, entity_id: int, ids: list[int]) -> None:
    for pos, sid in enumerate(ids):
        conn.execute("UPDATE states SET pos=? WHERE id=? AND entity_id=?", (pos, sid, entity_id))


def _domain_guards(conn, domain_id: int) -> list[dict]:
    return db.rows(conn, "guards", """
        SELECT g.* FROM guards g JOIN transitions t ON t.id=g.transition_id JOIN entities e ON e.id=t.entity_id
        WHERE e.domain_id=?""", (domain_id,))


def state_impact(conn, state_id: int) -> list[dict]:
    """Cosa si perde o va rivisto eliminando lo stato, anche nelle altre entità (per la conferma)."""
    did = domain_of_state(conn, state_id)
    out = [{"kind": "action", "action": r["action"]} for r in conn.execute(
        "SELECT action FROM transitions WHERE from_state_id=? OR to_state_id=?", (state_id, state_id))]
    out += [{"kind": "effect", "action": r["action"], "entity": r["entity"]} for r in conn.execute("""
        SELECT t.action, e.name AS entity FROM effects f JOIN transitions t ON t.id=f.transition_id
        JOIN entities e ON e.id=t.entity_id WHERE f.to_state_id=?""", (state_id,))]
    names = {r["id"]: (r["action"], r["entity"]) for r in conn.execute("""
        SELECT t.id, t.action, e.name AS entity FROM transitions t JOIN entities e ON e.id=t.entity_id
        WHERE e.domain_id=?""", (did,))}
    out += [{"kind": "guard", "action": names[g["transition_id"]][0], "entity": names[g["transition_id"]][1]}
            for g in _domain_guards(conn, did) if state_id in g["state_ids"]]
    out += [{"kind": "invariant", "name": i["name"]}
            for i in db.rows(conn, "invariants", "SELECT * FROM invariants WHERE domain_id=?", (did,))
            if any(state_id in r.get("state_ids", []) for r in i["rules"])]
    return out


def delete_state(conn, state_id: int) -> None:
    """Elimina lo stato e lo toglie dai riferimenti JSON, che le chiavi esterne non coprono.

    Transizioni ed effetti che lo usano spariscono per cascata. Una guardia o una condizione che resta
    senza stati non ha più senso e viene rimossa; dagli scenari salvati sparisce l'entità in quello stato.
    """
    did = domain_of_state(conn, state_id)
    for g in _domain_guards(conn, did):
        if state_id in g["state_ids"]:
            ids = [x for x in g["state_ids"] if x != state_id]
            if ids:
                db.update(conn, "guards", g["id"], {"state_ids": ids})
            else:
                conn.execute("DELETE FROM guards WHERE id=?", (g["id"],))
    for inv in db.rows(conn, "invariants", "SELECT * FROM invariants WHERE domain_id=?", (did,)):
        rules = [{**r, "state_ids": [x for x in r.get("state_ids", []) if x != state_id]} for r in inv["rules"]]
        rules = [r for r in rules if r["state_ids"]]
        if rules != inv["rules"]:
            db.update(conn, "invariants", inv["id"], {"rules": rules})
    for sc in db.rows(conn, "scenarios", "SELECT * FROM scenarios WHERE domain_id=?", (did,)):
        state = {k: v for k, v in sc["state"].items() if v != state_id}
        if state != sc["state"]:
            db.update(conn, "scenarios", sc["id"], {"state": state})
    conn.execute("DELETE FROM states WHERE id=?", (state_id,))


# ---------------------------------------------------------------- azioni
def _clean_effect(f: dict) -> dict:
    """Un effetto usa solo il campo del suo tipo: stato di arrivo, nome dell'azione o niente (nascita)."""
    kind = f.get("kind") or "state"
    return {**f, "kind": kind,
            "to_state_id": f.get("to_state_id") if kind == "state" else None,
            "action": (f.get("action") or "").strip() if kind == "action" else ""}


def save_transition(conn, data: dict, transition_id: int | None = None) -> int:
    """Salva un'azione con guardie ed effetti, rimpiazzando quelli esistenti. Ritorna l'id dell'azione."""
    fields = {k: data[k] for k in db.TABLES["transitions"]["fields"] if k in data}
    fields["action"] = (fields.get("action") or "").strip()
    fields["cascade_only"] = int(bool(fields.get("cascade_only")))
    if fields.get("kind") == "create":
        fields["from_state_id"] = None
    if transition_id:
        db.update(conn, "transitions", transition_id, fields)
        conn.execute("DELETE FROM guards WHERE transition_id=?", (transition_id,))
        conn.execute("DELETE FROM effects WHERE transition_id=?", (transition_id,))
    else:
        transition_id = db.insert(conn, "transitions", fields)
    for g in data.get("guards", []):
        g = {k: v for k, v in g.items() if k != "id"}
        db.insert(conn, "guards", {**g, "transition_id": transition_id})
    for f in data.get("effects", []):
        f = {k: v for k, v in _clean_effect(f).items() if k != "id"}
        db.insert(conn, "effects", {**f, "transition_id": transition_id})
    return transition_id

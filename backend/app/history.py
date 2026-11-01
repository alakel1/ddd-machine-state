"""Storico del modello: un'istantanea del dominio prima di ogni modifica, per annullare e confrontare versioni.

- record(): salva il modello com'è prima della modifica che sta per avvenire, con una descrizione.
- undo(): ripristina l'ultima istantanea e la toglie dalla pila (annullare due volte torna indietro di due passi).
- restore(): ripristina una versione qualsiasi; prima salva lo stato attuale, così anche il ripristino si annulla.
- diff(): cosa cambia tra due versioni, in frasi brevi.
Il ripristino rimette le righe con i loro id: le pagine aperte (es. /entity/12) restano valide.
"""
import json

from . import db

KEEP = 100  # istantanee conservate per dominio

# ordine di inserimento nel ripristino (prima i padri) e colonne di ogni tabella
_TABLES = ["entities", "attributes", "states", "transitions", "guards", "effects", "relations", "invariants"]


def _model(conn, domain_id: int) -> dict | None:
    return db.load_model(conn, domain_id)


def _dump(m: dict) -> str:
    return json.dumps(m, sort_keys=True, default=str)


def record(conn, domain_id: int | None, label: str) -> None:
    """Istantanea del dominio prima di una modifica; niente se è uguale all'ultima."""
    if domain_id is None:
        return
    m = _model(conn, domain_id)
    if not m:
        return
    data = _dump(m)
    last = conn.execute("SELECT data FROM snapshots WHERE domain_id=? ORDER BY id DESC LIMIT 1", (domain_id,)).fetchone()
    if last and last["data"] == data:
        return
    conn.execute("INSERT INTO snapshots (domain_id, label, data) VALUES (?, ?, ?)", (domain_id, label, data))
    conn.execute("DELETE FROM snapshots WHERE domain_id=? AND id NOT IN "
                 "(SELECT id FROM snapshots WHERE domain_id=? ORDER BY id DESC LIMIT ?)", (domain_id, domain_id, KEEP))


def _rows(m: dict) -> dict[str, list[dict]]:
    """Le righe del modello per tabella, con i loro id."""
    out = {t: [] for t in _TABLES}
    for e in m["entities"]:
        out["entities"].append(e)
        out["attributes"] += e.get("attributes", [])
        out["states"] += e.get("states", [])
        for t in e.get("transitions", []):
            out["transitions"].append(t)
            out["guards"] += t.get("guards", [])
            out["effects"] += t.get("effects", [])
    out["relations"] = m.get("relations", [])
    out["invariants"] = m.get("invariants", [])
    return out


def _write(conn, domain_id: int, m: dict) -> None:
    """Sostituisce il contenuto del dominio con il modello m, conservando gli id."""
    conn.execute("UPDATE domains SET name=?, description=? WHERE id=?", (m["name"], m.get("description", ""), domain_id))
    conn.execute("DELETE FROM invariants WHERE domain_id=?", (domain_id,))
    conn.execute("DELETE FROM relations WHERE domain_id=?", (domain_id,))
    conn.execute("DELETE FROM entities WHERE domain_id=?", (domain_id,))  # a cascata: stati, azioni, guardie, effetti
    for table, rows in _rows(m).items():
        spec = db.TABLES[table]
        for r in rows:
            cols = ["id"] + [f for f in spec["fields"] if f in r]
            vals = [r["id"]] + [json.dumps(r[c]) if c in spec["json"] else r[c] for c in cols[1:]]
            if table in ("entities", "relations", "invariants"):
                vals[cols.index("domain_id")] = domain_id
            conn.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)


def undo(conn, domain_id: int) -> str | None:
    """Torna all'ultima istantanea diversa dal modello attuale. Ritorna la descrizione della modifica annullata."""
    current = _dump(_model(conn, domain_id))
    for snap in conn.execute("SELECT * FROM snapshots WHERE domain_id=? ORDER BY id DESC", (domain_id,)).fetchall():
        conn.execute("DELETE FROM snapshots WHERE id=?", (snap["id"],))
        if snap["data"] != current:
            _write(conn, domain_id, json.loads(snap["data"]))
            return snap["label"]
    return None


def restore(conn, domain_id: int, snapshot_id: int, label: str) -> bool:
    snap = conn.execute("SELECT * FROM snapshots WHERE id=? AND domain_id=?", (snapshot_id, domain_id)).fetchone()
    if not snap:
        return False
    record(conn, domain_id, label)
    _write(conn, domain_id, json.loads(snap["data"]))
    return True


def load(conn, domain_id: int, snapshot_id: int | None) -> dict | None:
    """Una versione: un'istantanea, oppure (None) il modello attuale."""
    if snapshot_id is None:
        return _model(conn, domain_id)
    snap = conn.execute("SELECT data FROM snapshots WHERE id=? AND domain_id=?", (snapshot_id, domain_id)).fetchone()
    return json.loads(snap["data"]) if snap else None


# ---------------------------------------------------------------- confronto

def diff(a: dict, b: dict, t) -> list[str]:
    """Cosa cambia passando dalla versione a alla versione b."""
    out = []
    q = lambda x: t("q", x=x)
    ents_a, ents_b = {e["id"]: e for e in a["entities"]}, {e["id"]: e for e in b["entities"]}
    sname = {s["id"]: s["name"] for m in (a, b) for e in m["entities"] for s in e["states"]}
    sn = lambda sid: sname.get(sid, t("none_state")) if sid else t("h.any")
    ename = {e["id"]: e["name"] for m in (a, b) for e in m["entities"]}

    if a["name"] != b["name"] or a.get("description") != b.get("description"):
        out.append(t("h.d.domain"))
    for eid in ents_b.keys() - ents_a.keys():
        out.append(t("h.d.entity_added", entity=ents_b[eid]["name"]))
    for eid in ents_a.keys() - ents_b.keys():
        out.append(t("h.d.entity_removed", entity=ents_a[eid]["name"]))
    for eid in ents_a.keys() & ents_b.keys():
        ea, eb = ents_a[eid], ents_b[eid]
        name = eb["name"]
        if ea["name"] != eb["name"]:
            out.append(t("h.d.entity_renamed", old=ea["name"], entity=eb["name"]))
        elif any(ea.get(k) != eb.get(k) for k in ("kind", "context", "color", "description")):
            out.append(t("h.d.entity_changed", entity=name))
        sa, sb = {s["id"]: s for s in ea["states"]}, {s["id"]: s for s in eb["states"]}
        for sid in sb.keys() - sa.keys():
            out.append(t("h.d.state_added", entity=name, state=sb[sid]["name"]))
        for sid in sa.keys() - sb.keys():
            out.append(t("h.d.state_removed", entity=name, state=sa[sid]["name"]))
        for sid in sa.keys() & sb.keys():
            x, y = sa[sid], sb[sid]
            if x["name"] != y["name"]:
                out.append(t("h.d.state_renamed", entity=name, old=x["name"], state=y["name"]))
            if any(x.get(k) != y.get(k) for k in ("is_initial", "is_final", "liveness", "description")):
                out.append(t("h.d.state_changed", entity=name, state=y["name"]))
        if [s["id"] for s in ea["states"]] != [s["id"] for s in eb["states"]] and sa.keys() == sb.keys():
            out.append(t("h.d.states_reordered", entity=name))
        ta, tb = {x["id"]: x for x in ea["transitions"]}, {x["id"]: x for x in eb["transitions"]}
        label = lambda x: t("h.d.transition", action=q(x["action"]), src=sn(x["from_state_id"]) if x["kind"] != "create" else t("none_state"),
                            dst=sn(x["to_state_id"]))
        for tid in tb.keys() - ta.keys():
            out.append(t("h.d.transition_added", entity=name, transition=label(tb[tid])))
        for tid in ta.keys() - tb.keys():
            out.append(t("h.d.transition_removed", entity=name, transition=label(ta[tid])))
        strip = lambda rows: sorted(_dump({k: v for k, v in r.items() if k not in ("id", "transition_id")}) for r in rows)
        for tid in ta.keys() & tb.keys():
            x, y = ta[tid], tb[tid]
            what = []
            if any(x.get(k) != y.get(k) for k in ("from_state_id", "to_state_id", "action", "kind", "cascade_only", "actor", "description")):
                what.append(t("h.d.what.action"))
            if strip(x["guards"]) != strip(y["guards"]):
                what.append(t("h.d.what.guards"))
            if strip(x["effects"]) != strip(y["effects"]):
                what.append(t("h.d.what.effects"))
            if what:
                out.append(t("h.d.transition_changed", entity=name, transition=label(y), what=", ".join(what)))
        if strip(ea.get("attributes", [])) != strip(eb.get("attributes", [])):
            out.append(t("h.d.attributes", entity=name))
    rel = lambda r: t("h.d.relation", src=ename.get(r["source_id"], "?"), dst=ename.get(r["target_id"], "?"))
    ra, rb = {r["id"]: r for r in a.get("relations", [])}, {r["id"]: r for r in b.get("relations", [])}
    for rid in rb.keys() - ra.keys():
        out.append(t("h.d.relation_added", relation=rel(rb[rid])))
    for rid in ra.keys() - rb.keys():
        out.append(t("h.d.relation_removed", relation=rel(ra[rid])))
    for rid in ra.keys() & rb.keys():
        if _dump(ra[rid]) != _dump(rb[rid]):
            out.append(t("h.d.relation_changed", relation=rel(rb[rid])))
    ia, ib = {i["id"]: i for i in a.get("invariants", [])}, {i["id"]: i for i in b.get("invariants", [])}
    for iid in ib.keys() - ia.keys():
        out.append(t("h.d.invariant_added", name=ib[iid]["name"]))
    for iid in ia.keys() - ib.keys():
        out.append(t("h.d.invariant_removed", name=ia[iid]["name"]))
    for iid in ia.keys() & ib.keys():
        if _dump(ia[iid]) != _dump(ib[iid]):
            out.append(t("h.d.invariant_changed", name=ib[iid]["name"]))
    return out


def listing(conn, domain_id: int, t) -> list[dict]:
    """Le versioni dalla più recente: per ognuna cosa ha cambiato la modifica che l'ha seguita."""
    snaps = conn.execute("SELECT id, label, created_at, data FROM snapshots WHERE domain_id=? ORDER BY id DESC",
                         (domain_id,)).fetchall()
    after = _model(conn, domain_id)
    out = []
    for s in snaps:
        before = json.loads(s["data"])
        out.append({"id": s["id"], "label": s["label"], "created_at": s["created_at"], "changes": diff(before, after, t)})
        after = before
    return out

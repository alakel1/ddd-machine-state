"""Import/export del dominio: JSON (round-trip) e Markdown (specifica funzionale)."""
from . import db
from .engine import Engine


def import_model(conn, m: dict) -> int:
    """Inserisce un modello (formato di load_model) rimappando tutti gli id."""
    did = db.insert(conn, "domains", {"name": m.get("name") or "Dominio importato",
                                      "description": m.get("description", "")})
    E, S, T = {}, {}, {}
    for i, e in enumerate(m["entities"]):
        E[e["id"]] = db.insert(conn, "entities", {
            "domain_id": did, "name": e["name"], "description": e.get("description", ""),
            "kind": e.get("kind", "aggregate"), "context": e.get("context", ""),
            "color": e.get("color", "indigo"), "pos": e.get("pos", i)})
        for a in e.get("attributes", []):
            db.insert(conn, "attributes", {**{k: a[k] for k in ("name", "type", "required", "description") if k in a},
                                           "entity_id": E[e["id"]]})
        for j, s in enumerate(e.get("states", [])):
            S[s["id"]] = db.insert(conn, "states", {
                "entity_id": E[e["id"]], "name": s["name"], "description": s.get("description", ""),
                "is_initial": s.get("is_initial", 0), "is_final": s.get("is_final", 0), "pos": s.get("pos", j)})
    S[0] = 0  # stato implicito "non esiste"
    for e in m["entities"]:
        for t in e.get("transitions", []):
            T[t["id"]] = db.insert(conn, "transitions", {
                "entity_id": E[e["id"]], "kind": t.get("kind", "normal"), "cascade_only": t.get("cascade_only", 0),
                "from_state_id": S.get(t.get("from_state_id")) or None,
                "to_state_id": S[t["to_state_id"]], "action": t["action"],
                "actor": t.get("actor", ""), "description": t.get("description", "")})
    for e in m["entities"]:
        for t in e.get("transitions", []):
            for g in t.get("guards", []):
                db.insert(conn, "guards", {
                    "transition_id": T[t["id"]], "kind": g.get("kind", "state"),
                    "entity_id": E.get(g.get("entity_id")),
                    "state_ids": [S[x] for x in g.get("state_ids", []) if x in S],
                    "negate": g.get("negate", 0), "description": g.get("description", "")})
            for f in t.get("effects", []):
                db.insert(conn, "effects", {
                    "transition_id": T[t["id"]], "entity_id": E[f["entity_id"]], "kind": f.get("kind", "state"), "action": f.get("action", ""), "skip_final": f.get("skip_final", 0),
                    "to_state_id": S.get(f["to_state_id"]) or None, "description": f.get("description", "")})
    for r in m.get("relations", []):
        db.insert(conn, "relations", {"domain_id": did, "source_id": E[r["source_id"]], "target_id": E[r["target_id"]],
                                      "cardinality": r.get("cardinality", "1-1"), "label": r.get("label", ""),
                                      "composition": r.get("composition", 0), "instances": r.get("instances", 1),
                                      "parent_gone_states": [S[x] for x in r.get("parent_gone_states", [0]) if x in S],
                                      "child_gone_states": [S[x] for x in r.get("child_gone_states", []) if x in S]})
    for inv in m.get("invariants", []):
        db.insert(conn, "invariants", {
            "domain_id": did, "name": inv["name"], "description": inv.get("description", ""),
            "severity": inv.get("severity", "error"),
            "rules": [{"entity_id": E[r["entity_id"]], "state_ids": [S[x] for x in r.get("state_ids", []) if x in S],
                       "negate": r.get("negate", 0)} for r in inv.get("rules", []) if r.get("entity_id") in E]})
    for sc in m.get("scenarios", []):
        db.insert(conn, "scenarios", {
            "domain_id": did, "name": sc["name"], "description": sc.get("description", ""),
            "state": {str(E[int(k)]): S[v] for k, v in sc.get("state", {}).items() if int(k) in E and v in S},
            "history": []})
    return did


def export_markdown(m: dict) -> str:
    eng = Engine(m)
    sn, en = eng.sname, eng.ename
    L = [f"# {m['name']}", "", m["description"] or "", ""]

    contexts = {}
    for e in m["entities"]:
        contexts.setdefault(e["context"] or "—", []).append(e["name"])
    L += ["## Bounded context", ""]
    L += [f"- **{c}**: {', '.join(n)}" for c, n in contexts.items()] + [""]

    if m["relations"]:
        L += ["## Relazioni", "", "| Da | Relazione | A | Cardinalità | Composizione |", "|---|---|---|---|---|"]
        L += [f"| {en(r['source_id'])} | {r['label']} | {en(r['target_id'])} | {r['cardinality']} | "
              + (f"{en(r['target_id'])} non esiste senza {en(r['source_id'])}" if r.get("composition") else "") + " |"
              for r in m["relations"]]
        L += [""]

    for e in m["entities"]:
        L += [f"## {e['name']}", "", f"*{e['kind']}* · contesto: {e['context'] or '—'}", "", e["description"] or "", ""]
        if e["attributes"]:
            L += ["### Attributi", "", "| Nome | Tipo | Obbl. | Note |", "|---|---|---|---|"]
            L += [f"| {a['name']} | {a['type']} | {'sì' if a['required'] else ''} | {a['description']} |" for a in e["attributes"]]
            L += [""]
        if e["states"]:
            L += ["### Stati", ""]
            for s in e["states"]:
                tags = (" `iniziale`" if s["is_initial"] else "") + (" `finale`" if s["is_final"] else "")
                L.append(f"- **{s['name']}**{tags}" + (f" — {s['description']}" if s["description"] else ""))
            L.append("")
        if e["transitions"]:
            L += ["### Azioni", "", "| Da | Azione | A | Attore | Guardie | Effetti |", "|---|---|---|---|---|---|"]
            for t in e["transitions"]:
                gs = []
                for g in t["guards"]:
                    if g["kind"] == "rule":
                        gs.append(f"[regola] {g['description']}")
                    else:
                        op = "∉" if g["negate"] else "∈"
                        gs.append(f"{en(g['entity_id'])} {op} {{{', '.join(sn(x) for x in g['state_ids'])}}}")
                fx = [f"{en(f['entity_id'])} → " + ("*nasce*" if f.get("kind") == "create"
                                                   else f"esegue «{f.get('action')}»" if f.get("kind") == "action"
                                                   else sn(f["to_state_id"]))
                      for f in t["effects"]]
                frm = "*(creazione)*" if t.get("kind") == "create" else (
                    sn(t["from_state_id"]) if t["from_state_id"] else "*(qualsiasi)*")
                L.append(f"| {frm} | **{t['action']}** | {sn(t['to_state_id'])} | {t['actor']} | "
                         f"{'<br>'.join(gs)} | {'<br>'.join(fx)} |")
            L.append("")

    if m["invariants"]:
        L += ["## Invarianti", ""]
        for inv in m["invariants"]:
            cond = " **E** ".join(
                f"{en(r['entity_id'])} {'∉' if r.get('negate') else '∈'} {{{', '.join(sn(x) for x in r['state_ids'])}}}"
                for r in inv["rules"])
            L += [f"- **{inv['name']}** ({inv['severity']}): vietato quando {cond}" +
                  (f" — {inv['description']}" if inv["description"] else "")]
        L.append("")

    res = eng.analyze()
    L += ["## Analisi edge case", "",
          f"Stati globali raggiungibili: {res['stats']['reachable']} su {res['stats']['theoretical']} teorici.", ""]
    for i in res["issues"]:
        L.append(f"- [{i['severity'].upper()}] **{i['title']}** {i['detail']}")
        if i.get("trace"):
            L.append("  - percorso: " + " → ".join(f"{en(s['entity_id'])}.{s['action']}" for s in i["trace"]))
    return "\n".join(L) + "\n"

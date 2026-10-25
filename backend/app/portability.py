"""Import/export del dominio: JSON (round-trip) e Markdown (specifica funzionale)."""
from . import db
from .engine import Engine
from .relations import legacy_gone_states


def import_model(conn, m: dict, default_name: str = "") -> int:
    """Inserisce un modello (formato di load_model) rimappando tutti gli id."""
    did = db.insert(conn, "domains", {"name": m.get("name") or default_name,
                                      "description": m.get("description", "")})
    E, S, T = {}, {}, {}
    # vita degli stati: formato 3 "liveness"; formato 2 "is_gone" (1 = eliminato);
    # formato 1: gli stati "assente" erano sulle relazioni e si ricavano da lì
    states = [s for e in m["entities"] for s in e.get("states", [])]
    has_flags = any("liveness" in s or "is_gone" in s for s in states)
    legacy_gone = set() if has_flags else legacy_gone_states(m)
    liveness = lambda s: s.get("liveness") or ("gone" if s.get("is_gone") or s["id"] in legacy_gone else "active")
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
                "is_initial": s.get("is_initial", 0), "is_final": s.get("is_final", 0),
                "liveness": liveness(s), "pos": s.get("pos", j)})
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
        src, dst, card = E[r["source_id"]], E[r["target_id"]], r.get("cardinality", "1-1")
        if card == "N-1":  # solo 1-1, 1-N, N-N: si legge dal lato "1"
            card = "1-N"
            if not r.get("composition"):
                src, dst = dst, src
        db.insert(conn, "relations", {"domain_id": did, "source_id": src, "target_id": dst,
                                      "cardinality": card, "label": r.get("label", ""),
                                      "composition": r.get("composition", 0), "instances": r.get("instances", 1)})
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


def export_markdown(m: dict, lang: str | None = None) -> str:
    eng = Engine(m, lang)
    t, sn, en = eng.t, eng.sname, eng.ename
    L = [f"# {m['name']}", "", m["description"] or "", ""]

    contexts = {}
    for e in m["entities"]:
        contexts.setdefault(e["context"] or "—", []).append(e["name"])
    L += [f"## {t('md.contexts')}", ""]
    L += [f"- **{c}**: {', '.join(n)}" for c, n in contexts.items()] + [""]

    if m["relations"]:
        L += [f"## {t('md.relations')}", "", t("md.relations_header"), "|---|---|---|---|---|"]
        L += [f"| {en(r['source_id'])} | {r['label']} | {en(r['target_id'])} | {r['cardinality']} | "
              + (t("md.composition", child=en(r["target_id"]), parent=en(r["source_id"])) if r.get("composition") else "") + " |"
              for r in m["relations"]]
        L += [""]

    for e in m["entities"]:
        L += [f"## {e['name']}", "", t("md.context_line", kind=t(f"kind.{e['kind']}"), context=e["context"] or "—"), "",
              e["description"] or "", ""]
        if e["attributes"]:
            L += [f"### {t('md.attributes')}", "", t("md.attributes_header"), "|---|---|---|---|"]
            L += [f"| {a['name']} | {a['type']} | {t('md.yes') if a['required'] else ''} | {a['description']} |" for a in e["attributes"]]
            L += [""]
        if e["states"]:
            L += [f"### {t('md.states')}", ""]
            for s in e["states"]:
                tags = (f" `{t('md.initial')}`" if s["is_initial"] else "") + (f" `{t('md.final')}`" if s["is_final"] else "") \
                    + (f" `{t('md.' + s['liveness'])}`" if s.get("liveness") in ("off", "gone") else "")
                L.append(f"- **{s['name']}**{tags}" + (f" — {s['description']}" if s["description"] else ""))
            L.append("")
        if e["transitions"]:
            L += [f"### {t('md.actions')}", "", t("md.actions_header"), "|---|---|---|---|---|---|"]
            for tr in e["transitions"]:
                gs = []
                for g in tr["guards"]:
                    if g["kind"] == "rule":
                        gs.append(t("md.rule", text=g["description"]))
                    else:
                        op = "∉" if g["negate"] else "∈"
                        gs.append(f"{en(g['entity_id'])} {op} {{{', '.join(sn(x) for x in g['state_ids'])}}}")
                fx = [f"{en(f['entity_id'])} → " + (t("md.created") if f.get("kind") == "create"
                                                   else t("md.runs", action=f.get("action")) if f.get("kind") == "action"
                                                   else sn(f["to_state_id"]))
                      for f in tr["effects"]]
                frm = t("md.creation") if tr.get("kind") == "create" else (
                    sn(tr["from_state_id"]) if tr["from_state_id"] else t("md.any"))
                L.append(f"| {frm} | **{tr['action']}** | {sn(tr['to_state_id'])} | {tr['actor']} | "
                         f"{'<br>'.join(gs)} | {'<br>'.join(fx)} |")
            L.append("")

    if m["invariants"]:
        L += [f"## {t('md.invariants')}", ""]
        for inv in m["invariants"]:
            cond = f" {t('md.and')} ".join(
                f"{en(r['entity_id'])} {'∉' if r.get('negate') else '∈'} {{{', '.join(sn(x) for x in r['state_ids'])}}}"
                for r in inv["rules"])
            L += [t("md.forbidden", name=inv["name"], severity=t(f"severity.{inv['severity']}"), cond=cond) +
                  (f" — {inv['description']}" if inv["description"] else "")]
        L.append("")

    res = eng.analyze()
    L += [f"## {t('md.analysis')}", "",
          t("md.reachable", reachable=res["stats"]["reachable"], theoretical=res["stats"]["theoretical"]), ""]
    for i in res["issues"]:
        sev = t(f"severity.{i['severity']}").upper()
        L.append(f"- [{sev}] **{i['title']}** {i['detail']}")
        if i.get("trace"):
            L.append(f"  - {t('md.path')}: " + " → ".join(f"{en(s['entity_id'])}.{s['action']}" for s in i["trace"]))
    return "\n".join(L) + "\n"

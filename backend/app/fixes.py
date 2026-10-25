"""Soluzioni proposte per le segnalazioni dell'analisi ("Risolvi").

Ogni soluzione è {"key", "title", "detail", "ops"}: ops sono modifiche atomiche al modello che
POST /api/domains/{id}/fix applica in una sola transazione (editing.apply_ops). Si propongono solo
correzioni ricavate da dati certi: l'effetto che fallisce, la guardia che blocca, l'ultima azione del
percorso che porta all'invariante violata. Quando non ce ne sono, la lista è vuota e resta la spiegazione.
"""
from .relations import NONE, gone_states

# ---------------------------------------------------------------- operazioni


def op_add_transition(eid, src, dst, action):
    return {"op": "add_transition", "entity_id": eid, "from_state_id": src, "to_state_id": dst,
            "action": action, "cascade_only": 1}


def op_add_effect(tid, eid, dst):
    return {"op": "add_effect", "transition_id": tid, "entity_id": eid, "kind": "state", "to_state_id": dst, "skip_final": 1}


def op_add_guard(tid, eid, state_ids, negate=0, description=""):
    return {"op": "add_guard", "transition_id": tid, "entity_id": eid, "state_ids": list(state_ids),
            "negate": int(bool(negate)), "description": description}


def op_update(table, id_, **data):
    return {"op": "update", "table": table, "id": id_, "data": data}


def op_delete(table, id_):
    return {"op": "delete", "table": table, "id": id_}


# ---------------------------------------------------------------- utilità


class _Ctx:
    """Viste sul modello (non espanso) che servono a costruire le proposte."""

    def __init__(self, e):
        self.e = e
        self.effects = {f["id"]: (t, f) for t in e.transitions for f in t["effects"] if f.get("id") is not None}
        self.guards = {g["id"]: (t, g) for t in e.transitions for g in t["guards"] if g.get("id") is not None}

    def fix(self, key, ops, **params):
        t = self.e.t
        return {"key": key, "title": t(f"fx.{key}.title", **params), "detail": t(f"fx.{key}.detail", **params), "ops": ops}

    def states(self, ids):
        names = [self.e.sname(s) for s in ids]
        return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} {self.e.t('or')} {names[-1]}" if names else "—"

    def name_for(self, eid, dst, fallback):
        """Nome per una nuova transizione: quello di un'azione dell'entità che arriva già lì, se c'è."""
        same = [t["action"] for t in self.e.entity[eid]["transitions"] if t["to_state_id"] == dst and t["kind"] != "create"]
        return same[0] if same else fallback

    def first_state(self, eid, liveness):
        return next((s["id"] for s in self.e.entity[eid]["states"] if s.get("liveness", "active") == liveness), None)

    def alive_states(self, eid):
        """Stati in cui l'entità c'è (non eliminata)."""
        return [s["id"] for s in self.e.entity[eid]["states"] if s["id"] not in gone_states(self.e.entity[eid])]

    def active_ids(self, eid):
        return [s["id"] for s in self.e.entity[eid]["states"] if s.get("liveness", "active") == "active"]


# ---------------------------------------------------------------- per tipo di segnalazione


def _conflict_fixes(x: _Ctx, issue, base, tbase):
    e, c = x.e, issue["conflict"]
    child = base(c["entity_id"])
    trig = e.transition.get(tbase(c["trigger_id"]))
    eff = x.effects.get(c.get("effect_id"))
    if not trig or child not in e.entity:
        return []
    p = {"child": e.ename(child), "parent": e.ename(trig["entity_id"]), "action": trig["action"]}
    out = []
    gap = c.get("gap")
    if gap and e.is_final(gap["from"]):
        if eff:
            out.append(x.fix("skip_final", [op_update("effects", eff[1]["id"], skip_final=1)], state=e.sname(gap["from"]), **p))
    elif gap and gap.get("to"):
        name = x.name_for(child, gap["to"], trig["action"])
        out.append(x.fix("add_transition", [op_add_transition(child, gap["from"], gap["to"], name)],
                         entity=e.ename(child), src=e.sname(gap["from"]), dst=e.sname(gap["to"]), name=name))
    elif gap and gap.get("action"):
        targets = {t["to_state_id"] for t in e.entity[child]["transitions"]
                   if t["action"].strip().lower() == gap["action"].lower()}
        if len(targets) == 1:
            dst = targets.pop()
            out.append(x.fix("add_transition", [op_add_transition(child, gap["from"], dst, gap["action"])],
                             entity=e.ename(child), src=e.sname(gap["from"]), dst=e.sname(dst), name=gap["action"]))
    seen = set()
    for m in c.get("misses") or ():
        if m["guard_id"] in seen or m["guard_id"] not in x.guards:
            continue
        seen.add(m["guard_id"])
        owner, g = x.guards[m["guard_id"]]
        cur = [s for s in m["current"] if s != NONE or g["negate"]]
        gp = {"action": owner["action"], "entity": e.ename(g["entity_id"]), "required": x.states(g["state_ids"]),
              "states": x.states(m["current"])}
        if cur and not g.get("each_entity_id"):
            ids = [s for s in g["state_ids"] if s not in cur] if g["negate"] else sorted(set(g["state_ids"]) | set(cur))
            out.append(x.fix("narrow_guard" if g["negate"] else "widen_guard",
                             [op_update("guards", g["id"], state_ids=ids)], **gp))
        out.append(x.fix("delete_guard", [op_delete("guards", g["id"])], **gp))
    if eff:
        out.append(x.fix("delete_effect", [op_delete("effects", eff[1]["id"])], **p))
    return out


def _missing_fixes(x: _Ctx, issue):
    e, gap = x.e, issue["gap"]
    eid = issue["entity_id"]
    if not gap.get("to"):
        return []
    req = issue.get("requested_by") or [{}]
    name = gap.get("action") or x.name_for(eid, gap["to"], req[0].get("action") or "")
    return [x.fix("add_transition", [op_add_transition(eid, gap["from"], gap["to"], name)],
                  entity=e.ename(eid), src=e.sname(gap["from"]), dst=e.sname(gap["to"]), name=name)]


def _composition_effect(x: _Ctx, rel, kind, trans):
    """Effetto sull'azione del padre che porta il figlio nello stato spento/eliminato."""
    e, child = x.e, rel["target_id"]
    dst = x.first_state(child, kind)
    if dst is None or any(f["entity_id"] == child for f in trans["effects"]):
        return []
    return [x.fix("effect_parent", [op_add_effect(trans["id"], child, dst)], child=e.ename(child),
                  parent=e.ename(rel["source_id"]), action=trans["action"], state=e.sname(dst))]


def _uncompose(x: _Ctx, rel):
    e = x.e
    return x.fix("uncompose", [op_update("relations", rel["id"], composition=0)],
                 child=e.ename(rel["target_id"]), parent=e.ename(rel["source_id"]))


def _invariant_fixes(x: _Ctx, issue, inv, tbase):
    e = x.e
    trace = issue.get("trace") or []
    last = e.transition.get(tbase(trace[-1]["transition_id"])) if trace else None
    out = []
    if inv and inv.get("derived"):
        rel = next((r for r in e.model.get("relations", []) if r["id"] == inv["relation_id"]), None)
        if not rel:
            return []
        parent, child, kind = rel["source_id"], rel["target_id"], inv["kind"]
        if last and last["entity_id"] == parent:
            out += _composition_effect(x, rel, kind, last)
        elif last and last["entity_id"] == child:
            allowed = x.alive_states(parent) if kind == "gone" else x.active_ids(parent)
            if allowed:
                out.append(x.fix("guard_child", [op_add_guard(last["id"], parent, allowed)], child=e.ename(child),
                                 parent=e.ename(parent), action=last["action"], states=x.states(allowed),
                                 status=e.t(f"fx.status.{kind}")))
        out.append(_uncompose(x, rel))
        return out
    # invariante dell'utente con due condizioni: l'ultima azione porta la sua entità nella combinazione vietata,
    # quindi le si aggiunge la guardia opposta all'altra condizione
    rules = (inv or {}).get("rules") or []
    if last and len(rules) == 2 and any(r["entity_id"] == last["entity_id"] for r in rules):
        other = next((r for r in rules if r["entity_id"] != last["entity_id"]), None)
        if other:
            g = {"kind": "state", "entity_id": other["entity_id"], "state_ids": other["state_ids"],
                 "negate": 0 if other.get("negate") else 1}
            out.append(x.fix("guard_invariant", [op_add_guard(last["id"], other["entity_id"], other["state_ids"],
                                                              g["negate"], inv.get("name", ""))],
                             action=last["action"], owner=e.ename(last["entity_id"]), guard=e._guard_text(g)))
    return out


def _local_fixes(x: _Ctx, issue):
    e, code = x.e, issue.get("code")
    sid, tid = issue.get("state_id"), issue.get("transition_id")
    s = e.state.get(sid)
    t = e.transition.get(tid)
    if code == "a.dead_end" and s:
        return [x.fix("mark_final", [op_update("states", sid, is_final=1)], state=s["name"], entity=e.ename(s["entity_id"]))]
    if code == "a.final_outgoing" and s:
        return [x.fix("unmark_final", [op_update("states", sid, is_final=0)], state=s["name"])]
    if code == "a.gone_exit" and s:
        return [x.fix("liveness_off", [op_update("states", sid, liveness="off")], state=s["name"])]
    if code == "a.off_no_way_back" and s:
        return [x.fix("liveness_gone", [op_update("states", sid, liveness="gone")], state=s["name"])]
    if code == "a.empty_guard" and t:
        ops = [op_delete("guards", g["id"]) for g in t["guards"] if g["kind"] == "state" and not g["state_ids"]]
        return [x.fix("delete_empty_guard", ops, action=t["action"])] if ops else []
    if code == "a.cascade_never" and t:
        return [x.fix("cascade_direct", [op_update("transitions", t["id"], cascade_only=0)], action=t["action"])]
    rel = next((r for r in e.model.get("relations", []) if r["id"] == issue.get("relation_id")), None)
    if code in ("r.parent_leaves", "r.parent_off") and rel and t:
        return _composition_effect(x, rel, "gone" if code == "r.parent_leaves" else "off", t) + [_uncompose(x, rel)]
    if code == "r.create_without_parent" and rel and t:
        parent = rel["source_id"]
        allowed = x.alive_states(parent)
        out = [x.fix("guard_parent_alive", [op_add_guard(t["id"], parent, allowed)], parent=e.ename(parent),
                     action=t["action"], states=x.states(allowed))] if allowed else []
        return out + [_uncompose(x, rel)]
    return []


def fixes_for(engine, issue, inv=None, base=lambda x: x, tbase=lambda x: x) -> list[dict]:
    x = getattr(engine, "_fix_ctx", None)
    if x is None:
        x = engine._fix_ctx = _Ctx(engine)
    cat = issue["category"]
    if cat == "effect_conflict":
        return _conflict_fixes(x, issue, base, tbase)
    if cat == "missing_transition":
        return _missing_fixes(x, issue)
    if issue.get("code") == "a.violable":
        return _invariant_fixes(x, issue, inv, tbase)
    return _local_fixes(x, issue)

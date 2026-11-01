"""Soluzioni proposte per le segnalazioni dell'analisi ("Risolvi").

Ogni soluzione è {"key", "title", "detail", "ops"}: ops sono modifiche atomiche al modello che
POST /api/domains/{id}/fix applica in una sola transazione (editing.apply_ops). Si propongono solo
correzioni ricavate da dati certi: l'effetto che fallisce, la guardia che blocca, l'ultima azione del
percorso che porta all'invariante violata. Quando non ce ne sono, la lista è vuota e resta la spiegazione.
"""
from .relations import NONE, gone_states, is_create

# ---------------------------------------------------------------- operazioni


def op_add_transition(eid, src, dst, action, cascade_only=1, guards=(), kind="normal"):
    return {"op": "add_transition", "entity_id": eid, "from_state_id": src, "to_state_id": dst,
            "action": action, "cascade_only": cascade_only, "guards": list(guards), "kind": kind}


def op_add_effect(tid, eid, dst):
    return {"op": "add_effect", "transition_id": tid, "entity_id": eid, "kind": "state", "to_state_id": dst, "skip_final": 1}


def op_add_create_effect(tid, eid):
    return {"op": "add_effect", "transition_id": tid, "entity_id": eid, "kind": "create", "skip_final": 0}


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
        births = [t for t in e.entity[parent]["transitions"] if is_create(t)]
        if not trace and kind == "gone" and births and not any(is_create(t) for t in e.entity[child]["transitions"]):
            # violata già all'inizio: il figlio non ha azioni di creazione, quindi esiste dallo stato iniziale anche
            # quando il padre non c'è ancora. Nasce insieme al padre
            start = next((s["id"] for s in e.entity[child]["states"] if s["is_initial"]), None) \
                or next((s["id"] for s in e.entity[child]["states"]), None)
            if start is not None:
                name = e.t("fx.create_name")
                ops = [op_add_transition(child, None, start, name, kind="create")]
                ops += [op_add_create_effect(t["id"], child) for t in births]
                out.append(x.fix("born_with_parent", ops, child=e.ename(child), parent=e.ename(parent),
                                 state=e.sname(start), name=name,
                                 actions=", ".join(e.t("q", x=t["action"]) for t in births)))
        if last and last["entity_id"] == parent:
            out += _composition_effect(x, rel, kind, last)
        elif last and last["entity_id"] == child:
            allowed = x.alive_states(parent) if kind == "gone" else x.active_ids(parent)
            # se è una creazione, la stessa guardia serve a tutte le creazioni del figlio che non guardano il padre:
            # altrimenti l'analisi trova la stessa violazione dall'altra strada
            same = [last] + [t for t in e.entity[child]["transitions"] if is_create(last) and is_create(t)
                             and t["id"] != last["id"] and not any(g.get("entity_id") == parent for g in t["guards"])]
            if allowed:
                out.append(x.fix("guard_child", [op_add_guard(t["id"], parent, allowed) for t in same], child=e.ename(child),
                                 parent=e.ename(parent), action=", ".join(e.t("q", x=t["action"]) for t in same),
                                 states=x.states(allowed), status=e.t(f"fx.status.{kind}")))
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


def _deadlock_fixes(x: _Ctx, issue, base, tbase):
    """Dal motivo del deadlock: l'azione che ha fermato l'altra entità porta anche questa in uno stato finale;
    oppure si toglie la guardia che blocca l'uscita; oppure lo stato in cui resta ferma diventa finale."""
    e = x.e
    w = (issue.get("deadlock") or {}).get("why")
    if not w:
        return []
    # un'uscita si blocca per una cascata: prima le soluzioni di quel conflitto
    out = _conflict_fixes(x, {"conflict": w["conflict"]}, base, tbase) if w.get("conflict") else []
    if w["kind"] == "cascade":
        return out
    eid, sid = base(w["entity_id"]), w["state_id"]
    s = e.state.get(sid)
    if eid not in e.entity or not s:
        return out
    p = {"entity": e.ename(eid), "state": s["name"]}
    exits = {t["to_state_id"]: t for t in e.outgoing(eid, sid)}
    finals = [st["id"] for st in e.entity[eid]["states"] if st["is_final"]]
    blocker = base(w["blocker"]) if w.get("blocker") is not None else None
    if blocker in e.entity and w.get("bstate") not in (None, NONE) and finals:
        # un'uscita apposta per questa situazione: da {state} a uno stato finale quando l'altra entità è dove si è fermata
        dst = next((d for d in finals if d in exits), finals[0])
        name = x.name_for(eid, dst, e.sname(dst).lower())
        out.append(x.fix("deadlock_exit", [op_add_transition(eid, sid, dst, name, cascade_only=0, guards=[
            {"entity_id": blocker, "state_ids": [w["bstate"]]}])], dst=e.sname(dst), name=name,
            blocker=e.ename(blocker), bstate=e.sname(w["bstate"]), **p))
    by = e.transition.get(tbase(w["by"])) if w.get("kind") == "by" and w.get("by") else None
    if by and not any(f["entity_id"] == eid for f in by["effects"]):
        # uno stato finale raggiungibile senza guardie; se la sola transizione verso lì ha guardie, l'effetto
        # verrebbe bloccato come l'azione: meglio non proporlo
        dst = next((d for d in finals if d in exits and not exits[d]["guards"]), None) \
            or next((d for d in finals if d not in exits), None)
        if dst is not None:
            ops = [op_add_effect(by["id"], eid, dst)]
            if dst not in exits:
                ops.insert(0, op_add_transition(eid, sid, dst, x.name_for(eid, dst, by["action"])))
            out.append(x.fix("deadlock_effect", ops, by=by["action"], owner=e.ename(by["entity_id"]), dst=e.sname(dst), **p))
    if w.get("guard_id") in x.guards:
        owner, g = x.guards[w["guard_id"]]
        out.append(x.fix("delete_guard", [op_delete("guards", g["id"])], action=owner["action"],
                         entity=e.ename(g["entity_id"]), required=x.states(g["state_ids"]), states=""))
    if w.get("kind") in ("guard", "no_exit") and not s["is_final"]:
        out.append(x.fix("mark_final", [op_update("states", sid, is_final=1)], **p))
    return out


def fixes_for(engine, issue, inv=None, base=lambda x: x, tbase=lambda x: x) -> list[dict]:
    x = getattr(engine, "_fix_ctx", None)
    if x is None:
        x = engine._fix_ctx = _Ctx(engine)
    cat = issue["category"]
    if cat == "effect_conflict":
        return _conflict_fixes(x, issue, base, tbase)
    if cat == "missing_transition":
        return _missing_fixes(x, issue)
    if cat == "deadlock":
        return _deadlock_fixes(x, issue, base, tbase)
    if issue.get("code") == "a.violable":
        return _invariant_fixes(x, issue, inv, tbase)
    return _local_fixes(x, issue)

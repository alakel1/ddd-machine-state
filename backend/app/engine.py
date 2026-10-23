"""Motore della macchina a stati del dominio.

Lo "stato globale" è una mappa {entity_id: state_id}: un'istanza per entità,
pensata per ragionare sugli scenari di un singolo caso di business
(es. un ordine con il suo pagamento, la sua spedizione, la sua fattura).

Nascita delle entità: se un'entità ha azioni di creazione (kind="create"),
parte dallo stato implicito NONE ("non esiste") e nasce tramite una di esse,
ognuna con le proprie guardie, effetti e attore. Le entità senza azioni di
creazione partono, come prima, dal loro stato iniziale.
"""
import hashlib
import json
import threading
from collections import OrderedDict, deque

from .i18n import translator
from .relations import derived_invariants, expand, relation_issues

NONE = 0  # stato implicito "non esiste" (gli id SQLite partono da 1)
MAX_CASCADE_DEPTH = 8
DEFAULT_EXPLORE_LIMIT = 300_000
_EXPLORE_CACHE: "OrderedDict[tuple, dict]" = OrderedDict()  # esplorazioni recenti, per modello e limite
_EXPLORE_CACHE_SIZE = 6
_EXPLORE_LOCK = threading.Lock()  # richieste parallele sullo stesso modello aspettano il primo calcolo


def is_create(t: dict) -> bool:
    return t.get("kind") == "create"


class Engine:
    def __init__(self, model: dict, lang: str | None = None):
        self.model = model
        self.t = translator(lang)
        self.entities = [e for e in model["entities"] if e["states"]]
        self.entity = {e["id"]: e for e in model["entities"]}
        self.state = {s["id"]: s for e in model["entities"] for s in e["states"]}
        self.transitions = [t for e in model["entities"] for t in e["transitions"]]
        self.transition = {t["id"]: t for t in self.transitions}
        # le composizioni aggiungono le loro invarianti (già incluse se il modello è stato espanso)
        self.invariants = model["invariants"] + ([] if model.get("_derived_done") else derived_invariants(model, self.t))
        self.order = [e["id"] for e in self.entities]
        self._outgoing = {}
        # forme precompilate per i percorsi caldi dell'esplorazione
        self._inv_fast = [(inv, [(r["entity_id"], frozenset(r.get("state_ids", [])), bool(r.get("negate")),
                                  tuple(r.get("entity_ids") or ())) for r in inv["rules"]]) for inv in self.invariants]
        self._guards_fast = {
            t["id"]: [(g["entity_id"], tuple(g.get("entity_ids") or ()), frozenset(g["state_ids"]), bool(g["negate"]))
                      for g in t["guards"] if g["kind"] == "state" and g.get("entity_id") is not None]
            for t in self.transitions}
        self.creatable = {e["id"] for e in self.entities if any(is_create(t) for t in e["transitions"])}
        # stati "di riposo" che non contano come blocco: NONE e l'iniziale delle entità non creabili
        self.resting = {NONE} | {sid for eid, sid in self.initial_state().items() if eid not in self.creatable}

    # ---------- utilità di naming ----------
    def sname(self, sid):
        if sid == NONE:
            return self.t("none_state")
        s = self.state.get(sid)
        return s["name"] if s else "∅"

    def ename(self, eid):
        e = self.entity.get(eid)
        return e["name"] if e else "?"

    def is_final(self, sid):
        s = self.state.get(sid)
        return bool(s and s["is_final"])

    def states_of(self, eid) -> list[dict]:
        """Stati dell'entità, preceduti da NON_ESISTE se l'entità è creabile."""
        virtual = [{"id": NONE, "name": self.t("none_state"), "virtual": True}] if eid in self.creatable else []
        return virtual + self.entity[eid]["states"]

    # ---------- stato ----------
    def initial_state(self) -> dict:
        st = {}
        for e in self.entities:
            if any(is_create(t) for t in e["transitions"]):
                st[e["id"]] = NONE
                continue
            init = [s for s in e["states"] if s["is_initial"]]
            st[e["id"]] = (init or e["states"])[0]["id"]
        return st

    def normalize(self, state: dict) -> dict:
        """Accetta chiavi stringa (JSON) e completa con lo stato di partenza."""
        st = self.initial_state()
        for k, v in (state or {}).items():
            k = int(k)
            if k not in st:
                continue
            if v == NONE and k in self.creatable:
                st[k] = NONE
            elif v in self.state and self.state[v]["entity_id"] == k:
                st[k] = v
        return st

    def key(self, state: dict) -> tuple:
        return tuple(state[e] for e in self.order)

    def from_key(self, key: tuple) -> dict:
        return dict(zip(self.order, key))

    # ---------- transizioni ----------
    def applies_from(self, t: dict, sid) -> bool:
        if is_create(t):
            return sid == NONE
        if sid == NONE:
            return False
        if t["from_state_id"] is None:
            return not self.is_final(sid) and sid != t["to_state_id"]
        return t["from_state_id"] == sid

    def outgoing(self, eid, sid) -> list[dict]:
        # dipende solo da (entità, stato): memorizzato, l'esplorazione lo chiede centinaia di migliaia di volte
        key = (eid, sid)
        out = self._outgoing.get(key)
        if out is None:
            out = self._outgoing[key] = [t for t in self.entity[eid]["transitions"] if self.applies_from(t, sid)]
        return out

    def guard_failures(self, t: dict, state: dict) -> list[str]:
        fails = []
        for g in t["guards"]:
            if g["kind"] != "state" or g["entity_id"] not in state:
                continue
            check = lambda s: (s not in g["state_ids"]) if g["negate"] else (s in g["state_ids"])
            if g.get("entity_ids"):
                # entità con più istanze (analisi): vale per tutte le istanze esistenti
                existing = [x for x in g["entity_ids"] if state.get(x, NONE) != NONE] or g["entity_ids"][:1]
                bad = [x for x in existing if not check(state[x])]
                ok, cur = not bad, state[bad[0]] if bad else None
                if bad:
                    g = {**g, "entity_id": bad[0]}
            else:
                cur = state[g["entity_id"]]
                ok = check(cur)
            if not ok:
                names = ", ".join(self.sname(s) for s in g["state_ids"]) or "—"
                op = self.t("guard.must_not" if g["negate"] else "guard.must")
                fails.append(
                    self.t("guard.fail", entity=self.ename(g["entity_id"]), op=op, states=names, current=self.sname(cur))
                    + (f" — {g['description']}" if g["description"] else "")
                )
        return fails

    def guards_ok(self, t: dict, state: dict) -> bool:
        """Come guard_failures, ma solo sì/no: senza costruire messaggi (usata nell'esplorazione)."""
        for eid, many, ids, neg in self._guards_fast[t["id"]]:
            if eid not in state:
                continue
            if many:
                existing = [state[x] for x in many if state.get(x, NONE) != NONE] or [state[many[0]]]
            else:
                existing = (state[eid],)
            for s in existing:
                if (s in ids) == neg:
                    return False
        return True

    def rule_notes(self, t: dict) -> list[str]:
        return [g["description"] for g in t["guards"] if g["kind"] == "rule" and g["description"]]

    def fire(self, t: dict, state: dict, depth: int = 0, chain: frozenset = frozenset()):
        """Esegue t e propaga gli effetti. Ritorna (nuovo_stato, passi, conflitti)."""
        new = dict(state)
        steps = [{
            "transition_id": t["id"], "entity_id": t["entity_id"], "action": t["action"],
            "from": state.get(t["entity_id"]), "to": t["to_state_id"], "depth": depth,
            "create": is_create(t),
        }]
        conflicts = []
        new[t["entity_id"]] = t["to_state_id"]
        chain = chain | {t["id"]}

        for eff in t["effects"]:
            eid, target = eff["entity_id"], eff["to_state_id"]
            if eid not in new:
                continue
            cur = new[eid]
            if eff.get("skip_final") and self.is_final(cur) and cur != target:
                # effetto su "tutte le istanze attive": un'istanza già conclusa (es. nel cestino) è esclusa
                steps.append({"transition_id": None, "entity_id": eid, "action": self.t("step.excluded"),
                              "from": cur, "to": cur, "depth": depth + 1, "skipped": True})
                continue
            if eff.get("kind") in ("create", "action"):
                handler = self._fire_creation if eff["kind"] == "create" else self._fire_named_action
                new, sub_steps, sub_conf = handler(t, eff, new, depth, chain)
                steps += sub_steps
                conflicts += sub_conf
                continue
            if cur == target:
                continue
            label = self.t("effect.label_state", action=t["action"], entity=self.ename(eid), src=self.sname(cur), dst=self.sname(target))
            cands = [c for c in self.outgoing(eid, cur) if c["to_state_id"] == target]
            if not cands and cur == NONE:
                # l'entità non esiste e non c'è un modo di crearla in quello stato:
                # l'effetto non si applica (es. "annulla il pagamento" quando non c'è pagamento)
                steps.append({"transition_id": None, "entity_id": eid, "action": self.t("step.missing"),
                              "from": cur, "to": cur, "depth": depth + 1, "skipped": True})
                continue
            if not cands:
                conflicts.append(self.t("effect.no_transition", label=label, src=self.sname(cur), dst=self.sname(target)))
                new[eid] = target
                steps.append({"transition_id": None, "entity_id": eid, "action": "(forzato)",
                              "from": cur, "to": target, "depth": depth + 1, "forced": True})
                continue
            chosen, why = None, []
            for c in cands:
                f = self.guard_failures(c, new)
                if not f:
                    chosen = c
                    break
                why += f
            if chosen is None:
                conflicts.append(self.t("effect.blocked", label=label, action=cands[0]["action"], why="; ".join(why)))
                new[eid] = target
                steps.append({"transition_id": None, "entity_id": eid, "action": "(forzato)",
                              "from": cur, "to": target, "depth": depth + 1, "forced": True})
                continue
            if chosen["id"] in chain or depth >= MAX_CASCADE_DEPTH:
                conflicts.append(self.t("effect.cycle_on", label=label, action=chosen["action"]))
                continue
            new, sub_steps, sub_conf = self.fire(chosen, new, depth + 1, chain)
            steps += sub_steps
            conflicts += sub_conf
        return new, steps, conflicts

    def _fire_named_action(self, t: dict, eff: dict, state: dict, depth: int, chain: frozenset):
        """Effetto "esegui azione": l'entità esegue l'azione con quel nome disponibile dal suo stato attuale."""
        eid, name = eff["entity_id"], eff.get("action", "").strip()
        cur = state[eid]
        label = self.t("effect.label_action", action=t["action"], entity=self.ename(eid), name=name)
        if cur == NONE:
            # nessuna istanza da coinvolgere (es. un regolamento senza modelli): l'effetto non si applica
            return state, [{"transition_id": None, "entity_id": eid, "action": self.t("step.missing_action", action=name),
                            "from": cur, "to": cur, "depth": depth + 1, "skipped": True}], []
        same_name = [c for c in self.entity[eid]["transitions"] if c["action"].strip().lower() == name.lower()]
        cands = [c for c in same_name if self.applies_from(c, cur)]
        if not cands and any(c["to_state_id"] == cur for c in same_name):
            # già nello stato a cui l'azione porta (es. arrivata in cascata da un altro ramo): niente da fare
            return state, [], []
        if not cands:
            return state, [], [self.t("effect.action_unavailable", label=label, state=self.sname(cur))]
        ok, why = [], []
        for c in cands:
            f = self.guard_failures(c, state)
            if f:
                why += f
            else:
                ok.append(c)
        if not ok:
            return state, [], [self.t("effect.action_blocked", label=label, why="; ".join(why))]
        if len(ok) > 1:
            names = ", ".join(self.sname(c["to_state_id"]) for c in ok)
            return state, [], [self.t("effect.action_ambiguous", label=label, state=self.sname(cur), targets=names)]
        if ok[0]["id"] in chain or depth >= MAX_CASCADE_DEPTH:
            return state, [], [self.t("effect.cycle", label=label)]
        return self.fire(ok[0], state, depth + 1, chain)

    def _fire_creation(self, t: dict, eff: dict, state: dict, depth: int, chain: frozenset):
        """Effetto "crea": l'entità nasce tramite l'unica azione di creazione con guardie soddisfatte."""
        eid = eff["entity_id"]
        label = self.t("effect.label_create", action=t["action"], entity=self.ename(eid))
        if state[eid] != NONE:
            # un'istanza esiste già: con relazioni 1-N ne nascerebbe un'altra, che il simulatore non rappresenta
            return state, [{"transition_id": None, "entity_id": eid, "action": self.t("step.already_exists"),
                             "from": state[eid], "to": state[eid], "depth": depth + 1, "skipped": True}], []
        creations = [c for c in self.entity[eid]["transitions"] if is_create(c)]
        if not creations:
            return state, [], [self.t("effect.no_creations", label=label)]
        ok, why = [], []
        for c in creations:
            f = self.guard_failures(c, state)
            if f:
                why.append(self.t("q", x=c["action"]) + ": " + "; ".join(f))
            else:
                ok.append(c)
        if not ok:
            return state, [], [self.t("effect.no_creation_possible", label=label, why=" | ".join(why))]
        if len(ok) > 1:
            names = ", ".join(f"{self.t('q', x=c['action'])} → {self.sname(c['to_state_id'])}" for c in ok)
            return state, [], [self.t("effect.creations_ambiguous", label=label, options=names)]
        if ok[0]["id"] in chain or depth >= MAX_CASCADE_DEPTH:
            return state, [], [self.t("effect.cycle_on", label=label, action=ok[0]["action"])]
        return self.fire(ok[0], state, depth + 1, chain)

    def triggers(self, t: dict) -> list[dict]:
        """Azioni che, con un loro effetto, possono innescare la transizione t."""
        out = []
        for src in self.transitions:
            for eff in src["effects"]:
                if eff["entity_id"] != t["entity_id"]:
                    continue
                kind = eff.get("kind", "state")
                if (kind == "create" and is_create(t)) or (kind == "action" and eff.get("action", "").strip().lower() == t["action"].strip().lower()) \
                        or (kind == "state" and eff["to_state_id"] == t["to_state_id"]):
                    out.append(src)
        return out

    # ---------- invarianti ----------
    def violations(self, state: dict) -> list[dict]:
        out = []
        for inv, rules in self._inv_fast:
            rules = [r for r in rules if r[0] in state]
            if not rules:
                continue
            hit = True
            for eid, ids, neg, many in rules:
                # "many": condizione da verificare su tutte le istanze (analisi con istanze multiple)
                if any((state[x] in ids) == neg for x in (many or (eid,))):
                    hit = False
                    break
            if hit:
                out.append({"id": inv["id"], "name": inv["name"], "severity": inv["severity"],
                            "description": inv["description"]})
        return out

    # ---------- valutazione azioni disponibili ----------
    def evaluate(self, state: dict) -> dict:
        state = self.normalize(state)
        current_viol = {v["id"] for v in self.violations(state)}
        actions = []
        for t in self.transitions:
            eid = t["entity_id"]
            if eid not in state:
                continue
            base = {
                "transition_id": t["id"], "entity_id": eid, "action": t["action"], "actor": t["actor"],
                "description": t["description"], "from": state[eid], "to": t["to_state_id"],
                "rules": self.rule_notes(t), "create": is_create(t),
            }
            if not self.applies_from(t, state[eid]):
                why = self.t("eval.exists", entity=self.ename(eid)) if is_create(t) \
                    else self.t("eval.is_in", entity=self.ename(eid), state=self.sname(state[eid]))
                actions.append({**base, "status": "unavailable", "reasons": [why]})
                continue
            if t.get("cascade_only"):
                via = ", ".join(f"{self.t('q', x=x['action'])} ({self.ename(x['entity_id'])})" for x in self.triggers(t)) \
                    or self.t("eval.no_action")
                actions.append({**base, "status": "blocked", "cascade_only": True,
                                "reasons": [self.t("eval.cascade_only", via=via)]})
                continue
            fails = self.guard_failures(t, state)
            if fails:
                actions.append({**base, "status": "blocked", "reasons": fails})
                continue
            new, steps, conflicts = self.fire(t, state)
            new_viol = [v for v in self.violations(new) if v["id"] not in current_viol]
            actions.append({
                **base,
                "status": "conflict" if conflicts else "enabled",
                "reasons": conflicts,
                "result": new,
                "steps": steps,
                "introduces_violations": new_viol,
            })
        return {"state": state, "actions": actions, "violations": self.violations(state)}

    def apply(self, state: dict, transition_id: int, force: bool = False) -> dict:
        state = self.normalize(state)
        t = self.transition.get(transition_id)
        if not t:
            return {"ok": False, "error": self.t("apply.no_transition")}
        if not self.applies_from(t, state[t["entity_id"]]) and not force:
            return {"ok": False, "error": self.t("apply.unavailable", action=t["action"], state=self.sname(state[t["entity_id"]]))}
        if t.get("cascade_only") and not force:
            return {"ok": False, "error": self.t("apply.cascade_only", action=t["action"])}
        fails = self.guard_failures(t, state)
        if fails and not force:
            return {"ok": False, "error": self.t("apply.guards"), "reasons": fails}
        new, steps, conflicts = self.fire(t, state)
        if conflicts and not force:
            return {"ok": False, "error": self.t("apply.conflict"), "reasons": conflicts}
        return {"ok": True, "state": new, "steps": steps, "conflicts": conflicts,
                "forced": bool(force and (fails or conflicts)), "violations": self.violations(new)}

    # ---------- esplorazione dello spazio degli stati ----------
    def explore(self, start: dict | None = None, limit: int = DEFAULT_EXPLORE_LIMIT) -> dict:
        # l'esplorazione è il calcolo costoso: analisi e matrice la condividono finché il modello non cambia
        key = (hashlib.sha1(json.dumps(self.model, sort_keys=True, default=str).encode()).hexdigest(),
               json.dumps(start, sort_keys=True), limit, self.t.lang)
        with _EXPLORE_LOCK:
            if key in _EXPLORE_CACHE:
                _EXPLORE_CACHE.move_to_end(key)
                return _EXPLORE_CACHE[key]
            result = self._explore(start, limit)
            _EXPLORE_CACHE[key] = result
            while len(_EXPLORE_CACHE) > _EXPLORE_CACHE_SIZE:
                _EXPLORE_CACHE.popitem(last=False)
            return result

    def _explore(self, start: dict | None, limit: int) -> dict:
        start = self.normalize(start) if start else self.initial_state()
        k0 = self.key(start)
        parent = {k0: None}
        queue = deque([k0])
        enabled_ever = set()
        conflicts, deadlocks, violations = {}, [], {}
        truncated = False

        while queue:
            k = queue.popleft()
            st = self.from_key(k)
            for v in self.violations(st):
                violations.setdefault(v["id"], {**v, "key": k})
            moves = 0
            for eid in self.order:
                for t in self.outgoing(eid, st[eid]):
                    if t.get("cascade_only") or not self.guards_ok(t, st):
                        continue
                    new, steps, conf = self.fire(t, st)
                    if conf:
                        for c in conf:
                            conflicts.setdefault((t["id"], c), {"transition_id": t["id"], "message": c, "key": k})
                        continue
                    moves += 1
                    # conta anche le azioni scattate in cascata come effetto
                    enabled_ever.update(x["transition_id"] for x in steps if x["transition_id"])
                    nk = self.key(new)
                    if nk not in parent:
                        if len(parent) >= limit:
                            truncated = True
                            continue
                        parent[nk] = (k, t["id"])
                        queue.append(nk)
            # un'entità che non esiste o mai avviata non è "bloccata"
            if moves == 0 and any(not self.is_final(s) and s not in self.resting for s in k):
                if len(deadlocks) < 25:
                    deadlocks.append(k)

        def trace(key):
            out = []
            while parent[key] is not None:
                prev, tid = parent[key]
                t = self.transition[tid]
                out.append({"transition_id": tid, "entity_id": t["entity_id"], "action": t["action"],
                            "to": t["to_state_id"], "create": is_create(t)})
                key = prev
            return list(reversed(out))

        return {
            "start": start,
            "reachable": set(parent.keys()),
            "truncated": truncated,
            "enabled_ever": enabled_ever,
            "violations": [{**v, "state": self.from_key(v["key"]), "trace": trace(v["key"])} for v in violations.values()],
            "conflicts": [{**c, "state": self.from_key(c["key"]), "trace": trace(c["key"])} for c in conflicts.values()],
            "deadlocks": [{"state": self.from_key(k), "trace": trace(k)} for k in deadlocks],
        }

    # ---------- analisi edge case ----------
    def _guard_sig(self, g: dict):
        if g["kind"] == "rule":
            return ("rule", g["description"].strip().lower())
        return ("state", g["entity_id"], tuple(sorted(g["state_ids"])), bool(g["negate"]))

    def _guard_text(self, g: dict) -> str:
        if g["kind"] == "rule":
            return g["description"]
        op = "∉" if g["negate"] else "∈"
        return f"{self.ename(g['entity_id'])} {op} {{{', '.join(self.sname(s) for s in g['state_ids'])}}}"

    def analyze(self, limit: int = DEFAULT_EXPLORE_LIMIT) -> dict:
        issues = []
        T, q = self.t, lambda x: self.t("q", x=x)

        # category è un codice stabile: il frontend lo traduce e ci filtra sopra
        def add(sev, cat, key, detail=None, params=None, **extra):
            params = params or {}
            issues.append({"severity": sev, "category": cat, "title": T(f"{key}.title", **params),
                           "detail": T(f"{key}.detail", **params) if detail is None else detail, **extra})

        # --- analisi statica per entità ---
        for e in self.model["entities"]:
            name, sts = e["name"], e["states"]
            if not sts:
                add("info", "model", "a.no_states", params={"entity": name}, entity_id=e["id"])
                continue
            creations = [t for t in e["transitions"] if is_create(t)]
            inits = [s for s in sts if s["is_initial"]]
            if not creations and not inits:
                add("error", "model", "a.no_start", params={"entity": name, "state": sts[0]["name"]}, entity_id=e["id"])
            elif not creations and len(inits) > 1:
                add("warning", "model", "a.many_initial",
                    params={"entity": name, "states": ", ".join(s["name"] for s in inits)}, entity_id=e["id"])
            if not any(s["is_final"] for s in sts):
                add("info", "model", "a.no_final", params={"entity": name}, entity_id=e["id"])

            # raggiungibilità locale (ignorando le guardie)
            starts = {t["to_state_id"] for t in creations} if creations else {(inits or sts)[0]["id"]}
            seen, stack = set(starts), list(starts)
            while stack:
                s = stack.pop()
                for t in self.outgoing(e["id"], s):
                    if t["to_state_id"] not in seen:
                        seen.add(t["to_state_id"])
                        stack.append(t["to_state_id"])
            for s in sts:
                p = {"entity": name, "state": s["name"]}
                if s["id"] not in seen:
                    add("warning", "reachability", "a.unreachable", params=p, entity_id=e["id"], state_id=s["id"])
                outs = self.outgoing(e["id"], s["id"])
                if not s["is_final"] and not outs:
                    add("warning", "dead_end", "a.dead_end", params=p, entity_id=e["id"], state_id=s["id"])
                if s["is_final"] and any(t["from_state_id"] == s["id"] and not is_create(t) for t in e["transitions"]):
                    add("warning", "model", "a.final_outgoing", detail="", params=p, entity_id=e["id"], state_id=s["id"])
                by_action = {}
                for t in outs:
                    by_action.setdefault(t["action"].strip().lower(), set()).add(t["to_state_id"])
                for act, targets in by_action.items():
                    if len(targets) > 1:
                        add("warning", "nondeterminism", "a.nondeterminism", detail=" / ".join(self.sname(x) for x in targets),
                            params={**p, "action": act}, entity_id=e["id"], state_id=s["id"])

            for t in e["transitions"]:
                if t.get("cascade_only") and not self.triggers(t):
                    add("warning", "cascade", "a.cascade_never", params={"entity": name, "action": t["action"]},
                        entity_id=e["id"], transition_id=t["id"])

            # nascite che saltano i controlli del percorso normale
            for c in creations:
                incoming = [t for t in e["transitions"] if not is_create(t) and t["to_state_id"] == c["to_state_id"]]
                guarded = [t for t in incoming if t["guards"]]
                if not guarded:
                    continue
                required = set.intersection(*({self._guard_sig(g) for g in t["guards"]} for t in incoming)) if incoming else set()
                own = {self._guard_sig(g) for g in c["guards"]}
                missing = [g for g in guarded[0]["guards"] if self._guard_sig(g) in required - own]
                p = {"action": c["action"], "entity": name, "state": self.sname(c["to_state_id"])}
                if missing:
                    add("warning", "birth", "a.birth_skips",
                        params={**p, "via": ", ".join(q(t["action"]) for t in incoming),
                                "guards": "; ".join(self._guard_text(g) for g in missing)},
                        entity_id=e["id"], transition_id=c["id"])
                elif not required:
                    add("info", "birth", "a.birth_direct", params={**p, "via": ", ".join(q(t["action"]) for t in guarded)},
                        entity_id=e["id"], transition_id=c["id"])

            for t in e["transitions"]:
                p = {"entity": name, "action": t["action"]}
                for g in t["guards"]:
                    if g["kind"] == "state" and not g["state_ids"]:
                        add("error", "guards", "a.empty_guard", params=p, entity_id=e["id"], transition_id=t["id"])
                    if g["kind"] == "state" and g["entity_id"] == e["id"]:
                        add("info", "guards", "a.self_guard", params=p, entity_id=e["id"], transition_id=t["id"])

        for inv in self.invariants:
            if not inv["rules"]:
                add("warning", "invariants", "a.empty_invariant", detail="", params={"name": inv["name"]}, invariant_id=inv["id"])

        issues += relation_issues(self.model, self.sname, T)

        # --- analisi dinamica (esplorazione), sul modello con le istanze multiple ---
        expanded, labels = expand(self.model, T)
        dyn = Engine(expanded, T.lang) if labels else self
        ex = dyn.explore(limit=limit)
        for v in ex["violations"]:
            add("error" if v["severity"] == "error" else "warning", "invariants", "a.violable",
                detail=v["description"] or None, params={"name": v["name"]},
                invariant_id=v["id"], state=v["state"], trace=v["trace"])
        for c in ex["conflicts"]:
            t = dyn.transition[c["transition_id"]]
            add("error", "effect_conflict", "a.conflict", detail=c["message"],
                params={"action": t["action"], "entity": dyn.ename(t["entity_id"])},
                transition_id=t["id"], entity_id=t["entity_id"], state=c["state"], trace=c["trace"])
        for d in ex["deadlocks"]:
            stuck = [f"{dyn.ename(eid)}.{dyn.sname(sid)}" for eid, sid in d["state"].items()
                     if not dyn.is_final(sid) and sid not in dyn.resting]
            add("error", "deadlock", "a.deadlock", params={"stuck": ", ".join(stuck)}, state=d["state"], trace=d["trace"])

        reached_states = {sid for k in ex["reachable"] for sid in k}
        for e in self.entities:
            for s in e["states"]:
                if s["id"] not in reached_states and not any(
                    i.get("state_id") == s["id"] and i["category"] == "reachability" for i in issues
                ):
                    add("warning", "reachability", "a.never_reached", params={"entity": e["name"], "state": s["name"]},
                        entity_id=e["id"], state_id=s["id"])
        enabled_base = {dyn.transition[x].get("base_id", x) for x in ex["enabled_ever"]}
        for t in self.transitions:
            if t["entity_id"] in self.order and t["id"] not in enabled_base:
                add("warning", "dead_actions", "a.dead_action", params={"action": t["action"], "entity": self.ename(t["entity_id"])},
                    entity_id=t["entity_id"], transition_id=t["id"])

        order = {"error": 0, "warning": 1, "info": 2}
        issues.sort(key=lambda i: order.get(i["severity"], 3))
        return {
            "issues": issues,
            "stats": {
                "entities": len(self.entities),
                "states": len(self.state),
                "transitions": len(self.transitions),
                "reachable": len(ex["reachable"]),
                "theoretical": _product(len(dyn.states_of(e["id"])) for e in dyn.entities),
                "truncated": ex["truncated"],
                "instances": {str(v["base"]): v["index"] for v in labels.values() if v["index"] > 1},
            },
            "labels": {str(k): v for k, v in labels.items()},
        }

    def matrix(self, a: int, b: int, limit: int = DEFAULT_EXPLORE_LIMIT) -> dict:
        """Matrice stati(A) x stati(B): raggiungibilità e violazioni."""
        ex = self.explore(limit=limit)
        ia, ib = self.order.index(a), self.order.index(b)
        relevant = {i["id"] for i in self.invariants if {r["entity_id"] for r in i["rules"]} & {a, b}}
        sa_list, sb_list = self.states_of(a), self.states_of(b)
        cells = {}
        for k in ex["reachable"]:
            c = cells.setdefault((k[ia], k[ib]), {"reachable": 0, "violating": 0, "invariants": set()})
            c["reachable"] += 1
            v = [x for x in self.violations(self.from_key(k)) if x["id"] in relevant]
            if v:
                c["violating"] += 1
                c["invariants"].update(x["name"] for x in v)
        # combinazioni vietate "a priori" da invarianti che citano solo A e B
        forbidden = {}
        for inv in self.invariants:
            ents = {r["entity_id"] for r in inv["rules"]}
            if ents and ents <= {a, b}:
                for sa in sa_list:
                    for sb in sb_list:
                        st = {a: sa["id"], b: sb["id"]}
                        ok = all((st[r["entity_id"]] in r["state_ids"]) != bool(r.get("negate")) for r in inv["rules"])
                        if ok:
                            forbidden.setdefault((sa["id"], sb["id"]), []).append(inv["name"])
        out = []
        for sa in sa_list:
            for sb in sb_list:
                c = cells.get((sa["id"], sb["id"]), {"reachable": 0, "violating": 0, "invariants": set()})
                out.append({
                    "a": sa["id"], "b": sb["id"], "reachable": c["reachable"], "violating": c["violating"],
                    "invariants": sorted(c["invariants"] | set(forbidden.get((sa["id"], sb["id"]), []))),
                    "forbidden": (sa["id"], sb["id"]) in forbidden,
                })
        strip = lambda ls: [{"id": s["id"], "name": s["name"]} for s in ls]
        return {"a": a, "b": b, "a_states": strip(sa_list), "b_states": strip(sb_list), "cells": out,
                "truncated": ex["truncated"], "reachable": len(ex["reachable"])}


def _product(xs):
    p = 1
    for x in xs:
        p *= x
    return p

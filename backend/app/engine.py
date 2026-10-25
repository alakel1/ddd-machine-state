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
from operator import itemgetter

from .fixes import fixes_for
from .i18n import translator
from .relations import derived_invariants, expand, gone_states, relation_issues

NONE = 0  # stato implicito "non esiste" (gli id SQLite partono da 1)
MAX_CASCADE_DEPTH = 8
DEFAULT_EXPLORE_LIMIT = 1_000_000
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
        self._invariant_by_id = {str(inv["id"]): inv for inv in self.invariants}
        self.order = [e["id"] for e in self.entities]
        self._outgoing = {}
        # forme precompilate per i percorsi caldi dell'esplorazione
        self._inv_fast = [(inv, [(r["entity_id"], frozenset(r.get("state_ids", [])), bool(r.get("negate")),
                                  tuple(r.get("entity_ids") or ())) for r in inv["rules"]]) for inv in self.invariants]
        # guardie "per ogni padre": each = [(padre, stati in cui il padre non c'è, figli da controllare)];
        # il modello espanso le ha già, abbinate per istanza
        for t in self.transitions:
            for g in t["guards"]:
                if g["kind"] == "state" and g.get("each_entity_id") and "each" not in g:
                    p = self.entity.get(g["each_entity_id"])
                    g["each"] = [[p["id"], gone_states(p), [g["entity_id"]]]] if p and p["states"] else None
        self._guards_fast = {
            t["id"]: [(g["entity_id"], tuple(g.get("entity_ids") or ()), frozenset(g["state_ids"]), bool(g["negate"]),
                       tuple((pid, frozenset(gone), tuple(kids)) for pid, gone, kids in g.get("each") or ()))
                      for g in t["guards"] if g["kind"] == "state" and g.get("entity_id") is not None]
            for t in self.transitions}
        # invarianti sulla chiave di stato (tupla): regole come (posizioni, stati, negate),
        # più l'indice posizione -> invarianti che la leggono, per ricontrollare solo quelle toccate da una mossa
        pos = {eid: i for i, eid in enumerate(self.order)}
        self._inv_key, self._inv_by_pos = [], {}
        for inv, rules in self._inv_fast:
            compiled = [(tuple(pos[x] for x in (many or (eid,))), ids, neg) for eid, ids, neg, many in rules
                        if eid in pos and all(x in pos for x in many)]
            if not compiled:
                continue
            for p in {p for r in compiled for p in r[0]}:
                self._inv_by_pos.setdefault(p, []).append(len(self._inv_key))
            self._inv_key.append((inv, compiled))
        self._keyget = itemgetter(*self.order) if len(self.order) > 1 else None
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
        if self._keyget:
            return self._keyget(state)
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
            if g["kind"] == "state" and g.get("each"):
                names = ", ".join(self.sname(s) for s in g["state_ids"]) or "—"
                for pid, gone, kids in g["each"]:
                    cur = [state.get(x, NONE) for x in kids]
                    if state.get(pid, NONE) in gone or any(s in g["state_ids"] for s in cur) != bool(g["negate"]):
                        continue
                    fails.append(
                        self.t("guard.fail_each_not" if g["negate"] else "guard.fail_each", parent=self.ename(pid),
                               entity=self.ename(kids[0]), states=names, current=", ".join(self.sname(s) for s in cur))
                        + (f" — {g['description']}" if g["description"] else "")
                    )
                continue
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
        for eid, many, ids, neg, each in self._guards_fast[t["id"]]:
            if each:
                # per ogni padre esistente: almeno un suo figlio negli stati (con negate: nessuno)
                for pid, gone, kids in each:
                    if state.get(pid, NONE) not in gone and any(state.get(x, NONE) in ids for x in kids) == neg:
                        return False
                continue
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

    def guard_misses(self, t: dict, state: dict) -> list[dict]:
        """Guardie di stato di t che non passano, con gli stati che hanno trovato (per proporre come allargarle)."""
        out = []
        for g in t["guards"]:
            if g["kind"] != "state" or g.get("entity_id") is None:
                continue
            if g.get("each"):
                cur = []
                for pid, gone, kids in g["each"]:
                    found = [state.get(x, NONE) for x in kids]
                    if state.get(pid, NONE) not in gone and any(s in g["state_ids"] for s in found) == bool(g["negate"]):
                        cur += found
            else:
                ids = g.get("entity_ids") or [g["entity_id"]]
                if g["entity_id"] not in state:
                    continue
                existing = [x for x in ids if state.get(x, NONE) != NONE] or ids[:1]
                cur = [state[x] for x in existing if (state[x] in g["state_ids"]) == bool(g["negate"])]
            if cur:
                out.append({"guard_id": g["id"], "transition_id": t.get("base_id", t["id"]), "current": sorted(set(cur))})
        return out

    def _conflict(self, t: dict, what: str, eid, depth: int, reason: str, details=(), gap=None, fix=None,
                  effect=None, misses=()) -> dict:
        """Conflitto strutturato: quale effetto di t (what) non riesce e perché (reason, details = guardie che bloccano).
        gap: la transizione che manca all'entità coinvolta ({"from", "to"} oppure {"from", "action"}).
        fix: (chiave, entità su cui intervenire, parametri) del suggerimento; con un gap si ricava da solo.
        effect: l'effetto che non riesce; misses: le guardie che lo bloccano (vedi guard_misses).
        what può essere una funzione: il testo si costruisce solo quando il conflitto c'è davvero."""
        what = what() if callable(what) else what
        if gap and fix is None:
            src = self.sname(gap["from"])
            if self.is_final(gap["from"]):
                # l'entità è già conclusa: non le manca nulla, è l'effetto che non dovrebbe toccarla
                fix = ("fix.skip_final", t["entity_id"], {"action": t["action"]})
            elif gap.get("action"):
                fix = ("fix.add_action", eid, {"name": gap["action"], "src": src})
            else:
                fix = ("fix.add_transition", eid, {"src": src, "dst": self.sname(gap["to"])})
        details = list(details)
        message = self.t("effect.conflict", action=t["action"], what=what, reason=reason) + (f" — {'; '.join(details)}" if details else "")
        return {"message": message, "trigger_id": t["id"], "entity_id": eid, "depth": depth + 1,
                "what": what, "reason": reason, "details": details, "gap": gap,
                "effect_id": effect and effect.get("id"), "misses": list(misses),
                "fix": fix and {"text": self.t(fix[0], entity=self.ename(fix[1]), **fix[2]), "entity_id": fix[1]}}

    def fire(self, t: dict, state: dict, depth: int = 0, chain: frozenset = frozenset()):
        """Esegue t e propaga gli effetti. Ritorna (nuovo_stato, passi, conflitti); i conflitti sono dict (vedi _conflict)."""
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
            what = lambda: self.t("effect.what_state", entity=self.ename(eid), src=self.sname(cur), dst=self.sname(target))
            cands = [c for c in self.outgoing(eid, cur) if c["to_state_id"] == target]
            if not cands and cur == NONE:
                # l'entità non esiste e non c'è un modo di crearla in quello stato:
                # l'effetto non si applica (es. "annulla il pagamento" quando non c'è pagamento)
                steps.append({"transition_id": None, "entity_id": eid, "action": self.t("step.missing"),
                              "from": cur, "to": cur, "depth": depth + 1, "skipped": True})
                continue
            forced = {"transition_id": None, "entity_id": eid, "action": self.t("step.forced"),
                      "from": cur, "to": target, "depth": depth + 1, "forced": True}
            if not cands:
                conflicts.append(self._conflict(t, what, eid, depth, self.t("effect.no_transition"), gap={"from": cur, "to": target},
                                                effect=eff))
                new[eid] = target
                steps.append(forced)
                continue
            chosen, why = None, []
            for c in cands:
                f = self.guard_failures(c, new)
                if not f:
                    chosen = c
                    break
                why += f
            if chosen is None:
                conflicts.append(self._conflict(t, what, eid, depth, self.t("effect.blocked", action=cands[0]["action"]), why,
                                                fix=("fix.guard", eid, {"name": cands[0]["action"]}), effect=eff,
                                                misses=[m for c in cands for m in self.guard_misses(c, new)]))
                new[eid] = target
                steps.append(forced)
                continue
            if chosen["id"] in chain or depth >= MAX_CASCADE_DEPTH:
                conflicts.append(self._conflict(t, what, eid, depth, self.t("effect.cycle_on", action=chosen["action"]),
                                                fix=("fix.cycle", eid, {"action": chosen["action"]}), effect=eff))
                continue
            new, sub_steps, sub_conf = self.fire(chosen, new, depth + 1, chain)
            steps += sub_steps
            conflicts += sub_conf
        return new, steps, conflicts

    def _fire_named_action(self, t: dict, eff: dict, state: dict, depth: int, chain: frozenset):
        """Effetto "esegui azione": l'entità esegue l'azione con quel nome disponibile dal suo stato attuale."""
        eid, name = eff["entity_id"], eff.get("action", "").strip()
        cur = state[eid]
        what = lambda: self.t("effect.what_action", entity=self.ename(eid), name=name)
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
            return state, [], [self._conflict(t, what, eid, depth, self.t("effect.action_unavailable", state=self.sname(cur)),
                                              gap={"from": cur, "action": name}, effect=eff)]
        ok, why = [], []
        for c in cands:
            f = self.guard_failures(c, state)
            if f:
                why += f
            else:
                ok.append(c)
        if not ok:
            return state, [], [self._conflict(t, what, eid, depth, self.t("effect.blocked", action=name), why,
                                              fix=("fix.guard", eid, {"name": name}), effect=eff,
                                              misses=[m for c in cands for m in self.guard_misses(c, state)])]
        if len(ok) > 1:
            names = ", ".join(self.sname(c["to_state_id"]) for c in ok)
            return state, [], [self._conflict(t, what, eid, depth, self.t("effect.action_ambiguous", state=self.sname(cur), targets=names),
                                              fix=("fix.ambiguous", eid, {}), effect=eff)]
        if ok[0]["id"] in chain or depth >= MAX_CASCADE_DEPTH:
            return state, [], [self._conflict(t, what, eid, depth, self.t("effect.cycle"), fix=("fix.cycle", eid, {"action": name}),
                                              effect=eff)]
        return self.fire(ok[0], state, depth + 1, chain)

    def _fire_creation(self, t: dict, eff: dict, state: dict, depth: int, chain: frozenset):
        """Effetto "crea": l'entità nasce tramite l'unica azione di creazione con guardie soddisfatte."""
        eid = eff["entity_id"]
        what = lambda: self.t("effect.what_create", entity=self.ename(eid))
        if state[eid] != NONE:
            # un'istanza esiste già: con relazioni 1-N ne nascerebbe un'altra, che il simulatore non rappresenta
            return state, [{"transition_id": None, "entity_id": eid, "action": self.t("step.already_exists"),
                             "from": state[eid], "to": state[eid], "depth": depth + 1, "skipped": True}], []
        creations = [c for c in self.entity[eid]["transitions"] if is_create(c)]
        if not creations:
            return state, [], [self._conflict(t, what, eid, depth, self.t("effect.no_creations"), fix=("fix.add_creation", eid, {}),
                                              effect=eff)]
        ok, why = [], []
        for c in creations:
            f = self.guard_failures(c, state)
            if f:
                why += [f"{self.t('q', x=c['action'])}: {x}" for x in f] if len(creations) > 1 else f
            else:
                ok.append(c)
        if not ok:
            return state, [], [self._conflict(t, what, eid, depth, self.t("effect.no_creation_possible"), why,
                                              fix=("fix.guard", eid, {"name": creations[0]["action"]}), effect=eff,
                                              misses=[m for c in creations for m in self.guard_misses(c, state)])]
        if len(ok) > 1:
            names = ", ".join(f"{self.t('q', x=c['action'])} → {self.sname(c['to_state_id'])}" for c in ok)
            return state, [], [self._conflict(t, what, eid, depth, self.t("effect.creations_ambiguous", options=names),
                                              fix=("fix.ambiguous", eid, {}), effect=eff)]
        if ok[0]["id"] in chain or depth >= MAX_CASCADE_DEPTH:
            return state, [], [self._conflict(t, what, eid, depth, self.t("effect.cycle_on", action=ok[0]["action"]),
                                              fix=("fix.cycle", eid, {"action": ok[0]["action"]}), effect=eff)]
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
    def violations(self, state: dict, skip=()) -> list[dict]:
        """Invarianti violate nello stato; skip: id da non controllare (l'esplorazione salta quelle già trovate)."""
        out = []
        for inv, rules in self._inv_fast:
            if inv["id"] in skip:
                continue
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
                "reasons": [c["message"] for c in conflicts],
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
            return {"ok": False, "error": self.t("apply.conflict"), "reasons": [c["message"] for c in conflicts]}
        return {"ok": True, "state": new, "steps": steps, "conflicts": [c["message"] for c in conflicts],
                "forced": bool(force and (fails or conflicts)), "violations": self.violations(new)}

    def replay(self, trace: list[dict]) -> dict:
        """Riesegue un percorso dallo stato iniziale; si ferma al primo passo non eseguibile."""
        state, history = self.initial_state(), []
        for step in trace:
            t = self.transition.get(int(step["transition_id"]))
            r = self.apply(state, t["id"] if t else 0)
            if not r["ok"]:
                break
            history.append({**r, "prev": state, "transition_id": t["id"], "entity_id": t["entity_id"],
                            "action": t["action"], "actor": t["actor"]})
            state = r["state"]
        return {"state": state, "history": history, "complete": len(history) == len(trace),
                "evaluation": self.evaluate(state)}

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

    def _reads(self, t: dict) -> list:
        """Entità il cui stato può influire sull'esito di t con la sua cascata: la sua, quelle delle guardie
        e, seguendo gli effetti, i bersagli con tutte le loro azioni (guardie ed effetti a loro volta)."""
        ents, seen, stack = {t["entity_id"]}, set(), [t]
        while stack:
            x = stack.pop()
            if x["id"] in seen:
                continue
            seen.add(x["id"])
            for g in x["guards"]:
                if g["kind"] == "state" and g.get("entity_id") is not None:
                    ents.update((g["entity_id"], *(g.get("entity_ids") or ())))
                    for pid, _, kids in g.get("each") or ():
                        ents.update((pid, *kids))
            for f in x["effects"]:
                ents.add(f["entity_id"])
                stack.extend(self.entity[f["entity_id"]]["transitions"])
        return [e for e in self.order if e in ents]

    def _explore(self, start: dict | None, limit: int) -> dict:
        start = self.normalize(start) if start else self.initial_state()
        k0 = self.key(start)
        parent = {k0: None}
        queue = deque([k0])
        enabled_ever = set()
        conflicts, deadlocks, violations = {}, [], {}
        truncated = False
        inv_key, by_pos = self._inv_key, self._inv_by_pos

        def check(key, candidates):
            # la BFS trova per prima la violazione col percorso più breve: le invarianti già violate non si ricontrollano
            for i in candidates:
                inv, rules = inv_key[i]
                if inv["id"] in violations:
                    continue
                for positions, ids, neg in rules:
                    if any((key[p] in ids) == neg for p in positions):
                        break
                else:
                    violations[inv["id"]] = {"id": inv["id"], "name": inv["name"], "severity": inv["severity"],
                                             "description": inv["description"], "key": key}

        # l'esito di un'azione dipende solo dalle entità che la sua cascata può leggere:
        # si memorizza per (azione, stati di quelle entità) e si riusa negli altri stati globali
        readers, outcomes = {}, {}

        def fire_cached(t, st):
            r = readers.get(t["id"])
            if r is None:
                ids = self._reads(t)
                r = readers[t["id"]] = (ids, itemgetter(*ids) if len(ids) > 1 else (lambda s, e=ids[0]: s[e]))
            ids, get = r
            mk = (t["id"], get(st))
            hit = outcomes.get(mk)
            if hit is None:
                new, steps, conf = self.fire(t, st)
                hit = outcomes[mk] = ({e: new[e] for e in ids if new[e] != st[e]}, steps, conf)
                if not conf:
                    # conta anche le azioni scattate in cascata come effetto (un esito riusato non aggiunge nulla)
                    enabled_ever.update(x["transition_id"] for x in steps if x["transition_id"])
                return new, steps, conf
            changes, steps, conf = hit
            return ({**st, **changes} if changes else st), steps, conf

        # azioni che si possono lanciare direttamente da (entità, stato), precalcolate
        movable = {}
        for e in self.entities:
            for s in self.states_of(e["id"]):
                movable[(e["id"], s["id"])] = [t for t in self.outgoing(e["id"], s["id"]) if not t.get("cascade_only")]
        order, guards_ok = self.order, self.guards_ok

        check(k0, range(len(inv_key)))
        while queue:
            k = queue.popleft()
            st = self.from_key(k)
            moves = 0
            for eid, sid in zip(order, k):
                for t in movable.get((eid, sid), ()):
                    if not guards_ok(t, st):
                        continue
                    new, steps, conf = fire_cached(t, st)
                    if conf:
                        for c in conf:
                            conflicts.setdefault((t["id"], c["message"]), {"transition_id": t["id"], "message": c["message"],
                                                                           "key": k, "conflict": c, "steps": steps})
                        continue
                    moves += 1
                    nk = self.key(new)
                    if nk not in parent:
                        if len(parent) >= limit:
                            truncated = True
                            continue
                        parent[nk] = (k, t["id"])
                        queue.append(nk)
                        # le invarianti valide in k restano valide se la mossa non tocca le loro entità
                        touched = set()
                        for p, (a, b) in enumerate(zip(k, nk)):
                            if a != b:
                                touched.update(by_pos.get(p, ()))
                        check(nk, sorted(touched))
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
        return ("state", g["entity_id"], tuple(sorted(g["state_ids"])), bool(g["negate"]), g.get("each_entity_id"))

    def _guard_text(self, g: dict) -> str:
        """Guardia in forma leggibile: "Procedura in CREATA, INDETTA o COMPILATA"."""
        if g["kind"] == "rule":
            return self.t("q", x=g["description"])
        names = [self.sname(s) for s in g["state_ids"]]
        states = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} {self.t('or')} {names[-1]}"
        text = self.t("guard.text_not_in" if g["negate"] else "guard.text_in", entity=self.ename(g["entity_id"]), states=states)
        return self.t("guard.text_each", text=text, each=self.ename(g["each_entity_id"])) if g.get("each_entity_id") else text

    def _broken(self, ref: dict) -> bool:
        """Guardia o condizione che cita uno stato eliminato dal modello (o di un'altra entità)."""
        return any(sid != NONE and (sid not in self.state or self.state[sid]["entity_id"] != ref["entity_id"])
                   for sid in ref.get("state_ids", []))

    def _local_reach(self, eid, sid) -> set:
        """Stati dell'entità raggiungibili da sid con le sue sole transizioni, ignorando le guardie."""
        seen, stack = set(), [sid]
        while stack:
            for t in self.outgoing(eid, stack.pop()):
                if t["to_state_id"] not in seen:
                    seen.add(t["to_state_id"])
                    stack.append(t["to_state_id"])
        return seen

    def _covers(self, own: dict, req: dict) -> bool:
        """La guardia own è almeno severa quanto req (es. Procedura ∈ {A, B} copre Procedura ∈ {A, B, C})."""
        if own["kind"] != "state" or req["kind"] != "state" or own["entity_id"] != req["entity_id"] \
                or bool(own["negate"]) != bool(req["negate"]) or own.get("each_entity_id") != req.get("each_entity_id"):
            return self._guard_sig(own) == self._guard_sig(req)
        a, b = set(own["state_ids"]), set(req["state_ids"])
        return a >= b if own["negate"] else a <= b

    def analyze(self, limit: int = DEFAULT_EXPLORE_LIMIT) -> dict:
        issues = []
        T, q = self.t, lambda x: self.t("q", x=x)

        # category è un codice stabile: il frontend lo traduce e ci filtra sopra
        def add(sev, cat, key, detail=None, params=None, **extra):
            params = params or {}
            issues.append({"severity": sev, "category": cat, "code": key, "title": T(f"{key}.title", **params),
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
                # vita dello stato: da eliminato non si torna indietro, da spento sì
                life = s.get("liveness", "active")
                back = [t for t in outs if self.state[t["to_state_id"]].get("liveness", "active") != "gone"]
                if life == "gone" and back:
                    add("warning", "liveness", "a.gone_exit", params={**p, "actions": ", ".join(q(t["action"]) for t in back)},
                        entity_id=e["id"], state_id=s["id"])
                if life == "off" and not any(self.state[x].get("liveness", "active") == "active"
                                             for x in self._local_reach(e["id"], s["id"])):
                    add("warning", "liveness", "a.off_no_way_back", params=p, entity_id=e["id"], state_id=s["id"])
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
                missing = [g for g in guarded[0]["guards"] if self._guard_sig(g) in required
                           and not any(self._covers(o, g) for o in c["guards"])]
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
                    if g["kind"] == "state" and self._broken(g):
                        add("error", "guards", "a.broken_guard", params={**p, "target": self.ename(g["entity_id"])},
                            entity_id=e["id"], transition_id=t["id"])

        for inv in self.invariants:
            if not inv["rules"]:
                add("warning", "invariants", "a.empty_invariant", detail="", params={"name": inv["name"]}, invariant_id=inv["id"])
            if any(self._broken(r) for r in inv["rules"]):
                add("error", "invariants", "a.broken_invariant", params={"name": inv["name"]}, invariant_id=inv["id"])

        issues += relation_issues(self.model, self.sname, T)

        # --- analisi dinamica (esplorazione), sul modello con le istanze multiple ---
        expanded, labels = expand(self.model, T)
        dyn = Engine(expanded, T.lang) if labels else self
        ex = dyn.explore(limit=limit)
        for v in ex["violations"]:
            add("error" if v["severity"] == "error" else "warning", "invariants", "a.violable",
                detail=v["description"] or None, params={"name": v["name"]},
                invariant_id=v["id"], state=v["state"], trace=v["trace"])
        base = lambda eid: labels.get(eid, {}).get("base", eid)  # dalle copie #2, #3 all'entità del modello
        for c in ex["conflicts"]:
            t = dyn.transition[c["transition_id"]]
            # passi davvero eseguiti prima del conflitto (senza quelli forzati o saltati) e il punto in cui si blocca
            done = [x for x in c["steps"] if not x.get("forced") and not x.get("skipped")]
            if c["conflict"].get("fix"):
                c["conflict"]["fix"]["entity_id"] = base(c["conflict"]["fix"]["entity_id"])
            add("error", "effect_conflict", "a.conflict", detail=c["message"],
                params={"action": t["action"], "entity": dyn.ename(t["entity_id"]), "target": dyn.ename(c["conflict"]["entity_id"])},
                transition_id=t.get("base_id", t["id"]), entity_id=base(t["entity_id"]), state=c["state"], trace=c["trace"],
                conflict=c["conflict"], cascade=done, entity_ids=[base(t["entity_id"]), base(c["conflict"]["entity_id"])])
        issues += self._missing_transitions(dyn, ex["conflicts"], labels)
        for d in ex["deadlocks"]:
            stuck = [eid for eid, sid in d["state"].items() if not dyn.is_final(sid) and sid not in dyn.resting]
            add("error", "deadlock", "a.deadlock", params={"stuck": ", ".join(f"{dyn.ename(x)}.{dyn.sname(d['state'][x])}" for x in stuck)},
                state=d["state"], trace=d["trace"], entity_ids=[base(x) for x in stuck])

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

        # il simulatore ha un'istanza per entità: i percorsi che passano dalle copie #2, #3 (id negativi) non si riproducono
        # invarianti per id del modello (le copie per istanza hanno id "5#2")
        inv_of = lambda i: self._invariant_by_id.get(str(i.get("invariant_id")).split("#")[0])
        relation_ends = {r["id"]: [r["source_id"], r["target_id"]] for r in self.model.get("relations", [])}
        tbase = lambda tid: dyn.transition[tid].get("base_id", tid) if tid in dyn.transition else tid
        for i in issues:
            if i.get("trace") is not None:
                i["replayable"] = all(s["transition_id"] > 0 for s in i["trace"])
            # entità a cui la segnalazione si riferisce: la scheda di ciascuna la mostra
            inv = inv_of(i) if i.get("invariant_id") is not None else None
            ids = list(i.get("entity_ids", [])) + ([i["entity_id"]] if i.get("entity_id") is not None else [])
            ids += [r["entity_id"] for r in inv["rules"]] if inv else []
            ids += relation_ends.get(i.get("relation_id"), [])
            i["entity_ids"] = sorted({base(x) for x in ids if x is not None})
            i["fixes"] = fixes_for(self, i, inv=inv, base=base, tbase=tbase)
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

    def _missing_transitions(self, dyn: "Engine", conflicts: list, labels: dict) -> list[dict]:
        """Transizioni che mancano a un'entità perché gli effetti a cascata che la coinvolgono si possano applicare.
        Una per (entità, stato di partenza, arrivo o azione), con le azioni che la richiedono. Se l'entità è già in uno
        stato finale non manca nulla: va attivato "ignora se già finale" sull'effetto, che è un problema del padre."""
        gaps = {}
        for c in conflicts:
            gap = c["conflict"].get("gap")
            if not gap or self.is_final(gap["from"]):
                continue
            eid = labels.get(c["conflict"]["entity_id"], {}).get("base", c["conflict"]["entity_id"])
            key = (eid, gap["from"], gap.get("to"), (gap.get("action") or "").lower())
            g = gaps.setdefault(key, {"entity_id": eid, **gap, "requested_by": {}})
            trig = dyn.transition[c["conflict"]["trigger_id"]]
            tid = trig.get("base_id", trig["id"])
            g["requested_by"][tid] = {"transition_id": tid, "entity_id": self.transition[tid]["entity_id"],
                                      "action": trig["action"]}
        out = []
        for g in gaps.values():
            eid, src = g["entity_id"], g["from"]
            if g.get("action"):
                # stato d'arrivo suggerito: quello delle altre transizioni con lo stesso nome, se è uno solo
                targets = {t["to_state_id"] for t in self.entity[eid]["transitions"]
                           if t["action"].strip().lower() == g["action"].lower()}
                g["to"] = targets.pop() if len(targets) == 1 else None
                key, params = "a.missing_action", {"action": g["action"]}
            else:
                key, params = "a.missing_transition", {"dst": self.sname(g["to"])}
            requested = list(g.pop("requested_by").values())
            params.update(entity=self.ename(eid), src=self.sname(src),
                          by=", ".join(f"{self.ename(r['entity_id'])} {self.t('q', x=r['action'])}" for r in requested))
            out.append({"severity": "error", "category": "missing_transition", "code": key,
                        "title": self.t(f"{key}.title", **params), "detail": self.t(f"{key}.detail", **params),
                        "entity_id": eid, "state_id": src, "gap": g, "requested_by": requested})
        return out

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

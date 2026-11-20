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
import itertools
import json
import math
import os
import resource
import threading
import time
from collections import OrderedDict, deque
from operator import itemgetter

from .expr import ExprError, domain_of, initial_of
from .expr import build as build_expr
from .expr import parse as parse_expr
from .fixes import fixes_for
from .i18n import translator
from .relations import compositions, derived_invariants, expand, gone_states, relation_issues, vbase

NONE = 0  # stato implicito "non esiste" (gli id SQLite partono da 1)
MAX_CASCADE_DEPTH = 8
DEFAULT_EXPLORE_LIMIT = 1_000_000
# limiti dell'esplorazione oltre al numero di stati: superati, si ferma e il certificato lo dice
EXPLORE_SECONDS = float(os.environ.get("DDD_EXPLORE_SECONDS", 300))
EXPLORE_MB = float(os.environ.get("DDD_EXPLORE_MB", 3072))  # picco di memoria del processo
_EXPLORE_CACHE: "OrderedDict[tuple, dict]" = OrderedDict()  # esplorazioni recenti, per modello e limite
_EXPLORE_CACHE_SIZE = 8
_EXPLORE_CACHE_STATES = 1_500_000  # stati tenuti in cache in tutto: un'esplorazione grande occupa centinaia di MB
_LOCKS_GUARD = threading.Lock()  # protegge cache e tabella dei lock
_EXPLORE_LOCKS: dict = {}  # un lock per esplorazione: richieste sullo stesso modello aspettano il primo calcolo,
#                            quelle su modelli diversi vanno in parallelo
# riduzione per simmetria delle istanze multiple (vedi Engine._symmetry); False = BFS su tutti gli stati concreti
SYMMETRY = True


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
        self._ent_n = len(self.order)  # le prime posizioni della chiave sono entità, le altre attributi
        # attributi verificati: variabili dello stato globale, chiave "entità.attributo" (ogni copia ha le sue)
        self.vars, self.var_of, self._vars_of = {}, {}, {}
        for e in self.entities:
            for a in e.get("attributes") or ():
                vals = domain_of(a)
                if vals:
                    k = f"{e['id']}.{a['id']}"
                    self.vars[k] = (e["id"], a, vals, initial_of(a, vals))
                    self.var_of[(e["id"], a["name"])] = k
                    self._vars_of.setdefault(e["id"], []).append(k)
        self.order += list(self.vars)
        # effetti asincroni: ogni effetto "deferred" è un tipo di messaggio; la coda dei messaggi in volo è una
        # posizione dello stato ("_q": tupla ordinata, i messaggi si consegnano in qualsiasi ordine)
        self.messages = [(t, f) for t in self.transitions for f in t["effects"] if f.get("deferred")]
        self._msg_index = {id(f): i for i, (_, f) in enumerate(self.messages)}
        self.max_in_flight = max(1, int(model.get("max_in_flight") or 2))
        self.delivery = model.get("delivery") or "exactly_once"
        if self.messages:
            self.order.append("_q")
        self._name_ids = {e["name"]: e["id"] for e in self.entities}
        self._outgoing = {}
        self._compile_exprs()
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
        # per azione: clausole in AND, ognuna una lista di guardie alternative (vedi clauses)
        self._guards_fast = {
            t["id"]: self.clauses([(g["entity_id"], tuple(g.get("entity_ids") or ()), frozenset(g["state_ids"]),
                                    bool(g["negate"]), tuple((pid, frozenset(gone), tuple(kids)) for pid, gone, kids in g.get("each") or ()),
                                    g.get("alt_group") or 0)
                                   for g in t["guards"] if g["kind"] == "state" and g.get("entity_id") is not None])
            for t in self.transitions}
        # invarianti sulla chiave di stato (tupla): regole come (posizioni, stati, negate),
        # più l'indice posizione -> invarianti che la leggono, per ricontrollare solo quelle toccate da una mossa
        pos = {eid: i for i, eid in enumerate(self.order)}
        self._inv_key, self._inv_by_pos = [], {}
        for inv, rules in self._inv_fast:
            compiled = [(tuple(pos[x] for x in (many or (eid,))), ids, neg) for eid, ids, neg, many in rules
                        if eid in pos and all(x in pos for x in many)]
            # condizione sugli attributi: una regola in più (neg None), letta dalle posizioni delle sue variabili
            ex = self._inv_exprs.get(str(inv["id"]))
            if ex:
                keys, fn = ex
                ps = tuple(pos[k] for k in keys)
                compiled.append((ps, lambda key, f=fn, ks=keys, ps=ps: f(dict(zip(ks, (key[p] for p in ps)))), None))
            if not compiled:
                continue
            for p in {p for r in compiled for p in r[0]}:
                self._inv_by_pos.setdefault(p, []).append(len(self._inv_key))
            self._inv_key.append((inv, compiled))
        self._keyget = itemgetter(*self.order) if len(self.order) > 1 else None
        self.creatable = {e["id"] for e in self.entities if any(is_create(t) for t in e["transitions"])}
        # stati "di riposo" che non contano come blocco: NONE e l'iniziale delle entità non creabili
        self.resting = {NONE} | {sid for eid, sid in self.initial_state().items()
                                 if not isinstance(eid, str) and eid not in self.creatable}
        # padri in composizione di ogni entità; nel modello espanso ogni copia ha i suoi (li calcola expand)
        self.parents = {}
        for e in self.entities:
            if "_parents" in e:
                self.parents[e["id"]] = e["_parents"]
        if not self.parents:
            for r in compositions(self.model):
                if r["source_id"] != r["target_id"] and r["target_id"] in self.entity:
                    self.parents.setdefault(r["target_id"], []).append(r["source_id"])
        self._by_base = {(t["entity_id"], t.get("base_id", t["id"])): t for t in self.transitions}
        self._sym_refused = []  # gruppi di istanze non scambiabili, con il motivo (per il certificato)
        self._gens = []  # generatori del gruppo di simmetria: scambi di due sottoalberi fratelli (mappe di entità)
        # con messaggi in volo la coda cita le copie: niente simmetria (il certificato lo dice)
        self._sym = self._symmetry() if SYMMETRY and self.model.get("_tree") and not self.messages else None

    # ---------- attributi ed espressioni ----------
    def _compile(self, text: str, scope, refs_map: dict | None):
        """Funzione stato -> bool/valore per un'espressione, e le variabili che legge. Un riferimento senza entità è
        un attributo dell'entità scope. refs_map (modello espanso) dice a quali copie va ogni riferimento: con più
        copie l'espressione si valuta per ogni combinazione, e una guardia vale se vale per tutte."""
        parsed, refs = parse_expr(text)
        choices = []
        for ent, attr in refs:
            if refs_map is not None:
                keys = refs_map.get(f"{ent or ''}|{attr}")
                keys = keys if keys and all(k in self.vars for k in keys) else None
            else:
                eid = scope if ent is None else self._name_ids.get(ent)
                if eid is None:
                    raise ExprError(self.t("expr.no_entity", entity=ent))
                keys = [self.var_of[(eid, attr)]] if (eid, attr) in self.var_of else None
            if not keys:
                raise ExprError(self.t("expr.no_attr", attr=f"{ent + '.' if ent else ''}{attr}"))
            choices.append(keys)
        fns = [build_expr(parsed, dict(zip(refs, combo)).__getitem__) for combo in itertools.product(*choices)]
        read = sorted({k for ks in choices for k in ks})
        return (fns[0] if len(fns) == 1 else (lambda s: all(f(s) for f in fns))), read, len(fns)

    def _compile_exprs(self):
        """Guardie "condizione", effetti "imposta attributo" e condizioni delle invarianti, compilati una volta.
        Gli errori (sintassi, attributi inesistenti o non verificati) finiscono in expr_errors e quell'espressione
        non conta: una guardia così passa sempre, come una regola testuale (il certificato lo dice)."""
        self.expr_errors = []
        self._exprs, self._sets, self._inv_exprs, self._expr_reads = {}, {}, {}, {}
        for t in self.transitions:
            for g in t["guards"]:
                if g["kind"] == "expr":
                    try:
                        fn, read, _ = self._compile(g["description"], t["entity_id"], g.get("_refs"))
                    except ExprError as exc:
                        self.expr_errors.append({"where": "guard", "transition_id": t.get("base_id", t["id"]),
                                                 "entity_id": t["entity_id"], "text": g["description"], "error": str(exc)})
                        continue
                    self._exprs.setdefault(t["id"], []).append((g, fn))
                    self._expr_reads.setdefault(t["id"], set()).update(self.vars[k][0] for k in read)
            for f in t["effects"]:
                if f.get("kind") == "set":
                    key = self.var_of.get((f["entity_id"], f.get("action")))
                    try:
                        if not key:
                            raise ExprError(self.t("expr.no_attr", attr=f"{self.ename(f['entity_id'])}.{f.get('action')}"))
                        fn, read, n = self._compile(f.get("expr") or "", t["entity_id"], f.get("_refs"))
                        if n > 1:
                            raise ExprError(self.t("expr.ambiguous"))
                    except ExprError as exc:
                        self.expr_errors.append({"where": "effect", "transition_id": t.get("base_id", t["id"]),
                                                 "entity_id": t["entity_id"], "text": f.get("expr") or "", "error": str(exc)})
                        continue
                    self._sets[id(f)] = (key, fn)
                    self._expr_reads.setdefault(t["id"], set()).update(self.vars[k][0] for k in read)
        for inv in self.invariants:
            if (inv.get("expr") or "").strip():
                try:
                    fn, read, _ = self._compile(inv["expr"], None, inv.get("_refs"))
                except ExprError as exc:
                    self.expr_errors.append({"where": "invariant", "invariant_id": inv["id"], "text": inv["expr"], "error": str(exc)})
                    continue
                self._inv_exprs[str(inv["id"])] = (read, fn)

    def owner(self, key) -> int | None:
        """L'entità di una posizione dello stato: se stessa, quella di un attributo, nessuna per la coda."""
        if key == "_q":
            return None
        return self.vars[key][0] if isinstance(key, str) else key

    def _map_key(self, phi: dict, key):
        """Una chiave dello stato (entità o attributo) portata dalla mappa di entità phi."""
        if isinstance(key, str):
            e, a = key.rsplit(".", 1)
            return f"{phi.get(int(e), int(e))}.{a}"
        return phi.get(key, key)

    # ---------- simmetria delle istanze multiple ----------
    # Le copie di un figlio sotto la stessa copia del padre sono intercambiabili, ciascuna con tutto il suo
    # sottoalbero (i suoi figli, nipoti, ...): scambiarne due dà lo stesso modello. L'esplorazione tiene un solo
    # stato per orbita (forma canonica: sottoalberi fratelli in ordine, dal basso verso l'alto) e ricostruisce
    # dopo, per permutazione, gli stati, i percorsi e le segnalazioni delle altre istanze.
    def _symmetry(self) -> list | None:
        """Gruppi di sottoalberi fratelli permutabili, dal più profondo: [{"bases", "members", "cols"}], con members
        = per ogni fratello le sue copie (lui e i discendenti) in un ordine relativo uguale per tutti, e cols le loro
        posizioni nella chiave di stato. Ogni scambio di due fratelli vicini si verifica sul modello espanso: un
        gruppo che non resta identico non si riduce."""
        tree = self.model["_tree"]
        up = {int(c): v for c, v in tree["up"].items()}
        anc = {int(e): v for e, v in tree["anc"].items()}
        where = {int(c): (v[0], tuple(v[1])) for c, v in tree["copies"].items()}
        pos = {eid: p for p, eid in enumerate(self.order)}
        same = self._model_sig({})
        groups = []
        for child, (parent, n) in up.items():
            if n < 2:
                continue
            depth = len(anc[child]) - 1  # posizione dell'indice del figlio nel percorso
            subtree = [b for b in anc if child in anc[b]]  # il figlio e le entità composte sotto di lui
            parents = sorted({p[:depth] for e, p in where.values() if e == child})
            for pp in parents:
                members = [sorted((c for c, (e, p) in where.items() if e in subtree and p[:depth + 1] == pp + (j,)),
                                  key=lambda c: (subtree.index(where[c][0]), where[c][1][depth + 1:]))
                           for j in range(1, n + 1)]
                gens = [{**dict(zip(members[j], members[j + 1])), **dict(zip(members[j + 1], members[j]))}
                        for j in range(n - 1)]
                sigs = [self._model_sig(g) for g in gens]
                if all(s == same for s in sigs):
                    self._gens += gens
                    groups.append({"bases": [child], "depth": depth, "members": members,
                                   "cols": [[p for c in mm for p in (pos[c], *(pos[v] for v in self._vars_of.get(c, ())))]
                                            for mm in members]})
                elif not any(r["bases"] == [child] for r in self._sym_refused):
                    # perché no: cambiano le azioni (guardie, effetti) o solo le invarianti
                    bad = next(s for s in sigs if s != same)
                    self._sym_refused.append({"bases": [child], "instances": n,
                                              "reason": "actions" if bad[0] != same[0] else "invariants"})
        if self._sym_refused:  # un gruppo di copie di quell'entità è stato rifiutato: non si riduce nessuno
            bad = {r["bases"][0] for r in self._sym_refused}
            groups = [g for g in groups if g["bases"][0] not in bad]
            self._gens = [g for grp in groups for g in
                          ({**dict(zip(grp["members"][j], grp["members"][j + 1])),
                            **dict(zip(grp["members"][j + 1], grp["members"][j]))} for j in range(len(grp["members"]) - 1))]
        groups.sort(key=lambda g: -g["depth"])
        return groups or None

    def _model_sig(self, phi: dict):
        """Il modello espanso visto attraverso la mappa di entità phi, in forma confrontabile. Guardie su "tutte le
        istanze" e "per ogni padre" sono insiemi; gli effetti di un'azione restano in ordine (la cascata è
        sequenziale) salvo che le loro cascate non si tocchino a vicenda (_commuting)."""
        m = lambda x: phi.get(x, x)
        # riferimenti delle espressioni (modello espanso): a quali variabili va ogni nome, portati da phi
        refs = lambda x: tuple(sorted((k, tuple(sorted(self._map_key(phi, v) for v in vs))) for k, vs in (x.get("_refs") or {}).items()))

        def guard(g):
            if g["kind"] == "expr":
                return ("expr", g.get("description", ""), refs(g))
            if g["kind"] != "state":
                return ("rule", g.get("description", ""))
            many, each = g.get("entity_ids") or (), g.get("each") or ()
            return ("state", None if (many or each) else m(g["entity_id"]), tuple(sorted(m(x) for x in many)),
                    tuple(sorted(g["state_ids"])), bool(g["negate"]), g.get("alt_group") or 0,
                    tuple(sorted(((m(p), tuple(sorted(gone)), tuple(sorted(m(k) for k in kids))) for p, gone, kids in each), key=repr)))

        def effects(t):
            fx = [(m(f["entity_id"]), f.get("kind") or "state", f.get("to_state_id"), (f.get("action") or "").strip().lower(),
                   (bool(f.get("skip_final")), f.get("expr") or "", refs(f))) for f in t["effects"]]
            return self._effects_normal_form(fx)

        ts = sorted(((m(t["entity_id"]), t.get("base_id", t["id"]), t.get("kind"), bool(t.get("cascade_only")),
                      t["from_state_id"], t["to_state_id"], tuple(sorted(map(guard, t["guards"]), key=repr)), effects(t))
                     for t in self.transitions), key=repr)
        rule = lambda r: (None if r.get("entity_ids") else m(r["entity_id"]), tuple(sorted(m(x) for x in r.get("entity_ids") or ())),
                          tuple(sorted(r.get("state_ids", []))), bool(r.get("negate")))
        invs = sorted(((str(inv["id"]).split("#")[0], tuple(sorted(map(rule, inv["rules"]), key=repr)),
                        inv.get("expr") or "", refs(inv)) for inv in self.invariants), key=repr)
        return ts, invs

    def _effect_targets(self, f: dict) -> list:
        """Le azioni del bersaglio che un effetto può far scattare (come le sceglie fire)."""
        ts = self.entity[f["entity_id"]]["transitions"]
        kind = f.get("kind") or "state"
        if kind == "create":
            return [x for x in ts if is_create(x)]
        if kind == "action":
            name = (f.get("action") or "").strip().lower()
            return [x for x in ts if not is_create(x) and x["action"].strip().lower() == name]
        if kind == "set":
            return []
        return [x for x in ts if not is_create(x) and x["to_state_id"] == f.get("to_state_id")]

    def _effect_footprint(self, f: dict) -> tuple[set, set]:
        """(scritture, letture) di un effetto con tutta la sua cascata: le entità che può cambiare e quelle il cui
        stato può influire sull'esito (le proprie e quelle delle guardie delle azioni che può far scattare)."""
        fp = self.__dict__.setdefault("_fp", {})
        key = (f["entity_id"], f.get("kind") or "state", f.get("to_state_id"), (f.get("action") or "").strip().lower())
        if key not in fp:
            w, r, seen, stack = set(), set(), set(), [f]
            while stack:
                e = stack.pop()
                k = (e["entity_id"], e.get("kind") or "state", e.get("to_state_id"), (e.get("action") or "").strip().lower())
                if k in seen:
                    continue
                seen.add(k)
                w.add(e["entity_id"])
                r.add(e["entity_id"])
                if (e.get("kind") or "state") == "set":
                    # un'assegnazione può leggere attributi di qualsiasi entità: prudenza, dipende da tutto
                    r.update(self.entity)
                    continue
                for x in self._effect_targets(e):
                    r.update(self._expr_reads.get(x["id"], ()))
                    for g in x["guards"]:
                        if g["kind"] == "state" and g.get("entity_id") is not None:
                            r.update((g["entity_id"], *(g.get("entity_ids") or ())))
                            for pid, _, kids in g.get("each") or ():
                                r.update((pid, *kids))
                    stack.extend(x["effects"])
            fp[key] = (w, r)
        return fp[key]

    def _effects_normal_form(self, fx: list[tuple]) -> tuple:
        """Forma normale di una sequenza di effetti (entità, tipo, stato, azione, ignora_finali): due effetti le cui
        cascate non si toccano (nessuno scrive ciò che l'altro legge o scrive) si possono scambiare senza cambiare
        l'esito, quindi sequenze uguali a meno di questi scambi hanno la stessa forma: ordinamento topologico delle
        dipendenze che sceglie ogni volta il primo effetto libero in ordine lessicografico."""
        fps = [self._effect_footprint({"entity_id": e, "kind": k, "to_state_id": to, "action": a}) for e, k, to, a, _ in fx]
        dep = lambda i, j: bool(fps[i][0] & (fps[j][0] | fps[j][1]) or fps[j][0] & fps[i][1])
        rest, out = list(range(len(fx))), []
        while rest:
            free = [i for n, i in enumerate(rest) if not any(dep(j, i) for j in rest[:n])]
            pick = min(free, key=lambda i: repr(fx[i]))
            out.append(fx[pick])
            rest.remove(pick)
        return tuple(out)

    def _canon(self, k: tuple, with_perm: bool = False):
        """Forma canonica della chiave di stato: in ogni gruppo, dal più profondo, i sottoalberi fratelli in ordine
        crescente (un sottoalbero si confronta già in forma canonica). Con with_perm ritorna anche la mappa di entità
        canonica → k: k è lo stato in cui perm(e) ha lo stato che e ha nella forma canonica."""
        out, perm = None, {}
        for g in self._sym:
            cur = out or k
            cols = [tuple(cur[p] for p in c) for c in g["cols"]]
            order = sorted(range(len(cols)), key=cols.__getitem__)
            if order == list(range(len(cols))):
                continue
            out = out or list(k)
            step = {}
            for j, o in enumerate(order):
                for p, v in zip(g["cols"][j], cols[o]):
                    out[p] = v
                if with_perm and o != j:
                    step.update(zip(g["members"][j], g["members"][o]))
            if step:
                perm = {a: perm.get(step.get(a, a), step.get(a, a)) for a in {*perm, *step}}
        ck = tuple(out) if out else k
        return (ck, perm) if with_perm else ck

    def _orbit_size(self, k: tuple) -> int:
        """Quanti stati concreti rappresenta la chiave canonica k: per ogni gruppo, le permutazioni distinte dei
        suoi sottoalberi fratelli (quelli uguali non contano due volte)."""
        n = 1
        for g in self._sym:
            cols = [tuple(k[p] for p in c) for c in g["cols"]]
            n *= math.factorial(len(cols))
            for c in set(cols):
                n //= math.factorial(cols.count(c))
        return n

    def _orbit(self, seed, act, cap: int = 100_000) -> dict:
        """Tutte le immagini di seed sotto il gruppo, ognuna con una mappa di entità che ce la porta, visitando i
        generatori (senza elencare il gruppo, che con più livelli di istanze può essere grande)."""
        seen, queue = {seed: {}}, deque([seed])
        while queue and len(seen) < cap:
            x = queue.popleft()
            for g in self._gens:
                y = act(g, x)
                if y not in seen:
                    m = seen[x]
                    seen[y] = {a: g.get(m.get(a, a), m.get(a, a)) for a in {*m, *g}}
                    queue.append(y)
        return seen

    def _apply_perm(self, phi: dict, k: tuple) -> tuple:
        """Lo stato in cui l'entità phi(e) ha lo stato (e gli attributi) che e ha in k."""
        st = self.from_key(k)
        return self.key({self._map_key(phi, e): s for e, s in st.items()})

    def _map_transition(self, phi: dict, tid: int) -> int:
        t = self.transition[tid]
        return self._by_base[(phi.get(t["entity_id"], t["entity_id"]), t.get("base_id", t["id"]))]["id"]

    def stuck_ids(self, state: dict) -> list:
        """Entità che dovrebbero ancora muoversi: non in uno stato finale o di riposo, e non "congelate" da un padre
        in composizione (anche indiretto) concluso o inesistente: chiuso l'aggregato, le sue parti restano come sono."""
        memo = {}

        def done(eid):
            if eid not in memo:
                memo[eid] = False  # contro i cicli di composizione
                s = state.get(eid, NONE)
                memo[eid] = s == NONE or self.is_final(s) or any(done(p) for p in self.parents.get(eid, ()))
            return memo[eid]

        return [eid for eid, s in state.items() if not isinstance(eid, str)
                and not self.is_final(s) and s not in self.resting and not any(done(p) for p in self.parents.get(eid, ()))]

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
        for k, (_, _, _, v0) in self.vars.items():
            st[k] = v0
        if self.messages:
            st["_q"] = ()
        return st

    def normalize(self, state: dict) -> dict:
        """Accetta chiavi stringa (JSON) e completa con lo stato di partenza."""
        st = self.initial_state()
        for k, v in (state or {}).items():
            if k == "_q":  # coda dei messaggi in volo
                if self.messages:
                    st["_q"] = tuple(sorted(int(x) for x in v if 0 <= int(x) < len(self.messages)))[:self.max_in_flight]
                continue
            if isinstance(k, str) and "." in k:  # attributo: "entità.attributo"
                if k in self.vars and v in self.vars[k][2]:
                    st[k] = v
                continue
            k = int(k)
            if k not in st:
                continue
            if v == NONE and k in self.creatable:
                st[k] = NONE
            # le copie di un'istanza multipla (id negativi) usano gli stati dell'entità di base
            elif v in self.state and self.state[v]["entity_id"] == vbase(k):
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

    @staticmethod
    def clauses(guards: list) -> list[list]:
        """Guardie in AND di clausole: le guardie con lo stesso alt_group (≠ 0) sono alternative (OR) tra loro."""
        out, groups = [], {}
        for g in guards:
            alt = g.get("alt_group") if isinstance(g, dict) else g[-1]
            if alt:
                if alt not in groups:
                    groups[alt] = []
                    out.append(groups[alt])
                groups[alt].append(g)
            else:
                out.append([g])
        return out

    def guard_failures(self, t: dict, state: dict) -> list[str]:
        """Perché le guardie di stato di t non passano: una voce per clausola che fallisce."""
        fails = []
        for clause in self.clauses([g for g in t["guards"] if g["kind"] == "state"]):
            per = [self._guard_messages(g, state) for g in clause]
            if not all(per):  # basta un'alternativa soddisfatta
                continue
            if len(clause) == 1:
                fails += per[0]
            else:
                fails.append(self.t("guard.any_of", items=" / ".join(x for msgs in per for x in msgs)))
        for g, fn in self._exprs.get(t["id"], ()):
            if not fn(state):
                fails.append(self.t("guard.fail_expr", expr=g["description"],
                                    values=", ".join(f"{self._var_name(k)} = {state[k]}" for k in self._expr_vars(g, t))))
        return fails

    def _expr_vars(self, g: dict, t: dict) -> list:
        """Le variabili lette da una guardia "condizione" (per mostrarne i valori quando non passa)."""
        try:
            return self._compile(g["description"], t["entity_id"], g.get("_refs"))[1]
        except ExprError:
            return []

    def _var_name(self, key: str) -> str:
        eid, a, _, _ = self.vars[key]
        return f"{self.ename(eid)}.{a['name']}"

    def _guard_messages(self, g: dict, state: dict) -> list[str]:
        """Messaggi di una singola guardia di stato che non passa (lista vuota se passa)."""
        fails = []
        if g.get("each"):
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
            return fails
        if g.get("entity_id") is None or g["entity_id"] not in state:
            return fails
        check = lambda s: (s not in g["state_ids"]) if g["negate"] else (s in g["state_ids"])
        eid = g["entity_id"]
        if g.get("entity_ids"):
            # entità con più istanze (analisi): vale per tutte le istanze esistenti
            existing = [x for x in g["entity_ids"] if state.get(x, NONE) != NONE] or g["entity_ids"][:1]
            bad = [x for x in existing if not check(state[x])]
            if not bad:
                return fails
            eid = bad[0]
        elif check(state[eid]):
            return fails
        names = ", ".join(self.sname(s) for s in g["state_ids"]) or "—"
        op = self.t("guard.must_not" if g["negate"] else "guard.must")
        fails.append(self.t("guard.fail", entity=self.ename(eid), op=op, states=names, current=self.sname(state[eid]))
                     + (f" — {g['description']}" if g["description"] else ""))
        return fails

    def guards_ok(self, t: dict, state: dict) -> bool:
        """Come guard_failures, ma solo sì/no: senza costruire messaggi (usata nell'esplorazione)."""
        for clause in self._guards_fast[t["id"]]:
            if not any(self._fast_ok(g, state) for g in clause):
                return False
        for _, fn in self._exprs.get(t["id"], ()):
            if not fn(state):
                return False
        return True

    @staticmethod
    def _fast_ok(g: tuple, state: dict) -> bool:
        eid, many, ids, neg, each, _ = g
        if each:
            # per ogni padre esistente: almeno un suo figlio negli stati (con negate: nessuno)
            return not any(state.get(pid, NONE) not in gone and any(state.get(x, NONE) in ids for x in kids) == neg
                           for pid, gone, kids in each)
        if eid not in state:
            return True
        if many:
            existing = [state[x] for x in many if state.get(x, NONE) != NONE] or [state[many[0]]]
        else:
            existing = (state[eid],)
        return all((s in ids) != neg for s in existing)

    def rule_notes(self, t: dict) -> list[str]:
        return [g["description"] for g in t["guards"] if g["kind"] == "rule" and g["description"]]

    def guard_misses(self, t: dict, state: dict) -> list[dict]:
        """Guardie di stato di t che non passano, con gli stati che hanno trovato (per proporre come allargarle)."""
        out = []
        for clause in self.clauses([g for g in t["guards"] if g["kind"] == "state" and g.get("entity_id") is not None]):
            misses = [m for g in clause for m in self._guard_miss(t, g, state)]
            if len(misses) == len(clause):  # nessuna alternativa passa: tutte vanno proposte
                out += misses
        return out

    def _guard_miss(self, t: dict, g: dict, state: dict) -> list[dict]:
        """La guardia g di t, se non passa, con gli stati trovati; lista vuota se passa."""
        if g.get("each"):
            cur = []
            for pid, gone, kids in g["each"]:
                found = [state.get(x, NONE) for x in kids]
                if state.get(pid, NONE) not in gone and any(s in g["state_ids"] for s in found) == bool(g["negate"]):
                    cur += found
        else:
            if g["entity_id"] not in state:
                return []
            ids = g.get("entity_ids") or [g["entity_id"]]
            existing = [x for x in ids if state.get(x, NONE) != NONE] or ids[:1]
            cur = [state[x] for x in existing if (state[x] in g["state_ids"]) == bool(g["negate"])]
        return [{"guard_id": g["id"], "transition_id": t.get("base_id", t["id"]), "current": sorted(set(cur))}] if cur else []

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

    def fire(self, t: dict, state: dict, depth: int = 0, chain: frozenset = frozenset(), only: list | None = None):
        """Esegue t e propaga gli effetti. Ritorna (nuovo_stato, passi, conflitti); i conflitti sono dict (vedi _conflict).
        only: consegna di un messaggio, cioè solo quegli effetti di t (asincroni, ora applicati), senza rifare t."""
        new = dict(state)
        steps = [] if only is not None else [{
            "transition_id": t["id"], "entity_id": t["entity_id"], "action": t["action"],
            "from": state.get(t["entity_id"]), "to": t["to_state_id"], "depth": depth,
            "create": is_create(t),
        }]
        conflicts = []
        if only is None:
            new[t["entity_id"]] = t["to_state_id"]
        chain = chain | {t["id"]}

        for eff in t["effects"] if only is None else only:
            eid, target = eff["entity_id"], eff["to_state_id"]
            if eid not in new:
                continue
            cur = new[eid]
            if eff.get("deferred") and only is None:
                # effetto asincrono: parte un messaggio, consegnato in un passo successivo (vedi deliveries)
                q = new["_q"]
                if len(q) >= self.max_in_flight:
                    c = self._conflict(t, lambda: self.t("msg.what", entity=self.ename(eid)), eid, depth,
                                       self.t("msg.full", n=self.max_in_flight), effect=eff)
                    conflicts.append({**c, "limit": "queue"})
                    continue
                new["_q"] = tuple(sorted(q + (self._msg_index[id(eff)],)))
                steps.append({"transition_id": None, "entity_id": eid, "action": self.t("msg.sent"), "from": cur,
                              "to": cur, "depth": depth + 1, "skipped": True, "message": True})
                continue
            if eff.get("kind") == "set":
                # attributo := espressione, valutata sullo stato com'è a questo punto della cascata
                key, fn = self._sets.get(id(eff), (None, None))
                if key is None or cur == NONE:  # espressione non valida (segnalata a parte) o entità che non c'è
                    continue
                value = fn(new)
                if value not in self.vars[key][2]:
                    conflicts.append(self._conflict(
                        t, lambda: self.t("effect.what_set", attr=self._var_name(key), value=value), eid, depth,
                        self.t("effect.out_of_domain", values=", ".join(map(str, self.vars[key][2][:8]))), effect=eff))
                    continue
                new[key] = value
                continue
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
            cut = self._cut(t, what, eid, depth, chosen, eff, chain)
            if cut:
                conflicts.append(cut)
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
            # nessuna istanza da coinvolgere (es. un ordine senza righe): l'effetto non si applica
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
        cut = self._cut(t, what, eid, depth, ok[0], eff, chain)
        if cut:
            return state, [], [cut]
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
        cut = self._cut(t, what, eid, depth, ok[0], eff, chain)
        if cut:
            return state, [], [cut]
        return self.fire(ok[0], state, depth + 1, chain)

    # ---------- messaggi asincroni ----------
    def deliveries(self, state: dict):
        """Le consegne possibili dallo stato: per ogni messaggio in coda (in qualsiasi ordine) la consegna, e secondo
        la garanzia del dominio anche il duplicato (consegnato ma ancora in coda) o la perdita (tolto senza effetto).
        Ritorna [(messaggio, variante, nuovo_stato, passi, conflitti)]."""
        out = []
        for mid in sorted(set(state.get("_q") or ())):
            t, f = self.messages[mid]
            rest = list(state["_q"])
            rest.remove(mid)
            base = {**state, "_q": tuple(rest)}
            new, steps, conf = self.fire(t, base, only=[f])
            out.append((mid, "deliver", new, steps, conf))
            if self.delivery == "at_least_once":
                new, steps, conf = self.fire(t, state, only=[f])
                out.append((mid, "duplicate", new, steps, conf))
            elif self.delivery == "at_most_once":
                out.append((mid, "lose", base, [], []))
        return out

    def message_label(self, mid: int, variant: str = "deliver") -> str:
        t, f = self.messages[mid]
        return self.t(f"msg.{variant}", action=t["action"], entity=self.ename(t["entity_id"]), target=self.ename(f["entity_id"]))

    def deliver(self, state: dict, mid: int, variant: str = "deliver") -> dict:
        """Consegna (o duplica, o perde) un messaggio in coda, come apply per un'azione."""
        state = self.normalize(state)
        found = next((d for d in self.deliveries(state) if d[0] == mid and d[1] == variant), None)
        if not found:
            return {"ok": False, "error": self.t("msg.not_pending")}
        _, _, new, steps, conflicts = found
        if conflicts:
            return {"ok": False, "error": self.t("apply.conflict"), "reasons": [c["message"] for c in conflicts]}
        return {"ok": True, "state": new, "steps": steps, "conflicts": [], "forced": False, "violations": self.violations(new)}

    def _cut(self, t, what, eid, depth, chosen, eff, chain):
        """La cascata non prosegue: perché l'azione scelta è già in corso più in alto (un ciclo vero, errore del
        modello) oppure perché si è arrivati al limite di profondità (limite tecnico dell'analisi, non un errore:
        la cascata non è stata verificata fino in fondo e il certificato lo dichiara). None se si può proseguire."""
        if chosen["id"] in chain:
            return self._conflict(t, what, eid, depth, self.t("effect.cycle_on", action=chosen["action"]),
                                  fix=("fix.cycle", eid, {"action": chosen["action"]}), effect=eff)
        if depth >= MAX_CASCADE_DEPTH:
            c = self._conflict(t, what, eid, depth, self.t("effect.too_deep", action=chosen["action"], depth=MAX_CASCADE_DEPTH),
                               fix=("fix.too_deep", eid, {"action": chosen["action"]}), effect=eff)
            return {**c, "limit": True}
        return None

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
            ex = self._inv_exprs.get(str(inv["id"]))
            if not rules and not ex:
                continue
            hit = not ex or bool(ex[1](state))  # condizione sugli attributi, in AND con le regole
            for eid, ids, neg, many in rules if hit else ():
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
        # messaggi in volo: ognuno con le consegne possibili (e il loro esito, per il simulatore)
        messages = [{"message": mid, "variant": v, "label": self.message_label(mid, v), "ok": not conf,
                     "reasons": [c["message"] for c in conf]} for mid, v, _, _, conf in self.deliveries(state)] if self.messages else []
        return {"state": state, "actions": actions, "violations": self.violations(state), "messages": messages}

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
            if step.get("message") is not None:  # consegna di un messaggio in coda
                mid, variant = int(step["message"]), step.get("variant") or "deliver"
                r = self.deliver(state, mid, variant)
                if not r["ok"]:
                    break
                history.append({**r, "prev": state, "transition_id": None, "message": mid, "variant": variant,
                                "entity_id": self.messages[mid][1]["entity_id"], "action": self.message_label(mid, variant), "actor": ""})
                state = r["state"]
                continue
            t = self.transition.get(int(step["transition_id"] or 0))
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
               json.dumps(start, sort_keys=True), limit, self.t.lang, self._sym is not None)
        with _LOCKS_GUARD:
            lock = _EXPLORE_LOCKS.setdefault(key, threading.Lock())
        with lock:
            with _LOCKS_GUARD:
                if key in _EXPLORE_CACHE:
                    _EXPLORE_CACHE.move_to_end(key)
                    return _EXPLORE_CACHE[key]
            result = self._explore(start, limit)
            with _LOCKS_GUARD:
                _EXPLORE_CACHE[key] = result
                # si tengono le più recenti finché stanno nel budget di stati (almeno l'ultima)
                while len(_EXPLORE_CACHE) > 1 and (len(_EXPLORE_CACHE) > _EXPLORE_CACHE_SIZE or
                                                   sum(len(r["parent"]) for r in _EXPLORE_CACHE.values()) > _EXPLORE_CACHE_STATES):
                    old, _ = _EXPLORE_CACHE.popitem(last=False)
                    _EXPLORE_LOCKS.pop(old, None)
            return result

    def _reads(self, t: dict) -> list:
        """Entità il cui stato può influire sull'esito di t con la sua cascata: la sua, quelle delle guardie
        e, seguendo gli effetti, i bersagli con tutte le loro azioni (guardie ed effetti a loro volta)."""
        ents, seen, stack, queue = {t["entity_id"]}, set(), [t], False
        while stack:
            x = stack.pop()
            if x["id"] in seen:
                continue
            seen.add(x["id"])
            queue = queue or any(f.get("deferred") for f in x["effects"])
            for g in x["guards"]:
                if g["kind"] == "state" and g.get("entity_id") is not None:
                    ents.update((g["entity_id"], *(g.get("entity_ids") or ())))
                    for pid, _, kids in g.get("each") or ():
                        ents.update((pid, *kids))
            ents.update(self._expr_reads.get(x["id"], ()))  # attributi letti da condizioni e assegnazioni
            for f in x["effects"]:
                ents.add(f["entity_id"])
                stack.extend(self.entity[f["entity_id"]]["transitions"])
        return [e for e in self.order if self.owner(e) in ents or (e == "_q" and queue)]

    def _explore(self, start: dict | None, limit: int) -> dict:
        start = self.normalize(start) if start else self.initial_state()
        k0 = self.key(start)
        # con la simmetria si esplora un rappresentante per orbita; serve uno stato di partenza simmetrico
        # (quello iniziale lo è: le copie partono tutte dallo stesso stato)
        sym = self._sym if self._sym and self._canon(k0) == k0 else None
        concrete = self._orbit_size(k0) if sym else 1  # stati concreti rappresentati
        parent = {k0: None}
        queue = deque([k0])
        enabled_ever = set()
        conflicts, deadlocks, violations = {}, [], {}
        truncated, stopped, started = False, None, time.monotonic()
        inv_key, by_pos = self._inv_key, self._inv_by_pos

        def check(key, candidates):
            # la BFS trova per prima la violazione col percorso più breve: le invarianti già violate non si ricontrollano
            for i in candidates:
                inv, rules = inv_key[i]
                if inv["id"] in violations:
                    continue
                for positions, ids, neg in rules:
                    # neg None: condizione sugli attributi (ids è la funzione della chiave)
                    if (not ids(key)) if neg is None else any((key[p] in ids) == neg for p in positions):
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

        def reach(k, new, how) -> bool:
            """Lo stato new raggiunto da k con how (azione, o ("msg", messaggio, variante)); True se scartato per il
            limite di stati."""
            nonlocal concrete
            nk = self._canon(self.key(new)) if sym else self.key(new)
            if nk in parent:
                return False
            if len(parent) >= limit:
                return True
            parent[nk] = (k, how)
            queue.append(nk)
            concrete += self._orbit_size(nk) if sym else 1
            # le invarianti valide in k restano valide se la mossa non tocca le loro entità
            touched = set()
            for p, (a, b) in enumerate(zip(k, nk)):
                if a != b:
                    touched.update(by_pos.get(p, ()))
            check(nk, sorted(touched))
            return False

        check(k0, range(len(inv_key)))
        popped = 0
        while queue:
            # tempo e memoria si controllano ogni tanto: superati, ci si ferma qui (risultato incompleto)
            popped += 1
            if popped % 2048 == 0:
                if time.monotonic() - started > EXPLORE_SECONDS:
                    truncated, stopped = True, "time"
                    break
                if resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 > EXPLORE_MB:
                    truncated, stopped = True, "memory"
                    break
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
                    truncated |= reach(k, new, t["id"])
            # consegne dei messaggi in coda: altre mosse, in qualsiasi ordine
            for mid, variant, new, steps, conf in self.deliveries(st) if self.messages else ():
                t = self.messages[mid][0]
                if conf:
                    for c in conf:
                        conflicts.setdefault((t["id"], c["message"]), {"transition_id": t["id"], "message": c["message"],
                                                                       "key": k, "conflict": c, "steps": steps, "delivery": mid})
                    continue
                moves += 1
                truncated |= reach(k, new, ("msg", mid, variant))
            # un'entità che non esiste o mai avviata non è "bloccata"
            if moves == 0 and any(not self.is_final(s) and s not in self.resting for s in k[:self._ent_n]) \
                    and self.stuck_ids(self.from_key(k)):
                if len(deadlocks) < 25:
                    deadlocks.append(k)

        if sym:
            # le altre istanze: quello che vale per uno stato vale, permutato, per tutta la sua orbita, alla stessa
            # distanza dall'inizio. Si scorre in ordine di scoperta, così ogni segnalazione resta alla distanza minima
            enabled_ever = {x for t in enabled_ever for x in self._orbit(t, lambda g, y: self._map_transition(g, y))}

            def depth(k):
                n = 0
                while parent[k] is not None:
                    k, n = parent[k][0], n + 1
                return n

            # per ogni invariante, la violazione più vicina all'inizio tra tutte le immagini di quelle trovate
            best = {}
            for v in list(violations.values()):
                d = depth(v["key"])
                for k2 in self._orbit(v["key"], self._apply_perm):
                    for inv, rules in inv_key:
                        if all(ids(k2) if neg is None else all((k2[p] in ids) != neg for p in positions)
                               for positions, ids, neg in rules):
                            if inv["id"] not in best or d < best[inv["id"]][0]:
                                best[inv["id"]] = (d, k2, inv)
            violations = {i: {"id": i, "name": inv["name"], "severity": inv["severity"], "description": inv["description"],
                              "key": k2} for i, (d, k2, inv) in sorted(best.items(), key=lambda x: x[1][0])}
            # conflitti: lo stesso, rifacendo l'azione permutata sullo stato permutato (i messaggi citano le istanze);
            # l'orbita è della coppia (azione, stato): la stessa azione può finire su istanze diverse
            found = {}
            for c in list(conflicts.values()):
                d = depth(c["key"])
                pair = (c["transition_id"], c["key"])
                for t2id, k2 in self._orbit(pair, lambda g, p: (self._map_transition(g, p[0]), self._apply_perm(g, p[1]))):
                    _, steps, conf = self.fire(self.transition[t2id], self.from_key(k2))
                    for x in conf:
                        ck = (t2id, x["message"])
                        if ck not in found or d < found[ck][0]:
                            found[ck] = (d, {"transition_id": t2id, "message": x["message"], "key": k2,
                                             "conflict": x, "steps": steps})
            conflicts = {ck: c for ck, (d, c) in sorted(found.items(), key=lambda x: x[1][0])}
            for k in list(deadlocks):
                for k2 in self._orbit(k, self._apply_perm, cap=26):
                    if len(deadlocks) < 25 and k2 not in deadlocks:
                        deadlocks.append(k2)

        trace = lambda key: self._trace(parent, key, bool(sym))
        return {
            "start": start,
            # in ordine di scoperta (BFS): il primo stato che soddisfa una condizione ha il percorso più breve.
            # Con la simmetria sono i rappresentanti delle orbite: proprietà come "quali stati compaiono" valgono uguali
            "reachable": parent.keys(),
            "states": concrete,
            "symmetry": list({g["bases"][0]: {"bases": g["bases"], "instances": len(g["members"])} for g in sym or ()}.values()),
            "parent": parent,
            "truncated": truncated,
            "stopped": stopped or ("states" if truncated else None),
            "seconds": time.monotonic() - started,
            "enabled_ever": enabled_ever,
            "violations": [{**v, "state": self.from_key(v["key"]), "trace": trace(v["key"])} for v in violations.values()],
            "conflicts": [{**c, "state": self.from_key(c["key"]), "trace": trace(c["key"])} for c in conflicts.values()],
            "deadlocks": [{"state": self.from_key(k), "trace": trace(k)} for k in deadlocks],
        }

    def _step(self, how) -> dict:
        """Un passo di percorso: un'azione (id) o la consegna di un messaggio ("msg", messaggio, variante)."""
        if isinstance(how, tuple):
            _, mid, variant = how
            return {"transition_id": None, "message": mid, "variant": variant, "entity_id": self.messages[mid][1]["entity_id"],
                    "action": self.message_label(mid, variant), "to": None, "create": False}
        t = self.transition[how]
        return {"transition_id": how, "entity_id": t["entity_id"], "action": t["action"], "to": t["to_state_id"],
                "create": is_create(t)}

    def _trace(self, parent: dict, key: tuple, sym: bool = False) -> list[dict]:
        """Percorso più breve dallo stato iniziale a key, dalla mappa dei predecessori dell'esplorazione.
        Con la simmetria la mappa collega rappresentanti di orbite: il percorso si rigioca tenendo la permutazione
        tra rappresentante e stato concreto, e alla fine si porta sull'istanza di key. È un percorso concreto,
        eseguibile così com'è nel simulatore."""
        step = self._step
        if not sym:
            out = []
            while parent[key] is not None:
                prev, tid = parent[key]
                out.append(step(tid))
                key = prev
            return list(reversed(out))
        ck, q = self._canon(key, with_perm=True)  # key = q(ck)
        chain, k = [], ck
        while parent[k] is not None:
            prev, tid = parent[k]
            chain.append((prev, tid))
            k = prev
        p, tids = {}, []  # p: rappresentante corrente → stato concreto raggiunto
        for prev, tid in reversed(chain):
            tids.append(self._map_transition(p, tid))
            _, qx = self._canon(self.key(self.fire(self.transition[tid], self.from_key(prev))[0]), with_perm=True)
            p = {e: p.get(qx.get(e, e), qx.get(e, e)) for e in self.order}
        # il percorso concreto finisce in p(ck); portato con q∘p⁻¹ (lo stato iniziale non cambia) finisce in key
        inv = {v: e for e, v in p.items()}
        fix = {e: q.get(inv.get(e, e), inv.get(e, e)) for e in self.order}
        return [step(self._map_transition(fix, tid)) for tid in tids]

    # ---------- proprietà temporali ----------
    def _successors(self, ex: dict) -> dict:
        """Il grafo dello spazio degli stati esplorato: per ogni chiave, le mosse (azione o consegna) e dove portano.
        L'esplorazione tiene solo i predecessori; questo si calcola quando serve e resta con l'esplorazione."""
        if "succ" in ex:
            return ex["succ"]
        succ = {}
        movable = {(e["id"], s["id"]): [t for t in self.outgoing(e["id"], s["id"]) if not t.get("cascade_only")]
                   for e in self.entities for s in self.states_of(e["id"])}
        for k in ex["reachable"]:
            st, out = self.from_key(k), []
            for eid, sid in zip(self.order, k[:self._ent_n]):
                for t in movable.get((eid, sid), ()):
                    if self.guards_ok(t, st):
                        new, _, conf = self.fire(t, st)
                        if not conf:
                            out.append((self.key(new), t["id"]))
            for mid, variant, new, _, conf in self.deliveries(st) if self.messages else ():
                if not conf:
                    out.append((self.key(new), ("msg", mid, variant)))
            succ[k] = [(nk, how) for nk, how in out if nk in ex["parent"]]
        ex["succ"] = succ
        return succ

    def _matcher(self, rules: list):
        """Funzione chiave -> bool per una combinazione di condizioni sugli stati (come nelle invarianti)."""
        pos = {eid: i for i, eid in enumerate(self.order)}
        rs = [(pos[r["entity_id"]], set(r.get("state_ids", [])), bool(r.get("negate"))) for r in rules or ()
              if r.get("entity_id") in pos]
        return (lambda k: all((k[p] in ids) != neg for p, ids, neg in rs)) if rs else None

    def _can_reach(self, ex: dict, goal) -> set:
        """Gli stati da cui goal è raggiungibile (all'indietro dagli stati che lo soddisfano)."""
        succ, back = self._successors(ex), {}
        for k, out in succ.items():
            for nk, _ in out:
                back.setdefault(nk, []).append(k)
        seen = {k for k in ex["reachable"] if goal(k)}
        queue = deque(seen)
        while queue:
            for p in back.get(queue.popleft(), ()):
                if p not in seen:
                    seen.add(p)
                    queue.append(p)
        return seen

    def _path(self, ex: dict, start: tuple, end: tuple, inside) -> list:
        """Passi da start a end restando negli stati che soddisfano inside (il più breve)."""
        succ, prev, queue = self._successors(ex), {start: None}, deque([start])
        while queue:
            k = queue.popleft()
            if k == end:
                break
            for nk, how in succ.get(k, ()):
                if nk not in prev and inside(nk):
                    prev[nk] = (k, how)
                    queue.append(nk)
        steps, k = [], end
        while prev.get(k):
            k, how = prev[k][0], prev[k][1]
            steps.append(how)
        return list(reversed(steps))

    def _temporal(self, x: dict) -> dict:
        """always: da ogni stato raggiungibile l'obiettivo (rules) resta raggiungibile.
        leads_to: ogni volta che vale rules, prima o poi vale target; con equità (nessuna azione possibile viene
        ignorata per sempre) vuol dire che, da uno stato rules, finché target non vale si resta in stati da cui
        target è ancora raggiungibile. Il controesempio è un percorso concreto fino allo stato da cui non si arriva
        più: il percorso dall'inizio, e per leads_to poi i passi fino lì senza passare da target."""
        ex = self.explore()
        goal = self._matcher(x.get("rules") if x["kind"] == "always" else x.get("target"))
        trigger = self._matcher(x.get("rules")) if x["kind"] == "leads_to" else None
        if goal is None or (x["kind"] == "leads_to" and trigger is None):
            return {"ok": False, "empty": True}
        ok_from = self._can_reach(ex, goal)
        if x["kind"] == "always":
            bad = next((k for k in ex["reachable"] if k not in ok_from), None)
            if bad is None:
                return {"ok": True, "truncated": ex["truncated"]}
            return {"ok": False, "trace": self._trace(ex["parent"], bad), "state": self.from_key(bad), "truncated": ex["truncated"]}
        starts = [k for k in ex["reachable"] if trigger(k) and not goal(k)]
        if not any(trigger(k) for k in ex["reachable"]):
            return {"ok": True, "vacuous": True, "truncated": ex["truncated"]}
        for p in starts:
            # dagli stati raggiungibili da p senza passare da target, target deve restare raggiungibile
            seen, queue = {p}, deque([p])
            while queue:
                k = queue.popleft()
                if k not in ok_from:
                    after = [self._step(how) for how in self._path(ex, p, k, lambda y: not goal(y))]
                    return {"ok": False, "trace": self._trace(ex["parent"], p) + after, "trigger_at": len(self._trace(ex["parent"], p)),
                            "state": self.from_key(k), "truncated": ex["truncated"]}
                for nk, _ in self._successors(ex).get(k, ()):
                    if nk not in seen and not goal(nk):
                        seen.add(nk)
                        queue.append(nk)
        return {"ok": True, "truncated": ex["truncated"]}

    # ---------- aspettative ----------
    def check_expectation(self, x: dict) -> dict:
        """Verifica un'aspettativa. kind="goal": la combinazione di stati (rules, come le invarianti) dev'essere
        raggiungibile dallo stato iniziale; kind="path": il percorso (trace) dev'essere eseguibile ("possible")
        oppure bloccato ("blocked"). Ritorna {ok, trace} per un obiettivo, {ok, done, total, failed, reason} per un percorso.
        kind="always" e "leads_to": proprietà temporali (vedi _temporal)."""
        if x["kind"] in ("always", "leads_to"):
            return self._temporal(x)
        if x["kind"] == "goal":
            rules = [(r["entity_id"], set(r.get("state_ids", [])), bool(r.get("negate")))
                     for r in x.get("rules") or [] if r["entity_id"] in self.order]
            if not rules:
                return {"ok": False, "empty": True}
            pos = {eid: i for i, eid in enumerate(self.order)}
            ex = self.explore()
            for k in ex["reachable"]:
                if all((k[pos[e]] in ids) != neg for e, ids, neg in rules):
                    return {"ok": True, "trace": self._trace(ex["parent"], k), "state": self.from_key(k)}
            return {"ok": False, "trace": None, "truncated": ex["truncated"]}
        trace = x.get("trace") or []
        r = self.replay(trace)
        done = len(r["history"])
        failed = reason = None
        if done < len(trace):
            step = trace[done]
            if step.get("message") is not None:  # consegna di un messaggio
                failed = step.get("action")
                res = self.deliver(r["state"], int(step["message"]), step.get("variant") or "deliver")
            else:
                t = self.transition.get(int(step["transition_id"] or 0))
                failed = step.get("action") or (t and t["action"])
                res = self.apply(r["state"], t["id"]) if t else {"error": self.t("apply.no_transition")}
            reason = res.get("error", "") + (f": {res['reasons'][0]}" if res.get("reasons") else "")
        possible = done == len(trace)
        ok = possible if x.get("expect", "possible") == "possible" else not possible
        return {"ok": ok, "done": done, "total": len(trace), "failed": failed, "reason": reason}

    # ---------- analisi edge case ----------
    def _guard_sig(self, g: dict):
        if g["kind"] in ("rule", "expr"):
            return (g["kind"], g["description"].strip().lower())
        return ("state", g["entity_id"], tuple(sorted(g["state_ids"])), bool(g["negate"]), g.get("each_entity_id"))

    def _guard_text(self, g: dict) -> str:
        """Guardia in forma leggibile: "Pagamento in AUTORIZZATO o CATTURATO"."""
        if g["kind"] in ("rule", "expr"):
            return self.t("q", x=g["description"])
        names = [self.sname(s) for s in g["state_ids"]]
        states = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} {self.t('or')} {names[-1]}"
        text = self.t("guard.text_not_in" if g["negate"] else "guard.text_in", entity=self.ename(g["entity_id"]), states=states)
        return self.t("guard.text_each", text=text, each=self.ename(g["each_entity_id"])) if g.get("each_entity_id") else text

    def _explain_deadlock(self, state: dict, trace: list) -> dict:
        """Perché uno stato globale è un deadlock: per ogni entità ferma, le azioni che escono dal suo stato e cosa le
        blocca. La causa è la prima guardia che non passa: nel deadlock nessuna entità si muove, quindi chi la fa fallire
        resta dov'è per sempre; l'ultima azione del percorso che l'ha portato lì è quella da correggere."""
        T, q = self.t, lambda x: self.t("q", x=x)
        stuck, cause, blocked = [], None, None
        for eid in self.stuck_ids(state):
            sid = state[eid]
            exits = []
            for t in self.outgoing(eid, sid):
                reasons = []
                if t.get("cascade_only"):
                    via = ", ".join(f"{q(x['action'])} ({self.ename(x['entity_id'])})" for x in self.triggers(t)) or T("eval.no_action")
                    reasons.append(T("eval.cascade_only", via=via))
                else:
                    for g in t["guards"]:
                        f = self.guard_failures({**t, "guards": [g]}, state)
                        reasons += f
                        if f and cause is None:
                            cause = (eid, sid, t, g)
                    if not reasons:
                        conf = self.fire(t, state)[2]
                        reasons = [c["message"] for c in conf]
                        if conf and blocked is None:
                            blocked = (eid, sid, conf[0])
                exits.append({"transition_id": t.get("base_id", t["id"]), "action": t["action"], "to": t["to_state_id"],
                              "reasons": reasons})
            stuck.append({"entity_id": eid, "state": sid, "exits": exits})

        first = stuck[0]
        p = {"entity": self.ename(first["entity_id"]), "state": self.sname(first["state"])}
        # why: la causa per id (del modello espanso), da cui partono soluzioni e raggruppamento per causa
        why = {"kind": "generic", "entity_id": first["entity_id"], "state_id": first["state"]}
        if cause:
            eid, sid, t, g = cause
            p = {"entity": self.ename(eid), "state": self.sname(sid), "action": t["action"]}
            cands = [*(g.get("entity_ids") or [g["entity_id"]]), *(x for _, _, kids in g.get("each") or () for x in kids)]
            blocker = next((x for x in cands if (state.get(x, NONE) in g["state_ids"]) == bool(g["negate"])), g["entity_id"])
            bstate = state.get(blocker, NONE)
            by = next((s for s in reversed(trace) if s["entity_id"] == blocker and s["to"] == bstate), None)
            p.update(guard=self._guard_text(g), blocker=self.ename(blocker), bstate=self.sname(bstate))
            detail = T("a.deadlock.needs", **p) + " " + T("a.deadlock.blocker_final" if self.is_final(bstate) else "a.deadlock.blocker_stuck", **p)
            why = {"kind": "by" if by else "guard", "entity_id": eid, "state_id": sid,
                   "transition_id": t.get("base_id", t["id"]), "guard_id": g.get("id"), "blocker": blocker,
                   "bstate": bstate, "by": by and by["transition_id"]}
            if by:
                detail += T("a.deadlock.by", by=by["action"], **p)
                fix = ("fix.deadlock_effect", blocker, {**p, "by": by["action"]})
            else:
                detail += "."
                fix = ("fix.deadlock_guard", eid, p)
        elif blocked:
            # nessuna guardia ferma le uscite, ma la loro cascata si blocca: la causa è quella della cascata bloccata
            eid, sid, c = blocked
            detail, fix = T("a.deadlock.generic", **p), None
            why = {"kind": "cascade", "entity_id": eid, "state_id": sid, "conflict": c}
        elif not first["exits"]:
            detail, fix = T("a.deadlock.no_exit", **p), ("fix.deadlock_final", first["entity_id"], p)
            why["kind"] = "no_exit"
        else:
            detail, fix = T("a.deadlock.generic", **p), None
        if blocked:
            # un'uscita la cui cascata si blocca è un errore del modello (le guardie che non passano di solito no):
            # causa e soluzioni partono da lì
            why["conflict"] = blocked[2]
            fix = None
        hint = {"text": T(fix[0], **fix[2]), "entity_id": fix[1]} if fix else blocked and blocked[2].get("fix")
        return {"stuck": stuck, "detail": detail, "why": why, "fix": hint and dict(hint)}

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
        """La guardia own è almeno severa quanto req (es. Ordine ∈ {A, B} copre Ordine ∈ {A, B, C})."""
        if own["kind"] != "state" or req["kind"] != "state" or own["entity_id"] != req["entity_id"] \
                or bool(own["negate"]) != bool(req["negate"]) or own.get("each_entity_id") != req.get("each_entity_id"):
            return self._guard_sig(own) == self._guard_sig(req)
        a, b = set(own["state_ids"]), set(req["state_ids"])
        return a >= b if own["negate"] else a <= b

    def dynamic(self) -> tuple["Engine", dict]:
        """Il motore su cui gira l'esplorazione dell'analisi: il modello con le istanze multiple, se ce ne sono."""
        expanded, labels = expand(self.model, self.t)
        return (Engine(expanded, self.t.lang) if labels else self), labels

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
                # solo le guardie obbligatorie: un'alternativa (OR) non è un controllo che si possa saltare
                required = set.intersection(*({self._guard_sig(g) for g in t["guards"] if not g.get("alt_group")}
                                              for t in incoming)) if incoming else set()
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
        issues += self._cross_aggregate()
        for x in self.expr_errors:
            where = self.ename(x["entity_id"]) if x.get("entity_id") else x.get("invariant_id")
            add("error", "expressions", "a.bad_expr", params={"text": x["text"] or "—", "where": where, "error": x["error"]},
                **{k: x[k] for k in ("transition_id", "entity_id", "invariant_id") if x.get(k) is not None})

        # --- analisi dinamica (esplorazione), sul modello con le istanze multiple ---
        dyn, labels = self.dynamic()
        ex = dyn.explore(limit=limit)
        for v in ex["violations"]:
            add("error" if v["severity"] == "error" else "warning", "invariants", "a.violable",
                detail=v["description"] or None, params={"name": v["name"]},
                invariant_id=v["id"], state=v["state"], trace=v["trace"])
        base = lambda eid: labels.get(eid, {}).get("base", eid)  # dalle copie #2, #3 all'entità del modello
        # cascate interrotte dal limite di profondità: non sono errori del modello ma parti non verificate (certificato)
        # e messaggi che non partono perché la coda è piena: anche quello è un limite del modello, non un errore
        cut, full = {}, {}
        for c in ex["conflicts"]:
            if c["conflict"].get("limit"):
                t = dyn.transition[c["transition_id"]]
                (full if c["conflict"]["limit"] == "queue" else cut).setdefault(
                    t.get("base_id", t["id"]), {"action": t["action"], "entity": self.ename(base(t["entity_id"]))})
        for c in ex["conflicts"]:
            if c["conflict"].get("limit"):
                continue
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
            why = dyn._explain_deadlock(d["state"], d["trace"])
            if why["fix"]:
                why["fix"]["entity_id"] = base(why["fix"]["entity_id"])
            stuck = [x["entity_id"] for x in why["stuck"]]
            first = why["stuck"][0]
            key = "a.deadlock" if len(stuck) == 1 else "a.deadlock_many"
            add("error", "deadlock", key, detail=why["detail"],
                params={"entity": dyn.ename(first["entity_id"]), "state": dyn.sname(first["state"]),
                        "stuck": ", ".join(f"{dyn.ename(x['entity_id'])} ({dyn.sname(x['state'])})" for x in why["stuck"])},
                state=d["state"], trace=d["trace"], entity_ids=[base(x) for x in stuck], deadlock=why)

        reached_states = {sid for k in ex["reachable"] for sid in k[:dyn._ent_n]}
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
                # il simulatore ha un'istanza per entità: i passi sulle copie #2, #3 (id negativi) non si riproducono
                i["replayable"] = all(s.get("message") is not None or (s["transition_id"] or 0) > 0 for s in i["trace"])
            # entità a cui la segnalazione si riferisce: la scheda di ciascuna la mostra
            inv = inv_of(i) if i.get("invariant_id") is not None else None
            ids = list(i.get("entity_ids", [])) + ([i["entity_id"]] if i.get("entity_id") is not None else [])
            ids += [r["entity_id"] for r in inv["rules"]] if inv else []
            ids += relation_ends.get(i.get("relation_id"), [])
            i["entity_ids"] = sorted({base(x) for x in ids if x is not None})
            i["fixes"] = fixes_for(self, i, inv=inv, base=base, tbase=tbase)
            i["fingerprint"] = self._fingerprint(i, base, tbase)
            i["context"] = self._context_hash(i["entity_ids"], inv)
            i["cause"] = self._cause(i, inv, base, tbase)
        # un'azione mai eseguibile perché va sempre in conflitto ha la causa di quei conflitti
        by_trigger = {i["transition_id"]: i["cause"] for i in issues if i["category"] == "effect_conflict"}
        for i in issues:
            if i["category"] == "dead_actions" and i.get("transition_id") in by_trigger:
                i["cause"] = by_trigger[i["transition_id"]]
        order = {"error": 0, "warning": 1, "info": 2}
        issues.sort(key=lambda i: order.get(i["severity"], 3))
        return {
            "issues": issues,
            "stats": {
                "entities": len(self.entities),
                "states": len(self.state),
                "transitions": len(self.transitions),
                "reachable": ex.get("states", len(ex["reachable"])),
                "explored": len(ex["reachable"]),
                "symmetry": [{"entities": [self.ename(b) for b in s["bases"]], "instances": s["instances"]}
                             for s in ex.get("symmetry") or ()],
                "theoretical": _product(len(dyn.states_of(e["id"])) for e in dyn.entities),
                "truncated": ex["truncated"],
                "instances": {str(v["base"]): v["index"] for v in labels.values() if v["index"] > 1},
            },
            "certificate": self._certificate(ex, dyn, labels, list(cut.values()), issues, limit, list(full.values())),
            "labels": {str(k): v for k, v in labels.items()},
        }

    def aggregate_of(self, eid) -> int:
        """La radice dell'aggregato di un'entità: si risale lungo le composizioni (il figlio sta nell'aggregato del
        padre); un'entità senza padre è radice di se stessa."""
        up = {}
        for r in compositions(self.model):
            if r["source_id"] != r["target_id"]:
                up.setdefault(r["target_id"], r["source_id"])
        seen = set()
        while eid in up and eid not in seen:
            seen.add(eid)
            eid = up[eid]
        return eid

    def _cross_aggregate(self) -> list:
        """Azioni la cui cascata cambia più aggregati nello stesso passo: l'esecuzione atomica nasconde una
        transazione distribuita. Avviso architetturale (si può accettare con una motivazione), una volta per azione
        eseguibile dall'utente; le azioni solo in cascata sono già dentro quella che le innesca."""
        out = []
        for t in self.transitions:
            if t.get("cascade_only") or t["entity_id"] not in self.order:
                continue
            touched, seen, stack = {t["entity_id"]}, set(), list(t["effects"])
            while stack:
                f = stack.pop()
                k = (f["entity_id"], f.get("kind") or "state", f.get("to_state_id"), (f.get("action") or "").strip().lower())
                # un effetto asincrono è un evento: la reazione avviene in un altro passo, non nella stessa transazione
                if k in seen or f["entity_id"] not in self.entity or f.get("deferred"):
                    continue
                seen.add(k)
                touched.add(f["entity_id"])
                for x in self._effect_targets(f):
                    stack.extend(x["effects"])
            roots = sorted({self.aggregate_of(e) for e in touched}, key=self.order.index)
            if len(roots) < 2:
                continue
            contexts = sorted({self.entity[r].get("context") or "" for r in roots} - {""})
            q = lambda x: self.t("q", x=x)
            out.append({"severity": "warning", "category": "architecture", "code": "a.cross_aggregate",
                        "title": self.t("a.cross_aggregate.title", action=t["action"], entity=self.ename(t["entity_id"]), n=len(roots)),
                        "detail": self.t("a.cross_aggregate.detail", list=", ".join(self.ename(r) for r in roots)) +
                                  (" " + self.t("a.cross_aggregate.contexts", list=", ".join(contexts)) if len(contexts) > 1 else ""),
                        "entity_id": t["entity_id"], "transition_id": t["id"], "entity_ids": sorted(touched),
                        "aggregates": [self.ename(r) for r in roots]})
        return out

    def headline(self, verdict: str, errors: int) -> str:
        """La frase del certificato: il verdetto e quanti errori."""
        if verdict == "unverifiable":
            return self.t("cert.verdict.unverifiable")
        return self.t(f"cert.verdict.{verdict}") + " " + (self.t("cert.result.errors", n=errors) if errors else self.t("cert.result.clean"))

    def _certificate(self, ex: dict, dyn: "Engine", labels: dict, cut: list, issues: list, limit: int, full: list = ()) -> dict:
        """Cosa dimostra davvero l'analisi. verdict: complete (tutti gli stati del modello limitato esplorati),
        incomplete (esplorazione fermata, o cascate non seguite fino in fondo), unverifiable (il modello non ha un
        punto di partenza). rows: le dimensioni del modello limitato e le assunzioni, ognuna con lo stato
        (ok: verificato; limit: limite dell'analisi; assumption: non modellato, da tenere presente)."""
        T = self.t
        no_start = [i for i in issues if i.get("code") == "a.no_start"]
        with_states = [e for e in self.entities if e["states"]]
        rules = [{"entity": self.ename(t["entity_id"]), "action": t["action"], "rule": g["description"]}
                 for t in self.transitions for g in t["guards"] if g["kind"] == "rule" and g["description"]]
        stopped = ex.get("stopped") or ("states" if ex["truncated"] else None)
        if not with_states or no_start:
            verdict = "unverifiable"
        elif stopped or cut:
            verdict = "incomplete"
        else:
            verdict = "complete"
        errors = sum(1 for i in issues if i["severity"] == "error")
        copies = {}
        for v in labels.values():
            copies[v["base"]] = max(copies.get(v["base"], 1), v["index"])
        rows = [{"key": "exploration", "status": "limit" if stopped else "ok",
                 "value": T(f"cert.exploration.{stopped or 'done'}", states=ex.get("states", len(ex["reachable"])),
                            explored=len(ex["reachable"]), limit=limit, seconds=round(ex.get("seconds", 0), 1))},
                {"key": "instances", "status": "assumption",
                 "value": T("cert.instances.many", list=", ".join(f"{self.ename(b)} {n}" for b, n in copies.items()))
                 if copies else T("cert.instances.one")}]
        if dyn.messages and dyn.model.get("_tree"):
            rows.append({"key": "symmetry", "status": "assumption", "value": T("cert.symmetry.messages")})
        elif ex.get("symmetry") or dyn._sym_refused:
            used = "; ".join(f"{', '.join(self.ename(b) for b in s['bases'])} ×{s['instances']}" for s in ex.get("symmetry") or ())
            refused = "; ".join(T(f"cert.symmetry.why_{r['reason']}", entities=", ".join(self.ename(b) for b in r["bases"]))
                                for r in dyn._sym_refused)
            rows.append({"key": "symmetry", "status": "ok",
                         "value": " ".join(x for x in (used and T("cert.symmetry.used", list=used),
                                                       refused and T("cert.symmetry.refused", list=refused)) if x)})
        rows.append({"key": "cascades", "status": "limit" if cut else "ok",
                     "value": T("cert.cascades.cut", depth=MAX_CASCADE_DEPTH, n=len(cut),
                                list=", ".join(f"«{c['action']}» ({c['entity']})" for c in cut))
                     if cut else T("cert.cascades.ok", depth=MAX_CASCADE_DEPTH)})
        rows.append({"key": "rules", "status": "assumption" if rules else "ok",
                     "value": T("cert.rules.some", n=len(rules)) if rules else T("cert.rules.none")})
        attrs = [a for e in self.entities for a in e.get("attributes") or ()]
        if attrs:
            verified = [k for k in self.vars]
            rows.append({"key": "attributes", "status": "ok" if len(verified) == len(attrs) and not self.expr_errors else "assumption",
                         "value": T("cert.attributes", verified=len(verified), total=len(attrs),
                                    values=", ".join(f"{self._var_name(k)} {self.vars[k][2][0]}…{self.vars[k][2][-1]}"
                                                     for k in verified[:6]) or "—")
                         + (" " + T("cert.attributes_errors", n=len(self.expr_errors)) if self.expr_errors else "")})
        rows.append({"key": "time", "status": "assumption", "value": T("cert.time")})
        if self.messages:
            rows.append({"key": "messages", "status": "limit" if full else "ok",
                         "value": T("cert.messages_async", n=len(self.messages), max=self.max_in_flight,
                                    delivery=T(f"cert.delivery.{self.delivery}"))
                         + (" " + T("cert.messages_full", list=", ".join(f"«{c['action']}» ({c['entity']})" for c in full))
                            if full else "")})
        else:
            rows.append({"key": "messages", "status": "assumption", "value": T("cert.messages")})
        mode = "symmetry" if ex.get("symmetry") else "full"
        return {"verdict": verdict, "headline": self.headline(verdict, errors), "rows": rows, "cut": cut, "rules": rules,
                "mode": mode, "observations": [{"key": k, "value": T(f"cert.obs.{mode}.{k}")}
                                               for k in ("invariants", "reachability", "paths", "matrix")]}

    def _fingerprint(self, i: dict, base, tbase) -> str:
        """Impronta stabile di una segnalazione: non dipende da lingua, testi o istanze #1/#2
        (le copie dello stesso caso hanno la stessa impronta, e si accettano insieme)."""
        c = i.get("conflict") or {}
        gap = c.get("gap") or i.get("gap") or {}
        parts = [i["category"], i.get("code"), i.get("entity_id") and base(i["entity_id"]),
                 i.get("transition_id") and tbase(i["transition_id"]), i.get("state_id"), i.get("relation_id"),
                 str(i.get("invariant_id")).split("#")[0] if i.get("invariant_id") is not None else None,
                 c.get("trigger_id") and tbase(c["trigger_id"]), c.get("entity_id") and base(c["entity_id"]),
                 c.get("effect_id"), gap.get("from"), gap.get("to"), (gap.get("action") or "").lower() or None,
                 sorted({m["guard_id"] for m in c.get("misses") or ()})]
        if i["category"] == "deadlock":
            # solo gli stati delle entità (gli attributi sono altre posizioni dello stato)
            parts.append(sorted({(base(e), s) for e, s in i["state"].items() if not isinstance(e, str) and s != NONE}))
        if i["category"] == "relations":
            parts.append(i.get("entity_ids"))
        return hashlib.sha1(json.dumps(parts, default=str).encode()).hexdigest()[:16]

    def _cause(self, i: dict, inv: dict | None, base, tbase) -> dict:
        """La causa di una segnalazione: segnalazioni con la stessa causa si risolvono insieme.
        {"key", "title"}; senza una causa comune riconoscibile, la segnalazione è causa di se stessa."""
        T, q = self.t, lambda x: self.t("q", x=x)
        c = i.get("conflict") or {}
        gap = c.get("gap") or (i.get("gap") if i["category"] == "missing_transition" else None)
        child = base(c["entity_id"]) if c.get("entity_id") is not None else i.get("entity_id")
        if gap and self.is_final(gap["from"]) and c.get("effect_id") is not None:
            trig = self.transition.get(tbase(c["trigger_id"]))
            return {"key": f"skip:{c['effect_id']}",
                    "title": T("c.skip_final", action=trig["action"] if trig else "?",
                               parent=self.ename(trig["entity_id"]) if trig else "?", child=self.ename(child))}
        if gap:
            if gap.get("action"):
                return {"key": f"gap:{child}:{gap['from']}:a:{gap['action'].lower()}",
                        "title": T("c.gap_action", entity=self.ename(child), action=gap["action"], src=self.sname(gap["from"]))}
            return {"key": f"gap:{child}:{gap['from']}:{gap['to']}",
                    "title": T("c.gap", entity=self.ename(child), src=self.sname(gap["from"]), dst=self.sname(gap["to"]))}
        if c.get("misses"):
            m = c["misses"][0]
            owner = self.transition.get(m["transition_id"])
            return {"key": "guard:" + ",".join(str(x) for x in sorted({m["guard_id"] for m in c["misses"]})),
                    "title": T("c.guard", action=owner["action"] if owner else "?", entity=self.ename(owner["entity_id"]) if owner else "?")}
        rel_id = i.get("relation_id") or (inv or {}).get("relation_id")
        if rel_id is not None and (i["category"] == "composition" or (inv or {}).get("derived")):
            r = next((r for r in self.model.get("relations", []) if r["id"] == rel_id), None)
            if r:
                return {"key": f"relation:{rel_id}",
                        "title": T("c.relation", parent=self.ename(r["source_id"]), child=self.ename(r["target_id"]))}
        if i["category"] == "deadlock":
            # deadlock con la stessa causa (la stessa azione che lascia l'entità bloccata, la stessa guardia, lo stesso
            # stato senza uscita) si risolvono insieme
            w = (i.get("deadlock") or {}).get("why") or {}
            if w.get("conflict"):
                c = self._cause({"category": "effect_conflict", "conflict": w["conflict"], "fingerprint": "", "title": ""},
                                None, base, tbase)
                if not c["key"].startswith("self:"):
                    return c
            eid, sid = base(w["entity_id"]), w["state_id"]
            by = self.transition.get(tbase(w["by"])) if w.get("kind") == "by" and w.get("by") else None
            owner = self.transition.get(w.get("transition_id"))
            p = {"entity": self.ename(eid), "state": self.sname(sid)}
            if by:
                return {"key": f"deadlock:by:{by['id']}:{eid}:{sid}",
                        "title": T("c.deadlock_by", by=by["action"], owner=self.ename(by["entity_id"]), **p)}
            if w.get("kind") == "guard" and owner and w.get("guard_id") is not None:
                return {"key": f"deadlock:guard:{w['guard_id']}", "title": T("c.deadlock_guard", action=owner["action"], **p)}
            if w.get("kind") == "no_exit":
                return {"key": f"deadlock:exit:{sid}", "title": T("c.deadlock_exit", **p)}
            return {"key": "deadlock", "title": T("c.deadlock")}
        if inv is not None:
            return {"key": f"invariant:{inv['id']}", "title": i["title"].split(" (")[0]}
        return {"key": f"self:{i['fingerprint']}", "title": i["title"]}

    def _context_hash(self, entity_ids: list, inv: dict | None) -> str:
        """Impronta della parte di modello coinvolta: se cambia, una segnalazione accettata va rivista."""
        ids = set(entity_ids)
        part = [self.entity[e] for e in sorted(ids) if e in self.entity]
        part += [r for r in self.model.get("relations", []) if r["source_id"] in ids or r["target_id"] in ids]
        part += [inv] if inv else []
        return hashlib.sha1(json.dumps(part, sort_keys=True, default=str).encode()).hexdigest()[:16]

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

    def effect_sources(self, queries: list[dict]) -> list[list[int]]:
        """Per ogni {owner_id, from, entity_id}: gli stati non finali in cui entity_id si trova nelle combinazioni
        raggiungibili in cui owner_id è nello stato di partenza `from` (0 = sta per nascere, None = qualsiasi).
        Sono le partenze proposte per un effetto "porta in stato": gli stati che l'effetto incontrerà davvero."""
        dyn, labels = self.dynamic()
        reachable = dyn.explore()["reachable"]
        base = lambda x: labels.get(x, {}).get("base", x)
        out = []
        for q in queries:
            own = [i for i, x in enumerate(dyn.order) if base(x) == q["owner_id"]]
            kid = [i for i, x in enumerate(dyn.order) if base(x) == q["entity_id"]]
            f = q.get("from")
            ok = (lambda s: s == NONE) if f == 0 else (lambda s: s != NONE) if f is None else (lambda s: s == f)
            seen = set()
            for k in reachable:
                if any(ok(k[i]) for i in own):
                    seen.update(k[j] for j in kid)
            out.append([s["id"] for s in self.states_of(q["entity_id"])
                        if s["id"] in seen and s["id"] != NONE and not self.is_final(s["id"])])
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

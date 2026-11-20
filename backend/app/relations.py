"""Relazioni nel motore: composizione, istanze multiple e coerenza relazioni/regole.

- Vita di un'entità: ogni stato è "active" (esiste e opera), "off" (spento: esiste ma è disattivato,
  si può riaccendere) o "gone" (eliminato: non c'è più, non si torna indietro). NON_ESISTE conta come gone.
- Composizione: una relazione source → target con composition=1 dice che il figlio (target)
  non esiste senza il padre (source). Ne derivano due invarianti, "figlio non eliminato con padre
  eliminato" e "figlio attivo con padre spento", e dei controlli statici su creazioni, eliminazioni
  e spegnimenti.
- Istanze multiple: sulla composizione si può chiedere di simulare 2-3 figli per padre.
  `expand` duplica l'entità figlia nel modello usato dall'analisi; il simulatore resta a un'istanza.
"""
import copy
import re

from .expr import ExprError
from .expr import parse as parse_expr
from .i18n import translator

NONE = 0
# proposta per i nuovi stati in base al nome (it/en): spento si recupera, eliminato no
OFF_WORDS = re.compile(r"DISABILIT|DISATTIV|SOSPES|INATTIV|CESTIN|ARCHIV|DISABLED|INACTIVE|SUSPEND|PAUSED|TRASH", re.I)
GONE_WORDS = re.compile(r"ELIMIN|CANCEL|ANNULL|STORN|RIMOSS|DELET|REMOV|VOID|REVERS|WITHDRAW", re.I)
# formato 1 (stati "assente" sulle relazioni): parole usate allora, solo per convertire i dati vecchi
LEGACY_GONE_WORDS = re.compile(r"ELIMIN|CESTIN|CANCEL|ANNULL|STORN|RIMOSS|ARCHIV|DELET|REMOV|TRASH|VOID|REVERS|WITHDRAW", re.I)


def suggest_liveness(name: str) -> str:
    return "off" if OFF_WORDS.search(name) else "gone" if GONE_WORDS.search(name) else "active"


def is_create(t: dict) -> bool:
    return t.get("kind") == "create"


def compositions(model: dict) -> list[dict]:
    return [r for r in model.get("relations", []) if r.get("composition")]


LIVENESS = ("active", "off", "gone")


def gone_states(e: dict) -> list[int]:
    """Stati in cui l'entità è eliminata: NON_ESISTE più quelli marcati "gone" nella sua scheda."""
    return [NONE] + [s["id"] for s in e["states"] if s.get("liveness") == "gone"]


def off_states(e: dict) -> list[int]:
    """Stati in cui l'entità è spenta: esiste ancora, ma è disattivata."""
    return [s["id"] for s in e["states"] if s.get("liveness") == "off"]


def active_states(e: dict) -> list[int]:
    return [s["id"] for s in e["states"] if s.get("liveness", "active") == "active"]


def looks_gone(state: dict) -> bool:
    """Proposta dal nome: uno stato finale come ELIMINATA o DELETED di solito vuol dire "non c'è più"."""
    return bool(state.get("is_final")) and bool(LEGACY_GONE_WORDS.search(state["name"]))


def legacy_gone_states(model: dict) -> set[int]:
    """Formato precedente, con gli stati "assente" scelti su ogni relazione: unione per entità.

    Serve alla migrazione del database e all'import di JSON vecchi.
    """
    ent = {e["id"]: e for e in model["entities"]}
    out = set()
    for r in compositions(model):
        out |= set(r.get("parent_gone_states") or [NONE])
        child = ent.get(r["target_id"])
        if r.get("child_gone_states"):
            out |= set(r["child_gone_states"])
        elif child:
            out |= {s["id"] for s in child["states"] if looks_gone(s)}
    return out - {NONE}


def derived_invariants(model: dict, t=None) -> list[dict]:
    """Per composizione: vietato un figlio non eliminato con il padre eliminato, e un figlio attivo con il padre spento."""
    t = t or translator()
    ent = {e["id"]: e for e in model["entities"]}
    out = []
    for r in compositions(model):
        parent, child = ent.get(r["source_id"]), ent.get(r["target_id"])
        if not parent or not child or not child["states"]:
            continue
        names = {"child": child["name"], "parent": parent["name"]}
        not_gone = [s["id"] for s in child["states"] if s["id"] not in gone_states(child)]
        if not_gone:
            out.append({
                "id": f"rel{r['id']}", "derived": True, "relation_id": r["id"], "kind": "gone", "severity": "error",
                "name": t("r.derived.name", **names), "description": t("r.derived.desc", **names),
                "rules": [{"entity_id": parent["id"], "state_ids": gone_states(parent), "negate": 0},
                          {"entity_id": child["id"], "state_ids": not_gone, "negate": 0}],
            })
        if off_states(parent) and active_states(child):
            out.append({
                "id": f"rel{r['id']}off", "derived": True, "relation_id": r["id"], "kind": "off", "severity": "error",
                "name": t("r.derived_off.name", **names), "description": t("r.derived_off.desc", **names),
                "rules": [{"entity_id": parent["id"], "state_ids": off_states(parent), "negate": 0},
                          {"entity_id": child["id"], "state_ids": active_states(child), "negate": 0}],
            })
    return out


# ---------------------------------------------------------------- istanze multiple
MAX_INSTANCES = 5  # figli simulati per ogni padre, al massimo
_VID = 1000  # le copie oltre la prima hanno id -(id * _VID + k): fino a 999 copie per entità


def _vid(base_id: int, k: int) -> int:
    """Id virtuale della copia k (>=2); la copia 1 mantiene l'id reale."""
    return base_id if k == 1 else -(base_id * _VID + k)


def vbase(eid: int) -> int:
    """L'entità del modello di una copia (anche della prima, che ha l'id reale)."""
    return -eid // _VID if eid < 0 else eid


def composition_tree(model: dict) -> tuple[dict, dict]:
    """(up, anc): per ogni figlio il padre "principale" con il numero di istanze per padre (tra più composizioni
    verso lo stesso figlio vale quella con più istanze), e per ogni entità la catena degli antenati, dalla radice
    a se stessa. Un ciclo di composizioni si interrompe: l'entità che lo chiuderebbe resta radice."""
    up = {}
    for r in compositions(model):
        c, p = r["target_id"], r["source_id"]
        n = max(1, min(MAX_INSTANCES, int(r.get("instances") or 1)))
        if c != p and (c not in up or n > up[c][1]):
            up[c] = (p, n)
    anc = {}

    def chain(e, seen=()):
        if e not in anc:
            p = up.get(e)
            anc[e] = (chain(p[0], (*seen, e)) if p and p[0] not in seen and p[0] != e else []) + [e]
            if p and p[0] in seen:
                up.pop(e, None)
        return anc[e]

    for e in model["entities"]:
        chain(e["id"])
    return up, anc


def expand(model: dict, tr=None) -> tuple[dict, dict]:
    """Duplica i figli delle composizioni con instances > 1, per ogni padre: con 2 copie del padre e 3 figli per
    padre, i figli sono 6. Le copie formano un albero: ognuna ha un percorso, gli indici lungo la catena delle
    composizioni (la radice ha (1,)).

    Ritorna (modello espanso, etichette {id_copia: {"name", "base", "index", "path"}}).
    Un riferimento da una copia X verso un'entità Y (guardia, effetto, invariante) vale per le copie di Y che
    hanno la stessa copia dell'antenato comune più profondo: dal figlio al padre la sua copia, dal padre ai figli
    i suoi, tra fratelli quelli dello stesso padre; senza antenati comuni, tutte le copie. Più copie in una
    guardia vogliono dire "tutte le istanze esistenti"; un effetto le tocca tutte.
    Invarianti: una per ogni combinazione di copie coerente (stessi antenati comuni) delle entità con una
    condizione di presenza; una condizione di assenza (insieme che include NON_ESISTE) vale per tutte le copie
    coerenti con la combinazione ("nessuna riga c'è").
    """
    tr = tr or translator()
    up, anc = composition_tree(model)
    paths = {}

    def copies(e):
        if e not in paths:
            p = up.get(e)
            paths[e] = [(1,)] if not p else [pp + (j,) for pp in copies(p[0]) for j in range(1, p[1] + 1)]
        return paths[e]

    for e in model["entities"]:
        copies(e["id"])
    if all(len(v) == 1 for v in paths.values()):
        return model, {}

    m = copy.deepcopy(model)
    labels, where = {}, {}  # where: id copia -> (entità, percorso)
    ids = {}  # (entità, percorso) -> id copia
    entities = []
    for e in m["entities"]:
        ps = paths[e["id"]]
        if len(ps) == 1:
            entities.append(e)
            where[e["id"]] = (e["id"], ps[0])
            ids[(e["id"], ps[0])] = e["id"]
            continue
        base_name = e["name"]
        for k, path in enumerate(ps, 1):
            c = e if k == 1 else copy.deepcopy(e)
            c["id"] = _vid(e["id"], k)
            c["name"] = f"{base_name} #{k}"
            for t in c["transitions"]:
                t["entity_id"] = c["id"]
                t.setdefault("base_id", t["id"])
                if k > 1:
                    t["id"] = -(t["id"] * _VID + k)
            labels[c["id"]] = {"name": c["name"], "base": e["id"], "index": k, "path": list(path)}
            where[c["id"]] = (e["id"], path)
            ids[(e["id"], path)] = c["id"]
            entities.append(c)
    m["entities"] = entities

    def common(a, b):
        """Quanti antenati hanno in comune le entità a e b (prefisso delle loro catene)."""
        n = 0
        for x, y in zip(anc[a], anc[b]):
            if x != y:
                break
            n += 1
        return n

    def targets(ctx, ref):
        """Copie di ref a cui punta un riferimento fatto dalla copia ctx."""
        e, path = where[ctx]
        d = common(e, ref)
        return [ids[(ref, p)] for p in paths[ref] if p[:d] == path[:d]]

    def coherent(a, b):
        (ea, pa), (eb, pb) = where[a], where[b]
        d = common(ea, eb)
        return pa[:d] == pb[:d]

    original = {e["id"]: e for e in model["entities"]}
    name_ids = {e["name"]: e["id"] for e in model["entities"]}
    attr_ids = {(e["id"], a["name"]): a["id"] for e in model["entities"] for a in e.get("attributes") or ()}

    def expr_refs(text, ctx, chosen=None):
        """A quali variabili va ogni riferimento di un'espressione scritta nel contesto della copia ctx (o, per le
        invarianti, con le copie scelte in chosen): {"Entità|attributo": [chiavi]}. Riferimenti sconosciuti si
        lasciano fuori: li segnala il motore."""
        try:
            _, refs = parse_expr(text)
        except ExprError:
            return {}
        out = {}
        for ent, attr in refs:
            base = (where[ctx][0] if ctx is not None else None) if ent is None else name_ids.get(ent)
            aid = attr_ids.get((base, attr))
            if base is None or aid is None:
                continue
            copies_ = [chosen[base]] if chosen and base in chosen else targets(ctx, base) if ctx is not None \
                else [ids[(base, p)] for p in paths[base]]
            out[f"{ent or ''}|{attr}"] = [f"{c}.{aid}" for c in copies_]
        return out

    for e in m["entities"]:
        # padri in composizione di ogni copia (per i figli "congelati" da un padre concluso)
        e["_parents"] = [x for r in compositions(model) if r["target_id"] == where[e["id"]][0] and r["source_id"] != r["target_id"]
                         for x in targets(e["id"], r["source_id"])]
        for t in e["transitions"]:
            for g in t["guards"]:
                if g.get("kind") == "expr":
                    g["_refs"] = expr_refs(g.get("description") or "", e["id"])
                    continue
                parent = original.get(g.get("each_entity_id")) if g.get("kind") == "state" else None
                if parent and parent["states"] and g.get("entity_id") is not None:
                    # "per ogni padre": ogni copia del padre con i suoi figli
                    g["each"] = [[pv, gone_states(parent), targets(pv, g["entity_id"])] for pv in targets(e["id"], parent["id"])]
                    g["entity_id"] = g["each"][0][2][0]
                    continue
                if g.get("kind") == "state" and g.get("entity_id") in paths:
                    found = targets(e["id"], g["entity_id"])
                    g["entity_id"] = found[0]
                    if len(found) > 1:
                        g["entity_ids"] = found  # "tutte le istanze esistenti"
            t["effects"] = [{**f, "entity_id": vid, **({"_refs": expr_refs(f.get("expr") or "", e["id"])} if f.get("kind") == "set" else {})}
                            for f in t["effects"] for vid in targets(e["id"], f["entity_id"])]

    def matches_absence(r):
        return (NONE not in r["state_ids"]) if r.get("negate") else (NONE in r["state_ids"])

    invs = []
    for inv in list(m["invariants"]) + derived_invariants(model, tr):
        # entità citate dalla condizione sugli attributi: anche loro scelgono una copia per combinazione
        try:
            in_expr = [name_ids.get(ent) for ent, _ in parse_expr(inv["expr"])[1]] if (inv.get("expr") or "").strip() else []
        except ExprError:
            in_expr = []
        in_expr = [x for x in in_expr if x is not None]
        if all(len(paths.get(r["entity_id"], [()])) == 1 for r in inv["rules"]) and all(len(paths[x]) == 1 for x in in_expr):
            invs.append({**inv, "_refs": expr_refs(inv.get("expr") or "", None)} if in_expr else inv)
            continue
        present = list(dict.fromkeys([r["entity_id"] for r in inv["rules"] if not matches_absence(r)] + in_expr))
        combos = [()]
        for y in present:
            combos = [c + (ids[(y, p)],) for c in combos for p in paths[y] if all(coherent(x, ids[(y, p)]) for x in c)]
        for i, combo in enumerate(combos, 1):
            chosen = dict(zip(present, combo))

            def rewrite(r):
                y = r["entity_id"]
                if len(paths[y]) == 1:
                    return r
                if y in chosen and not matches_absence(r):
                    return {**r, "entity_id": chosen[y]}
                every = [ids[(y, p)] for p in paths[y] if all(coherent(x, ids[(y, p)]) for x in combo)]
                return {**r, "entity_id": every[0], "entity_ids": every}
            invs.append({**inv, "id": f"{inv['id']}#{i}" if len(combos) > 1 else inv["id"],
                         "name": tr("r.instance_inv", name=inv["name"], i=i) if len(combos) > 1 else inv["name"],
                         "rules": [rewrite(r) for r in inv["rules"]],
                         "_refs": expr_refs(inv.get("expr") or "", None, chosen)})
    m["invariants"] = invs
    m["_derived_done"] = True
    m["_tree"] = {"up": {str(c): list(v) for c, v in up.items()}, "anc": {str(e): v for e, v in anc.items()},
                  "copies": {str(c): [where[c][0], list(where[c][1])] for c in where}}
    return m, labels


# ---------------------------------------------------------------- controlli statici
def relation_issues(model: dict, sname, t=None) -> list[dict]:
    t = t or translator()
    ent = {e["id"]: e for e in model["entities"]}
    issues = []

    def add(sev, cat, title, detail="", **extra):
        issues.append({"severity": sev, "category": cat, "title": title, "detail": detail, **extra})

    def requires_parent(g, parent_id):
        """La guardia esclude che il padre non esista."""
        if g.get("kind") != "state" or g.get("entity_id") != parent_id:
            return False
        return (NONE in g["state_ids"]) if g["negate"] else (NONE not in g["state_ids"] and bool(g["state_ids"]))

    # --- N-N: il legame ha quasi sempre un suo ciclo di vita, quindi va modellato come entità di join ---
    for r in model.get("relations", []):
        a, b = ent.get(r["source_id"]), ent.get(r["target_id"])
        if r.get("cardinality") == "N-N" and a and b:
            add("error", "relations", t("r.many_to_many.title", a=a["name"], b=b["name"]),
                t("r.many_to_many.detail", a=a["name"], b=b["name"]), relation_id=r["id"],
                entity_ids=[a["id"], b["id"]], code="r.many_to_many")

    # --- composizioni ---
    for r in compositions(model):
        parent, child = ent.get(r["source_id"]), ent.get(r["target_id"])
        if not parent or not child:
            continue
        P, C = parent["name"], child["name"]
        creations = [t for t in child["transitions"] if is_create(t)]
        if not creations:
            add("warning", "composition", t("r.always_exists.title", child=C, parent=P),
                t("r.always_exists.detail", child=C, parent=P), entity_id=child["id"], relation_id=r["id"],
                code="r.always_exists")
        for c in creations:
            ok = any(requires_parent(g, parent["id"]) for g in c["guards"] if not g.get("alt_group"))
            if not ok and c.get("cascade_only"):
                # nasce solo in cascata: va bene se a innescarla sono soltanto azioni del padre
                srcs = [x for e in model["entities"] for x in e["transitions"]
                        if any(f["entity_id"] == child["id"] and f.get("kind") == "create" for f in x["effects"])]
                ok = bool(srcs) and all(x["entity_id"] == parent["id"] for x in srcs)
            if not ok:
                add("warning", "composition", t("r.create_without_parent.title", action=c["action"], child=C, parent=P),
                    t("r.create_without_parent.detail", child=C, parent=P),
                    entity_id=child["id"], transition_id=c["id"], relation_id=r["id"], code="r.create_without_parent")
        # le azioni che eliminano o spengono il padre devono occuparsi dei figli
        gone, off = set(gone_states(parent)) - {NONE}, set(off_states(parent))
        for x in parent["transitions"]:
            key = "r.parent_leaves" if x["to_state_id"] in gone else "r.parent_off" if x["to_state_id"] in off else None
            if is_create(x) or not key or any(f["entity_id"] == child["id"] for f in x["effects"]):
                continue
            st = sname(x["to_state_id"])
            add("warning", "composition", t(f"{key}.title", action=x["action"], parent=P, state=st, child=C),
                t(f"{key}.detail", state=st, child=C, parent=P),
                entity_id=parent["id"], transition_id=x["id"], relation_id=r["id"], code=key)

    # --- coerenza tra relazioni e regole ---
    linked = {}  # coppia non ordinata -> tipo di regola che lega le due entità
    for e in model["entities"]:
        for x in e["transitions"]:
            for g in x["guards"]:
                if g.get("kind") == "state" and g.get("entity_id") and g["entity_id"] != e["id"]:
                    linked[frozenset((e["id"], g["entity_id"]))] = "guard"
            for f in x["effects"]:
                if f["entity_id"] != e["id"]:
                    linked[frozenset((e["id"], f["entity_id"]))] = "effect"
    for inv in model["invariants"]:
        ids = {r["entity_id"] for r in inv["rules"]}
        for a in ids:
            for b in ids:
                if a != b:
                    linked.setdefault(frozenset((a, b)), "invariant")

    related = set()
    for r in model.get("relations", []):
        pair = frozenset((r["source_id"], r["target_id"]))
        related.add(pair)
        if r["source_id"] == r["target_id"] or pair in linked or r.get("composition"):
            continue
        a, b = ent.get(r["source_id"]), ent.get(r["target_id"])
        if a and b and a["states"] and b["states"]:
            add("info", "relations", t("r.no_rules.title", a=a["name"], b=b["name"]), t("r.no_rules.detail"),
                relation_id=r["id"], entity_ids=[a["id"], b["id"]])
    for pair, how in linked.items():
        if pair in related or len(pair) != 2:
            continue
        a, b = (ent.get(x) for x in pair)
        if a and b:
            add("info", "relations", t("r.undeclared.title", a=a["name"], b=b["name"]),
                t("r.undeclared.detail", how=t(f"link.{how}")), entity_ids=[a["id"], b["id"]])
    return issues

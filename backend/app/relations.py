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
                "id": f"rel{r['id']}", "derived": True, "severity": "error",
                "name": t("r.derived.name", **names), "description": t("r.derived.desc", **names),
                "rules": [{"entity_id": parent["id"], "state_ids": gone_states(parent), "negate": 0},
                          {"entity_id": child["id"], "state_ids": not_gone, "negate": 0}],
            })
        if off_states(parent) and active_states(child):
            out.append({
                "id": f"rel{r['id']}off", "derived": True, "severity": "error",
                "name": t("r.derived_off.name", **names), "description": t("r.derived_off.desc", **names),
                "rules": [{"entity_id": parent["id"], "state_ids": off_states(parent), "negate": 0},
                          {"entity_id": child["id"], "state_ids": active_states(child), "negate": 0}],
            })
    return out


# ---------------------------------------------------------------- istanze multiple
def _vid(base_id: int, i: int) -> int:
    """Id virtuale della copia i (>=2); la copia 1 mantiene l'id reale."""
    return base_id if i == 1 else -(base_id * 10 + i)


def expand(model: dict, tr=None) -> tuple[dict, dict]:
    """Duplica i figli delle composizioni con instances > 1.

    Ritorna (modello espanso, etichette {id_entità: {"name", "base"}}).
    Semantica dei riferimenti verso un'entità duplicata:
      - da una sua copia o da un'altra entità duplicata: si accoppia per indice (copia i ↔ copia i);
      - da un'entità singola: guardie su "tutte le istanze esistenti", effetti su tutte le istanze;
      - invarianti: una condizione di presenza ("lotto vivo") vale se vale per almeno un'istanza
        (si espande istanza per istanza), una condizione di assenza (insieme che include NON_ESISTE)
        vale solo se vale per tutte ("nessun lotto c'è").
    """
    tr = tr or translator()
    counts = {}
    for r in compositions(model):
        n = max(1, min(3, int(r.get("instances") or 1)))
        if n > 1:
            counts[r["target_id"]] = max(counts.get(r["target_id"], 1), n)
    if not counts:
        return model, {}

    m = copy.deepcopy(model)
    labels = {}
    owner_index = {}  # id entità (anche virtuale) -> indice istanza, solo per le duplicate
    entities = []
    for e in m["entities"]:
        k = counts.get(e["id"])
        if not k:
            entities.append(e)
            continue
        base_name = e["name"]
        for i in range(1, k + 1):
            c = e if i == 1 else copy.deepcopy(e)
            c["id"] = _vid(e["id"], i)
            c["name"] = f"{base_name} #{i}"
            for t in c["transitions"]:
                t["entity_id"] = c["id"]
                t.setdefault("base_id", t["id"])
                if i > 1:
                    t["id"] = -(t["id"] * 10 + i)
            owner_index[c["id"]] = i
            labels[c["id"]] = {"name": c["name"], "base": e["id"], "index": i}
            entities.append(c)
    m["entities"] = entities

    def targets(ref_id, ctx_index):
        """Copie a cui punta un riferimento verso ref_id, dato l'indice del contesto (o None)."""
        k = counts.get(ref_id)
        if not k:
            return [ref_id]
        if ctx_index and ctx_index <= k:
            return [_vid(ref_id, ctx_index)]
        return [_vid(ref_id, i) for i in range(1, k + 1)]

    for e in m["entities"]:
        ctx = owner_index.get(e["id"])
        for t in e["transitions"]:
            for g in t["guards"]:
                if g.get("kind") == "state" and g.get("entity_id") in counts:
                    ids = targets(g["entity_id"], ctx)
                    g["entity_id"] = ids[0]
                    if len(ids) > 1:
                        g["entity_ids"] = ids  # "tutte le istanze esistenti"
            effects = []
            for f in t["effects"]:
                for vid in targets(f["entity_id"], ctx):
                    effects.append({**f, "entity_id": vid})
            t["effects"] = effects

    def matches_absence(r):
        return (NONE not in r["state_ids"]) if r.get("negate") else (NONE in r["state_ids"])

    invs = []
    for inv in list(m["invariants"]) + derived_invariants(model, tr):
        multi = {r["entity_id"] for r in inv["rules"] if r["entity_id"] in counts}
        if not multi:
            invs.append(inv)
            continue
        # assenza su un'entità duplicata: deve valere per tutte le sue istanze
        def rewrite(r, i):
            eid = r["entity_id"]
            if eid not in counts:
                return r
            if matches_absence(r):
                return {**r, "entity_id": _vid(eid, 1), "entity_ids": [_vid(eid, j) for j in range(1, counts[eid] + 1)]}
            return {**r, "entity_id": _vid(eid, min(i, counts[eid]))}
        presence = {r["entity_id"] for r in inv["rules"] if r["entity_id"] in counts and not matches_absence(r)}
        n = max((counts[x] for x in presence), default=1)
        for i in range(1, n + 1):
            invs.append({**inv, "id": f"{inv['id']}#{i}" if n > 1 else inv["id"],
                         "name": tr("r.instance_inv", name=inv["name"], i=i) if n > 1 else inv["name"],
                         "rules": [rewrite(r, i) for r in inv["rules"]]})
    m["invariants"] = invs
    m["_derived_done"] = True
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

    # --- composizioni ---
    for r in compositions(model):
        parent, child = ent.get(r["source_id"]), ent.get(r["target_id"])
        if not parent or not child:
            continue
        P, C = parent["name"], child["name"]
        creations = [t for t in child["transitions"] if is_create(t)]
        if not creations:
            add("warning", "composition", t("r.always_exists.title", child=C, parent=P),
                t("r.always_exists.detail", child=C, parent=P), entity_id=child["id"], relation_id=r["id"])
        for c in creations:
            ok = any(requires_parent(g, parent["id"]) for g in c["guards"])
            if not ok and c.get("cascade_only"):
                # nasce solo in cascata: va bene se a innescarla sono soltanto azioni del padre
                srcs = [x for e in model["entities"] for x in e["transitions"]
                        if any(f["entity_id"] == child["id"] and f.get("kind") == "create" for f in x["effects"])]
                ok = bool(srcs) and all(x["entity_id"] == parent["id"] for x in srcs)
            if not ok:
                add("warning", "composition", t("r.create_without_parent.title", action=c["action"], child=C, parent=P),
                    t("r.create_without_parent.detail", child=C, parent=P),
                    entity_id=child["id"], transition_id=c["id"], relation_id=r["id"])
        # le azioni che eliminano o spengono il padre devono occuparsi dei figli
        gone, off = set(gone_states(parent)) - {NONE}, set(off_states(parent))
        for x in parent["transitions"]:
            key = "r.parent_leaves" if x["to_state_id"] in gone else "r.parent_off" if x["to_state_id"] in off else None
            if is_create(x) or not key or any(f["entity_id"] == child["id"] for f in x["effects"]):
                continue
            st = sname(x["to_state_id"])
            add("warning", "composition", t(f"{key}.title", action=x["action"], parent=P, state=st, child=C),
                t(f"{key}.detail", state=st, child=C, parent=P),
                entity_id=parent["id"], transition_id=x["id"], relation_id=r["id"])

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

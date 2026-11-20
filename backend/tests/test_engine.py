"""Motore: cascate, guardie, istanze multiple, aspettative, impronte delle segnalazioni."""
import pytest
from app.engine import NONE, Engine
from app.i18n import MESSAGES
from app.relations import expand


def test_cascade_needs_a_transition_on_the_child(model):
    model.entity("Order", ["OPEN", "CANCELLED"], initial="OPEN", final=["CANCELLED"])
    model.entity("Payment", ["NEW", "PAID", "VOID"], initial="NEW", final=["VOID"])
    model.transition("Payment", "NEW", "PAID", "pay")
    cancel = model.transition("Order", "OPEN", "CANCELLED", "cancel",
                              effects=[{"entity_id": model.eid("Payment"), "to_state_id": model.s("Payment", "VOID")}])
    eng = Engine(model.m, "en")
    r = eng.apply(eng.initial_state(), cancel)
    assert not r["ok"]  # Payment non ha nessuna transizione verso VOID

    issues = eng.analyze()["issues"]
    missing = [i for i in issues if i["category"] == "missing_transition"]
    assert {(i["gap"]["from"], i["gap"]["to"]) for i in missing} == {
        (model.s("Payment", "NEW"), model.s("Payment", "VOID")), (model.s("Payment", "PAID"), model.s("Payment", "VOID"))}
    assert all(i["fixes"] and i["fixes"][0]["ops"][0]["op"] == "add_transition" for i in missing)


def test_skip_final_leaves_concluded_children_alone(model):
    model.entity("Order", ["OPEN", "CLOSED"], initial="OPEN", final=["CLOSED"])
    model.entity("Item", ["ACTIVE", "DONE"], initial="ACTIVE", final=["DONE"])
    model.transition("Item", "ACTIVE", "DONE", "finish")
    close = model.transition("Order", "OPEN", "CLOSED", "close",
                             effects=[{"entity_id": model.eid("Item"), "to_state_id": model.s("Item", "DONE")}])
    eng = Engine(model.m, "en")
    st = {**eng.initial_state(), model.eid("Item"): model.s("Item", "DONE")}
    assert eng.apply(st, close)["ok"]  # già nello stato di arrivo: niente da fare


def test_guards_see_the_state_updated_by_the_cascade(model):
    model.entity("Order", ["OPEN", "CONFIRMED"], initial="OPEN")
    model.entity("Line", ["DRAFT", "CONFIRMED"], initial="DRAFT")
    model.transition("Line", "DRAFT", "CONFIRMED", "confirm", cascade_only=True,
                     guards=[{"entity_id": model.eid("Order"), "state_ids": [model.s("Order", "OPEN")]}])
    confirm = model.transition("Order", "OPEN", "CONFIRMED", "confirm",
                               effects=[{"entity_id": model.eid("Line"), "to_state_id": model.s("Line", "CONFIRMED")}])
    eng = Engine(model.m, "en")
    conflict = next(i for i in eng.analyze()["issues"] if i["category"] == "effect_conflict")
    assert conflict["conflict"]["misses"]  # la guardia della riga vede l'ordine già CONFIRMED
    assert any(f["key"] == "widen_guard" for f in conflict["fixes"])
    assert eng.apply(eng.initial_state(), confirm)["ok"] is False


def test_deadlock_names_its_cause_and_offers_a_way_out(model):
    model.entity("Order", ["NEW", "OPEN", "DONE"], initial="NEW", final=["DONE"])
    model.entity("Shipment", ["NEW", "DELIVERED", "LOST"], initial="NEW", final=["DELIVERED", "LOST"])
    model.transition("Order", "NEW", "OPEN", "confirm")  # lo stato iniziale non conta come bloccato
    model.transition("Shipment", "NEW", "DELIVERED", "deliver")
    lose = model.transition("Shipment", "NEW", "LOST", "lose")
    model.transition("Order", "OPEN", "DONE", "close",
                     guards=[{"entity_id": model.eid("Shipment"), "state_ids": [model.s("Shipment", "DELIVERED")]}])
    eng = Engine(model.m, "en")
    dl = next(i for i in eng.analyze()["issues"] if i["category"] == "deadlock")
    assert dl["cause"]["key"] == f"deadlock:by:{lose}:{model.eid('Order')}:{model.s('Order', 'OPEN')}"
    way_out = next(f for f in dl["fixes"] if f["key"] == "deadlock_exit")
    op = way_out["ops"][0]
    assert (op["from_state_id"], op["to_state_id"], op["cascade_only"]) == (model.s("Order", "OPEN"), model.s("Order", "DONE"), 0)
    assert op["guards"] == [{"entity_id": model.eid("Shipment"), "state_ids": [model.s("Shipment", "LOST")]}]


def test_children_of_a_concluded_parent_are_not_stuck(model):
    """Chiuso l'ordine (stato finale), le sue righe restano come sono: non è un deadlock, anche con più istanze."""
    model.entity("Order", ["NEW", "OPEN", "CLOSED"], initial="NEW", final=["CLOSED"])
    model.entity("Line", ["DRAFT", "READY", "SHIPPED"], initial="DRAFT", final=["SHIPPED"])
    model.transition("Order", "NEW", "OPEN", "open")
    model.transition("Order", "OPEN", "CLOSED", "close")
    model.transition("Line", "DRAFT", "READY", "prepare")
    model.transition("Line", "READY", "SHIPPED", "ship",
                     guards=[{"entity_id": model.eid("Order"), "state_ids": [model.s("Order", "OPEN")]}])
    model.relation("Order", "Line", instances=2)
    assert not [i for i in Engine(model.m, "en").analyze()["issues"] if i["category"] == "deadlock"]

    model.relation("Order", "Line", composition=False)  # senza composizione la riga READY resta davvero bloccata
    model.m["relations"] = model.m["relations"][-1:]
    assert [i for i in Engine(model.m, "en").analyze()["issues"] if i["category"] == "deadlock"]


def test_child_without_creation_is_born_with_its_parent(model):
    """Un figlio senza azioni di creazione esiste dall'inizio, anche senza padre: la soluzione lo fa nascere col padre."""
    model.entity("Tender", ["DRAFT"])
    model.entity("Phase", ["ENABLED"], initial="ENABLED")
    create = model.transition("Tender", None, "DRAFT", "create", create=True)
    model.relation("Tender", "Phase")
    inv = next(i for i in Engine(model.m, "en").analyze()["issues"] if i["category"] == "invariants")
    assert inv["trace"] == []  # violata già nello stato iniziale
    fix = next(f for f in inv["fixes"] if f["key"] == "born_with_parent")
    assert [(o["op"], o.get("kind")) for o in fix["ops"]] == [("add_transition", "create"), ("add_effect", "create")]
    assert fix["ops"][1]["transition_id"] == create


def _checkout_model(model):
    """Carrello → Ordine → Riga: confermare il carrello richiede che ogni ordine abbia una riga."""
    model.entity("Cart", ["OPEN", "CHECKED_OUT"], initial="OPEN", final=["CHECKED_OUT"])
    model.entity("Order", ["DRAFT"])
    model.entity("Line", ["DRAFT"])
    model.transition("Order", None, "DRAFT", "create", create=True)
    model.transition("Line", None, "DRAFT", "create", create=True,
                     guards=[{"entity_id": model.eid("Order"), "state_ids": [model.s("Order", "DRAFT")]}])
    checkout = model.transition("Cart", "OPEN", "CHECKED_OUT", "checkout",
                                guards=[{"entity_id": model.eid("Line"), "state_ids": [model.s("Line", "DRAFT")],
                                         "each_entity_id": model.eid("Order")}])
    model.relation("Cart", "Order", instances=2)
    model.relation("Order", "Line", instances=1)
    return checkout


def test_for_each_guard_single_instance(model):
    checkout = _checkout_model(model)
    eng = Engine(model.m, "en")
    t = eng.transition[checkout]
    st = eng.initial_state()
    assert eng.guard_failures(t, st) == []  # nessun ordine: niente da controllare
    st[model.eid("Order")] = model.s("Order", "DRAFT")
    assert eng.guard_failures(t, st)  # un ordine senza righe
    st[model.eid("Line")] = model.s("Line", "DRAFT")
    assert eng.guard_failures(t, st) == []


def test_children_of_duplicated_parents_are_duplicated_too(model):
    _checkout_model(model)
    _, labels = expand(model.m)
    copies = {}
    for v in labels.values():
        copies[v["base"]] = copies.get(v["base"], 0) + 1
    # Line ha instances=1, ma il padre Order ne ha 2: ogni ordine ha la sua riga
    assert copies == {model.eid("Order"): 2, model.eid("Line"): 2}


def test_alternative_guards(model):
    model.entity("Order", ["OPEN", "PAID", "SHIPPED"], initial="OPEN")
    model.entity("Payment", ["NEW", "CAPTURED"], initial="NEW")
    model.entity("Credit", ["NO", "YES"], initial="NO")
    order, payment = model.eid("Order"), model.eid("Payment")
    ship = model.transition("Order", "OPEN", "SHIPPED", "ship", guards=[
        {"entity_id": payment, "state_ids": [model.s("Payment", "CAPTURED")], "alt_group": 1},
        {"entity_id": model.eid("Credit"), "state_ids": [model.s("Credit", "YES")], "alt_group": 1},
        {"entity_id": order, "state_ids": [model.s("Order", "OPEN")]},
    ])
    eng = Engine(model.m, "en")
    t = eng.transition[ship]
    st = eng.initial_state()
    assert not eng.guards_ok(t, st) and len(eng.guard_failures(t, st)) == 1  # una sola clausola, con le due alternative
    assert len(eng.guard_misses(t, st)) == 2
    for alternative in ({payment: model.s("Payment", "CAPTURED")}, {model.eid("Credit"): model.s("Credit", "YES")}):
        assert eng.guards_ok(t, {**st, **alternative}) and eng.guard_failures(t, {**st, **alternative}) == []


def test_expectations(model):
    model.entity("Order", ["OPEN", "PAID", "SHIPPED"], initial="OPEN")
    pay = model.transition("Order", "OPEN", "PAID", "pay")
    ship = model.transition("Order", "PAID", "SHIPPED", "ship")
    eng = Engine(model.m, "en")
    oid = model.eid("Order")
    goal = eng.check_expectation({"kind": "goal", "rules": [{"entity_id": oid, "state_ids": [model.s("Order", "SHIPPED")]}]})
    assert goal["ok"] and [s["action"] for s in goal["trace"]] == ["pay", "ship"]
    step = lambda t: {"transition_id": t, "entity_id": oid}
    assert eng.check_expectation({"kind": "path", "expect": "possible", "trace": [step(pay), step(ship)]})["ok"]
    blocked = eng.check_expectation({"kind": "path", "expect": "possible", "trace": [step(ship)]})
    assert not blocked["ok"] and blocked["done"] == 0
    assert eng.check_expectation({"kind": "path", "expect": "blocked", "trace": [step(ship)]})["ok"]


def test_fingerprints_do_not_depend_on_language(model):
    test_cascade_needs_a_transition_on_the_child(model)
    it, en = (sorted(i["fingerprint"] for i in Engine(model.m, lang).analyze()["issues"]) for lang in ("it", "en"))
    assert it == en


def test_backend_messages_exist_in_every_language():
    keys = {lang: set(msgs) for lang, msgs in MESSAGES.items()}
    assert keys["it"] == keys["en"], keys["it"] ^ keys["en"]


def test_none_state_is_zero():
    assert NONE == 0


# ---------------------------------------------------------------- riduzione per simmetria

def _order_with_lines(model, lines=3):
    """Un ordine con righe intercambiabili: preparare, spedire (a ordine aperto), annullare a cascata."""
    model.entity("Order", ["OPEN", "CLOSED", "CANCELLED"], initial="OPEN", final=["CLOSED", "CANCELLED"])
    model.entity("Line", ["DRAFT", "READY", "SHIPPED", "CANCELLED"], final=["SHIPPED", "CANCELLED"], gone=["CANCELLED"])
    model.transition("Line", None, "DRAFT", "add", create=True,
                     guards=[{"entity_id": model.eid("Order"), "state_ids": [model.s("Order", "OPEN")]}])
    model.transition("Line", "DRAFT", "READY", "prepare")
    model.transition("Line", "READY", "SHIPPED", "ship",
                     guards=[{"entity_id": model.eid("Order"), "state_ids": [model.s("Order", "OPEN")]}])
    for src in ("DRAFT", "READY"):
        model.transition("Line", src, "CANCELLED", "cancel", cascade_only=True)
    model.transition("Order", "OPEN", "CANCELLED", "cancel",
                     effects=[{"entity_id": model.eid("Line"), "to_state_id": model.s("Line", "CANCELLED"), "skip_final": 1}])
    model.transition("Order", "OPEN", "CLOSED", "close")  # lascia righe a metà: deadlock e invarianti da trovare
    model.relation("Order", "Line", instances=lines)
    model.m["invariants"].append({"id": 900, "name": "Shipped after cancel", "description": "", "severity": "error",
                                  "rules": [{"entity_id": model.eid("Order"), "state_ids": [model.s("Order", "CANCELLED")], "negate": 0},
                                            {"entity_id": model.eid("Line"), "state_ids": [model.s("Line", "SHIPPED")], "negate": 0}]})


@pytest.mark.parametrize("lines", [2, 3])
@pytest.mark.parametrize("absence", [False, True])
def test_symmetry_preserves_every_observation(model, lines, absence):
    """BFS completa e BFS sulle orbite: stesse segnalazioni (istanze comprese), percorsi minimi, impronte e numero
    di stati; con meno stati esplorati e percorsi che si rigiocano tali e quali."""
    from tests.equivalence import compare
    _order_with_lines(model, lines=lines)
    if absence:  # una regola su "tutte le istanze": chiuso senza nessuna riga spedita
        model.m["invariants"].append({"id": 901, "name": "Closed with nothing shipped", "description": "", "severity": "warning",
                                      "rules": [{"entity_id": model.eid("Order"), "state_ids": [model.s("Order", "CLOSED")], "negate": 0},
                                                {"entity_id": model.eid("Line"), "state_ids": [model.s("Line", "SHIPPED")], "negate": 1}]})
    c = compare(model.m)
    assert all(c["equal"].values()), c["equal"]
    assert c["full"]["issues"]  # il confronto ha qualcosa da confrontare
    assert c["unreplayable"] == []
    assert c["symmetry"] and c["explored"][1] < c["explored"][0]


def test_nested_instances_are_per_parent_and_symmetric_on_two_levels(model):
    """2 fasi per gara, 3 compiti per fase: 6 compiti, 3 di ciascuna fase. Si scambiano le fasi (ognuna con i suoi
    compiti) e i compiti dentro ogni fase; i risultati restano quelli dell'esplorazione completa."""
    from tests.equivalence import compare
    model.entity("Tender", ["OPEN", "CLOSED"], initial="OPEN", final=["CLOSED"])
    model.entity("Phase", ["ON", "OFF"], initial="ON", off=["OFF"])
    model.entity("Task", ["TODO", "DONE"], initial="TODO", final=["DONE"])
    model.transition("Phase", "ON", "OFF", "disable")
    model.transition("Task", "TODO", "DONE", "do")
    model.relation("Tender", "Phase", instances=2)
    model.relation("Phase", "Task", instances=3)
    _, labels = expand(model.m)
    tasks = [v for v in labels.values() if v["base"] == model.eid("Task")]
    assert len(tasks) == 6 and sorted(v["path"][1] for v in tasks) == [1, 1, 1, 2, 2, 2]
    c = compare(model.m)
    assert {s["entities"][0] for s in c["symmetry"]} == {"Phase", "Task"}
    assert all(c["equal"].values()) and c["unreplayable"] == []
    assert c["explored"][1] < c["explored"][0]


def test_symmetry_is_refused_when_it_cannot_be_proved(model):
    """Annullare l'ordine annulla le righe una dopo l'altra, e annullare una riga riapre l'ordine: le cascate si
    leggono a vicenda, l'ordine degli effetti conta e scambiare le righe non è dimostrabilmente innocuo.
    La verifica rifiuta e l'analisi resta quella completa."""
    from tests.equivalence import compare
    _order_with_lines(model, lines=2)
    line_cancel = [t for e in model.m["entities"] if e["name"] == "Line" for t in e["transitions"] if t["action"] == "cancel"]
    for t in line_cancel:
        t["effects"].append({"id": 990 + t["id"], "transition_id": t["id"], "entity_id": model.eid("Order"), "kind": "state",
                             "to_state_id": model.s("Order", "OPEN"), "action": "", "skip_final": 0, "description": ""})
    c = compare(model.m)
    assert c["symmetry"] == []
    assert all(c["equal"].values()) and c["unreplayable"] == []


# ---------------------------------------------------------------- certificato di verifica

def test_certificate_says_what_was_verified(model):
    _order_with_lines(model, lines=2)
    cert = Engine(model.m, "en").analyze()["certificate"]
    assert cert["verdict"] == "complete" and cert["mode"] == "symmetry"
    assert {r["key"] for r in cert["rows"]} >= {"exploration", "instances", "symmetry", "cascades", "rules", "time", "messages"}
    small = Engine(model.m, "en").analyze(limit=5)["certificate"]
    assert small["verdict"] == "incomplete"
    assert next(r for r in small["rows"] if r["key"] == "exploration")["status"] == "limit"


def test_cascade_depth_limit_is_not_reported_as_a_cycle(model):
    """Una catena di cascate più lunga del limite: non è un errore del modello ma una parte non verificata,
    dichiarata nel certificato. Un ciclo vero resta un errore."""
    from app.engine import MAX_CASCADE_DEPTH
    names = [f"E{i}" for i in range(MAX_CASCADE_DEPTH + 2)]
    for n in names:
        model.entity(n, ["A", "B"], initial="A", final=["B"])
    for n, nxt in zip(names, names[1:] + [None]):
        model.transition(n, "A", "B", "go", cascade_only=n != names[0],
                         effects=[{"entity_id": model.eid(nxt), "to_state_id": model.s(nxt, "B")}] if nxt else [])
    r = Engine(model.m, "en").analyze()
    assert not [i for i in r["issues"] if i["category"] == "effect_conflict"]
    assert r["certificate"]["verdict"] == "incomplete" and r["certificate"]["cut"]

    from tests.conftest import Model
    loop = Model()
    loop.entity("P", ["A", "B"], initial="A")
    loop.entity("Q", ["A", "B"], initial="A")
    run = lambda name: [{"kind": "action", "action": name}]
    loop.transition("P", "A", "B", "go", effects=[{"entity_id": loop.eid("Q"), **run("ping")[0]}])
    loop.transition("Q", "A", "B", "ping", cascade_only=True, effects=[{"entity_id": loop.eid("P"), **run("pong")[0]}])
    loop.transition("P", "B", "A", "pong", cascade_only=True, effects=[{"entity_id": loop.eid("Q"), **run("ping2")[0]}])
    loop.transition("Q", "B", "A", "ping2", cascade_only=True, effects=[{"entity_id": loop.eid("P"), **run("go")[0]}])
    r = Engine(loop.m, "en").analyze()
    assert [i for i in r["issues"] if i["category"] == "effect_conflict"]
    assert not r["certificate"]["cut"]


def test_cascade_across_aggregates_is_an_architectural_warning(model):
    """Ordine e Pagamento sono aggregati diversi: annullare l'ordine che annulla il pagamento nello stesso passo è una
    transazione distribuita. Dentro lo stesso aggregato (riga composta nell'ordine) non lo è."""
    _order_with_lines(model, lines=1)
    model.entity("Payment", ["NEW", "VOID"], initial="NEW", final=["VOID"])
    model.transition("Payment", "NEW", "VOID", "void", cascade_only=True)
    cancel = next(t for e in model.m["entities"] if e["name"] == "Order" for t in e["transitions"] if t["action"] == "cancel")
    cancel["effects"].append({"id": 999, "transition_id": cancel["id"], "entity_id": model.eid("Payment"), "kind": "state",
                              "to_state_id": model.s("Payment", "VOID"), "action": "", "skip_final": 0, "description": ""})
    found = [i for i in Engine(model.m, "en").analyze()["issues"] if i.get("code") == "a.cross_aggregate"]
    assert [(i["transition_id"], i["aggregates"], i["severity"]) for i in found] == [(cancel["id"], ["Order", "Payment"], "warning")]


# ---------------------------------------------------------------- attributi ed espressioni

def _attr(model, entity, name, type_, initial, **domain):
    e = next(x for x in model.m["entities"] if x["name"] == entity)
    e["attributes"].append({"id": model._next(), "entity_id": e["id"], "name": name, "type": type_, "required": 0,
                            "description": "", "verify": 1, "domain": domain, "initial": initial})


def _refund_model(model, guarded=True):
    """Un rimborso non deve superare l'incassato: la guardia su «pay» lo impedisce, l'invariante lo verifica."""
    model.entity("Payment", ["NEW", "CAPTURED"], initial="NEW", final=["CAPTURED"])
    model.entity("Refund", ["DRAFT", "REQUESTED", "PAID"], initial="DRAFT", final=["PAID"])
    _attr(model, "Payment", "captured", "int", 0, min=0, max=3)
    _attr(model, "Refund", "amount", "int", 0, min=0, max=3)
    model.transition("Payment", "NEW", "CAPTURED", "capture",
                     effects=[{"entity_id": model.eid("Payment"), "kind": "set", "action": "captured", "expr": "2"}])
    for name, value in (("small", "1"), ("big", "3")):
        model.transition("Refund", "DRAFT", "REQUESTED", name,
                         effects=[{"entity_id": model.eid("Refund"), "kind": "set", "action": "amount", "expr": value}])
    model.transition("Refund", "REQUESTED", "PAID", "pay",
                     guards=[{"kind": "expr", "description": "amount <= Payment.captured", "entity_id": None, "state_ids": []}]
                     if guarded else [])
    model.m["invariants"].append({"id": 950, "name": "Refund above captured", "description": "", "severity": "error",
                                  "rules": [{"entity_id": model.eid("Refund"), "state_ids": [model.s("Refund", "PAID")], "negate": 0}],
                                  "expr": "Refund.amount > Payment.captured"})


def test_attribute_guard_prevents_what_the_invariant_forbids(model):
    _refund_model(model, guarded=True)
    r = Engine(model.m, "en").analyze()
    assert not [i for i in r["issues"] if i["category"] in ("invariants", "expressions")]
    assert next(x for x in r["certificate"]["rows"] if x["key"] == "attributes")["status"] == "ok"


def test_attribute_invariant_is_violated_without_the_guard(model):
    from tests.conftest import Model
    _refund_model(model, guarded=False)
    eng = Engine(model.m, "en")
    v = next(i for i in eng.analyze()["issues"] if i.get("code") == "a.violable")
    assert len(v["trace"]) == 2 and v["trace"][-1]["action"] == "pay"  # rimborso chiesto e pagato senza incasso
    st = eng.initial_state()
    for s in v["trace"]:
        st = eng.apply(st, s["transition_id"])["state"]
    assert eng.violations(st)

    bad = Model()
    _refund_model(bad, guarded=True)
    pay = next(t for e in bad.m["entities"] for t in e["transitions"] if t["action"] == "pay")
    pay["guards"][0]["description"] = "amount <= Payment.missing"
    issue = next(i for i in Engine(bad.m, "en").analyze()["issues"] if i.get("code") == "a.bad_expr")
    assert "missing" in issue["detail"]


# ---------------------------------------------------------------- messaggi asincroni

def _shop(model, deferred=True, max_in_flight=2, delivery="exactly_once"):
    """Pagare l'ordine manda un messaggio alla spedizione; annullare l'ordine annulla la spedizione subito."""
    model.m.update(max_in_flight=max_in_flight, delivery=delivery)
    model.entity("Order", ["OPEN", "PAID", "CANCELLED"], initial="OPEN", final=["CANCELLED"])
    model.entity("Shipment", ["NEW", "READY", "CANCELLED"], initial="NEW", final=["CANCELLED"])
    model.transition("Shipment", "NEW", "READY", "prepare", cascade_only=True)
    model.transition("Shipment", "NEW", "CANCELLED", "cancel", cascade_only=True)
    model.transition("Shipment", "READY", "CANCELLED", "cancel", cascade_only=True)
    pay = model.transition("Order", "OPEN", "PAID", "pay", effects=[
        {"entity_id": model.eid("Shipment"), "to_state_id": model.s("Shipment", "READY"), "deferred": int(deferred)}])
    model.transition("Order", "PAID", "CANCELLED", "cancel", effects=[
        {"entity_id": model.eid("Shipment"), "to_state_id": model.s("Shipment", "CANCELLED"), "skip_final": 1}])
    return pay


def test_async_message_can_arrive_after_a_cancellation(model):
    from tests.conftest import Model
    pay = _shop(model)
    eng = Engine(model.m, "en")
    r = eng.analyze()
    late = [i for i in r["issues"] if i["category"] == "effect_conflict"]
    assert late and [s.get("message") is not None for s in late[0]["trace"]] == [False, False]
    assert late[0]["state"]["_q"] and late[0]["replayable"]
    # il percorso fino allo stato del conflitto si rigioca, consegna compresa, e lì la consegna fallisce
    st = eng.replay(late[0]["trace"])["state"]
    assert not eng.deliver(st, st["_q"][0])["ok"]
    # pagare manda un evento: niente transazione atomica su due aggregati
    assert not [i for i in r["issues"] if i.get("code") == "a.cross_aggregate" and i["transition_id"] == pay]
    assert next(x for x in r["certificate"]["rows"] if x["key"] == "messages")["status"] == "ok"

    sync = Model()
    _shop(sync, deferred=False)
    issues = Engine(sync.m, "en").analyze()["issues"]
    assert not [i for i in issues if i["category"] == "effect_conflict"]  # in modo sincrono la corsa non esiste
    assert [i for i in issues if i.get("code") == "a.cross_aggregate"]


@pytest.mark.parametrize("delivery", ["exactly_once", "at_least_once", "at_most_once"])
def test_delivery_guarantees_change_what_can_happen(model, delivery):
    _shop(model, delivery=delivery)
    eng = Engine(model.m, "en")
    st = eng.apply(eng.initial_state(), next(t["id"] for t in model.m["entities"][0]["transitions"] if t["action"] == "pay"))["state"]
    variants = {m["variant"] for m in eng.evaluate(st)["messages"]}
    assert variants == {"deliver"} | ({"duplicate"} if delivery == "at_least_once" else {"lose"} if delivery == "at_most_once" else set())
    lost = eng.deliver(st, st["_q"][0], "lose")
    assert lost["ok"] == (delivery == "at_most_once")


def test_full_queue_is_a_limit_of_the_model(model):
    _shop(model, max_in_flight=1)
    pay = next(t for t in model.m["entities"][0]["transitions"] if t["action"] == "pay")
    model.transition("Order", "PAID", "PAID", "remind", effects=[
        {"entity_id": model.eid("Shipment"), "to_state_id": model.s("Shipment", "READY"), "deferred": 1}])
    r = Engine(model.m, "en").analyze()
    row = next(x for x in r["certificate"]["rows"] if x["key"] == "messages")
    assert row["status"] == "limit" and pay["id"]
    assert not [i for i in r["issues"] if "in flight" in (i.get("detail") or "")]


# ---------------------------------------------------------------- proprietà temporali

def _delivery_flow(model, lost=True):
    """Ordine pagato, che può essere messo in attesa e ripreso (un ciclo innocuo) e, se lost, perso senza ritorno."""
    model.entity("Order", ["OPEN", "PAID", "ON_HOLD", "LOST", "DONE"], initial="OPEN", final=["DONE"])
    model.transition("Order", "OPEN", "PAID", "pay")
    model.transition("Order", "PAID", "ON_HOLD", "hold")
    model.transition("Order", "ON_HOLD", "PAID", "resume")
    model.transition("Order", "PAID", "DONE", "deliver")
    if lost:
        model.transition("Order", "PAID", "LOST", "lose")
    rule = lambda s: [{"entity_id": model.eid("Order"), "state_ids": [model.s("Order", s)], "negate": 0}]
    return rule


@pytest.mark.parametrize("lost", [True, False])
def test_always_possible_finds_states_with_no_way_to_the_goal(model, lost):
    rule = _delivery_flow(model, lost)
    eng = Engine(model.m, "en")
    res = eng.check_expectation({"kind": "always", "rules": rule("DONE")})
    assert res["ok"] == (not lost)
    if lost:
        assert [s["action"] for s in res["trace"]] == ["pay", "lose"]


@pytest.mark.parametrize("lost", [True, False])
def test_leads_to_holds_under_fairness_despite_harmless_cycles(model, lost):
    """Pagato ⇒ prima o poi consegnato: il ciclo attesa/ripresa non lo rompe (equità), la perdita sì."""
    rule = _delivery_flow(model, lost)
    eng = Engine(model.m, "en")
    res = eng.check_expectation({"kind": "leads_to", "rules": rule("PAID"), "target": rule("DONE")})
    assert res["ok"] == (not lost)
    if lost:
        assert [s["action"] for s in res["trace"]] == ["pay", "lose"] and res["trigger_at"] == 1
        st = eng.replay(res["trace"])["state"]
        assert st[model.eid("Order")] == model.s("Order", "LOST")


def test_attributes_of_multiple_instances_are_symmetric(model):
    """Ogni riga ha la sua quantità: le copie si scambiano con i loro attributi, risultati identici."""
    from tests.equivalence import compare
    _order_with_lines(model, lines=3)
    _attr(model, "Line", "qty", "int", 0, min=0, max=2)
    model.transition("Line", "DRAFT", "DRAFT", "more", guards=[{"kind": "expr", "description": "qty < 2", "entity_id": None,
                                                                "state_ids": []}],
                     effects=[{"entity_id": model.eid("Line"), "kind": "set", "action": "qty", "expr": "qty + 1"}])
    c = compare(model.m)
    assert c["symmetry"] and all(c["equal"].values()) and c["unreplayable"] == []

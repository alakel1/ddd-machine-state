"""Motore: cascate, guardie, istanze multiple, aspettative, impronte delle segnalazioni."""
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

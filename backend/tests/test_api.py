"""API sul dominio di esempio: analisi, soluzioni, storico, segnalazioni accettate, aspettative."""
import json
from pathlib import Path

DOMAIN = 1


def analysis(client):
    return client.get(f"/api/domains/{DOMAIN}/analysis").json()["issues"]


def model(client):
    return client.get(f"/api/domains/{DOMAIN}/model").json()


def test_example_analysis_finds_the_deliberate_gaps(client):
    issues = analysis(client)
    cats = {i["category"] for i in issues}
    assert {"effect_conflict", "missing_transition", "invariants"} <= cats
    for i in issues:  # campi su cui contano il frontend e le soluzioni
        assert i["fingerprint"] and i["context"] and i["cause"]["key"] and "fixes" in i


def test_fix_preview_changes_nothing_and_fix_solves(client):
    missing = next(i for i in analysis(client) if i["category"] == "missing_transition")
    ops = missing["fixes"][0]["ops"]
    before = model(client)
    preview = client.post(f"/api/domains/{DOMAIN}/fix/preview", json={"ops": ops}).json()
    assert preview["resolved"]["error"] >= 1
    assert model(client) == before

    client.post(f"/api/domains/{DOMAIN}/fix", json={"ops": ops, "label": "test"}).raise_for_status()
    assert missing["fingerprint"] not in {i["fingerprint"] for i in analysis(client)}


def test_fix_rejects_rows_of_other_domains(client):
    r = client.post(f"/api/domains/{DOMAIN}/fix", json={"ops": [{"op": "delete", "table": "effects", "id": 999999}]})
    assert r.status_code == 400


def test_undo_restores_the_model_with_the_same_ids(client):
    before = model(client)
    entity = before["entities"][1]
    state = entity["states"][0]
    client.put(f"/api/states/{state['id']}", json={"description": "changed"}).raise_for_status()
    client.post("/api/states", json={"entity_id": entity["id"], "name": "new one"}).raise_for_status()
    labels = [v["label"] for v in client.get(f"/api/domains/{DOMAIN}/history").json()]
    assert len(labels) == 2
    client.post(f"/api/domains/{DOMAIN}/undo").raise_for_status()
    client.post(f"/api/domains/{DOMAIN}/undo").raise_for_status()
    assert model(client) == before
    assert client.post(f"/api/domains/{DOMAIN}/undo").json()["undone"] is None


def test_restore_and_compare(client):
    before = model(client)
    client.put(f"/api/domains/{DOMAIN}", json={"name": "renamed"}).raise_for_status()
    [version] = client.get(f"/api/domains/{DOMAIN}/history").json()
    assert client.get(f"/api/domains/{DOMAIN}/compare", params={"base": version["id"]}).json()["changes"]
    client.post(f"/api/domains/{DOMAIN}/history/{version['id']}/restore").raise_for_status()
    assert model(client) == before
    client.post(f"/api/domains/{DOMAIN}/undo").raise_for_status()  # anche il ripristino si annulla
    assert model(client)["name"] == "renamed"


def test_accepted_findings_become_stale_when_the_model_changes(client):
    issue = next(i for i in analysis(client) if i["category"] == "missing_transition")
    client.put(f"/api/domains/{DOMAIN}/acks", json={"fingerprint": issue["fingerprint"], "context": issue["context"],
                                                    "status": "accepted", "note": "ok"}).raise_for_status()
    ack = next(i for i in analysis(client) if i["fingerprint"] == issue["fingerprint"])["ack"]
    assert ack["status"] == "accepted" and not ack["stale"]

    entity = next(e for e in model(client)["entities"] if e["id"] == issue["entity_id"])
    client.put(f"/api/entities/{entity['id']}", json={"description": "changed"}).raise_for_status()
    assert next(i for i in analysis(client) if i["fingerprint"] == issue["fingerprint"])["ack"]["stale"]


def test_expectations_show_up_in_the_analysis(client):
    m = model(client)
    order = m["entities"][0]
    first, last = order["states"][0]["id"], order["states"][-1]["id"]
    client.post("/api/expectations", json={"domain_id": DOMAIN, "name": "reachable", "kind": "goal",
                                           "rules": [{"entity_id": order["id"], "state_ids": [last], "negate": 0}]})
    client.post("/api/expectations", json={"domain_id": DOMAIN, "name": "impossible", "kind": "goal",
                                           "rules": [{"entity_id": order["id"], "state_ids": [first], "negate": 1},
                                                     {"entity_id": order["id"], "state_ids": [first], "negate": 0}]})
    results = {x["name"]: x["result"]["ok"] for x in client.get(f"/api/domains/{DOMAIN}/expectations").json()}
    assert results == {"reachable": True, "impossible": False}
    failed = [i for i in analysis(client) if i["category"] == "expectations"]
    assert len(failed) == 1


def test_frontend_locales_have_the_same_keys():
    root = Path(__file__).resolve().parents[2] / "frontend" / "src" / "locales"
    if not root.exists():  # nel container del backend il frontend non è montato
        return

    def keys(d, prefix=""):
        out = set()
        for k, v in d.items():
            out |= keys(v, f"{prefix}{k}.") if isinstance(v, dict) else {prefix + k}
        return out

    it, en = (keys(json.loads((root / f"{lang}.json").read_text())) for lang in ("it", "en"))
    assert it == en, it ^ en

"""Fixture dei test: un database SQLite nuovo per ogni test, con il dominio di esempio (id 1)."""
import pytest
from fastapi.testclient import TestClient

from app import db


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.sqlite3")
    from app.main import app
    with TestClient(app, headers={"X-Lang": "en"}) as c:  # l'avvio crea lo schema e il dominio di esempio
        yield c


# ---------------------------------------------------------------- modelli minimi per il motore

class Model:
    """Costruisce un modello nel formato di db.load_model, con id progressivi."""

    def __init__(self):
        self._id = 100
        self.m = {"id": 1, "name": "test", "description": "", "entities": [], "relations": [], "invariants": []}
        self.states = {}

    def _next(self):
        self._id += 1
        return self._id

    def entity(self, name, states, initial=None, final=(), gone=(), off=()):
        e = {"id": self._next(), "name": name, "kind": "aggregate", "context": "", "color": "indigo", "description": "",
             "attributes": [], "states": [], "transitions": []}
        for s in states:
            sid = self._next()
            self.states[(name, s)] = sid
            e["states"].append({"id": sid, "entity_id": e["id"], "name": s, "description": "",
                                "is_initial": int(s == initial), "is_final": int(s in final),
                                "liveness": "gone" if s in gone else "off" if s in off else "active"})
        self.m["entities"].append(e)
        return e["id"]

    def s(self, entity, state):
        return 0 if state is None else self.states[(entity, state)]

    def eid(self, name):
        return next(e["id"] for e in self.m["entities"] if e["name"] == name)

    def transition(self, entity, src, dst, action, create=False, cascade_only=False, guards=(), effects=()):
        e = next(x for x in self.m["entities"] if x["name"] == entity)
        tid = self._next()
        e["transitions"].append({
            "id": tid, "entity_id": e["id"], "kind": "create" if create else "normal", "cascade_only": int(cascade_only),
            "from_state_id": None if create else self.s(entity, src), "to_state_id": self.s(entity, dst),
            "action": action, "actor": "", "description": "",
            "guards": [{"id": self._next(), "transition_id": tid, "kind": "state", "description": "", "negate": 0,
                        "each_entity_id": None, **g} for g in guards],
            "effects": [{"id": self._next(), "transition_id": tid, "kind": "state", "action": "", "skip_final": 0,
                         "description": "", "to_state_id": None, **f} for f in effects],
        })
        return tid

    def relation(self, src, dst, composition=True, instances=1):
        self.m["relations"].append({"id": self._next(), "domain_id": 1, "source_id": self.eid(src), "target_id": self.eid(dst),
                                    "cardinality": "1-N", "label": "", "composition": int(composition), "instances": instances})


@pytest.fixture()
def model():
    return Model()

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(os.environ.get("DDD_DB", Path(__file__).resolve().parent.parent / "ddd.sqlite3"))

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS domains (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY,
    domain_id INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'aggregate',      -- aggregate | entity | value_object
    context TEXT NOT NULL DEFAULT '',            -- bounded context
    color TEXT NOT NULL DEFAULT 'indigo',
    pos INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS attributes (
    id INTEGER PRIMARY KEY,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'string',
    required INTEGER NOT NULL DEFAULT 0,
    description TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS states (
    id INTEGER PRIMARY KEY,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    is_initial INTEGER NOT NULL DEFAULT 0,
    is_final INTEGER NOT NULL DEFAULT 0,
    pos INTEGER NOT NULL DEFAULT 0
);

-- kind=normal: from_state_id NULL = "da qualsiasi stato non finale"
-- kind=create: azione di creazione, parte da "non esiste" (from_state_id ignorato)
CREATE TABLE IF NOT EXISTS transitions (
    id INTEGER PRIMARY KEY,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'normal',
    cascade_only INTEGER NOT NULL DEFAULT 0,  -- 1 = avviene solo come effetto di un'altra azione
    from_state_id INTEGER REFERENCES states(id) ON DELETE CASCADE,
    to_state_id INTEGER NOT NULL REFERENCES states(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT ''
);

-- kind=state: l'entità entity_id deve (negate=0) / non deve (negate=1) trovarsi in uno di state_ids
-- kind=rule : regola di business testuale, non verificabile automaticamente
CREATE TABLE IF NOT EXISTS guards (
    id INTEGER PRIMARY KEY,
    transition_id INTEGER NOT NULL REFERENCES transitions(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'state',
    entity_id INTEGER REFERENCES entities(id) ON DELETE CASCADE,
    state_ids TEXT NOT NULL DEFAULT '[]',
    negate INTEGER NOT NULL DEFAULT 0,
    description TEXT NOT NULL DEFAULT ''
);

-- effetto collaterale, quando scatta la transizione:
-- kind=state : entity_id deve passare a to_state_id
-- kind=create: entity_id deve nascere; lo stato lo decide l'unica azione di creazione con guardie soddisfatte
-- kind=action: entity_id deve eseguire l'azione di nome `action` disponibile dal suo stato attuale
CREATE TABLE IF NOT EXISTS effects (
    id INTEGER PRIMARY KEY,
    transition_id INTEGER NOT NULL REFERENCES transitions(id) ON DELETE CASCADE,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'state',
    to_state_id INTEGER REFERENCES states(id) ON DELETE CASCADE,
    action TEXT NOT NULL DEFAULT '',
    skip_final INTEGER NOT NULL DEFAULT 0,  -- 1 = "su tutte le istanze attive": ignora quelle in stato finale
    description TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS relations (
    id INTEGER PRIMARY KEY,
    domain_id INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    source_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    target_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    cardinality TEXT NOT NULL DEFAULT '1-1',
    label TEXT NOT NULL DEFAULT '',
    -- composizione: target (figlio) non esiste senza source (padre)
    composition INTEGER NOT NULL DEFAULT 0,
    parent_gone_states TEXT NOT NULL DEFAULT '[0]',  -- stati del padre in cui i figli non possono restare vivi
    child_gone_states TEXT NOT NULL DEFAULT '[]',    -- stati in cui il figlio "non c'è più" ([] = proposta automatica)
    instances INTEGER NOT NULL DEFAULT 1            -- figli simulati per padre nell'analisi (1-3)
);

-- combinazione di stati vietata: rules = [{entity_id, state_ids, negate}] in AND
CREATE TABLE IF NOT EXISTS invariants (
    id INTEGER PRIMARY KEY,
    domain_id INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT 'error',
    rules TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS scenarios (
    id INTEGER PRIMARY KEY,
    domain_id INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT '{}',
    history TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

SCHEMA_EFFECTS = SCHEMA[SCHEMA.index("CREATE TABLE IF NOT EXISTS effects"):SCHEMA.index("CREATE TABLE IF NOT EXISTS relations")]

# Campi scrivibili per tabella, campi JSON e chiave padre per il CRUD generico
TABLES = {
    "domains": {"fields": ["name", "description"], "json": []},
    "entities": {"fields": ["domain_id", "name", "description", "kind", "context", "color", "pos"], "json": []},
    "attributes": {"fields": ["entity_id", "name", "type", "required", "description"], "json": []},
    "states": {"fields": ["entity_id", "name", "description", "is_initial", "is_final", "pos"], "json": []},
    "transitions": {"fields": ["entity_id", "kind", "cascade_only", "from_state_id", "to_state_id", "action", "actor", "description"], "json": []},
    "guards": {"fields": ["transition_id", "kind", "entity_id", "state_ids", "negate", "description"], "json": ["state_ids"]},
    "effects": {"fields": ["transition_id", "entity_id", "kind", "to_state_id", "action", "skip_final", "description"], "json": []},
    "relations": {"fields": ["domain_id", "source_id", "target_id", "cardinality", "label", "composition",
                              "parent_gone_states", "child_gone_states", "instances"],
                  "json": ["parent_gone_states", "child_gone_states"]},
    "invariants": {"fields": ["domain_id", "name", "description", "severity", "rules"], "json": ["rules"]},
    "scenarios": {"fields": ["domain_id", "name", "description", "state", "history"], "json": ["state", "history"]},
}


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def tx():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _effects_with_kind(conn):
    # to_state_id diventa opzionale: SQLite non altera i vincoli, la tabella va ricreata
    conn.execute("ALTER TABLE effects RENAME TO effects_old")
    conn.executescript(SCHEMA_EFFECTS)
    conn.execute("""INSERT INTO effects (id, transition_id, entity_id, kind, to_state_id, description)
                    SELECT id, transition_id, entity_id, 'state', to_state_id, description FROM effects_old""")
    conn.execute("DROP TABLE effects_old")


MIGRATIONS = [
    ("transitions", "kind", "ALTER TABLE transitions ADD COLUMN kind TEXT NOT NULL DEFAULT 'normal'"),
    ("effects", "kind", _effects_with_kind),
    ("effects", "action", "ALTER TABLE effects ADD COLUMN action TEXT NOT NULL DEFAULT ''"),
    ("transitions", "cascade_only", "ALTER TABLE transitions ADD COLUMN cascade_only INTEGER NOT NULL DEFAULT 0"),
    ("relations", "composition", "ALTER TABLE relations ADD COLUMN composition INTEGER NOT NULL DEFAULT 0"),
    ("relations", "parent_gone_states", "ALTER TABLE relations ADD COLUMN parent_gone_states TEXT NOT NULL DEFAULT '[0]'"),
    ("relations", "child_gone_states", "ALTER TABLE relations ADD COLUMN child_gone_states TEXT NOT NULL DEFAULT '[]'"),
    ("relations", "instances", "ALTER TABLE relations ADD COLUMN instances INTEGER NOT NULL DEFAULT 1"),
    ("effects", "skip_final", "ALTER TABLE effects ADD COLUMN skip_final INTEGER NOT NULL DEFAULT 0"),
]


def init_db():
    with tx() as conn:
        conn.executescript(SCHEMA)
        for table, col, sql in MIGRATIONS:
            if col not in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}:
                sql(conn) if callable(sql) else conn.execute(sql)


def row_to_dict(table: str, row: sqlite3.Row) -> dict:
    d = dict(row)
    for f in TABLES[table]["json"]:
        if f in d and isinstance(d[f], str):
            d[f] = json.loads(d[f])
    return d


def rows(conn, table: str, sql: str, params=()) -> list[dict]:
    return [row_to_dict(table, r) for r in conn.execute(sql, params).fetchall()]


def insert(conn, table: str, data: dict) -> int:
    spec = TABLES[table]
    cols = [f for f in spec["fields"] if f in data]
    vals = [json.dumps(data[c]) if c in spec["json"] else data[c] for c in cols]
    cur = conn.execute(
        f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals
    )
    return cur.lastrowid


def update(conn, table: str, id_: int, data: dict) -> None:
    spec = TABLES[table]
    cols = [f for f in spec["fields"] if f in data]
    if not cols:
        return
    vals = [json.dumps(data[c]) if c in spec["json"] else data[c] for c in cols]
    conn.execute(f"UPDATE {table} SET {','.join(c + '=?' for c in cols)} WHERE id=?", [*vals, id_])


def get(conn, table: str, id_: int) -> dict | None:
    r = conn.execute(f"SELECT * FROM {table} WHERE id=?", (id_,)).fetchone()
    return row_to_dict(table, r) if r else None


def load_model(conn, domain_id: int) -> dict | None:
    """Carica l'intero dominio come albero annidato."""
    domain = get(conn, "domains", domain_id)
    if not domain:
        return None
    entities = rows(conn, "entities", "SELECT * FROM entities WHERE domain_id=? ORDER BY pos, id", (domain_id,))
    ids = [e["id"] for e in entities] or [-1]
    ph = ",".join("?" * len(ids))
    attrs = rows(conn, "attributes", f"SELECT * FROM attributes WHERE entity_id IN ({ph}) ORDER BY id", ids)
    states = rows(conn, "states", f"SELECT * FROM states WHERE entity_id IN ({ph}) ORDER BY pos, id", ids)
    trans = rows(conn, "transitions", f"SELECT * FROM transitions WHERE entity_id IN ({ph}) ORDER BY id", ids)
    tids = [t["id"] for t in trans] or [-1]
    tph = ",".join("?" * len(tids))
    guards = rows(conn, "guards", f"SELECT * FROM guards WHERE transition_id IN ({tph}) ORDER BY id", tids)
    effects = rows(conn, "effects", f"SELECT * FROM effects WHERE transition_id IN ({tph}) ORDER BY id", tids)

    for t in trans:
        t["guards"] = [g for g in guards if g["transition_id"] == t["id"]]
        t["effects"] = [e for e in effects if e["transition_id"] == t["id"]]
    for e in entities:
        e["attributes"] = [a for a in attrs if a["entity_id"] == e["id"]]
        e["states"] = [s for s in states if s["entity_id"] == e["id"]]
        e["transitions"] = [t for t in trans if t["entity_id"] == e["id"]]

    domain["entities"] = entities
    domain["relations"] = rows(conn, "relations", "SELECT * FROM relations WHERE domain_id=? ORDER BY id", (domain_id,))
    domain["invariants"] = rows(conn, "invariants", "SELECT * FROM invariants WHERE domain_id=? ORDER BY id", (domain_id,))
    return domain

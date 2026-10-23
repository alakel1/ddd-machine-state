from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .engine import Engine
from .portability import export_markdown, import_model
from .relations import derived_invariants
from .seed import seed_if_empty

app = FastAPI(title="DDD Machine Status")


@app.on_event("startup")
def startup():
    db.init_db()
    seed_if_empty()


def _model_or_404(conn, domain_id: int) -> dict:
    m = db.load_model(conn, domain_id)
    if not m:
        raise HTTPException(404, "Dominio non trovato")
    return m


# ---------------- domini ----------------
@app.get("/api/domains")
def list_domains():
    with db.tx() as conn:
        return db.rows(conn, "domains", """
            SELECT d.*, (SELECT COUNT(*) FROM entities e WHERE e.domain_id=d.id) AS entity_count,
                   (SELECT COUNT(*) FROM invariants i WHERE i.domain_id=d.id) AS invariant_count
            FROM domains d ORDER BY d.id""")


@app.get("/api/domains/{domain_id}/model")
def get_model(domain_id: int):
    with db.tx() as conn:
        m = _model_or_404(conn, domain_id)
    m["derived_invariants"] = derived_invariants(m)  # sola lettura: nascono dalle composizioni
    return m


@app.get("/api/domains/{domain_id}/export")
def export_json(domain_id: int):
    with db.tx() as conn:
        m = _model_or_404(conn, domain_id)
        m["scenarios"] = db.rows(conn, "scenarios", "SELECT * FROM scenarios WHERE domain_id=?", (domain_id,))
        return {"format": "ddd-machine-status", "version": 1, "domain": m}


@app.get("/api/domains/{domain_id}/export.md", response_class=PlainTextResponse)
def export_md(domain_id: int):
    with db.tx() as conn:
        return export_markdown(_model_or_404(conn, domain_id))


@app.post("/api/domains/import")
def import_json(payload: dict = Body(...)):
    model = payload.get("domain", payload)
    if not isinstance(model, dict) or "entities" not in model:
        raise HTTPException(400, "File non valido: manca 'entities'")
    with db.tx() as conn:
        return {"id": import_model(conn, model)}


@app.post("/api/domains/{domain_id}/duplicate")
def duplicate(domain_id: int):
    with db.tx() as conn:
        m = _model_or_404(conn, domain_id)
        m["scenarios"] = db.rows(conn, "scenarios", "SELECT * FROM scenarios WHERE domain_id=?", (domain_id,))
        m["name"] = f"{m['name']} (copia)"
        return {"id": import_model(conn, m)}


# ---------------- motore ----------------
@app.post("/api/domains/{domain_id}/evaluate")
def evaluate(domain_id: int, payload: dict = Body(default={})):
    with db.tx() as conn:
        return Engine(_model_or_404(conn, domain_id)).evaluate(payload.get("state") or {})


@app.post("/api/domains/{domain_id}/apply")
def apply(domain_id: int, payload: dict = Body(...)):
    with db.tx() as conn:
        eng = Engine(_model_or_404(conn, domain_id))
    return eng.apply(payload.get("state") or {}, int(payload["transition_id"]), bool(payload.get("force")))


@app.get("/api/domains/{domain_id}/initial")
def initial(domain_id: int):
    with db.tx() as conn:
        return Engine(_model_or_404(conn, domain_id)).initial_state()


@app.get("/api/domains/{domain_id}/analysis")
def analysis(domain_id: int):
    with db.tx() as conn:
        eng = Engine(_model_or_404(conn, domain_id))
    return eng.analyze()


@app.get("/api/domains/{domain_id}/matrix")
def matrix(domain_id: int, a: int, b: int):
    with db.tx() as conn:
        eng = Engine(_model_or_404(conn, domain_id))
    if a not in eng.order or b not in eng.order:
        raise HTTPException(400, "Entità senza stati")
    return eng.matrix(a, b)


@app.get("/api/domains/{domain_id}/scenarios")
def scenarios(domain_id: int):
    with db.tx() as conn:
        return db.rows(conn, "scenarios", "SELECT * FROM scenarios WHERE domain_id=? ORDER BY id DESC", (domain_id,))


# ---------------- CRUD generico ----------------
def _check_table(table: str):
    if table not in db.TABLES:
        raise HTTPException(404, "Risorsa sconosciuta")


@app.post("/api/{table}")
def create(table: str, payload: dict = Body(...)):
    _check_table(table)
    with db.tx() as conn:
        new_id = db.insert(conn, table, payload)
        return db.get(conn, table, new_id)


@app.put("/api/{table}/{id_}")
def update(table: str, id_: int, payload: dict = Body(...)):
    _check_table(table)
    with db.tx() as conn:
        if not db.get(conn, table, id_):
            raise HTTPException(404, "Elemento non trovato")
        # un solo stato iniziale per entità
        if table == "states" and payload.get("is_initial"):
            ent = db.get(conn, "states", id_)["entity_id"]
            conn.execute("UPDATE states SET is_initial=0 WHERE entity_id=? AND id<>?", (ent, id_))
        db.update(conn, table, id_, payload)
        return db.get(conn, table, id_)


@app.delete("/api/{table}/{id_}")
def delete(table: str, id_: int):
    _check_table(table)
    with db.tx() as conn:
        conn.execute(f"DELETE FROM {table} WHERE id=?", (id_,))
    return {"ok": True}


# ---------------- frontend buildato ----------------
DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
# in Docker dist/ può esistere vuota (la crea il bind mount): serve solo se è stata buildata
if (DIST / "index.html").is_file() and (DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = DIST / path
        return FileResponse(f if path and f.is_file() else DIST / "index.html")

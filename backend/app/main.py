from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import db, editing
from .engine import Engine, is_create
from .i18n import translator
from .portability import export_markdown, import_model
from .relations import derived_invariants, gone_states, off_states
from .seed import seed_if_empty

app = FastAPI(title="DDD Machine Status")


@app.on_event("startup")
def startup():
    db.init_db()
    seed_if_empty()


def lang_of(request: Request) -> str:
    """Lingua dei messaggi: ?lang= (link di download), header X-Lang (frontend) o Accept-Language."""
    h = request.headers
    return translator(request.query_params.get("lang") or h.get("x-lang") or h.get("accept-language")).lang


def _model_or_404(conn, domain_id: int, lang: str) -> dict:
    m = db.load_model(conn, domain_id)
    if not m:
        raise HTTPException(404, translator(lang)("api.domain_not_found"))
    return m


# ---------------- domini ----------------
@app.get("/api/domains")
def list_domains():
    with db.tx() as conn:
        return db.rows(conn, "domains", """
            SELECT d.*, (SELECT COUNT(*) FROM entities e WHERE e.domain_id=d.id) AS entity_count,
                   (SELECT COUNT(*) FROM invariants i WHERE i.domain_id=d.id) AS invariant_count
            FROM domains d ORDER BY d.id""")


def model_payload(conn, domain_id: int, lang: str) -> dict:
    """Il modello come lo usa il frontend: con i dati ricavati dalle regole del motore, per non ricalcolarli lì."""
    m = _model_or_404(conn, domain_id, lang)
    eng = Engine(m, lang)
    for e in m["entities"]:
        e["gone_states"], e["off_states"] = gone_states(e), off_states(e)
        for t in e["transitions"]:
            # stati da cui l'azione parte davvero ("da qualsiasi" = ogni stato non finale diverso dall'arrivo)
            t["from_states"] = [] if is_create(t) else [s["id"] for s in e["states"] if eng.applies_from(t, s["id"])]
            for g in t["guards"]:
                g.pop("each", None)  # forma interna del motore per le guardie "per ogni"
    m["derived_invariants"] = derived_invariants(m, eng.t)  # sola lettura: nascono dalle composizioni
    return m


def _not_found(request: Request):
    raise HTTPException(404, translator(lang_of(request))("api.not_found"))


@app.get("/api/domains/{domain_id}/model")
def get_model(domain_id: int, request: Request):
    with db.tx() as conn:
        return model_payload(conn, domain_id, lang_of(request))


@app.get("/api/domains/{domain_id}/export")
def export_json(domain_id: int, request: Request):
    with db.tx() as conn:
        m = _model_or_404(conn, domain_id, lang_of(request))
        m["scenarios"] = db.rows(conn, "scenarios", "SELECT * FROM scenarios WHERE domain_id=?", (domain_id,))
        return {"format": "ddd-machine-status", "version": 3, "domain": m}


@app.get("/api/domains/{domain_id}/export.md", response_class=PlainTextResponse)
def export_md(domain_id: int, request: Request):
    lang = lang_of(request)
    with db.tx() as conn:
        return export_markdown(_model_or_404(conn, domain_id, lang), lang)


@app.post("/api/domains/import")
def import_json(request: Request, payload: dict = Body(...)):
    t = translator(lang_of(request))
    model = payload.get("domain", payload)
    if not isinstance(model, dict) or "entities" not in model:
        raise HTTPException(400, t("api.invalid_file"))
    with db.tx() as conn:
        return {"id": import_model(conn, model, t("api.imported"))}


@app.post("/api/domains/{domain_id}/duplicate")
def duplicate(domain_id: int, request: Request):
    t = translator(lang_of(request))
    with db.tx() as conn:
        m = _model_or_404(conn, domain_id, t.lang)
        m["scenarios"] = db.rows(conn, "scenarios", "SELECT * FROM scenarios WHERE domain_id=?", (domain_id,))
        m["name"] = t("api.copy", name=m["name"])
        return {"id": import_model(conn, m)}


# ---------------- motore ----------------
@app.post("/api/domains/{domain_id}/evaluate")
def evaluate(domain_id: int, request: Request, payload: dict = Body(default={})):
    lang = lang_of(request)
    with db.tx() as conn:
        return Engine(_model_or_404(conn, domain_id, lang), lang).evaluate(payload.get("state") or {})


@app.post("/api/domains/{domain_id}/apply")
def apply(domain_id: int, request: Request, payload: dict = Body(...)):
    lang = lang_of(request)
    with db.tx() as conn:
        eng = Engine(_model_or_404(conn, domain_id, lang), lang)
    r = eng.apply(payload.get("state") or {}, int(payload["transition_id"]), bool(payload.get("force")))
    # il simulatore mostra subito le azioni disponibili dal nuovo stato: niente seconda chiamata a /evaluate
    return {**r, "evaluation": eng.evaluate(r["state"])} if r["ok"] else r


@app.post("/api/domains/{domain_id}/fix")
def apply_fix(domain_id: int, request: Request, payload: dict = Body(...)):
    """Applica una soluzione proposta dall'analisi ("Risolvi"): operazioni in una sola transazione."""
    lang = lang_of(request)
    try:
        with db.tx() as conn:
            _model_or_404(conn, domain_id, lang)
            editing.apply_ops(conn, domain_id, payload.get("ops") or [])
            return {"model": model_payload(conn, domain_id, lang)}
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, translator(lang)("api.bad_fix")) from exc


@app.post("/api/domains/{domain_id}/replay")
def replay(domain_id: int, request: Request, payload: dict = Body(...)):
    lang = lang_of(request)
    with db.tx() as conn:
        eng = Engine(_model_or_404(conn, domain_id, lang), lang)
    return eng.replay(payload.get("trace") or [])


@app.get("/api/domains/{domain_id}/initial")
def initial(domain_id: int, request: Request):
    lang = lang_of(request)
    with db.tx() as conn:
        return Engine(_model_or_404(conn, domain_id, lang), lang).initial_state()


@app.get("/api/domains/{domain_id}/analysis")
def analysis(domain_id: int, request: Request):
    lang = lang_of(request)
    with db.tx() as conn:
        eng = Engine(_model_or_404(conn, domain_id, lang), lang)
    return eng.analyze()


@app.get("/api/domains/{domain_id}/matrix")
def matrix(domain_id: int, a: int, b: int, request: Request):
    lang = lang_of(request)
    with db.tx() as conn:
        eng = Engine(_model_or_404(conn, domain_id, lang), lang)
    if a not in eng.order or b not in eng.order:
        raise HTTPException(400, eng.t("api.no_states"))
    return eng.matrix(a, b)


@app.get("/api/domains/{domain_id}/scenarios")
def scenarios(domain_id: int):
    with db.tx() as conn:
        return db.rows(conn, "scenarios", "SELECT * FROM scenarios WHERE domain_id=? ORDER BY id DESC", (domain_id,))


# ---------------- modifiche con regole di dominio (prima del CRUD generico, che altrimenti le intercetta) ----------------
# rispondono con il modello aggiornato: il frontend non deve ricaricarlo
@app.post("/api/entities")
def create_entity(request: Request, payload: dict = Body(...)):
    with db.tx() as conn:
        eid = editing.create_entity(conn, payload)
        return {"id": eid, "model": model_payload(conn, payload["domain_id"], lang_of(request))}


@app.post("/api/states")
def create_state(request: Request, payload: dict = Body(...)):
    with db.tx() as conn:
        sid = editing.create_state(conn, payload)
        return {"id": sid, "model": model_payload(conn, editing.domain_of_entity(conn, payload["entity_id"]), lang_of(request))}


@app.get("/api/states/{state_id}/impact")
def state_impact(state_id: int, request: Request):
    with db.tx() as conn:
        if not db.get(conn, "states", state_id):
            _not_found(request)
        return editing.state_impact(conn, state_id)


@app.delete("/api/states/{state_id}")
def delete_state(state_id: int, request: Request):
    with db.tx() as conn:
        did = editing.domain_of_state(conn, state_id)
        if did is None:
            _not_found(request)
        editing.delete_state(conn, state_id)
        return {"model": model_payload(conn, did, lang_of(request))}


@app.post("/api/entities/{entity_id}/states/order")
def reorder_states(entity_id: int, request: Request, payload: dict = Body(...)):
    with db.tx() as conn:
        editing.reorder_states(conn, entity_id, [int(x) for x in payload["ids"]])
        return {"model": model_payload(conn, editing.domain_of_entity(conn, entity_id), lang_of(request))}


@app.post("/api/transitions")
@app.put("/api/transitions/{transition_id}")
def save_transition(request: Request, payload: dict = Body(...), transition_id: int | None = None):
    with db.tx() as conn:
        if transition_id is not None and not db.get(conn, "transitions", transition_id):
            _not_found(request)
        tid = editing.save_transition(conn, payload, transition_id)
        return {"id": tid, "model": model_payload(conn, editing.domain_of_entity(conn, payload["entity_id"]), lang_of(request))}


# ---------------- CRUD generico ----------------
def _check_table(table: str, request: Request):
    if table not in db.TABLES:
        raise HTTPException(404, translator(lang_of(request))("api.unknown_resource"))


@app.post("/api/{table}")
def create(table: str, request: Request, payload: dict = Body(...)):
    _check_table(table, request)
    with db.tx() as conn:
        new_id = db.insert(conn, table, payload)
        return db.get(conn, table, new_id)


@app.put("/api/{table}/{id_}")
def update(table: str, id_: int, request: Request, payload: dict = Body(...)):
    _check_table(table, request)
    with db.tx() as conn:
        if not db.get(conn, table, id_):
            raise HTTPException(404, translator(lang_of(request))("api.not_found"))
        # un solo stato iniziale per entità
        if table == "states" and payload.get("is_initial"):
            ent = db.get(conn, "states", id_)["entity_id"]
            conn.execute("UPDATE states SET is_initial=0 WHERE entity_id=? AND id<>?", (ent, id_))
        if table == "states" and "name" in payload:
            payload = {**payload, "name": editing.state_name(payload["name"])}
        db.update(conn, table, id_, payload)
        return db.get(conn, table, id_)


@app.delete("/api/{table}/{id_}")
def delete(table: str, id_: int, request: Request):
    _check_table(table, request)
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

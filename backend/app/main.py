import copy
import hashlib
import json
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import db, editing, history
from .engine import Engine, is_create
from .i18n import translator
from .portability import export_markdown, import_model
from .relations import derived_invariants, gone_states, off_states
from .seed import seed_if_empty

@asynccontextmanager
async def lifespan(_app):
    db.init_db()
    seed_if_empty()
    yield


app = FastAPI(title="DDD Machine Status", lifespan=lifespan)


def lang_of(request: Request) -> str:
    """Lingua dei messaggi: ?lang= (link di download), header X-Lang (frontend) o Accept-Language."""
    h = request.headers
    return translator(request.query_params.get("lang") or h.get("x-lang") or h.get("accept-language")).lang


def _model_or_404(conn, domain_id: int, lang: str) -> dict:
    m = db.load_model(conn, domain_id)
    if not m:
        raise HTTPException(404, translator(lang)("api.domain_not_found"))
    return m


def _record(conn, request: Request, domain_id, verb: str, table: str, name: str = "") -> None:
    """Istantanea per lo storico prima di una modifica, descritta come "Modifica: stato BOZZA"."""
    t = translator(lang_of(request))
    history.record(conn, domain_id, t("h.label", verb=t(f"h.verb.{verb}"), thing=t(f"h.table.{table}"), name=name or ""))


def _row_name(row: dict | None) -> str:
    row = row or {}
    return str(row.get("name") or row.get("action") or row.get("label") or "")


# ---------------- domini ----------------
@app.get("/api/domains")
def list_domains():
    with db.tx() as conn:
        return db.rows(conn, "domains", """
            SELECT d.*, (SELECT COUNT(*) FROM entities e WHERE e.domain_id=d.id) AS entity_count,
                   (SELECT COUNT(*) FROM invariants i WHERE i.domain_id=d.id) AS invariant_count
            FROM domains d ORDER BY d.id""")


_warming: dict[int, int] = {}  # dominio -> numero dell'ultima richiesta di calcolo anticipato


def _warm(domain_id: int, model: dict, lang: str) -> None:
    """Calcola l'esplorazione in background, così l'analisi è pronta quando la si apre (la cache è nel motore).
    Dopo una breve attesa parte solo l'ultima richiesta per il dominio: le modifiche a raffica non si accodano."""
    token = _warming[domain_id] = _warming.get(domain_id, 0) + 1

    def run():
        time.sleep(1.5)
        if _warming.get(domain_id) == token:
            try:
                Engine(model, lang).dynamic()[0].explore()
            except Exception:  # noqa: BLE001 - un calcolo anticipato fallito non deve fare danni: lo rifà l'analisi
                pass

    threading.Thread(target=run, daemon=True).start()


def model_payload(conn, domain_id: int, lang: str) -> dict:
    """Il modello come lo usa il frontend: con i dati ricavati dalle regole del motore, per non ricalcolarli lì."""
    m = _model_or_404(conn, domain_id, lang)
    _warm(domain_id, copy.deepcopy(m), lang)
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
        m["expectations"] = _expectations(conn, domain_id)
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
        m["expectations"] = _expectations(conn, domain_id)
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
            history.record(conn, domain_id, translator(lang)("h.fix", title=payload.get("label") or ""))
            editing.apply_ops(conn, domain_id, payload.get("ops") or [])
            return {"model": model_payload(conn, domain_id, lang)}
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, translator(lang)("api.bad_fix")) from exc


@app.post("/api/domains/{domain_id}/fix/preview")
def preview_fix(domain_id: int, request: Request, payload: dict = Body(...)):
    """Cosa cambierebbe nell'analisi applicando una soluzione: la si applica in una transazione annullata."""
    lang = lang_of(request)
    conn = db.connect()
    try:
        before = _model_or_404(conn, domain_id, lang)
        editing.apply_ops(conn, domain_id, payload.get("ops") or [])
        after = db.load_model(conn, domain_id)
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, translator(lang)("api.bad_fix")) from exc
    finally:
        conn.rollback()
        conn.close()
    old, new = (Engine(m, lang).analyze()["issues"] for m in (before, after))
    relevant = lambda issues: {i["fingerprint"]: i for i in issues if i["severity"] in ("error", "warning")}
    old, new = relevant(old), relevant(new)
    titles = lambda xs: sorted({i["title"] for i in xs})[:6]
    gone = [old[f] for f in old.keys() - new.keys()]
    added = [new[f] for f in new.keys() - old.keys()]
    count = lambda xs, s: sum(1 for i in xs if i["severity"] == s)
    return {"resolved": {"error": count(gone, "error"), "warning": count(gone, "warning")},
            "introduced": {"error": count(added, "error"), "warning": count(added, "warning"), "titles": titles(added)}}


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
        acks = {r["fingerprint"]: r for r in conn.execute("SELECT * FROM acks WHERE domain_id=?", (domain_id,))}
        expectations = _expectations(conn, domain_id)
    result = eng.analyze()
    result["issues"] = _expectation_issues(eng, expectations) + result["issues"]
    # segnalazioni accettate o rimandate; "stale" se la parte di modello coinvolta è cambiata da allora
    for i in result["issues"]:
        a = acks.get(i["fingerprint"])
        i["ack"] = a and {"status": a["status"], "note": a["note"], "created_at": a["created_at"],
                          "stale": a["context"] != i["context"]}
    return result


def _expectations(conn, domain_id: int) -> list[dict]:
    return db.rows(conn, "expectations", "SELECT * FROM expectations WHERE domain_id=? ORDER BY id", (domain_id,))


def _expectation_issues(eng: Engine, expectations: list[dict]) -> list[dict]:
    """Le aspettative non rispettate, come segnalazioni dell'analisi (errori)."""
    out = []
    for x in expectations:
        res = eng.check_expectation(x)
        if res["ok"]:
            continue
        if x["kind"] == "goal":
            ents = [r["entity_id"] for r in x["rules"]]
            detail = eng.t("x.goal_unreachable", goal="; ".join(eng._guard_text({"kind": "state", **r}) for r in x["rules"]))
        else:
            ents = sorted({s["entity_id"] for s in x["trace"] if s.get("entity_id")})
            detail = (eng.t("x.path_blocked", n=res["done"] + 1, action=res["failed"] or "?", reason=res["reason"] or "")
                      if x.get("expect", "possible") == "possible" else eng.t("x.path_possible"))
        out.append({"severity": "error", "category": "expectations", "code": f"x.{x['kind']}",
                    "title": eng.t("x.failed", name=x["name"]), "detail": detail, "expectation_id": x["id"],
                    "entity_id": ents[0] if ents else None, "entity_ids": sorted(set(ents)), "fixes": [],
                    "fingerprint": f"x{x['id']}", "context": hashlib.sha1(json.dumps(res, default=str).encode()).hexdigest()[:16],
                    "cause": {"key": f"expectation:{x['id']}", "title": eng.t("x.failed", name=x["name"])}})
    return out


@app.get("/api/domains/{domain_id}/expectations")
def get_expectations(domain_id: int, request: Request):
    """Le aspettative del dominio, ognuna con l'esito della verifica sul modello attuale."""
    lang = lang_of(request)
    with db.tx() as conn:
        eng = Engine(_model_or_404(conn, domain_id, lang), lang)
        expectations = _expectations(conn, domain_id)
    return [{**x, "result": eng.check_expectation(x)} for x in expectations]


@app.put("/api/domains/{domain_id}/acks")
def put_ack(domain_id: int, request: Request, payload: dict = Body(...)):
    """Accetta ("è voluto") o rimanda una segnalazione, con una nota; si riscrive se esiste già."""
    with db.tx() as conn:
        _model_or_404(conn, domain_id, lang_of(request))
        conn.execute("""INSERT INTO acks (domain_id, fingerprint, status, note, context, title) VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT (domain_id, fingerprint) DO UPDATE SET status=excluded.status, note=excluded.note,
                        context=excluded.context, title=excluded.title, created_at=CURRENT_TIMESTAMP""",
                     (domain_id, payload["fingerprint"], payload.get("status", "accepted"), payload.get("note", ""),
                      payload.get("context", ""), payload.get("title", "")))
    return {"ok": True}


@app.delete("/api/domains/{domain_id}/acks/{fingerprint}")
def delete_ack(domain_id: int, fingerprint: str):
    with db.tx() as conn:
        conn.execute("DELETE FROM acks WHERE domain_id=? AND fingerprint=?", (domain_id, fingerprint))
    return {"ok": True}


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
        _record(conn, request, payload["domain_id"], "create", "entities", payload.get("name"))
        eid = editing.create_entity(conn, payload)
        return {"id": eid, "model": model_payload(conn, payload["domain_id"], lang_of(request))}


@app.post("/api/states")
def create_state(request: Request, payload: dict = Body(...)):
    with db.tx() as conn:
        _record(conn, request, editing.domain_of_entity(conn, payload["entity_id"]), "create", "states",
                editing.state_name(payload.get("name", "")))
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
        _record(conn, request, did, "delete", "states", _row_name(db.get(conn, "states", state_id)))
        editing.delete_state(conn, state_id)
        return {"model": model_payload(conn, did, lang_of(request))}


@app.post("/api/entities/{entity_id}/states/order")
def reorder_states(entity_id: int, request: Request, payload: dict = Body(...)):
    with db.tx() as conn:
        _record(conn, request, editing.domain_of_entity(conn, entity_id), "reorder", "states",
                _row_name(db.get(conn, "entities", entity_id)))
        editing.reorder_states(conn, entity_id, [int(x) for x in payload["ids"]])
        return {"model": model_payload(conn, editing.domain_of_entity(conn, entity_id), lang_of(request))}


@app.post("/api/transitions")
@app.put("/api/transitions/{transition_id}")
def save_transition(request: Request, payload: dict = Body(...), transition_id: int | None = None):
    with db.tx() as conn:
        if transition_id is not None and not db.get(conn, "transitions", transition_id):
            _not_found(request)
        _record(conn, request, editing.domain_of_entity(conn, payload["entity_id"]),
                "update" if transition_id else "create", "transitions", payload.get("action"))
        tid = editing.save_transition(conn, payload, transition_id)
        return {"id": tid, "model": model_payload(conn, editing.domain_of_entity(conn, payload["entity_id"]), lang_of(request))}


# ---------------- storico ----------------
@app.get("/api/domains/{domain_id}/history")
def get_history(domain_id: int, request: Request):
    lang = lang_of(request)
    with db.tx() as conn:
        _model_or_404(conn, domain_id, lang)
        return history.listing(conn, domain_id, translator(lang))


@app.post("/api/domains/{domain_id}/undo")
def undo(domain_id: int, request: Request):
    lang = lang_of(request)
    with db.tx() as conn:
        _model_or_404(conn, domain_id, lang)
        label = history.undo(conn, domain_id)
        return {"undone": label, "model": model_payload(conn, domain_id, lang)}


@app.post("/api/domains/{domain_id}/history/{snapshot_id}/restore")
def restore(domain_id: int, snapshot_id: int, request: Request):
    lang = lang_of(request)
    with db.tx() as conn:
        _model_or_404(conn, domain_id, lang)
        if not history.restore(conn, domain_id, snapshot_id, translator(lang)("h.restore")):
            _not_found(request)
        return {"model": model_payload(conn, domain_id, lang)}


@app.get("/api/domains/{domain_id}/compare")
def compare(domain_id: int, request: Request, base: int, target: int | None = None):
    """Differenze tra due versioni (target assente = il modello attuale)."""
    lang = lang_of(request)
    with db.tx() as conn:
        a, b = history.load(conn, domain_id, base), history.load(conn, domain_id, target)
        if not a or not b:
            _not_found(request)
        return {"changes": history.diff(a, b, translator(lang))}


# ---------------- CRUD generico ----------------
# fuori dallo storico: scenari del simulatore e aspettative (non fanno parte del modello);
# un dominio nuovo non ha ancora storia e uno eliminato porta via anche la sua
UNTRACKED = {"scenarios", "expectations"}
def _check_table(table: str, request: Request):
    if table not in db.TABLES:
        raise HTTPException(404, translator(lang_of(request))("api.unknown_resource"))


@app.post("/api/{table}")
def create(table: str, request: Request, payload: dict = Body(...)):
    _check_table(table, request)
    with db.tx() as conn:
        if table not in UNTRACKED:
            _record(conn, request, editing.domain_of_payload(conn, payload), "create", table, _row_name(payload))
        new_id = db.insert(conn, table, payload)
        return db.get(conn, table, new_id)


@app.put("/api/{table}/{id_}")
def update(table: str, id_: int, request: Request, payload: dict = Body(...)):
    _check_table(table, request)
    with db.tx() as conn:
        row = db.get(conn, table, id_)
        if not row:
            raise HTTPException(404, translator(lang_of(request))("api.not_found"))
        if table not in UNTRACKED:
            _record(conn, request, editing.domain_of(conn, table, id_), "update", table, _row_name(row))
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
        if table not in UNTRACKED and table != "domains":
            _record(conn, request, editing.domain_of(conn, table, id_), "delete", table, _row_name(db.get(conn, table, id_)))
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

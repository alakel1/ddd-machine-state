"""Dominio di esempio: ciclo di vita di un ordine e-commerce.

Ogni entità nasce tramite azioni di creazione (anche più d'una, con controlli
diversi). Contiene di proposito alcune lacune per mostrare cosa trova l'analisi:
ordine creato da call center senza i controlli della conferma, rimborso senza
guardia sull'ordine, spedizione smarrita senza uscita, ordine chiuso senza fattura.
"""
from . import db
from .engine import NONE

EXAMPLE_NAME = "E-commerce · Ordini"


def seed_if_empty():
    with db.tx() as conn:
        if conn.execute("SELECT COUNT(*) FROM domains").fetchone()[0]:
            return
        seed(conn)


def seed(conn, name: str = EXAMPLE_NAME) -> int:
    did = db.insert(conn, "domains", {
        "name": name,
        "description": "Ciclo di vita di un ordine online con pagamento, spedizione e fatturazione. "
                       "Le entità nascono in modi diversi (azioni di creazione). "
                       "Esempio con alcune lacune volute: lancia l'Analisi per scoprirle.",
    })
    E, S = {}, {}

    def entity(name, context, color, desc, states, attrs=()):
        E[name] = db.insert(conn, "entities", {"domain_id": did, "name": name, "context": context, "color": color,
                                               "description": desc, "pos": len(E)})
        S[(name, None)] = NONE
        for i, (sname, flags, sdesc) in enumerate(states):
            S[(name, sname)] = db.insert(conn, "states", {
                "entity_id": E[name], "name": sname, "description": sdesc, "pos": i,
                "is_initial": int("i" in flags), "is_final": int("f" in flags)})
        for aname, atype, req in attrs:
            db.insert(conn, "attributes", {"entity_id": E[name], "name": aname, "type": atype, "required": req})

    def tr(ent, frm, action, to, actor="", desc="", guards=(), effects=(), create=False):
        tid = db.insert(conn, "transitions", {
            "entity_id": E[ent], "kind": "create" if create else "normal",
            "from_state_id": S[(ent, frm)] if frm else None,
            "to_state_id": S[(ent, to)], "action": action, "actor": actor, "description": desc})
        for g in guards:
            if isinstance(g, str):
                db.insert(conn, "guards", {"transition_id": tid, "kind": "rule", "description": g})
            else:
                gent, gstates, *neg = g
                db.insert(conn, "guards", {"transition_id": tid, "kind": "state", "entity_id": E[gent],
                                           "state_ids": [S[(gent, s)] for s in gstates], "negate": int(bool(neg))})
        for fent, fstate in effects:
            db.insert(conn, "effects", {"transition_id": tid, "entity_id": E[fent], "to_state_id": S[(fent, fstate)]})

    def create(ent, action, to, actor="", desc="", guards=(), effects=()):
        tr(ent, None, action, to, actor, desc, guards, effects, create=True)

    entity("Ordine", "Vendite", "indigo", "Aggregato radice: l'impegno d'acquisto del cliente.", [
        ("BOZZA", "", "Carrello convertito, non ancora confermato"),
        ("CONFERMATO", "", "Il cliente ha confermato l'ordine"),
        ("PAGATO", "", "Incasso registrato"),
        ("IN_SPEDIZIONE", "", "Merce affidata al corriere"),
        ("CONSEGNATO", "", "Merce ricevuta dal cliente"),
        ("CHIUSO", "f", "Finestra di reso scaduta"),
        ("RESO", "f", "Merce restituita"),
        ("ANNULLATO", "f", "Ordine annullato"),
    ], [("numero", "string", 1), ("totale", "decimal", 1), ("canale", "enum", 1), ("note", "text", 0)])

    entity("Pagamento", "Pagamenti", "emerald", "Transazione verso il PSP o bonifico.", [
        ("IN_ATTESA", "", "In attesa di autorizzazione dal PSP"),
        ("AUTORIZZATO", "", "Fondi bloccati sulla carta"),
        ("CATTURATO", "", "Fondi incassati"),
        ("FALLITO", "", "Autorizzazione rifiutata"),
        ("RIMBORSATO", "f", ""),
        ("ANNULLATO", "f", "Autorizzazione stornata"),
    ], [("importo", "decimal", 1), ("metodo", "enum", 1), ("id_psp", "string", 0)])

    entity("Spedizione", "Logistica", "amber", "Collo affidato al vettore.", [
        ("PRONTA", "", "Pacco preparato in magazzino"),
        ("SPEDITA", "", ""),
        ("CONSEGNATA", "f", ""),
        ("SMARRITA", "", "Il corriere ha perso il collo"),
        ("ANNULLATA", "f", ""),
    ], [("tracking", "string", 0), ("vettore", "string", 1)])

    entity("Fattura", "Contabilità", "rose", "Documento fiscale.", [
        ("EMESSA", "", ""),
        ("STORNATA", "f", "Emessa nota di credito"),
    ], [("numero", "string", 1), ("data_emissione", "date", 1)])

    # Ordine: due modi di nascere
    create("Ordine", "crea da carrello", "BOZZA", "Cliente")
    create("Ordine", "crea da call center", "CONFERMATO", "Operatore",
           "L'operatore inserisce l'ordine già confermato al telefono.")  # lacuna: salta i controlli di «conferma»
    tr("Ordine", "BOZZA", "conferma", "CONFERMATO", "Cliente", guards=["Almeno una riga d'ordine con quantità > 0"])
    tr("Ordine", "CONFERMATO", "registra incasso", "PAGATO", "Sistema", guards=[("Pagamento", ["CATTURATO"])])
    tr("Ordine", "PAGATO", "evadi", "IN_SPEDIZIONE", "Magazzino", guards=[("Spedizione", ["PRONTA"])],
       effects=[("Spedizione", "SPEDITA")])
    tr("Ordine", "IN_SPEDIZIONE", "conferma consegna", "CONSEGNATO", "Sistema", guards=[("Spedizione", ["CONSEGNATA"])])
    tr("Ordine", "CONSEGNATO", "chiudi", "CHIUSO", "Sistema", guards=["Trascorsi 14 giorni dalla consegna"])
    tr("Ordine", "CONSEGNATO", "registra reso", "RESO", "Operatore", guards=["Entro 14 giorni dalla consegna"],
       effects=[("Pagamento", "RIMBORSATO")])
    tr("Ordine", "BOZZA", "annulla", "ANNULLATO", "Cliente")
    tr("Ordine", "CONFERMATO", "annulla", "ANNULLATO", "Cliente", effects=[("Pagamento", "ANNULLATO")])
    tr("Ordine", "PAGATO", "annulla", "ANNULLATO", "Operatore", effects=[("Pagamento", "RIMBORSATO")])

    # Pagamento: carta (passa dal PSP) o bonifico (nasce già incassato)
    create("Pagamento", "paga con carta", "IN_ATTESA", "Cliente", guards=[("Ordine", ["CONFERMATO"])])
    create("Pagamento", "registra bonifico", "CATTURATO", "Amministrazione",
           guards=[("Ordine", ["CONFERMATO"]), "Bonifico riscontrato sull'estratto conto"])
    tr("Pagamento", "IN_ATTESA", "autorizza", "AUTORIZZATO", "PSP")
    tr("Pagamento", "IN_ATTESA", "rifiuta", "FALLITO", "PSP")
    tr("Pagamento", "AUTORIZZATO", "cattura", "CATTURATO", "Sistema")
    tr("Pagamento", "FALLITO", "riprova", "IN_ATTESA", "Cliente")
    tr("Pagamento", "CATTURATO", "rimborsa", "RIMBORSATO", "Operatore")  # lacuna: manca la guardia sull'Ordine
    tr("Pagamento", "IN_ATTESA", "storna", "ANNULLATO", "Sistema")
    tr("Pagamento", "AUTORIZZATO", "storna", "ANNULLATO", "Sistema")

    # Spedizione
    create("Spedizione", "prepara pacco", "PRONTA", "Magazzino", guards=[("Ordine", ["PAGATO"])])
    tr("Spedizione", "PRONTA", "affida al corriere", "SPEDITA", "Magazzino")
    tr("Spedizione", "SPEDITA", "consegna", "CONSEGNATA", "Corriere")
    tr("Spedizione", "SPEDITA", "segnala smarrimento", "SMARRITA", "Corriere")
    tr("Spedizione", None, "annulla spedizione", "ANNULLATA", "Sistema", guards=[("Ordine", ["ANNULLATO"])])

    # Fattura
    create("Fattura", "emetti", "EMESSA", "Sistema",
           guards=[("Ordine", ["PAGATO", "IN_SPEDIZIONE", "CONSEGNATO", "CHIUSO"])])
    tr("Fattura", "EMESSA", "emetti nota di credito", "STORNATA", "Amministrazione",
       guards=[("Pagamento", ["RIMBORSATO"])])

    for src, dst, label in [("Ordine", "Pagamento", "è saldato da"), ("Ordine", "Spedizione", "è evaso con"),
                            ("Ordine", "Fattura", "è documentato da")]:
        db.insert(conn, "relations", {"domain_id": did, "source_id": E[src], "target_id": E[dst],
                                      "cardinality": "1-1", "label": label})

    def inv(name, sev, desc, *rules):
        db.insert(conn, "invariants", {"domain_id": did, "name": name, "severity": sev, "description": desc, "rules": [
            {"entity_id": E[ent], "state_ids": [S[(ent, s)] for s in sts], "negate": int(neg)}
            for ent, sts, neg in rules]})

    inv("Merce in viaggio senza incasso", "error", "Non si spedisce/consegna se il pagamento non è catturato.",
        ("Ordine", ["IN_SPEDIZIONE", "CONSEGNATO", "CHIUSO"], False), ("Pagamento", ["CATTURATO"], True))
    inv("Spedizione orfana", "error", "Un ordine annullato non può avere colli in movimento.",
        ("Ordine", ["ANNULLATO"], False), ("Spedizione", ["PRONTA", "SPEDITA"], False))
    inv("Rimborso con fattura attiva", "warning", "Dopo un rimborso serve la nota di credito.",
        ("Pagamento", ["RIMBORSATO"], False), ("Fattura", ["EMESSA"], False),
        ("Ordine", ["ANNULLATO", "RESO", "CHIUSO"], False))
    inv("Ordine chiuso senza fattura", "warning", "Ogni ordine chiuso deve essere stato fatturato.",
        ("Ordine", ["CHIUSO"], False), ("Fattura", [None], False))

    db.insert(conn, "scenarios", {"domain_id": did, "name": "Pagamento catturato, ordine ancora confermato",
                                  "description": "Cosa succede se il cliente annulla proprio ora?",
                                  "state": {str(E["Ordine"]): S[("Ordine", "CONFERMATO")],
                                            str(E["Pagamento"]): S[("Pagamento", "CATTURATO")]},
                                  "history": []})
    return did

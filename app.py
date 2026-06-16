"""
Soléo — Backend Flask
API : leads installateurs + rapport premium en paiement MANUEL (preuve via WhatsApp/email,
validation par l'admin).

Endpoints
  GET  /                      -> sert le frontend (static/index.html)
  POST /api/leads             -> enregistre un lead installateur
  POST /api/pay/request       -> crée une demande de rapport, renvoie référence + instructions
  GET  /api/report/<ref>      -> état (en attente / payé) + données du rapport
  GET  /admin                 -> panneau admin (valide les paiements) — protégé par token
  GET  /api/admin/requests    -> demandes de paiement (protégé)
  POST /api/admin/approve     -> approuve une demande -> débloque le rapport (protégé)
  POST /api/admin/reject      -> rejette une demande (protégé)
  GET  /api/admin/leads       -> liste des leads (protégé)

Conçu pour un déploiement Render. Voir README.md.
"""

import os
import json
import sqlite3
import secrets
from datetime import datetime, timezone
from urllib.parse import quote

from flask import Flask, request, jsonify, send_from_directory, redirect

# Chargement du .env en local (sans effet si python-dotenv absent ou en prod)
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# --------------------------------------------------------------------------
# Configuration (via variables d'environnement)
# --------------------------------------------------------------------------
REPORT_PRICE = int(os.environ.get("REPORT_PRICE", "2000"))     # prix du rapport (FCFA)
CURRENCY = os.environ.get("CURRENCY", "XOF")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")               # protège le panneau admin
# Paiement manuel : numéro à payer + contact pour la preuve
PAY_NUMBER = os.environ.get("PAY_NUMBER", "07 00 00 00 00 (Orange Money / Wave)")
ADMIN_WHATSAPP = os.environ.get("ADMIN_WHATSAPP", "")         # format intl sans +, ex. 2250700000000
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")
# Sur Render free tier, le disque est éphémère : pointez DB_PATH vers un disque persistant.
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "soleo.db"))
# Persistance production : si DATABASE_URL est défini (ex. Neon/Supabase), on bascule sur Postgres.
DATABASE_URL = os.environ.get("DATABASE_URL", "")
IS_PG = DATABASE_URL.startswith("postgres")
if IS_PG:
    import psycopg
    from psycopg.rows import dict_row

app = Flask(__name__, static_folder="static")


# --------------------------------------------------------------------------
# Base de données — abstraction SQLite (local) / PostgreSQL (production)
# Le reste du code utilise conn.execute(sql, params) avec des '?' ; on traduit
# en '%s' pour Postgres. conn.insert_id(...) gère le retour d'ID dans les 2 cas.
# --------------------------------------------------------------------------
class DB:
    def __enter__(self):
        if IS_PG:
            self.conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
        else:
            self.conn = sqlite3.connect(DB_PATH)
            self.conn.row_factory = sqlite3.Row
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.conn.commit()
        self.conn.close()

    def _q(self, sql):
        return sql.replace("?", "%s") if IS_PG else sql

    def execute(self, sql, params=()):
        cur = self.conn.cursor()
        cur.execute(self._q(sql), params)
        return cur

    def insert_id(self, sql, params=()):
        if IS_PG:
            cur = self.conn.cursor()
            cur.execute(self._q(sql) + " RETURNING id", params)
            return cur.fetchone()["id"]
        return self.conn.execute(sql, params).lastrowid


def db():
    return DB()


def init_db():
    pk = "SERIAL PRIMARY KEY" if IS_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"
    stmts = [
        f"""CREATE TABLE IF NOT EXISTS leads (
            id          {pk},
            created_at  TEXT NOT NULL,
            nom         TEXT NOT NULL,
            telephone   TEXT NOT NULL,
            ville       TEXT,
            profil      TEXT,
            stockage    TEXT,
            kwc         REAL,
            payback_ans REAL,
            projet      TEXT,
            statut      TEXT DEFAULT 'nouveau'
        )""",
        f"""CREATE TABLE IF NOT EXISTS payments (
            id             {pk},
            created_at     TEXT NOT NULL,
            tx_ref         TEXT UNIQUE NOT NULL,
            transaction_id TEXT,
            email          TEXT,
            telephone      TEXT,
            montant        INTEGER,
            devise         TEXT,
            statut         TEXT DEFAULT 'en_attente',
            paye           INTEGER DEFAULT 0,
            projet         TEXT
        )""",
    ]
    with db() as conn:
        for s in stmts:
            conn.execute(s)


# --------------------------------------------------------------------------
# Moteur de calcul serveur (source autoritative pour le rapport payant)
# Miroir du moteur frontend — à terme remplacé par le moteur ESTHER.
# --------------------------------------------------------------------------
PSH = {  # irradiation moyenne (kWh/m²/j) — à affiner via NASA POWER
    "Abidjan": 4.6, "Yamoussoukro": 5.0, "Bouaké": 5.2, "Korhogo": 5.6,
    "San-Pédro": 4.5, "Daloa": 5.0, "Man": 4.9, "Odienné": 5.5,
    "Gagnoa": 4.9, "Abengourou": 4.8, "Bondoukou": 5.4,
}
RATES = {"maison": 79, "pme": 100}        # FCFA/kWh TTC (CIE/ANARE-CI, 1ère tranche)
COSTS = {"avec": 1_200_000, "sans": 700_000}  # FCFA/kWc (marché CI)
PR = 0.78


def compute_report(projet: dict) -> dict:
    """Recalcule le dimensionnement côté serveur à partir des entrées brutes."""
    ville = projet.get("ville", "Abidjan")
    profil = projet.get("profil", "maison")
    stockage = projet.get("stockage", "avec")
    psh = PSH.get(ville, 4.6)
    tarif = RATES.get(profil, 79)
    cpk = COSTS.get(stockage, 1_200_000)

    mode = projet.get("mode", "facture")
    saisie = float(projet.get("saisie", 0) or 0)
    kwh_mois = saisie / tarif if mode == "facture" else saisie
    if kwh_mois <= 0:
        return {"valide": False}

    daily = kwh_mois / 30.0
    kwc = max(0.5, round(daily / (psh * PR), 1))
    prod_an = kwc * psh * PR * 365
    conso_an = kwh_mois * 12
    couvert = min(prod_an, conso_an)
    econo_an = couvert * tarif
    cout = kwc * cpk
    payback = cout / econo_an if econo_an > 0 else 0

    return {
        "valide": True, "ville": ville, "profil": profil, "stockage": stockage,
        "psh": psh, "tarif_fcfa_kwh": tarif, "cout_fcfa_kwc": cpk,
        "kwh_mois": round(kwh_mois), "kwc": kwc,
        "production_an_kwh": round(prod_an), "economie_mois_fcfa": round(econo_an / 12),
        "economie_an_fcfa": round(econo_an), "cout_total_fcfa": round(cout),
        "payback_ans": round(payback, 1), "gain_20ans_fcfa": round(econo_an * 20 - cout),
    }


def generate_report_pdf(payment_row):
    """
    Appelé à la validation admin. Génère le PDF (moteur de ce module — extensible vers ESTHER)
    et l'envoie par email si le SMTP est configuré. Le PDF reste téléchargeable à la demande.
    """
    projet = json.loads(payment_row["projet"] or "{}")
    rep = compute_report(projet)
    if not rep.get("valide"):
        return
    try:
        from report import build_report_pdf
        pdf = build_report_pdf(rep, reference=payment_row["tx_ref"], email=payment_row["email"])
        _send_report_email(payment_row["email"], payment_row["tx_ref"], pdf)  # no-op si SMTP absent
    except Exception as e:
        app.logger.warning(f"Génération/envoi PDF différé: {e}")


def _send_report_email(to_email, reference, pdf_bytes):
    """Envoi optionnel par email (SMTP). Sans config SMTP, ne fait rien (téléchargement reste dispo)."""
    host = os.environ.get("SMTP_HOST")
    if not (host and to_email):
        return
    import smtplib
    from email.message import EmailMessage
    msg = EmailMessage()
    msg["Subject"] = f"Votre rapport solaire Soléo — {reference}"
    msg["From"] = os.environ.get("SMTP_FROM", os.environ.get("SMTP_USER", ""))
    msg["To"] = to_email
    msg.set_content("Bonjour,\n\nVoici votre rapport de dimensionnement solaire Soléo en pièce jointe.\n\nMerci de votre confiance.")
    msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf",
                       filename=f"rapport-soleo-{reference}.pdf")
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587"))) as s:
        s.starttls()
        if os.environ.get("SMTP_USER"):
            s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASS", ""))
        s.send_message(msg)


# --------------------------------------------------------------------------
# Frontend
# --------------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# --------------------------------------------------------------------------
# Leads installateurs
# --------------------------------------------------------------------------
@app.route("/api/leads", methods=["POST"])
def create_lead():
    data = request.get_json(silent=True) or {}
    nom = (data.get("nom") or "").strip()
    tel = (data.get("telephone") or "").strip()
    if not nom or not tel:
        return jsonify(ok=False, error="Nom et téléphone requis."), 400

    projet = data.get("projet") or {}
    with db() as conn:
        lead_id = conn.insert_id(
            """INSERT INTO leads (created_at, nom, telephone, ville, profil, stockage, kwc, payback_ans, projet)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (datetime.now(timezone.utc).isoformat(), nom, tel,
             projet.get("ville"), projet.get("profil"), projet.get("stockage"),
             projet.get("kwc"), projet.get("payback_ans"), json.dumps(projet, ensure_ascii=False)),
        )
    return jsonify(ok=True, id=lead_id)


# --------------------------------------------------------------------------
# Paiement MANUEL — l'utilisateur paie sur un numéro mobile money, puis envoie
# sa preuve (capture) via WhatsApp/email. L'admin valide ensuite la demande.
# --------------------------------------------------------------------------
def _make_ref():
    return "SOLEO-" + secrets.token_hex(3).upper()   # ex. SOLEO-9F2A1C


@app.route("/api/pay/request", methods=["POST"])
def pay_request():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    if "@" not in email:
        return jsonify(ok=False, error="Email invalide."), 400
    projet = data.get("projet") or {}
    tel = data.get("telephone")

    ref = _make_ref()
    with db() as conn:
        conn.execute(
            """INSERT INTO payments (created_at, tx_ref, email, telephone, montant, devise, statut, projet)
               VALUES (?,?,?,?,?,?, 'en_attente', ?)""",
            (datetime.now(timezone.utc).isoformat(), ref, email, tel,
             REPORT_PRICE, CURRENCY, json.dumps(projet, ensure_ascii=False)),
        )

    # Message WhatsApp pré-rempli avec la référence (la capture est jointe par l'utilisateur).
    msg = (f"Bonjour, j'ai payé {REPORT_PRICE} FCFA pour mon rapport Soléo. "
           f"Référence : {ref}. Voici ma preuve de paiement :")
    wa_link = f"https://wa.me/{ADMIN_WHATSAPP}?text={quote(msg)}" if ADMIN_WHATSAPP else None

    return jsonify(
        ok=True,
        reference=ref,
        montant=REPORT_PRICE,
        numero=PAY_NUMBER,
        whatsapp=wa_link,
        email_admin=ADMIN_EMAIL or None,
    )


def _mark_paid(tx_ref):
    """Débloque le rapport (appelé par l'admin après vérification de la preuve)."""
    with db() as conn:
        row = conn.execute("SELECT * FROM payments WHERE tx_ref=?", (tx_ref,)).fetchone()
        if not row or row["paye"]:
            return False
        conn.execute("UPDATE payments SET statut='paye', paye=1 WHERE tx_ref=?", (tx_ref,))
        generate_report_pdf(row)   # déclenche la génération (hook ESTHER)
        return True


# --------------------------------------------------------------------------
# État du paiement / données du rapport
# --------------------------------------------------------------------------
@app.route("/api/report/<tx_ref>")
def report_status(tx_ref):
    with db() as conn:
        row = conn.execute("SELECT * FROM payments WHERE tx_ref=?", (tx_ref,)).fetchone()
    if not row:
        return jsonify(ok=False, error="Transaction inconnue."), 404
    paye = bool(row["paye"])
    out = {"ok": True, "paye": paye, "statut": row["statut"]}
    if paye:
        out["rapport"] = compute_report(json.loads(row["projet"] or "{}"))
        out["pdf_url"] = f"/api/report/{tx_ref}/pdf"
    return jsonify(out)


@app.route("/api/report/<tx_ref>/pdf")
def report_pdf(tx_ref):
    """Génère et renvoie le PDF à la demande, uniquement si la demande est validée."""
    with db() as conn:
        row = conn.execute("SELECT * FROM payments WHERE tx_ref=?", (tx_ref,)).fetchone()
    if not row or not row["paye"]:
        return jsonify(ok=False, error="Rapport non disponible (paiement non validé)."), 403
    rep = compute_report(json.loads(row["projet"] or "{}"))
    if not rep.get("valide"):
        return jsonify(ok=False, error="Données insuffisantes."), 400
    from report import build_report_pdf
    pdf = build_report_pdf(rep, reference=tx_ref, email=row["email"])
    from flask import Response
    return Response(pdf, mimetype="application/pdf", headers={
        "Content-Disposition": f'inline; filename="rapport-soleo-{tx_ref}.pdf"'
    })


# --------------------------------------------------------------------------
# Admin : leads + validation des paiements (protégé par ADMIN_TOKEN)
# --------------------------------------------------------------------------
def _check_admin():
    return ADMIN_TOKEN and request.args.get("token") == ADMIN_TOKEN


@app.route("/api/admin/leads")
def admin_leads():
    if not _check_admin():
        return jsonify(ok=False, error="Non autorisé."), 401
    with db() as conn:
        rows = conn.execute("SELECT * FROM leads ORDER BY id DESC LIMIT 500").fetchall()
    return jsonify(ok=True, leads=[dict(r) for r in rows])


@app.route("/api/admin/requests")
def admin_requests():
    if not _check_admin():
        return jsonify(ok=False, error="Non autorisé."), 401
    with db() as conn:
        rows = conn.execute("SELECT * FROM payments ORDER BY id DESC LIMIT 500").fetchall()
    return jsonify(ok=True, demandes=[dict(r) for r in rows])


@app.route("/api/admin/approve", methods=["POST"])
def admin_approve():
    if not _check_admin():
        return jsonify(ok=False, error="Non autorisé."), 401
    ref = (request.get_json(silent=True) or {}).get("reference", "")
    done = _mark_paid(ref)
    return jsonify(ok=done, error=None if done else "Déjà validé ou introuvable.")


@app.route("/api/admin/reject", methods=["POST"])
def admin_reject():
    if not _check_admin():
        return jsonify(ok=False, error="Non autorisé."), 401
    ref = (request.get_json(silent=True) or {}).get("reference", "")
    with db() as conn:
        conn.execute("UPDATE payments SET statut='rejete' WHERE tx_ref=? AND paye=0", (ref,))
    return jsonify(ok=True)


@app.route("/admin")
def admin_page():
    # La page se charge ; le token est saisi dans l'UI et conservé en mémoire (jamais en dur).
    return send_from_directory(app.static_folder, "admin.html")


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=True)

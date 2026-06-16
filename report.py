"""
Soléo — Génération du rapport PDF premium.

build_report_pdf(rep, reference, email) -> bytes

`rep` est le dict produit par compute_report() dans app.py. Ce module est volontairement
autonome (aucune dépendance à app.py) : c'est le point d'extension naturel pour brancher
le moteur ESTHER (calculs plus fins, courbes de production horaires, P50/P90, etc.).
"""

import io
from datetime import date

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether,
)

# Palette Soléo
INDIGO = colors.HexColor("#15122a")
INDIGO2 = colors.HexColor("#241c44")
AMBER = colors.HexColor("#ffb02e")
GREEN = colors.HexColor("#1f9d6b")
INK = colors.HexColor("#2a2540")
DIM = colors.HexColor("#6f6788")
LINE = colors.HexColor("#e7e3ef")

# Hypothèses de modélisation (ajustables / à raffiner avec ESTHER)
MODULE_WP = 550          # puissance d'un panneau (Wc)
SURFACE_PER_KWC = 5.5    # m2 par kWc
DEGRADATION = 0.005      # perte de production annuelle (0,5 %/an)
DUREE_ANS = 20


def _fcfa(n):
    try:
        return f"{round(float(n)):,}".replace(",", " ") + " FCFA"
    except Exception:
        return "—"


def build_report_pdf(rep: dict, reference: str = "", email: str = "") -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=16 * mm, rightMargin=16 * mm,
        topMargin=16 * mm, bottomMargin=18 * mm,
        title="Soléo — Rapport solaire", author="Vé Mondé Ephraïm",
    )

    ss = getSampleStyleSheet()
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], textColor=INDIGO,
                        fontName="Helvetica-Bold", fontSize=13, spaceBefore=14, spaceAfter=7)
    body = ParagraphStyle("body", parent=ss["BodyText"], textColor=INK,
                          fontSize=9.5, leading=14)
    small = ParagraphStyle("small", parent=body, fontSize=8.3, textColor=DIM, leading=12)

    story = []

    # ---- Bandeau titre ----
    titre = Paragraph(
        '<font color="#ffffff"><b>Sol</b></font>'
        '<font color="#ffb02e"><b>éo</b></font>'
        '<font color="#ffffff"><b> — Rapport de dimensionnement solaire</b></font>',
        ParagraphStyle("t", fontSize=16, leading=20))
    sous = Paragraph(
        f'<font color="#cfc8e0">Réf. {reference or "—"} · {date.today().strftime("%d/%m/%Y")}'
        f'{" · " + email if email else ""}</font>',
        ParagraphStyle("st", fontSize=9, leading=13))
    band = Table([[titre], [sous]], colWidths=[doc.width])
    band.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), INDIGO),
        ("LEFTPADDING", (0, 0), (-1, -1), 14), ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (-1, 0), 14), ("BOTTOMPADDING", (0, -1), (-1, -1), 12),
        ("LINEBELOW", (0, 0), (-1, 0), 2, AMBER),
    ]))
    story += [band, Spacer(1, 14)]

    # ---- Synthèse ----
    profil = "Maison" if rep.get("profil") == "maison" else "Entreprise / PME"
    stock = "Avec batteries (secours)" if rep.get("stockage") == "avec" else "Sans batterie (réseau)"
    synth = [
        ["Localité", rep.get("ville", "—"), "Profil", profil],
        ["Type d'installation", stock, "Consommation", f'{rep.get("kwh_mois","—")} kWh/mois'],
        ["Puissance conseillée", f'{rep.get("kwc","—")} kWc', "Production estimée",
         f'{_int(rep.get("production_an_kwh"))} kWh/an'],
    ]
    t = Table(synth, colWidths=[doc.width * .22, doc.width * .28, doc.width * .22, doc.width * .28])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9), ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("TEXTCOLOR", (0, 0), (0, -1), DIM), ("TEXTCOLOR", (2, 0), (2, -1), DIM),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, colors.HexColor("#faf8fd")]),
        ("LINEBELOW", (0, 0), (-1, -2), .5, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story += [Paragraph("Synthèse de votre projet", h2), t]

    # ---- Bandeau résultat clé ----
    payback = rep.get("payback_ans", "—")
    eco = _fcfa(rep.get("economie_mois_fcfa"))
    cout = _fcfa(rep.get("cout_total_fcfa"))
    key = Table([[
        Paragraph(f'<font color="#ffffff" size="9">RENTABILISÉ EN</font><br/>'
                  f'<font color="#ffb02e" size="22"><b>{payback} ans</b></font>', body),
        Paragraph(f'<font color="#cfc8e0" size="9">ÉCONOMIE / MOIS</font><br/>'
                  f'<font color="#ffffff" size="15"><b>{eco}</b></font>', body),
        Paragraph(f'<font color="#cfc8e0" size="9">INVESTISSEMENT</font><br/>'
                  f'<font color="#ffffff" size="15"><b>{cout}</b></font>', body),
    ]], colWidths=[doc.width / 3] * 3)
    key.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), INDIGO2),
        ("LEFTPADDING", (0, 0), (-1, -1), 12), ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ("LINEAFTER", (0, 0), (-2, -1), .5, colors.HexColor("#3a3360")),
        ("ROUNDEDCORNERS", [8, 8, 8, 8]),
    ]))
    story += [Spacer(1, 10), key]

    # ---- Dimensionnement technique ----
    kwc = float(rep.get("kwc") or 0)
    nb_pan = max(1, round(kwc * 1000 / MODULE_WP))
    surface = round(kwc * SURFACE_PER_KWC)
    onduleur = round(kwc, 1)
    dim = [
        ["Élément", "Estimation"],
        [f"Panneaux solaires (~{MODULE_WP} Wc)", f"{nb_pan} panneaux"],
        ["Puissance onduleur", f"~{onduleur} kW"],
        ["Surface de toiture nécessaire", f"~{surface} m2"],
        ["Stockage", "Selon autonomie souhaitée" if rep.get("stockage") == "avec" else "Non inclus (réseau)"],
        ["Irradiation locale retenue", f'{rep.get("psh","—")} kWh/m2/jour'],
    ]
    td = _grid(dim, doc.width)
    story += [Paragraph("Dimensionnement technique", h2), td]

    # ---- Rentabilité sur 20 ans ----
    prod0 = float(rep.get("production_an_kwh") or 0)
    tarif = float(rep.get("tarif_fcfa_kwh") or 0)
    cout_tot = float(rep.get("cout_total_fcfa") or 0)
    conso_an = float(rep.get("kwh_mois") or 0) * 12

    rows = [["Année", "Production (kWh)", "Économie (FCFA)", "Cumul net (FCFA)"]]
    cumul = -cout_tot
    payback_year = None
    for an in range(1, DUREE_ANS + 1):
        prod = prod0 * ((1 - DEGRADATION) ** (an - 1))
        couvert = min(prod, conso_an)
        econ = couvert * tarif
        cumul += econ
        if payback_year is None and cumul >= 0:
            payback_year = an
        rows.append([str(an), _int(prod), _int(econ), _int(cumul)])

    tr = Table(rows, colWidths=[doc.width * .14, doc.width * .29, doc.width * .28, doc.width * .29],
               repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), INDIGO),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("TEXTCOLOR", (0, 1), (-1, -1), INK),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#faf8fd")]),
        ("LINEBELOW", (0, 0), (-1, -1), .4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 4.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
    ]
    if payback_year:
        style += [
            ("BACKGROUND", (0, payback_year), (-1, payback_year), colors.HexColor("#fff4dd")),
            ("FONTNAME", (0, payback_year), (-1, payback_year), "Helvetica-Bold"),
            ("LINEBELOW", (0, payback_year), (-1, payback_year), 1, AMBER),
        ]
    tr.setStyle(TableStyle(style))
    note_pb = (f'L\'année <b>{payback_year}</b> (surlignée) marque le retour sur investissement : '
               f'vos économies cumulées dépassent le coût initial.') if payback_year else \
        "Le retour sur investissement intervient au-delà de la période modélisée."
    story += [Paragraph("Rentabilité sur 20 ans", h2),
              Paragraph(note_pb, small), Spacer(1, 5), tr]

    # ---- Hypothèses & disclaimer ----
    hyp = (f'Tarif électricité retenu : <b>{_int(tarif)} FCFA/kWh</b> (grille CIE/ANARE-CI, TTC). '
           f'Coût installation : <b>{_int(rep.get("cout_fcfa_kwc"))} FCFA/kWc</b> (prix marché Côte d\'Ivoire). '
           f'Dégradation des panneaux : 0,5 %/an. Modèle d\'autoconsommation simplifié '
           f'(la production compense la consommation à hauteur du minimum des deux).')
    disc = ("Ce rapport est une estimation indicative destinée à éclairer votre décision. "
            "Il ne remplace pas une étude technique sur site réalisée par un installateur certifié. "
            "Les tarifs, prix et niveaux d'irradiation doivent être confirmés avant tout engagement.")
    story += [Paragraph("Hypothèses &amp; sources", h2), Paragraph(hyp, small),
              Spacer(1, 6), Paragraph(disc, small)]

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


def _grid(rows, width):
    t = Table(rows, colWidths=[width * .5, width * .5])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3eefb")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, 0), (-1, 0), INDIGO),
        ("FONTSIZE", (0, 0), (-1, -1), 9), ("TEXTCOLOR", (0, 1), (-1, -1), INK),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#faf8fd")]),
        ("LINEBELOW", (0, 0), (-1, -1), .4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(.5)
    canvas.line(16 * mm, 12 * mm, A4[0] - 16 * mm, 12 * mm)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(DIM)
    canvas.drawString(16 * mm, 8 * mm, "Soléo · Conçu par Vé Mondé Ephraïm · Abidjan")
    canvas.drawRightString(A4[0] - 16 * mm, 8 * mm, f"Page {doc.page}")
    canvas.restoreState()


def _int(n):
    try:
        return f"{round(float(n)):,}".replace(",", " ")
    except Exception:
        return "—"

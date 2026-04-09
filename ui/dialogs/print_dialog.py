# -*- coding: utf-8 -*-
"""
ui/dialogs/print_dialog.py — PISUM Print & PDF Dialog
=======================================================
Generates a PDF directly from the report payload using ReportLab
(pure Python — no Word, no LibreOffice needed).
Provides in-app page preview via PyMuPDF + Pillow.
"""

import os
import logging
import threading
import tempfile
import tkinter as tk
from datetime import datetime

import customtkinter as ctk
from ui.theme import C, F, S, R

logger = logging.getLogger(__name__)


# ── Section header map (same as report_view) ───────────────────────────────────

def _build_header_map() -> dict[str, str]:
    """Build section header → key map from Comptes_Rendus.TRANSLATIONS."""
    result: dict[str, str] = {}
    try:
        from Comptes_Rendus import TRANSLATIONS
        for t in TRANSLATIONS.values():
            sections = t.get("sections", [])
            n = len(sections)
            if n < 2:
                continue
            for i, header in enumerate(sections):
                key = header.strip().lower()
                if i == 0:
                    result[key] = "indication"
                elif i == 1:
                    result[key] = "technique"
                elif i == n - 1:
                    result[key] = "conclusion"
                else:
                    result[key] = "resultat"
    except Exception:
        pass
    return result


_HEADER_MAP: dict[str, str] = _build_header_map()

# ── kept for reference / fallback only ────────────────────────────────────────
_HEADER_MAP_FALLBACK: dict[str, str] = {
    # Français
    "indication :"               : "indication",
    "technique :"                : "technique",
    "résultat :"                 : "resultat",
    "résultats :"                : "resultat",
    "conclusion :"               : "conclusion",
    # English
    "clinical indication:"       : "indication",
    "imaging technique:"         : "technique",
    "findings:"                  : "resultat",
    "impression:"                : "conclusion",
    # Español
    "indicación clínica:"        : "indication",
    "técnica de imagen:"         : "technique",
    "hallazgos:"                 : "resultat",
    "impresión:"                 : "conclusion",
    # Deutsch
    "klinische indikation:"      : "indication",
    "bildgebungstechnik:"        : "technique",
    "befunde:"                   : "resultat",
    "beurteilung:"               : "conclusion",
    # Italiano
    "indicazione clinica:"       : "indication",
    "tecnica di imaging:"        : "technique",
    "reperti:"                   : "resultat",
    "conclusione:"               : "conclusion",
    # Português
    "indicação clínica:"         : "indication",
    "técnica de imagem:"         : "technique",
    "achados:"                   : "resultat",
    "impressão:"                 : "conclusion",
    # Русский
    "клинические показания:"     : "indication",
    "методика:"                  : "technique",
    "результаты:"                : "resultat",
    "заключение:"                : "conclusion",
    # 中文
    "临床指征："                   : "indication",
    "影像技术："                   : "technique",
    "影像所见："                   : "resultat",
    "影像诊断："                   : "conclusion",
    # 日本語
    "臨床適応："                   : "indication",
    "検査手法："                   : "technique",
    "所見："                      : "resultat",
    "診断："                      : "conclusion",
    # Türkçe
    "klinik endikasyon:"         : "indication",
    "görüntüleme tekniği:"       : "technique",
    "bulgular:"                  : "resultat",
    "sonuç:"                     : "conclusion",
    # Svenska
    "klinisk indikation:"        : "indication",
    "undersökningsmetodik:"      : "technique",
    "fynd:"                      : "resultat",
    "bedömning:"                 : "conclusion",
    # Norsk
    "klinisk indikasjon:"        : "indication",
    "undersøkelsesteknikk:"      : "technique",
    "funn:"                      : "resultat",
    "konklusjon:"                : "conclusion",
    # Dansk
    "billedteknik:"              : "technique",
    "fund:"                      : "resultat",
    "konklusion:"                : "conclusion",
    # Nederlands
    "klinische indicatie:"       : "indication",
    "onderzoekstechniek:"        : "technique",
    "bevindingen:"               : "resultat",
    "conclusie:"                 : "conclusion",
    # 한국어
    "임상 적응증:"                 : "indication",
    "검사 방법:"                  : "technique",
    "영상 소견:"                  : "resultat",
    "결론:"                      : "conclusion",
    # Bahasa Indonesia
    "indikasi klinis:"           : "indication",
    "teknik pencitraan:"         : "technique",
    "temuan:"                    : "resultat",
    "kesimpulan:"                : "conclusion",
    # Polski
    "wskazania kliniczne:"       : "indication",
    "technika badania:"          : "technique",
    "wyniki:"                    : "resultat",
    "wnioski:"                   : "conclusion",
    # Română
    "indicație clinică:"         : "indication",
    "tehnică imagistică:"        : "technique",
    "rezultate:"                 : "resultat",
    "concluzie:"                 : "conclusion",
    # Ελληνικά
    "κλινική ένδειξη:"           : "indication",
    "τεχνική:"                   : "technique",
    "ευρήματα:"                  : "resultat",
    "συμπέρασμα:"                : "conclusion",
    # Filipino
    "indikasyong klinikal:"      : "indication",
    "teknik ng imaging:"         : "technique",
    "resulta:"                   : "resultat",
    "konklusyon:"                : "conclusion",
}
# Merge: dynamic map wins (it's sourced directly from TRANSLATIONS)
_HEADER_MAP_FALLBACK.update(_HEADER_MAP)
_HEADER_MAP = _HEADER_MAP_FALLBACK

_SECTION_LABELS = {
    "indication": "Indication",
    "technique":  "Technique",
    "resultat":   "Résultats",
    "conclusion": "Conclusion",
}

# Internal key names produced by build_word_payload() — matched case-insensitively
_INTERNAL_KEY_MAP: dict[str, str] = {
    "indication": "indication",
    "technique":  "technique",
    "resultat":   "resultat",
    "résultat":   "resultat",
    "résultats":  "resultat",
    "results":    "resultat",
    "findings":   "resultat",
    "conclusion": "conclusion",
    "impression": "conclusion",
}


def _parse_sections(formula: str) -> dict[str, str]:
    """
    Parse formula text into {section_key: text}.
    Recognises both multilingual TRANSLATIONS headers (with colons/spaces)
    and the bare internal key names (INDICATION / TECHNIQUE / RESULTAT /
    CONCLUSION) written by build_word_payload().
    """
    result  = {k: [] for k in _SECTION_LABELS}
    current = None
    for raw in formula.splitlines():
        line       = raw.replace("▸ ", "").replace("▸", "").strip()
        normalized = line.lower()
        matched    = _HEADER_MAP.get(normalized) or _INTERNAL_KEY_MAP.get(normalized)
        if matched:
            current = matched
        elif current is not None:
            result[current].append(line)
    if current is None:
        result["resultat"] = [formula.strip()]
    return {k: "\n".join(v).strip() for k, v in result.items()}


_UI_LABELS: dict[str, dict[str, str]] = {
    # key: {language_name: translation}
    "patient":       {"Français": "Patient",      "French": "Patient",     "English": "Patient",
                      "Español": "Paciente",       "Deutsch": "Patient",    "Italiano": "Paziente",
                      "Português": "Paciente",     "Русский": "Пациент",    "中文": "患者",
                      "日本語": "患者",               "Türkçe": "Hasta",       "Svenska": "Patient",
                      "Norsk": "Pasient",          "Dansk": "Patient",      "Nederlands": "Patiënt",
                      "한국어": "환자",               "Bahasa Indonesia": "Pasien", "Polski": "Pacjent",
                      "Română": "Pacient",         "Ελληνικά": "Ασθενής",   "Filipino": "Pasyente"},
    "date_exam":     {"Français": "Date exam",    "French": "Exam date",   "English": "Exam date",
                      "Español": "Fecha examen",   "Deutsch": "Unters.-datum", "Italiano": "Data esame",
                      "Português": "Data exame",   "Русский": "Дата иссл.", "中文": "检查日期",
                      "日本語": "検査日",             "Türkçe": "Muayene tarihi", "Svenska": "Undersökningsdatum",
                      "Norsk": "Undersøkelsesdato","Dansk": "Undersøgelsesdato","Nederlands": "Onderzoeksdatum",
                      "한국어": "검사일",             "Bahasa Indonesia": "Tanggal pemeriksaan","Polski": "Data bad.",
                      "Română": "Data examinare", "Ελληνικά": "Ημ/νία εξέτ.","Filipino": "Petsa ng exam"},
    "date_naiss":    {"Français": "Date naiss.",  "French": "Date of birth","English": "Date of birth",
                      "Español": "Fecha nacim.",   "Deutsch": "Geburtsdatum","Italiano": "Data nascita",
                      "Português": "Data nascimento","Русский": "Дата рожд.","中文": "出生日期",
                      "日本語": "生年月日",            "Türkçe": "Doğum tarihi","Svenska": "Födelsedatum",
                      "Norsk": "Fødselsdato",     "Dansk": "Fødselsdato",  "Nederlands": "Geboortedatum",
                      "한국어": "생년월일",            "Bahasa Indonesia": "Tanggal lahir","Polski": "Data ur.",
                      "Română": "Data nașterii",  "Ελληνικά": "Ημ/νία γέν.","Filipino": "Petsa ng kapanganakan"},
    "sexe":          {"Français": "Sexe",         "French": "Sex",         "English": "Sex",
                      "Español": "Sexo",           "Deutsch": "Geschlecht", "Italiano": "Sesso",
                      "Português": "Sexo",         "Русский": "Пол",        "中文": "性别",
                      "日本語": "性別",               "Türkçe": "Cinsiyet",    "Svenska": "Kön",
                      "Norsk": "Kjønn",           "Dansk": "Køn",          "Nederlands": "Geslacht",
                      "한국어": "성별",               "Bahasa Indonesia": "Jenis kelamin","Polski": "Płeć",
                      "Română": "Sex",             "Ελληνικά": "Φύλο",      "Filipino": "Kasarian"},
    "radiologue":    {"Français": "Radiologue",   "French": "Radiologist", "English": "Radiologist",
                      "Español": "Radiólogo",      "Deutsch": "Radiologe",  "Italiano": "Radiologo",
                      "Português": "Radiologista", "Русский": "Радиолог",   "中文": "放射科医生",
                      "日本語": "放射線科医",          "Türkçe": "Radyolog",    "Svenska": "Radiolog",
                      "Norsk": "Radiolog",        "Dansk": "Radiolog",     "Nederlands": "Radioloog",
                      "한국어": "방사선과 의사",        "Bahasa Indonesia": "Radiolog","Polski": "Radiolog",
                      "Română": "Radiolog",        "Ελληνικά": "Ακτινολόγος","Filipino": "Radyolohista"},
    "etablissement": {"Français": "Établissement","French": "Institution", "English": "Institution",
                      "Español": "Institución",    "Deutsch": "Einrichtung","Italiano": "Istituto",
                      "Português": "Instituição",  "Русский": "Учреждение", "中文": "机构",
                      "日本語": "医療機関",            "Türkçe": "Kurum",       "Svenska": "Institution",
                      "Norsk": "Institusjon",     "Dansk": "Institution",  "Nederlands": "Instelling",
                      "한국어": "기관",               "Bahasa Indonesia": "Institusi","Polski": "Placówka",
                      "Română": "Instituție",     "Ελληνικά": "Ίδρυμα",    "Filipino": "Institusyon"},
    "fait_le":       {"Français": "Fait le",      "French": "Done on",     "English": "Done on",
                      "Español": "Hecho el",       "Deutsch": "Erstellt am","Italiano": "Fatto il",
                      "Português": "Feito em",     "Русский": "Составлено", "中文": "完成日期",
                      "日本語": "作成日",             "Türkçe": "Yapılma tarihi","Svenska": "Utfärdat",
                      "Norsk": "Utstedt",         "Dansk": "Udstedt",      "Nederlands": "Opgemaakt op",
                      "한국어": "작성일",             "Bahasa Indonesia": "Dibuat pada","Polski": "Sporządzono",
                      "Română": "Întocmit la",    "Ελληνικά": "Εκδόθηκε",  "Filipino": "Ginawa noong"},
    "compte_rendu":  {"Français": "COMPTE RENDU", "French": "MEDICAL REPORT","English": "MEDICAL REPORT",
                      "Español": "INFORME MÉDICO","Deutsch": "BEFUNDBERICHT","Italiano": "REFERTO MEDICO",
                      "Português": "RELATÓRIO MÉDICO","Русский": "ЗАКЛЮЧЕНИЕ","中文": "影像报告",
                      "日本語": "診断報告書",          "Türkçe": "TIBBİ RAPOR", "Svenska": "RÖNTGENSVAR",
                      "Norsk": "RØNTGENSVAR",     "Dansk": "RØNTGENSVAR",  "Nederlands": "MEDISCH VERSLAG",
                      "한국어": "진단 보고서",          "Bahasa Indonesia": "LAPORAN MEDIS","Polski": "OPIS BADANIA",
                      "Română": "RAPORT MEDICAL", "Ελληνικά": "ΙΑΤΡΙΚΗ ΕΚΘΕΣΗ","Filipino": "MEDIKAL NA ULAT"},
}


def _get_ui_labels(lang: str) -> dict[str, str]:
    """Return translated PDF UI labels for the given language."""
    result = {}
    # Try exact match, then common variants
    for key, translations in _UI_LABELS.items():
        result[key] = (
            translations.get(lang)
            or translations.get("Français")
            or list(translations.values())[0]
        )
    return result


def _get_section_labels(lang: str) -> dict[str, str]:
    """Return localised section labels for the given language (inline — no ui import)."""
    defaults = {
        "indication": "Indication",
        "technique":  "Technique",
        "resultat":   "Résultats",
        "conclusion": "Conclusion",
    }
    try:
        from Comptes_Rendus import TRANSLATIONS
        t = TRANSLATIONS.get(lang) or TRANSLATIONS.get("Français") or TRANSLATIONS.get("French", {})
        sections = t.get("sections", [])
        n = len(sections)
        if n >= 4:
            def _clean(s): return s.rstrip(": ").rstrip("：")
            defaults["indication"] = _clean(sections[0])
            defaults["technique"]  = _clean(sections[1])
            defaults["resultat"]   = _clean(sections[2])
            defaults["conclusion"] = _clean(sections[n - 1])
    except Exception:
        pass
    return defaults


# ── PDF generation (pure Python / ReportLab) ──────────────────────────────────

def _generate_pdf_from_payload(payload: dict, pdf_path: str,
                               is_free: bool = False) -> bool:
    """
    Build a formatted medical PDF from *payload* using ReportLab.
    No Word, no LibreOffice — pure Python.
    Returns True on success.
    """
    try:
        from reportlab.lib.pagesizes   import A4
        from reportlab.lib.units        import cm, mm
        from reportlab.lib              import colors
        from reportlab.lib.styles       import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums        import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY
        from reportlab.platypus         import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
            HRFlowable, KeepTogether,
        )
        from reportlab.pdfbase          import pdfmetrics
        from reportlab.pdfbase.ttfonts  import TTFont

        # ── Unicode font selection by language ─────────────────────────────
        import os as _os
        from reportlab.pdfbase.pdfmetrics import registerFontFamily as _regFamily

        _FONT_R = "Helvetica"
        _FONT_B = "Helvetica-Bold"
        _FONT_I = "Helvetica-Oblique"

        _win_fonts = _os.path.join(_os.environ.get("WINDIR", "C:/Windows"), "Fonts")

        def _try_register(pfx, r, b, i):
            """Register a TTF font family. Returns True on success."""
            if not _os.path.exists(r):
                return False
            try:
                pdfmetrics.registerFont(TTFont(pfx,          r))
                pdfmetrics.registerFont(TTFont(pfx+"-Bold",   b if _os.path.exists(b) else r))
                pdfmetrics.registerFont(TTFont(pfx+"-Italic", i if _os.path.exists(i) else r))
                _regFamily(pfx, normal=pfx, bold=pfx+"-Bold", italic=pfx+"-Italic")
                return True
            except Exception:
                return False

        def _wf(name): return _os.path.join(_win_fonts, name)

        # Language → preferred font candidates (regular, bold, italic, prefix)
        _report_lang = payload.get("language", "Français")
        _lang_fonts = {
            "中文":             [(_wf("msyh.ttc"),    _wf("msyhbd.ttc"),   _wf("msyh.ttc"),    "MSYaHei"),
                                 (_wf("simsun.ttc"),   _wf("simsun.ttc"),   _wf("simsun.ttc"),  "SimSun")],
            "日本語":           [(_wf("msgothic.ttc"), _wf("msgothic.ttc"), _wf("msgothic.ttc"),"MSGothic"),
                                 (_wf("meiryo.ttc"),   _wf("meiryob.ttc"),  _wf("meiryo.ttc"),  "Meiryo")],
            "한국어":           [(_wf("malgun.ttf"),   _wf("malgunbd.ttf"), _wf("malgun.ttf"),  "MalgunGothic"),
                                 (_wf("gulim.ttc"),    _wf("gulim.ttc"),    _wf("gulim.ttc"),   "Gulim")],
        }
        # Default candidates for all other languages (Latin, Cyrillic, Greek, Arabic, etc.)
        _default_fonts = [
            (_wf("arial.ttf"),    _wf("arialbd.ttf"),   _wf("ariali.ttf"),   "ArialUni"),
            (_wf("calibri.ttf"),  _wf("calibrib.ttf"),  _wf("calibrii.ttf"), "CalibriUni"),
            (_wf("segoeui.ttf"),  _wf("segoeuib.ttf"),  _wf("segoeuii.ttf"), "SegoeUni"),
        ]

        _candidates = _lang_fonts.get(_report_lang, []) + _default_fonts
        for _r, _b, _i, _pfx in _candidates:
            if _try_register(_pfx, _r, _b, _i):
                _FONT_R = _pfx
                _FONT_B = _pfx + "-Bold"
                _FONT_I = _pfx + "-Italic"
                break

        # ── colours ────────────────────────────────────────────────────────
        TEAL    = colors.HexColor("#14B8A6")
        TEAL_DK = colors.HexColor("#0F8A7A")
        DARK    = colors.HexColor("#1C2128")
        GRAY    = colors.HexColor("#8B949E")
        LIGHT   = colors.HexColor("#E6EDF3")
        WHITE   = colors.white
        BLACK   = colors.HexColor("#0D1117")

        # ── data ───────────────────────────────────────────────────────────
        patient = payload.get("patient_data") or {}
        examen  = payload.get("examen_data")  or {}
        nom     = patient.get("nom",    examen.get("nom",    ""))
        prenom  = patient.get("prenom", examen.get("prenom", ""))
        ddn     = patient.get("date_naissance", examen.get("date_naissance", ""))
        sexe    = patient.get("sexe",   examen.get("sexe",   ""))
        mod     = payload.get("modality",  examen.get("modalite",    ""))
        etype   = payload.get("exam_type", examen.get("type_examen", ""))
        etab    = payload.get("etablissement", "")
        med     = payload.get("medecin", "")
        date    = examen.get("date_examen", datetime.today().strftime("%d/%m/%Y"))
        formula = payload.get("formula", "")

        sections  = _parse_sections(formula)
        _lang     = payload.get("language", "Français")
        _ui       = _get_ui_labels(_lang)

        # ── document ───────────────────────────────────────────────────────
        doc = SimpleDocTemplate(
            pdf_path,
            pagesize=A4,
            leftMargin=1.8*cm, rightMargin=1.8*cm,
            topMargin=1.5*cm,  bottomMargin=2*cm,
            title="Compte Rendu Médical",
            author=med or "PISUM",
        )

        base  = getSampleStyleSheet()
        story = []

        # ── styles ─────────────────────────────────────────────────────────
        def S_(name, **kw):
            s = ParagraphStyle(name, parent=base["Normal"], **kw)
            return s

        sty_etab = S_("etab",
            fontSize=9, textColor=GRAY, alignment=TA_RIGHT,
            fontName=_FONT_R,
        )
        sty_title = S_("title",
            fontSize=15, textColor=WHITE, alignment=TA_CENTER,
            fontName=_FONT_B, spaceAfter=2,
        )
        sty_subtitle = S_("subtitle",
            fontSize=10, textColor=colors.HexColor("#14B8A6"),
            alignment=TA_CENTER, fontName=_FONT_R, spaceAfter=4,
        )
        sty_patient_lbl = S_("plbl",
            fontSize=8.5, textColor=GRAY, fontName=_FONT_R,
        )
        sty_patient_val = S_("pval",
            fontSize=9.5, textColor=LIGHT, fontName=_FONT_B,
        )
        sty_section_hdr = S_("shdr",
            fontSize=10, textColor=WHITE, fontName=_FONT_B,
            leftIndent=6,
        )
        sty_body = S_("body",
            fontSize=10, textColor=DARK, fontName=_FONT_R,
            leading=15, alignment=TA_JUSTIFY,
            spaceAfter=4,
        )
        sty_empty = S_("empty",
            fontSize=9, textColor=GRAY, fontName=_FONT_I,
        )
        sty_sig   = S_("sig",
            fontSize=10, textColor=DARK, fontName=_FONT_B,
            alignment=TA_RIGHT,
        )
        sty_footer = S_("footer",
            fontSize=8, textColor=GRAY, fontName=_FONT_R,
            alignment=TA_CENTER,
        )

        page_w = A4[0] - 1.8*cm*2
        TEAL_HEX = "#14B8A6"

        # ══ HEADER BAR ══════════════════════════════════════════════════════
        # FREE  → show "PISUM" branding on the left
        # Paid  → show only the facility name, centred — no PISUM branding
        if is_free:
            header_data = [[
                Paragraph("<b>PISUM</b>", S_("logo",
                    fontSize=14, textColor=WHITE, fontName=_FONT_B)),
                Paragraph(etab or "Medical Center", S_("etab2",
                    fontSize=9, textColor=colors.HexColor("#8B949E"),
                    alignment=TA_RIGHT, fontName=_FONT_R)),
            ]]
            header_tbl = Table(header_data, colWidths=[page_w*0.4, page_w*0.6])
        else:
            header_data = [[
                Paragraph(etab or "Medical Center", S_("etab3",
                    fontSize=13, textColor=WHITE, fontName=_FONT_B,
                    alignment=TA_CENTER)),
            ]]
            header_tbl = Table(header_data, colWidths=[page_w])
        header_tbl.setStyle(TableStyle([
            ("BACKGROUND",   (0, 0), (-1, -1), colors.HexColor("#0F8A7A")),
            ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING",  (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING",   (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 10),
            ("ROUNDEDCORNERS", [6]),
        ]))
        story.append(header_tbl)
        story.append(Spacer(1, 10))

        # ══ REPORT TITLE ════════════════════════════════════════════════════
        title_text = _ui["compte_rendu"]
        if mod:
            title_text += f" — {mod.upper()}"
        story.append(Paragraph(title_text, sty_title))
        if etype:
            story.append(Paragraph(etype, sty_subtitle))
        story.append(Spacer(1, 6))

        # ══ PATIENT INFO BOX ════════════════════════════════════════════════
        full_name = f"{nom.upper()}, {prenom}".strip(", ") or "—"
        info_rows = [
            [
                Paragraph(_ui["patient"], sty_patient_lbl),
                Paragraph(full_name, sty_patient_val),
                Paragraph(_ui["date_exam"], sty_patient_lbl),
                Paragraph(date or "—", sty_patient_val),
            ],
            [
                Paragraph(_ui["date_naiss"], sty_patient_lbl),
                Paragraph(ddn or "—", sty_patient_val),
                Paragraph(_ui["sexe"], sty_patient_lbl),
                Paragraph(sexe or "—", sty_patient_val),
            ],
            [
                Paragraph(_ui["radiologue"], sty_patient_lbl),
                Paragraph(med or "—", sty_patient_val),
                Paragraph(_ui["etablissement"], sty_patient_lbl),
                Paragraph(etab or "—", sty_patient_val),
            ],
        ]
        col_w = page_w / 4
        info_tbl = Table(info_rows, colWidths=[col_w*0.7, col_w*1.3, col_w*0.7, col_w*1.3])
        info_tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, -1), colors.HexColor("#1C2128")),
            ("GRID",          (0, 0), (-1, -1), 0.5, colors.HexColor("#30363D")),
            ("ROWBACKGROUNDS",(0, 0), (-1, -1), [colors.HexColor("#1C2128"), colors.HexColor("#21262D")]),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
            ("TOPPADDING",    (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(info_tbl)
        story.append(Spacer(1, 14))

        # ══ SEPARATOR ═══════════════════════════════════════════════════════
        story.append(HRFlowable(
            width="100%", thickness=1.5,
            color=colors.HexColor("#14B8A6"), spaceAfter=10,
        ))

        # ══ REPORT SECTIONS ═════════════════════════════════════════════════
        _lang_labels = _get_section_labels(_lang)

        section_order = ["indication", "technique", "resultat", "conclusion"]
        for key in section_order:
            label   = _lang_labels.get(key, _SECTION_LABELS[key])
            content = sections.get(key, "").strip()

            # Section header pill
            hdr_data = [[Paragraph(label.upper(), sty_section_hdr)]]
            hdr_tbl  = Table(hdr_data, colWidths=[page_w])
            hdr_tbl.setStyle(TableStyle([
                ("BACKGROUND",    (0, 0), (-1, -1), colors.HexColor("#0F8A7A")),
                ("LEFTPADDING",   (0, 0), (-1, -1), 10),
                ("RIGHTPADDING",  (0, 0), (-1, -1), 10),
                ("TOPPADDING",    (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))

            body_paras = []
            if content:
                for line in content.splitlines():
                    line = line.strip()
                    if line:
                        body_paras.append(Paragraph(line, sty_body))
                    else:
                        body_paras.append(Spacer(1, 4))
            else:
                body_paras.append(Paragraph("—", sty_empty))

            block = [
                hdr_tbl,
                Spacer(1, 4),
                *body_paras,
                Spacer(1, 10),
            ]
            story.append(KeepTogether(block[:4]))  # keep header with first lines
            for el in block[4:]:
                story.append(el)

        # ══ SEPARATOR ═══════════════════════════════════════════════════════
        story.append(HRFlowable(
            width="100%", thickness=0.5,
            color=colors.HexColor("#30363D"), spaceBefore=4, spaceAfter=10,
        ))

        # ══ SIGNATURE ═══════════════════════════════════════════════════════
        sig_date = datetime.today().strftime("%d/%m/%Y")
        sig_data = [[
            Paragraph("", sty_sig),
            Paragraph(
                f"Dr. <b>{med}</b><br/>"
                f"<font size='8' color='#8B949E'>{_ui['fait_le']} {sig_date}</font>"
                if med else f"<font size='8' color='#8B949E'>{_ui['fait_le']} {sig_date}</font>",
                S_("sig2", fontSize=10, textColor=DARK, fontName=_FONT_R,
                   alignment=TA_RIGHT),
            ),
        ]]
        sig_tbl = Table(sig_data, colWidths=[page_w*0.5, page_w*0.5])
        sig_tbl.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
            ("TOPPADDING",    (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        story.append(sig_tbl)

        # ── Build ─────────────────────────────────────────────────────────
        def _page_decorator(canvas, doc):
            canvas.saveState()
            w, h = A4

            # Watermark — FREE plan only
            if is_free:
                canvas.setFont(_FONT_B, 72)
                canvas.setFillColorRGB(0.08, 0.72, 0.65, alpha=0.07)
                canvas.translate(w / 2, h / 2)
                canvas.rotate(45)
                canvas.drawCentredString(0,  60, "PISUM")
                canvas.drawCentredString(0, -60, "FREE")
                canvas.rotate(-45)
                canvas.translate(-w / 2, -h / 2)

            # Footer
            canvas.setFont(_FONT_R, 8)
            canvas.setFillColor(GRAY)
            canvas.drawCentredString(
                w / 2, 1.2*cm,
                f"PISUM — {etab or 'Medical Center'}  •  Page {doc.page}",
            )
            canvas.restoreState()

        doc.build(story, onFirstPage=_page_decorator, onLaterPages=_page_decorator)
        return True

    except Exception as e:
        logger.error("ReportLab PDF generation failed: %s", e, exc_info=True)
        return False


# ── Printer helpers ────────────────────────────────────────────────────────────

def _list_printers() -> tuple[list[str], str]:
    try:
        import win32print
        printers = [
            p[2] for p in win32print.EnumPrinters(
                win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
            )
        ]
        return printers, win32print.GetDefaultPrinter()
    except Exception:
        return ["Default Printer"], "Default Printer"


def _send_to_printer(pdf_path: str, printer_name: str) -> bool:
    try:
        import win32api
        win32api.ShellExecute(
            0, "printto", os.path.abspath(pdf_path),
            f'"{printer_name}"', ".", 0,
        )
        return True
    except Exception as e:
        logger.warning("ShellExecute printto failed: %s", e)
    try:
        os.startfile(pdf_path)
        return True
    except Exception as e:
        logger.error("Open PDF failed: %s", e)
        return False


# ── PDF page renderer ─────────────────────────────────────────────────────────

def _render_pages(pdf_path: str, zoom: float = 1.5) -> list:
    """Render PDF pages to PIL PhotoImage list. Returns [] if PyMuPDF missing."""
    try:
        import fitz
        from PIL import Image, ImageTk
        mat  = fitz.Matrix(zoom, zoom)
        fdoc = fitz.open(pdf_path)
        pages = []
        for i in range(len(fdoc)):
            pix = fdoc[i].get_pixmap(matrix=mat, alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            pages.append(ImageTk.PhotoImage(img))
        fdoc.close()
        return pages
    except ImportError:
        logger.warning("PyMuPDF not available — preview disabled")
        return []
    except Exception as e:
        logger.warning("PDF render failed: %s", e)
        return []


# ══════════════════════════════════════════════════════════════════════════════
#  PrintDialog
# ══════════════════════════════════════════════════════════════════════════════

class PrintDialog(ctk.CTkToplevel):
    """
    In-app PDF preview + print dialog.

    Generates PDF directly from *payload* using ReportLab (pure Python).
    No Word or LibreOffice required.

    Left  : scrollable rendered PDF preview.
    Right : patient card, printer selector, status, Save / Print buttons.
    """

    _PAGE_GAP    = 16
    _PAGE_SHADOW = 4

    def __init__(self, master, payload: dict,
                 docx_path: str = None, on_close=None, plan: str = "FREE", **kw):
        super().__init__(master, **kw)
        self._payload   = payload
        self._docx_path = docx_path
        self._on_close  = on_close
        self._is_free   = (plan.strip().lower() == "free")
        self._pdf_path: str | None = None
        self._page_imgs  = []
        self._canvas_ids = []
        self._zoom       = 1.5

        self.title("Print / Save PDF")
        self.geometry("1100x760")
        self.minsize(820, 560)
        self.resizable(True, True)
        self.configure(fg_color=C.BG)
        try:
            self.transient("")
        except Exception:
            pass
        self.lift()
        self.after(100, self.focus_force)
        self.after(150, self._safe_grab)
        self.protocol("WM_DELETE_WINDOW", self._close)

        self._build()
        self._generate_async()

    # ── Layout ─────────────────────────────────────────────────────────────────

    def _build(self):
        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=0)
        self.grid_rowconfigure(0, weight=1)
        self._build_preview()
        self._build_controls()

    # ── Left: PDF preview ──────────────────────────────────────────────────────

    def _build_preview(self):
        frame = ctk.CTkFrame(self, fg_color="#1A1A1A", corner_radius=0)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.grid_rowconfigure(1, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        # Top bar
        topbar = ctk.CTkFrame(frame, fg_color="#111111", height=36, corner_radius=0)
        topbar.grid(row=0, column=0, columnspan=2, sticky="ew")
        topbar.grid_propagate(False)

        self._page_lbl = ctk.CTkLabel(
            topbar, text="Generating…",
            font=ctk.CTkFont(*F.BODY_SM), text_color=C.TEXT_3,
        )
        self._page_lbl.pack(side="left", padx=S.LG)

        zoom_frame = ctk.CTkFrame(topbar, fg_color="transparent")
        zoom_frame.pack(side="right", padx=S.MD)
        ctk.CTkButton(zoom_frame, text="−", width=28, height=24,
                      fg_color=C.SURFACE_3, hover_color=C.BORDER,
                      text_color=C.TEXT_1, font=ctk.CTkFont(*F.BODY),
                      corner_radius=4, command=self._zoom_out,
                      ).pack(side="left", padx=2)
        self._zoom_lbl = ctk.CTkLabel(zoom_frame, text="150%", width=40,
                                      font=ctk.CTkFont(*F.BODY_SM), text_color=C.TEXT_2)
        self._zoom_lbl.pack(side="left")
        ctk.CTkButton(zoom_frame, text="+", width=28, height=24,
                      fg_color=C.SURFACE_3, hover_color=C.BORDER,
                      text_color=C.TEXT_1, font=ctk.CTkFont(*F.BODY),
                      corner_radius=4, command=self._zoom_in,
                      ).pack(side="left", padx=2)

        # Canvas + scrollbar
        container = ctk.CTkFrame(frame, fg_color="transparent")
        container.grid(row=1, column=0, sticky="nsew")
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        self._canvas = tk.Canvas(container, bg="#2D2D2D",
                                 highlightthickness=0, cursor="hand2")
        self._canvas.grid(row=0, column=0, sticky="nsew")

        vsb = ctk.CTkScrollbar(container, command=self._canvas.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        self._canvas.configure(yscrollcommand=vsb.set)

        self._canvas.bind("<MouseWheel>",  self._on_wheel)
        self._canvas.bind("<Configure>",   self._on_canvas_resize)

        # Centered loading text
        self._loading_id = self._canvas.create_text(
            400, 300,
            text="⏳  Generating PDF…",
            fill="#6E7681",
            font=("Segoe UI", 13),
            tags="loading",
        )

    # ── Right: controls ────────────────────────────────────────────────────────

    def _build_controls(self):
        panel = ctk.CTkFrame(self, fg_color=C.SURFACE, corner_radius=0, width=320)
        panel.grid(row=0, column=1, sticky="nsew")
        panel.grid_propagate(False)
        panel.grid_columnconfigure(0, weight=1)

        r = 0

        ctk.CTkLabel(panel, text="Print / Save PDF",
                     font=ctk.CTkFont(*F.TITLE_LG), text_color=C.TEXT_1,
                     ).grid(row=r, column=0, padx=S.LG, pady=(S.LG, S.SM), sticky="w")
        r += 1

        ctk.CTkFrame(panel, height=1, fg_color=C.BORDER, corner_radius=0
                     ).grid(row=r, column=0, sticky="ew")
        r += 1

        # Patient card
        card = ctk.CTkFrame(panel, fg_color=C.SURFACE_2,
                            border_color=C.BORDER, border_width=1, corner_radius=R.MD)
        card.grid(row=r, column=0, padx=S.LG, pady=S.MD, sticky="ew")
        card.grid_columnconfigure(1, weight=1)
        r += 1

        patient = self._payload.get("patient_data") or {}
        examen  = self._payload.get("examen_data")  or {}
        nom     = patient.get("nom",    examen.get("nom",    ""))
        prenom  = patient.get("prenom", examen.get("prenom", ""))
        name    = f"{nom.upper()}, {prenom}".strip(", ") or "—"
        mod     = self._payload.get("modality",  examen.get("modalite",    ""))
        etype   = self._payload.get("exam_type", examen.get("type_examen", ""))
        etab    = self._payload.get("etablissement", "")
        med     = self._payload.get("medecin", "")
        date    = examen.get("date_examen", "")

        for i, (lbl, val) in enumerate([
            ("Patient",     name),
            ("Exam",        f"{mod} {etype}".strip() or "—"),
            ("Date",        date or "—"),
            ("Radiologist", med  or "—"),
            ("Facility",    etab or "—"),
        ]):
            ctk.CTkLabel(card, text=f"{lbl}:",
                         font=ctk.CTkFont(*F.BODY_SM), text_color=C.TEXT_3, anchor="w",
                         ).grid(row=i, column=0, sticky="w", padx=(S.MD, S.XS), pady=2)
            ctk.CTkLabel(card, text=val,
                         font=ctk.CTkFont(*F.BODY_SM), text_color=C.TEXT_1, anchor="w",
                         wraplength=160,
                         ).grid(row=i, column=1, sticky="w", padx=(0, S.MD), pady=2)
        ctk.CTkLabel(card, text="", height=4).grid(row=5, column=0)

        # Printer
        ctk.CTkLabel(panel, text="Printer",
                     font=ctk.CTkFont(*F.BODY_SM), text_color=C.TEXT_2, anchor="w",
                     ).grid(row=r, column=0, padx=S.LG, sticky="w")
        r += 1

        printers, default = _list_printers()
        self._printer_combo = ctk.CTkComboBox(
            panel, values=printers,
            fg_color=C.SURFACE_3, border_color=C.BORDER,
            button_color=C.SURFACE_3, button_hover_color=C.BORDER_2,
            text_color=C.TEXT_1,
            dropdown_fg_color=C.SURFACE_2, dropdown_text_color=C.TEXT_1,
            dropdown_hover_color=C.SURFACE_3,
            font=ctk.CTkFont(*F.BODY), height=34, corner_radius=R.MD,
        )
        self._printer_combo.grid(row=r, column=0, padx=S.LG,
                                 pady=(S.XS, S.MD), sticky="ew")
        self._printer_combo.set(default if default in printers else (printers[0] if printers else ""))
        r += 1

        ctk.CTkFrame(panel, height=1, fg_color=C.BORDER, corner_radius=0
                     ).grid(row=r, column=0, sticky="ew")
        r += 1

        # Status
        self._status_lbl = ctk.CTkLabel(
            panel, text="⏳  Generating PDF…",
            font=ctk.CTkFont(*F.BODY_SM), text_color=C.TEXT_3,
            wraplength=280, justify="center",
        )
        self._status_lbl.grid(row=r, column=0, padx=S.LG, pady=S.MD)
        r += 1

        # Spacer
        spacer = ctk.CTkFrame(panel, fg_color="transparent")
        spacer.grid(row=r, column=0, sticky="nsew")
        panel.grid_rowconfigure(r, weight=1)
        r += 1

        # Buttons
        btn = ctk.CTkFrame(panel, fg_color="transparent")
        btn.grid(row=r, column=0, padx=S.LG, pady=(0, S.MD), sticky="ew")
        btn.grid_columnconfigure((0, 1), weight=1)
        r += 1

        self._save_btn = ctk.CTkButton(
            btn, text="💾  Save PDF",
            fg_color=C.SURFACE_3, hover_color=C.BORDER,
            text_color=C.TEXT_1, border_color=C.BORDER, border_width=1,
            font=ctk.CTkFont(*F.BODY_SM), height=36, corner_radius=R.MD,
            state="disabled", command=self._save_pdf,
        )
        self._save_btn.grid(row=0, column=0, padx=(0, S.XS), sticky="ew")

        self._print_btn = ctk.CTkButton(
            btn, text="🖨  Print",
            fg_color=C.TEAL, hover_color=C.TEAL_DARK, text_color=C.TEXT_INV,
            font=ctk.CTkFont(*F.SUBHEADING), height=36, corner_radius=R.MD,
            state="disabled", command=self._print,
        )
        self._print_btn.grid(row=0, column=1, sticky="ew")

        ctk.CTkButton(
            panel, text="✕  Close",
            fg_color="transparent", hover_color=C.SURFACE_3,
            text_color=C.TEXT_3, border_color=C.BORDER, border_width=1,
            font=ctk.CTkFont(*F.BODY_SM), height=32, corner_radius=R.MD,
            command=self._close,
        ).grid(row=r, column=0, padx=S.LG, pady=(0, S.LG), sticky="ew")

    # ── PDF pipeline ───────────────────────────────────────────────────────────

    def _generate_async(self):
        threading.Thread(target=self._do_generate, daemon=True).start()

    def _do_generate(self):
        pdf_path = os.path.join(
            tempfile.gettempdir(),
            f"pisum_preview_{os.getpid()}.pdf",
        )
        ok = _generate_pdf_from_payload(self._payload, pdf_path, is_free=self._is_free)
        self.after(0, lambda: self._on_pdf_ready(pdf_path if ok else None))

    def _on_pdf_ready(self, pdf_path: str | None):
        if not self.winfo_exists():
            return
        if not pdf_path:
            self._set_status("⚠  PDF generation failed.", C.ERROR)
            self._canvas.itemconfigure("loading",
                text="⚠  PDF generation failed.\nCheck logs for details.")
            return

        self._pdf_path = pdf_path
        self._set_status("⏳  Rendering preview…", C.TEXT_3)
        threading.Thread(target=self._render_async, daemon=True).start()

    def _render_async(self):
        imgs = _render_pages(self._pdf_path, zoom=self._zoom)
        self.after(0, lambda: self._on_render_ready(imgs))

    def _on_render_ready(self, imgs: list):
        if not self.winfo_exists():
            return
        if imgs:
            self._page_imgs = imgs
            self._draw_pages()
            n = len(imgs)
            self._page_lbl.configure(
                text=f"{n} page{'s' if n > 1 else ''}")
        else:
            self._canvas.itemconfigure(
                "loading",
                text="Preview requires PyMuPDF.\npip install pymupdf\n\n"
                     "Save PDF and Print still work.",
            )
        self._set_status("✓  PDF ready", C.SUCCESS)
        self._save_btn.configure(state="normal")
        self._print_btn.configure(state="normal")

    # ── Canvas ─────────────────────────────────────────────────────────────────

    def _draw_pages(self):
        self._canvas.delete("all")
        self._canvas_ids.clear()
        if not self._page_imgs:
            return
        cw = self._canvas.winfo_width() or 700
        y  = self._PAGE_GAP
        for img in self._page_imgs:
            pw, ph = img.width(), img.height()
            cx     = max(cw // 2, pw // 2 + self._PAGE_GAP)
            # shadow
            self._canvas.create_rectangle(
                cx - pw//2 + self._PAGE_SHADOW, y + self._PAGE_SHADOW,
                cx + pw//2 + self._PAGE_SHADOW, y + ph + self._PAGE_SHADOW,
                fill="#111111", outline="",
            )
            # page
            cid = self._canvas.create_image(cx, y, anchor="n", image=img)
            self._canvas_ids.append(cid)
            y += ph + self._PAGE_GAP
        self._canvas.configure(scrollregion=(0, 0, cw, y + self._PAGE_GAP))

    def _on_canvas_resize(self, _e):
        if self._page_imgs:
            self._draw_pages()

    def _on_wheel(self, event):
        self._canvas.yview_scroll(-1 * (event.delta // 120), "units")

    # ── Zoom ───────────────────────────────────────────────────────────────────

    def _zoom_in(self):
        if self._zoom < 3.0 and self._pdf_path:
            self._zoom = round(self._zoom + 0.25, 2)
            self._zoom_lbl.configure(text=f"{int(self._zoom * 100)}%")
            self._rerender()

    def _zoom_out(self):
        if self._zoom > 0.5 and self._pdf_path:
            self._zoom = round(self._zoom - 0.25, 2)
            self._zoom_lbl.configure(text=f"{int(self._zoom * 100)}%")
            self._rerender()

    def _rerender(self):
        self._save_btn.configure(state="disabled")
        self._print_btn.configure(state="disabled")
        threading.Thread(target=self._render_async, daemon=True).start()

    # ── Actions ────────────────────────────────────────────────────────────────

    def _save_pdf(self):
        if not self._pdf_path:
            return
        from tkinter import filedialog
        patient = self._payload.get("patient_data") or {}
        nom     = patient.get("nom", "rapport").lower().replace(" ", "_")
        dest = filedialog.asksaveasfilename(
            parent=self, title="Save PDF",
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
            initialfile=f"rapport_{nom}.pdf",
        )
        if not dest:
            return
        try:
            import shutil
            shutil.copy2(self._pdf_path, dest)
            self._set_status(f"✓  Saved: {os.path.basename(dest)}", C.SUCCESS)
        except Exception as e:
            self._set_status(f"⚠  Save failed: {e}", C.ERROR)

    def _print(self):
        if not self._pdf_path:
            return
        printer = self._printer_combo.get()
        self._set_status(f"⏳  Sending to {printer}…", C.TEXT_3)
        ok = _send_to_printer(self._pdf_path, printer)
        if ok:
            self._set_status(f"✓  Sent to {printer}", C.SUCCESS)
            self.after(2500, self._close)
        else:
            self._set_status("⚠  Failed — check printer.", C.ERROR)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _set_status(self, text: str, color=None):
        if self.winfo_exists():
            kw = {"text": text}
            if color:
                kw["text_color"] = color
            self._status_lbl.configure(**kw)

    def _safe_grab(self):
        try:
            self.grab_set()
        except Exception:
            pass

    def _close(self):
        if self._on_close:
            self._on_close()
        self.destroy()

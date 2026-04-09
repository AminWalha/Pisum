# -*- coding: utf-8 -*-
"""
pacs_ris_db.py — Module PACS/RIS local
===================================================
Gestion complète des dossiers médicaux radiologiques des patients avec support multilingue.
"""

import sqlite3
import uuid
import hashlib
import hmac
import datetime
import os
import logging
import platform
import base64
import json
import getpass
import threading
from pathlib import Path
from pisum_license_manager import LicenseManager

# ── Chiffrement AES-256-GCM (cryptography) ───────────────────────────────────
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes as _crypto_hashes
    _CRYPTO_OK = True
except ImportError:  # pragma: no cover
    _CRYPTO_OK = False
    logging.getLogger(__name__).critical(
        "❌ 'cryptography' introuvable — pip install cryptography"
    )

import wx
import wx.lib.scrolledpanel as scrolled

logger = logging.getLogger(__name__)

# ── _PACS_STATE : résistant au renommage RFT PyArmor ────────────────────────
_PACS_STATE = {"patient": None, "examen": None}

# ══════════════════════════════════════════════════════════════════════════════
#  TRADUCTIONS
# ══════════════════════════════════════════════════════════════════════════════

PACS_TRANSLATIONS = {
    'Français': {
        'pacs_title': "🏥 PACS/RIS — Dossiers Radiologiques",
        'search_hint': "🔍 Rechercher un patient…",
        'btn_new_pat': "➕ Patient",
        'col_dos': "N° Dossier",
        'col_nom': "Nom",
        'col_prenom': "Prénom",
        'col_ddn': "DDN",
        'col_pays': "Pays",
        'col_exam': "Examens",
        'lbl_select_pat': "Sélectionner un patient",
        'btn_edit': "✏ Modifier",
        'btn_del': "🗑 Supprimer",
        'btn_new_exam': "📋 Nouvel examen",
        'lbl_hist': "  📁 Historique des examens (double-clic = voir CRs)",
        'col_acc': "N° Accession",
        'col_date': "Date",
        'col_mod': "Modalité",
        'col_type': "Type",
        'col_presc': "Prescripteur",
        'col_rad': "Radiologue",
        'col_statut': "Statut",
        'lbl_cr_edit': "  📝 Compte rendu (édition rapide)",
        'btn_use_cr': "📤 Utiliser dans CR",
        'btn_save_cr': "💾 Sauvegarder CR",
        'btn_copy_cr': "📋 Copier CR",
        'btn_view_crs': "📄 Voir tous les CRs",
        'btn_edit_exam': "✏ Modifier examen",
        'btn_del_exam': "🗑 Supprimer examen",
        'dlg_edit_pat': "✏️ Modifier le patient",
        'dlg_new_pat': "👤 Nouveau Patient",
        'hdr_edit_pat': "  ✏️ Modifier le dossier patient",
        'hdr_new_pat': "  👤 Nouveau dossier patient",
        'lbl_nom_req': "Nom *",
        'lbl_prenom_req': "Prénom *",
        'lbl_ddn': "Date naissance",
        'lbl_sexe': "Sexe",
        'lbl_cin': "CIN / Pièce ID",
        'lbl_tel': "Téléphone",
        'lbl_adresse': "Adresse",
        'lbl_rem': "Remarques",
        'btn_cancel': "✕ Annuler",
        'btn_save_mod': "✔ Enregistrer les modifications",
        'btn_save': "✔ Enregistrer",
        'dlg_edit_exam': "✏ Modifier l'examen",
        'dlg_new_exam': "📋 Nouvel Examen",
        'lbl_date_req': "Date examen *",
        'lbl_mod_req': "Modalité *",
        'lbl_ind': "Indication",
        'lbl_etab': "Établissement",
        'lbl_lang': "Langue CR",
        'btn_create_exam': "✔ Créer l'examen",
        'dlg_crs': "📄 CRs — ",
        'lbl_info_none': "   Aucune information complémentaire",
        'lbl_versions': "  Versions",
        'col_version': "Version",
        'lbl_cr_content': "  Contenu du compte rendu",
        'btn_copy': "📋 Copier",
        'btn_open_main': "📖 Ouvrir dans l'éditeur",
        'btn_close': "✕ Fermer",
        'lbl_prescripteur': "Prescripteur",
        'lbl_radiologue': "Radiologue",
        'lbl_indication': "Indication",
        'stats_patients': "patients",
        'stats_examens': "examens",
        'stats_today': "aujourd'hui",
        'confirm_del_patient': "Supprimer le dossier et tous ses examens ?\nCette action est irréversible.",
        'confirm_del_patient_title': "Confirmer",
        'confirm_del_exam': "Supprimer cet examen et tous ses comptes rendus ?",
        'confirm_del_exam_title': "Confirmer",
        'no_exam_selected': "Sélectionnez un examen dans la liste.",
        'no_exam_title': "Sélection manquante",
        'use_cr_ok_title': "Chargé dans CR",
        'statut_en_cours': "En cours",
        'statut_finalise': "Finalisé",
        'statut_archive': "Archivé",
        'sexe_M':     "M",
        'sexe_F':     "F",
        'sexe_Autre': "Autre",
        'modalites': ["IRM", "Scanner", "Echographie", "Radiographie conventionnelle",
                      "Radiologie Interventionnelle", "Sénologie", "Consultations", "Autre"],
    },
    'English': {
        'pacs_title': "🏥 PACS/RIS — Radiological Records",
        'search_hint': "🔍 Search for a patient…",
        'btn_new_pat': "➕ Patient",
        'col_dos': "File No",
        'col_nom': "Last Name",
        'col_prenom': "First Name",
        'col_ddn': "DOB",
        'col_pays': "Country",
        'col_exam': "Exams",
        'lbl_select_pat': "Select a patient",
        'btn_edit': "✏ Edit",
        'btn_del': "🗑 Delete",
        'btn_new_exam': "📋 New Exam",
        'lbl_hist': "  📁 Exam History (double-click = view reports)",
        'col_acc': "Accession No",
        'col_date': "Date",
        'col_mod': "Modality",
        'col_type': "Type",
        'col_presc': "Ref. Physician",
        'col_rad': "Radiologist",
        'col_statut': "Status",
        'lbl_cr_edit': "  📝 Report (quick edit)",
        'btn_use_cr': "📤 Use in Report",
        'btn_save_cr': "💾 Save Report",
        'btn_copy_cr': "📋 Copy Report",
        'btn_view_crs': "📄 View all reports",
        'btn_edit_exam': "✏ Edit exam",
        'btn_del_exam': "🗑 Delete exam",
        'dlg_edit_pat': "✏️ Edit Patient",
        'dlg_new_pat': "👤 New Patient",
        'hdr_edit_pat': "  ✏️ Edit patient record",
        'hdr_new_pat': "  👤 New patient record",
        'lbl_nom_req': "Last Name *",
        'lbl_prenom_req': "First Name *",
        'lbl_ddn': "Date of Birth",
        'lbl_sexe': "Gender",
        'lbl_cin': "ID / Passport",
        'lbl_tel': "Phone",
        'lbl_adresse': "Address",
        'lbl_rem': "Notes",
        'btn_cancel': "✕ Cancel",
        'btn_save_mod': "✔ Save changes",
        'btn_save': "✔ Save",
        'dlg_edit_exam': "✏ Edit Exam",
        'dlg_new_exam': "📋 New Exam",
        'lbl_date_req': "Exam Date *",
        'lbl_mod_req': "Modality *",
        'lbl_ind': "Indication",
        'lbl_etab': "Facility",
        'lbl_lang': "Report Lang",
        'btn_create_exam': "✔ Create exam",
        'dlg_crs': "📄 Reports — ",
        'lbl_info_none': "   No additional information",
        'lbl_versions': "  Versions",
        'col_version': "Version",
        'lbl_cr_content': "  Report Content",
        'btn_copy': "📋 Copy",
        'btn_open_main': "📖 Open in Editor",
        'btn_close': "✕ Close",
        'lbl_prescripteur': "Ref. Physician",
        'lbl_radiologue': "Radiologist",
        'lbl_indication': "Indication",
        'stats_patients': "patients",
        'stats_examens': "exams",
        'stats_today': "today",
        'confirm_del_patient': "Delete this record and all its exams?\nThis action cannot be undone.",
        'confirm_del_patient_title': "Confirm",
        'confirm_del_exam': "Delete this exam and all its reports?",
        'confirm_del_exam_title': "Confirm",
        'no_exam_selected': "Please select an exam from the list.",
        'no_exam_title': "No selection",
        'use_cr_ok_title': "Loaded in Report",
        'statut_en_cours': "In progress",
        'statut_finalise': "Finalized",
        'statut_archive': "Archived",
        'sexe_M':     "M",
        'sexe_F':     "F",
        'sexe_Autre': "Other",
        'modalites': ["MRI", "CT Scan", "Ultrasound", "Conventional Radiography",
                      "Interventional Radiology", "Breast Imaging", "Consultations", "Other"],
    },
    '中文': {
        'pacs_title': "🏥 PACS/RIS — 放射科档案",
        'search_hint': "🔍 搜索患者…",
        'btn_new_pat': "➕ 患者",
        'col_dos': "档案号",
        'col_nom': "姓",
        'col_prenom': "名",
        'col_ddn': "出生日期",
        'col_pays': "国家",
        'col_exam': "检查",
        'lbl_select_pat': "选择患者",
        'btn_edit': "✏ 编辑",
        'btn_del': "🗑 删除",
        'btn_new_exam': "📋 新检查",
        'lbl_hist': "  📁 检查历史 (双击查看报告)",
        'col_acc': "检查号",
        'col_date': "日期",
        'col_mod': "检查类别",
        'col_type': "类型",
        'col_presc': "申请医生",
        'col_rad': "放射科医生",
        'col_statut': "状态",
        'lbl_cr_edit': "  📝 报告 (快速编辑)",
        'btn_use_cr': "📤 用于报告",
        'btn_save_cr': "💾 保存报告",
        'btn_copy_cr': "📋 复制报告",
        'btn_view_crs': "📄 查看所有报告",
        'btn_edit_exam': "✏ 编辑检查",
        'btn_del_exam': "🗑 删除检查",
        'dlg_edit_pat': "✏️ 编辑患者",
        'dlg_new_pat': "👤 新患者",
        'hdr_edit_pat': "  ✏️ 编辑患者档案",
        'hdr_new_pat': "  👤 新建患者档案",
        'lbl_nom_req': "姓 *",
        'lbl_prenom_req': "名 *",
        'lbl_ddn': "出生日期",
        'lbl_sexe': "性别",
        'lbl_cin': "身份证/护照",
        'lbl_tel': "电话",
        'lbl_adresse': "地址",
        'lbl_rem': "备注",
        'btn_cancel': "✕ 取消",
        'btn_save_mod': "✔ 保存修改",
        'btn_save': "✔ 保存",
        'dlg_new_exam': "📋 新检查",
        'lbl_date_req': "检查日期 *",
        'lbl_mod_req': "检查类别 *",
        'lbl_ind': "临床表现",
        'lbl_etab': "机构",
        'lbl_lang': "报告语言",
        'btn_create_exam': "✔ 创建检查",
        'dlg_crs': "📄 报告 — ",
        'lbl_info_none': "   无附加信息",
        'lbl_versions': "  版本",
        'col_version': "版本",
        'lbl_cr_content': "  报告内容",
        'btn_copy': "📋 复制",
        'btn_open_main': "📖 在编辑器中打开",
        'btn_close': "✕ 关闭",
        'lbl_prescripteur': "申请医生",
        'lbl_radiologue': "放射科医生",
        'lbl_indication': "临床表现",
        'stats_patients': "患者",
        'stats_examens': "检查",
        'stats_today': "今日",
        'confirm_del_patient': "删除该档案及其所有检查？\n此操作无法撤销。",
        'confirm_del_patient_title': "确认",
        'confirm_del_exam': "删除此检查及其所有报告？",
        'confirm_del_exam_title': "确认",
        'no_exam_selected': "请从列表中选择一项检查。",
        'no_exam_title': "未选择",
        'use_cr_ok_title': "已加载到报告",
        'statut_en_cours': "进行中",
        'statut_finalise': "已完成",
        'statut_archive': "已归档",
        'sexe_M':     "男",
        'sexe_F':     "女",
        'sexe_Autre': "其他",
        'modalites': ["MRI", "CT扫描", "超声波", "常规放射线",
                      "介入放射学", "乳腺影像", "咨询", "其他"],
    },
    'Español': {
        'pacs_title': "🏥 PACS/RIS — Registros Radiológicos",
        'search_hint': "🔍 Buscar paciente…",
        'btn_new_pat': "➕ Paciente",
        'col_dos': "Nº Expediente",
        'col_nom': "Apellido",
        'col_prenom': "Nombre",
        'col_ddn': "Fecha Nac.",
        'col_pays': "País",
        'col_exam': "Exámenes",
        'lbl_select_pat': "Seleccionar un paciente",
        'btn_edit': "✏ Editar",
        'btn_del': "🗑 Eliminar",
        'btn_new_exam': "📋 Nuevo Examen",
        'lbl_hist': "  📁 Historial (doble clic = ver informes)",
        'col_acc': "Nº Acceso",
        'col_date': "Fecha",
        'col_mod': "Modalidad",
        'col_type': "Tipo",
        'col_presc': "Médico Solicitante",
        'col_rad': "Radiólogo",
        'col_statut': "Estado",
        'lbl_cr_edit': "  📝 Informe (edición rápida)",
        'btn_use_cr': "📤 Usar en Informe",
        'btn_save_cr': "💾 Guardar Informe",
        'btn_copy_cr': "📋 Copiar Informe",
        'btn_view_crs': "📄 Ver informes",
        'btn_edit_exam': "✏ Editar examen",
        'btn_del_exam': "🗑 Eliminar examen",
        'dlg_edit_pat': "✏️ Editar Paciente",
        'dlg_new_pat': "👤 Nuevo Paciente",
        'hdr_edit_pat': "  ✏️ Editar expediente",
        'hdr_new_pat': "  👤 Nuevo expediente",
        'lbl_nom_req': "Apellido *",
        'lbl_prenom_req': "Nombre *",
        'lbl_ddn': "Fecha de nacimiento",
        'lbl_sexe': "Sexo",
        'lbl_cin': "DNI / Pasaporte",
        'lbl_tel': "Teléfono",
        'lbl_adresse': "Dirección",
        'lbl_rem': "Notas",
        'btn_cancel': "✕ Cancelar",
        'btn_save_mod': "✔ Guardar cambios",
        'btn_save': "✔ Guardar",
        'dlg_edit_exam': "✏ Editar examen",
        'dlg_new_exam': "📋 Nuevo Examen",
        'lbl_date_req': "Fecha examen *",
        'lbl_mod_req': "Modalidad *",
        'lbl_ind': "Indicación",
        'lbl_etab': "Centro",
        'lbl_lang': "Idioma Informe",
        'btn_create_exam': "✔ Crear examen",
        'dlg_crs': "📄 Informes — ",
        'lbl_info_none': "   Sin información adicional",
        'lbl_versions': "  Versiones",
        'col_version': "Versión",
        'lbl_cr_content': "  Contenido del informe",
        'btn_copy': "📋 Copiar",
        'btn_open_main': "📖 Abrir en editor",
        'btn_close': "✕ Cerrar",
        'lbl_prescripteur': "Médico Solicitante",
        'lbl_radiologue': "Radiólogo",
        'lbl_indication': "Indicación",
        'stats_patients': "pacientes",
        'stats_examens': "exámenes",
        'stats_today': "hoy",
        'confirm_del_patient': "¿Eliminar el expediente y todos sus exámenes?\nEsta acción es irreversible.",
        'confirm_del_patient_title': "Confirmar",
        'confirm_del_exam': "¿Eliminar este examen y todos sus informes?",
        'confirm_del_exam_title': "Confirmar",
        'no_exam_selected': "Seleccione un examen de la lista.",
        'no_exam_title': "Selección requerida",
        'use_cr_ok_title': "Cargado en Informe",
        'statut_en_cours': "En curso",
        'statut_finalise': "Finalizado",
        'statut_archive': "Archivado",
        'sexe_M':     "M",
        'sexe_F':     "F",
        'sexe_Autre': "Otro",
        'modalites': ["IRM", "TAC", "Ecografía", "Radiografía convencional",
                      "Radiología Intervencionista", "Senología", "Consultas", "Otro"],
    },
    'Deutsch': {
        'pacs_title': "🏥 PACS/RIS — Radiologische Akten",
        'search_hint': "🔍 Patient suchen…",
        'btn_new_pat': "➕ Patient",
        'col_dos': "Akte-Nr.",
        'col_nom': "Nachname",
        'col_prenom': "Vorname",
        'col_ddn': "Geburtsdatum",
        'col_pays': "Land",
        'col_exam': "Untersuchungen",
        'lbl_select_pat': "Patient auswählen",
        'btn_edit': "✏ Bearbeiten",
        'btn_del': "🗑 Löschen",
        'btn_new_exam': "📋 Neue Untersuchung",
        'lbl_hist': "  📁 Historie (Doppelklick = Befunde)",
        'col_acc': "Accession-Nr.",
        'col_date': "Datum",
        'col_mod': "Modalität",
        'col_type': "Typ",
        'col_presc': "Zuweiser",
        'col_rad': "Radiologe",
        'col_statut': "Status",
        'lbl_cr_edit': "  📝 Befund (Schnellbearbeitung)",
        'btn_use_cr': "📤 In Befund verwenden",
        'btn_save_cr': "💾 Befund speichern",
        'btn_copy_cr': "📋 Befund kopieren",
        'btn_view_crs': "📄 Alle Befunde",
        'btn_edit_exam': "✏ Untersuchung bearbeiten",
        'btn_del_exam': "🗑 Untersuchung löschen",
        'dlg_edit_pat': "✏️ Patient bearbeiten",
        'dlg_new_pat': "👤 Neuer Patient",
        'hdr_edit_pat': "  ✏️ Patientenakte bearbeiten",
        'hdr_new_pat': "  👤 Neue Patientenakte",
        'lbl_nom_req': "Nachname *",
        'lbl_prenom_req': "Vorname *",
        'lbl_ddn': "Geburtsdatum",
        'lbl_sexe': "Geschlecht",
        'lbl_cin': "Ausweis-Nr.",
        'lbl_tel': "Telefon",
        'lbl_adresse': "Adresse",
        'lbl_rem': "Notizen",
        'btn_cancel': "✕ Abbrechen",
        'btn_save_mod': "✔ Änderungen speichern",
        'btn_save': "✔ Speichern",
        'dlg_edit_exam': "✏ Untersuchung bearbeiten",
        'dlg_new_exam': "📋 Neue Untersuchung",
        'lbl_date_req': "Datum *",
        'lbl_mod_req': "Modalität *",
        'lbl_ind': "Indikation",
        'lbl_etab': "Einrichtung",
        'lbl_lang': "Befundsprache",
        'btn_create_exam': "✔ Erstellen",
        'dlg_crs': "📄 Befunde — ",
        'lbl_info_none': "   Keine Zusatzinfos",
        'lbl_versions': "  Versionen",
        'col_version': "Version",
        'lbl_cr_content': "  Befundinhalt",
        'btn_copy': "📋 Kopieren",
        'btn_open_main': "📖 Im Editor öffnen",
        'btn_close': "✕ Schließen",
        'lbl_prescripteur': "Zuweiser",
        'lbl_radiologue': "Radiologe",
        'lbl_indication': "Indikation",
        'stats_patients': "Patienten",
        'stats_examens': "Untersuchungen",
        'stats_today': "heute",
        'confirm_del_patient': "Akte und alle Untersuchungen löschen?\nDieser Vorgang kann nicht rückgängig gemacht werden.",
        'confirm_del_patient_title': "Bestätigen",
        'confirm_del_exam': "Diese Untersuchung und alle Befunde löschen?",
        'confirm_del_exam_title': "Bestätigen",
        'no_exam_selected': "Bitte wählen Sie eine Untersuchung aus der Liste.",
        'no_exam_title': "Keine Auswahl",
        'use_cr_ok_title': "In Befund geladen",
        'statut_en_cours': "Laufend",
        'statut_finalise': "Abgeschlossen",
        'statut_archive': "Archiviert",
        'sexe_M':     "M",
        'sexe_F':     "W",
        'sexe_Autre': "Divers",
        'modalites': ["MRT", "CT", "Ultraschall", "Konventionelles Röntgen",
                      "Interventionelle Radiologie", "Mammadiagnostik", "Konsultationen", "Sonstiges"],
    },
    'Italiano': {
        'pacs_title': "🏥 PACS/RIS — Cartelle Radiologiche",
        'search_hint': "🔍 Cerca paziente…",
        'btn_new_pat': "➕ Paziente",
        'col_dos': "N° Cartella",
        'col_nom': "Cognome",
        'col_prenom': "Nome",
        'col_ddn': "Data Nasc.",
        'col_pays': "Paese",
        'col_exam': "Esami",
        'lbl_select_pat': "Seleziona paziente",
        'btn_edit': "✏ Modifica",
        'btn_del': "🗑 Elimina",
        'btn_new_exam': "📋 Nuovo Esame",
        'lbl_hist': "  📁 Cronologia (doppio clic = referti)",
        'col_acc': "N° Accessione",
        'col_date': "Data",
        'col_mod': "Modalità",
        'col_type': "Tipo",
        'col_presc': "Medico Prescrittore",
        'col_rad': "Radiologo",
        'col_statut': "Stato",
        'lbl_cr_edit': "  📝 Referto (modifica rapida)",
        'btn_use_cr': "📤 Usa nel Referto",
        'btn_save_cr': "💾 Salva Referto",
        'btn_copy_cr': "📋 Copia Referto",
        'btn_view_crs': "📄 Vedi referti",
        'btn_edit_exam': "✏ Modifica esame",
        'btn_del_exam': "🗑 Elimina esame",
        'dlg_edit_pat': "✏️ Modifica Paziente",
        'dlg_new_pat': "👤 Nuovo Paziente",
        'hdr_edit_pat': "  ✏️ Modifica cartella",
        'hdr_new_pat': "  👤 Nuova cartella",
        'lbl_nom_req': "Cognome *",
        'lbl_prenom_req': "Nome *",
        'lbl_ddn': "Data di nascita",
        'lbl_sexe': "Sesso",
        'lbl_cin': "ID / Passaporto",
        'lbl_tel': "Telefono",
        'lbl_adresse': "Indirizzo",
        'lbl_rem': "Note",
        'btn_cancel': "✕ Annulla",
        'btn_save_mod': "✔ Salva modifiche",
        'btn_save': "✔ Salva",
        'dlg_edit_exam': "✏ Modifica esame",
        'dlg_new_exam': "📋 Nuovo Esame",
        'lbl_date_req': "Data esame *",
        'lbl_mod_req': "Modalità *",
        'lbl_ind': "Indicazione",
        'lbl_etab': "Struttura",
        'lbl_lang': "Lingua Referto",
        'btn_create_exam': "✔ Crea esame",
        'dlg_crs': "📄 Referti — ",
        'lbl_info_none': "   Nessuna info extra",
        'lbl_versions': "  Versioni",
        'col_version': "Versione",
        'lbl_cr_content': "  Contenuto referto",
        'btn_copy': "📋 Copia",
        'btn_open_main': "📖 Apri nell'editor",
        'btn_close': "✕ Chiudi",
        'lbl_prescripteur': "Medico Prescrittore",
        'lbl_radiologue': "Radiologo",
        'lbl_indication': "Indicazione",
        'stats_patients': "pazienti",
        'stats_examens': "esami",
        'stats_today': "oggi",
        'confirm_del_patient': "Eliminare la cartella e tutti gli esami?\nQuesta azione è irreversibile.",
        'confirm_del_patient_title': "Conferma",
        'confirm_del_exam': "Eliminare questo esame e tutti i referti?",
        'confirm_del_exam_title': "Conferma",
        'no_exam_selected': "Seleziona un esame dalla lista.",
        'no_exam_title': "Nessuna selezione",
        'use_cr_ok_title': "Caricato nel Referto",
        'statut_en_cours': "In corso",
        'statut_finalise': "Finalizzato",
        'statut_archive': "Archiviato",
        'sexe_M':     "M",
        'sexe_F':     "F",
        'sexe_Autre': "Altro",
        'modalites': ["RM", "TC", "Ecografia", "Radiografia convenzionale",
                      "Radiologia Interventistica", "Senologia", "Consulenze", "Altro"],
    },
    'Português': {
        'pacs_title': "🏥 PACS/RIS — Registos Radiológicos",
        'search_hint': "🔍 Procurar paciente…",
        'btn_new_pat': "➕ Paciente",
        'col_dos': "N° Processo",
        'col_nom': "Apelido",
        'col_prenom': "Nome",
        'col_ddn': "Data Nasc.",
        'col_pays': "País",
        'col_exam': "Exames",
        'lbl_select_pat': "Selecionar paciente",
        'btn_edit': "✏ Editar",
        'btn_del': "🗑 Eliminar",
        'btn_new_exam': "📋 Novo Exame",
        'lbl_hist': "  📁 Histórico (duplo clique = relatórios)",
        'col_acc': "N° Acesso",
        'col_date': "Data",
        'col_mod': "Modalidade",
        'col_type': "Tipo",
        'col_presc': "Médico Prescritor",
        'col_rad': "Radiologista",
        'col_statut': "Estado",
        'lbl_cr_edit': "  📝 Relatório (edição rápida)",
        'btn_use_cr': "📤 Usar no Relatório",
        'btn_save_cr': "💾 Guardar Relatório",
        'btn_copy_cr': "📋 Copiar Relatório",
        'btn_view_crs': "📄 Ver relatórios",
        'btn_edit_exam': "✏ Editar exame",
        'btn_del_exam': "🗑 Eliminar exame",
        'dlg_edit_pat': "✏️ Editar Paciente",
        'dlg_new_pat': "👤 Novo Paciente",
        'hdr_edit_pat': "  ✏️ Editar registo",
        'hdr_new_pat': "  👤 Novo registo",
        'lbl_nom_req': "Apelido *",
        'lbl_prenom_req': "Nome *",
        'lbl_ddn': "Data de nascimento",
        'lbl_sexe': "Sexo",
        'lbl_cin': "ID / Passaporte",
        'lbl_tel': "Telefone",
        'lbl_adresse': "Morada",
        'lbl_rem': "Notas",
        'btn_cancel': "✕ Cancelar",
        'btn_save_mod': "✔ Guardar alterações",
        'btn_save': "✔ Guardar",
        'dlg_edit_exam': "✏ Editar exame",
        'dlg_new_exam': "📋 Novo Exame",
        'lbl_date_req': "Data exame *",
        'lbl_mod_req': "Modalidade *",
        'lbl_ind': "Indicação",
        'lbl_etab': "Instituição",
        'lbl_lang': "Idioma Relatório",
        'btn_create_exam': "✔ Criar exame",
        'dlg_crs': "📄 Relatórios — ",
        'lbl_info_none': "   Sem informações extras",
        'lbl_versions': "  Versões",
        'col_version': "Versão",
        'lbl_cr_content': "  Conteúdo do relatório",
        'btn_copy': "📋 Copiar",
        'btn_open_main': "📖 Abrir no editor",
        'btn_close': "✕ Fechar",
        'lbl_prescripteur': "Médico Prescritor",
        'lbl_radiologue': "Radiologista",
        'lbl_indication': "Indicação",
        'stats_patients': "pacientes",
        'stats_examens': "exames",
        'stats_today': "hoje",
        'confirm_del_patient': "Eliminar o registo e todos os exames?\nEsta ação é irreversível.",
        'confirm_del_patient_title': "Confirmar",
        'confirm_del_exam': "Eliminar este exame e todos os relatórios?",
        'confirm_del_exam_title': "Confirmar",
        'no_exam_selected': "Selecione um exame da lista.",
        'no_exam_title': "Seleção em falta",
        'use_cr_ok_title': "Carregado no Relatório",
        'statut_en_cours': "Em curso",
        'statut_finalise': "Finalizado",
        'statut_archive': "Arquivado",
        'sexe_M':     "M",
        'sexe_F':     "F",
        'sexe_Autre': "Outro",
        'modalites': ["RM", "TC", "Ecografia", "Radiografia convencional",
                      "Radiologia Intervencionista", "Senologia", "Consultas", "Outro"],
    },
    'Русский': {
        'pacs_title': "🏥 PACS/RIS — Радиологические карты",
        'search_hint': "🔍 Поиск пациента…",
        'btn_new_pat': "➕ Пациент",
        'col_dos': "№ Карты",
        'col_nom': "Фамилия",
        'col_prenom': "Имя",
        'col_ddn': "Дата рожд.",
        'col_pays': "Страна",
        'col_exam': "Исследования",
        'lbl_select_pat': "Выбрать пациента",
        'btn_edit': "✏ Изменить",
        'btn_del': "🗑 Удалить",
        'btn_new_exam': "📋 Новое иссл.",
        'lbl_hist': "  📁 История (двойной клик = отчеты)",
        'col_acc': "№ Доступа",
        'col_date': "Дата",
        'col_mod': "Модальность",
        'col_type': "Тип",
        'col_presc': "Направивший врач",
        'col_rad': "Радиолог",
        'col_statut': "Статус",
        'lbl_cr_edit': "  📝 Отчет (правка)",
        'btn_use_cr': "📤 В отчет",
        'btn_save_cr': "💾 Сохранить отчет",
        'btn_copy_cr': "📋 Копировать",
        'btn_view_crs': "📄 Все отчеты",
        'btn_edit_exam': "✏ Изменить исследование",
        'btn_del_exam': "🗑 Удалить исследование",
        'dlg_edit_pat': "✏️ Редактировать",
        'dlg_new_pat': "👤 Новый пациент",
        'hdr_edit_pat': "  ✏️ Правка карты",
        'hdr_new_pat': "  👤 Создание карты",
        'lbl_nom_req': "Фамилия *",
        'lbl_prenom_req': "Имя *",
        'lbl_ddn': "Дата рождения",
        'lbl_sexe': "Пол",
        'lbl_cin': "Паспорт / ID",
        'lbl_tel': "Телефон",
        'lbl_adresse': "Адрес",
        'lbl_rem': "Заметки",
        'btn_cancel': "✕ Отмена",
        'btn_save_mod': "✔ Сохранить изменения",
        'btn_save': "✔ Сохранить",
        'dlg_edit_exam': "✏ Изменить исследование",
        'dlg_new_exam': "📋 Новое исследование",
        'lbl_date_req': "Дата иссл. *",
        'lbl_mod_req': "Модальность *",
        'lbl_ind': "Показания",
        'lbl_etab': "Учреждение",
        'lbl_lang': "Язык отчета",
        'btn_create_exam': "✔ Создать",
        'dlg_crs': "📄 Отчеты — ",
        'lbl_info_none': "   Нет доп. информации",
        'lbl_versions': "  Версии",
        'col_version': "Версия",
        'lbl_cr_content': "  Содержание отчета",
        'btn_copy': "📋 Копировать",
        'btn_open_main': "📖 Открыть в редакторе",
        'btn_close': "✕ Закрыть",
        'lbl_prescripteur': "Направивший врач",
        'lbl_radiologue': "Радиолог",
        'lbl_indication': "Показания",
        'stats_patients': "пациентов",
        'stats_examens': "исследований",
        'stats_today': "сегодня",
        'confirm_del_patient': "Удалить карту и все исследования?\nЭто действие необратимо.",
        'confirm_del_patient_title': "Подтверждение",
        'confirm_del_exam': "Удалить это исследование и все отчёты?",
        'confirm_del_exam_title': "Подтверждение",
        'no_exam_selected': "Выберите исследование из списка.",
        'no_exam_title': "Нет выбора",
        'use_cr_ok_title': "Загружено в отчёт",
        'statut_en_cours': "Выполняется",
        'statut_finalise': "Завершено",
        'statut_archive': "Архивировано",
        'sexe_M':     "М",
        'sexe_F':     "Ж",
        'sexe_Autre': "Другое",
        'modalites': ["МРТ", "КТ", "УЗИ", "Обычная рентгенография",
                      "Интервенционная радиология", "Маммография", "Консультации", "Другое"],
    },
    'Türkçe': {
        'pacs_title': "🏥 PACS/RIS — Radyoloji Kayıtları",
        'search_hint': "🔍 Hasta ara…",
        'btn_new_pat': "➕ Hasta",
        'col_dos': "Dosya No",
        'col_nom': "Soyadı",
        'col_prenom': "Adı",
        'col_ddn': "Doğum Tarihi",
        'col_pays': "Ülke",
        'col_exam': "Tetkikler",
        'lbl_select_pat': "Hasta seçin",
        'btn_edit': "✏ Düzenle",
        'btn_del': "🗑 Sil",
        'btn_new_exam': "📋 Yeni Tetkik",
        'lbl_hist': "  📁 Tetkik Geçmişi (çift tık = raporlar)",
        'col_acc': "Erişim No",
        'col_date': "Tarih",
        'col_mod': "Modalite",
        'col_type': "Tür",
        'col_presc': "İsteyen Doktor",
        'col_rad': "Radyolog",
        'col_statut': "Durum",
        'lbl_cr_edit': "  📝 Rapor (hızlı düzenle)",
        'btn_use_cr': "📤 Raporda Kullan",
        'btn_save_cr': "💾 Raporu Kaydet",
        'btn_copy_cr': "📋 Raporu Kopyala",
        'btn_view_crs': "📄 Tüm raporlar",
        'btn_edit_exam': "✏ Tetkiki düzenle",
        'btn_del_exam': "🗑 Tetkiki sil",
        'dlg_edit_pat': "✏️ Hastayı Düzenle",
        'dlg_new_pat': "👤 Yeni Hasta",
        'hdr_edit_pat': "  ✏️ Kayıt düzenle",
        'hdr_new_pat': "  👤 Yeni kayıt",
        'lbl_nom_req': "Soyadı *",
        'lbl_prenom_req': "Adı *",
        'lbl_ddn': "Doğum Tarihi",
        'lbl_sexe': "Cinsiyet",
        'lbl_cin': "TC No / Pasaport",
        'lbl_tel': "Telefon",
        'lbl_adresse': "Adres",
        'lbl_rem': "Notlar",
        'btn_cancel': "✕ İptal",
        'btn_save_mod': "✔ Değişiklikleri kaydet",
        'btn_save': "✔ Kaydet",
        'dlg_edit_exam': "✏ Tetkiki düzenle",
        'dlg_new_exam': "📋 Yeni Tetkik",
        'lbl_date_req': "Tetkik Tarihi *",
        'lbl_mod_req': "Modalite *",
        'lbl_ind': "Endikasyon",
        'lbl_etab': "Kurum",
        'lbl_lang': "Rapor Dili",
        'btn_create_exam': "✔ Tetkik oluştur",
        'dlg_crs': "📄 Raporlar — ",
        'lbl_info_none': "   Ek bilgi yok",
        'lbl_versions': "  Versiyonlar",
        'col_version': "Versiyon",
        'lbl_cr_content': "  Rapor İçeriği",
        'btn_copy': "📋 Kopyala",
        'btn_open_main': "📖 Editörde aç",
        'btn_close': "✕ Kapat",
        'lbl_prescripteur': "İsteyen Doktor",
        'lbl_radiologue': "Radyolog",
        'lbl_indication': "Endikasyon",
        'stats_patients': "hasta",
        'stats_examens': "tetkik",
        'stats_today': "bugün",
        'confirm_del_patient': "Kayıt ve tüm tetkikler silinsin mi?\nBu işlem geri alınamaz.",
        'confirm_del_patient_title': "Onayla",
        'confirm_del_exam': "Bu tetkik ve tüm raporlar silinsin mi?",
        'confirm_del_exam_title': "Onayla",
        'no_exam_selected': "Listeden bir tetkik seçin.",
        'no_exam_title': "Seçim gerekli",
        'use_cr_ok_title': "Rapora Yüklendi",
        'statut_en_cours': "Devam ediyor",
        'statut_finalise': "Tamamlandı",
        'statut_archive': "Arşivlendi",
        'sexe_M':     "E",
        'sexe_F':     "K",
        'sexe_Autre': "Diğer",
        'modalites': ["MRI", "BT", "Ultrason", "Konvansiyonel Radyografi",
                      "Girişimsel Radyoloji", "Meme Görüntüleme", "Danışmanlık", "Diğer"],
    },
    '日本語': {
        'pacs_title': "🏥 PACS/RIS — 放射線科記録",
        'search_hint': "🔍 患者を検索…",
        'btn_new_pat': "➕ 患者追加",
        'col_dos': "ID番号",
        'col_nom': "姓",
        'col_prenom': "名",
        'col_ddn': "生年月日",
        'col_pays': "国",
        'col_exam': "検査",
        'lbl_select_pat': "患者を選択してください",
        'btn_edit': "✏ 編集",
        'btn_del': "🗑 削除",
        'btn_new_exam': "📋 新規検査",
        'lbl_hist': "  📁 検査履歴 (ダブルクリックで読影レポートを表示)",
        'col_acc': "受付番号",
        'col_date': "日付",
        'col_mod': "モダリティ",
        'col_type': "タイプ",
        'col_presc': "依頼医",
        'col_rad': "読影医",
        'col_statut': "ステータス",
        'lbl_cr_edit': "  📝 レポート (簡易編集)",
        'btn_use_cr': "📤 レポートで使用",
        'btn_save_cr': "💾 レポート保存",
        'btn_copy_cr': "📋 コピー",
        'btn_view_crs': "📄 全レポート表示",
        'btn_edit_exam': "✏ 検査編集",
        'btn_del_exam': "🗑 検査削除",
        'dlg_edit_pat': "✏️ 患者情報を編集",
        'dlg_new_pat': "👤 新規患者登録",
        'hdr_edit_pat': "  ✏️ カルテ編集",
        'hdr_new_pat': "  👤 新規カルテ作成",
        'lbl_nom_req': "姓 *",
        'lbl_prenom_req': "名 *",
        'lbl_ddn': "生年月日",
        'lbl_sexe': "性別",
        'lbl_cin': "身分証/パスポート",
        'lbl_tel': "電話番号",
        'lbl_adresse': "住所",
        'lbl_rem': "備考",
        'btn_cancel': "✕ キャンセル",
        'btn_save_mod': "✔ 変更を保存",
        'btn_save': "✔ 保存",
        'dlg_edit_exam': "✏ 検査編集",
        'dlg_new_exam': "📋 新規検査",
        'lbl_date_req': "検査日 *",
        'lbl_mod_req': "モダリティ *",
        'lbl_ind': "適応/臨床症状",
        'lbl_etab': "施設",
        'lbl_lang': "レポート言語",
        'btn_create_exam': "✔ 検査作成",
        'dlg_crs': "📄 レポート — ",
        'lbl_info_none': "   追加情報なし",
        'lbl_versions': "  バージョン",
        'col_version': "版",
        'lbl_cr_content': "  レポート内容",
        'btn_copy': "📋 コピー",
        'btn_open_main': "📖 エディタで開く",
        'btn_close': "✕ 閉じる",
        'lbl_prescripteur': "依頼医",
        'lbl_radiologue': "読影医",
        'lbl_indication': "適応/臨床症状",
        'stats_patients': "患者",
        'stats_examens': "検査",
        'stats_today': "本日",
        'confirm_del_patient': "このカルテと全検査を削除しますか？\nこの操作は元に戻せません。",
        'confirm_del_patient_title': "確認",
        'confirm_del_exam': "この検査と全レポートを削除しますか？",
        'confirm_del_exam_title': "確認",
        'no_exam_selected': "リストから検査を選択してください。",
        'no_exam_title': "未選択",
        'use_cr_ok_title': "レポートに読み込みました",
        'statut_en_cours': "進行中",
        'statut_finalise': "完了",
        'statut_archive': "アーカイブ済",
        'sexe_M':     "男",
        'sexe_F':     "女",
        'sexe_Autre': "その他",
        'modalites': ["MRI", "CTスキャン", "超音波", "一般撮影",
                      "インターベンショナルラジオロジー", "乳腺イメージング", "診察", "その他"],
    },
    '한국어': {
        'pacs_title': "🏥 PACS/RIS — 방사선과 기록",
        'search_hint': "🔍 환자 검색…",
        'btn_new_pat': "➕ 환자",
        'col_dos': "차트 번호",
        'col_nom': "성",
        'col_prenom': "이름",
        'col_ddn': "생년월일",
        'col_pays': "국가",
        'col_exam': "검사",
        'lbl_select_pat': "환자를 선택하세요",
        'btn_edit': "✏ 수정",
        'btn_del': "🗑 삭제",
        'btn_new_exam': "📋 새 검사",
        'lbl_hist': "  📁 검사 이력 (더블 클릭 = 판독서 보기)",
        'col_acc': "접수 번호",
        'col_date': "날짜",
        'col_mod': "양식",
        'col_type': "유형",
        'col_presc': "의뢰의",
        'col_rad': "판독의",
        'col_statut': "상태",
        'lbl_cr_edit': "  📝 판독서 (빠른 편집)",
        'btn_use_cr': "📤 판독서에 사용",
        'btn_save_cr': "💾 판독서 저장",
        'btn_copy_cr': "📋 판독서 복사",
        'btn_view_crs': "📄 모든 판독서 보기",
        'btn_edit_exam': "✏ 검사 편집",
        'btn_del_exam': "🗑 검사 삭제",
        'dlg_edit_pat': "✏️ 환자 수정",
        'dlg_new_pat': "👤 새 환자",
        'hdr_edit_pat': "  ✏️ 환자 기록 수정",
        'hdr_new_pat': "  👤 새 환자 기록",
        'lbl_nom_req': "성 *",
        'lbl_prenom_req': "이름 *",
        'lbl_ddn': "생년월일",
        'lbl_sexe': "성별",
        'lbl_cin': "주민번호/여권",
        'lbl_tel': "전화번호",
        'lbl_adresse': "주소",
        'lbl_rem': "메모",
        'btn_cancel': "✕ 취소",
        'btn_save_mod': "✔ 변경사항 저장",
        'btn_save': "✔ 저장",
        'dlg_edit_exam': "✏ 검사 편집",
        'dlg_new_exam': "📋 새 검사",
        'lbl_date_req': "검사 날짜 *",
        'lbl_mod_req': "양식 *",
        'lbl_ind': "적응증",
        'lbl_etab': "기관",
        'lbl_lang': "판독 언어",
        'btn_create_exam': "✔ 검사 생성",
        'dlg_crs': "📄 판독서 — ",
        'lbl_info_none': "   추가 정보 없음",
        'lbl_versions': "  버전",
        'col_version': "버전",
        'lbl_cr_content': "  판독 내용",
        'btn_copy': "📋 복사",
        'btn_open_main': "📖 에디터에서 열기",
        'btn_close': "✕ 닫기",
        'lbl_prescripteur': "의뢰의",
        'lbl_radiologue': "판독의",
        'lbl_indication': "적응증",
        'stats_patients': "환자",
        'stats_examens': "검사",
        'stats_today': "오늘",
        'confirm_del_patient': "기록과 모든 검사를 삭제하시겠습니까?\n이 작업은 되돌릴 수 없습니다.",
        'confirm_del_patient_title': "확인",
        'confirm_del_exam': "이 검사와 모든 판독서를 삭제하시겠습니까?",
        'confirm_del_exam_title': "확인",
        'no_exam_selected': "목록에서 검사를 선택하세요.",
        'no_exam_title': "선택 필요",
        'use_cr_ok_title': "판독서에 로드됨",
        'statut_en_cours': "진행 중",
        'statut_finalise': "완료",
        'statut_archive': "보관됨",
        'sexe_M':     "남",
        'sexe_F':     "여",
        'sexe_Autre': "기타",
        'modalites': ["MRI", "CT 스캔", "초음파", "일반 방사선",
                      "중재 방사선학", "유방 영상", "상담", "기타"],
    },
    'हिन्दी': {
        'pacs_title': "🏥 PACS/RIS — रेडियोलॉजिकल रिकॉर्ड्स",
        'search_hint': "🔍 मरीज़ खोजें…",
        'btn_new_pat': "➕ नया मरीज़",
        'col_dos': "फ़ाइल नंबर",
        'col_nom': "सरनेम",
        'col_prenom': "नाम",
        'col_ddn': "जन्म तिथि",
        'col_pays': "देश",
        'col_exam': "जाँच",
        'lbl_select_pat': "मरीज़ चुनें",
        'btn_edit': "✏ संपादित करें",
        'btn_del': "🗑 हटाएँ",
        'btn_new_exam': "📋 नई जाँच",
        'lbl_hist': "  📁 जाँच इतिहास (डबल-क्लिक = रिपोर्ट)",
        'col_acc': "एक्सेशन नं.",
        'col_date': "तारीख",
        'col_mod': "मोडलिटी",
        'col_type': "प्रकार",
        'col_presc': "रेफरिंग डॉक्टर",
        'col_rad': "रेडियोलॉजिस्ट",
        'col_statut': "स्थिति",
        'lbl_cr_edit': "  📝 रिपोर्ट (त्वरित संपादन)",
        'btn_use_cr': "📤 रिपोर्ट में उपयोग करें",
        'btn_save_cr': "💾 रिपोर्ट सहेजें",
        'btn_copy_cr': "📋 रिपोर्ट कॉपी करें",
        'btn_view_crs': "📄 सभी रिपोर्ट देखें",
        'btn_edit_exam': "✏ जाँच संपादित करें",
        'btn_del_exam': "🗑 जाँच हटाएँ",
        'dlg_edit_pat': "✏️ मरीज़ बदलें",
        'dlg_new_pat': "👤 नया मरीज़",
        'hdr_edit_pat': "  ✏️ मरीज़ फ़ाइल संपादित करें",
        'hdr_new_pat': "  👤 नई मरीज़ फ़ाइल",
        'lbl_nom_req': "सरनेम *",
        'lbl_prenom_req': "नाम *",
        'lbl_ddn': "जन्म की तारीख",
        'lbl_sexe': "लिंग",
        'lbl_cin': "आईडी / पासपोर्ट",
        'lbl_tel': "फ़ोन",
        'lbl_adresse': "पता",
        'lbl_rem': "नोट्स",
        'btn_cancel': "✕ रद्द करें",
        'btn_save_mod': "✔ बदलाव सहेजें",
        'btn_save': "✔ सहेजें",
        'dlg_edit_exam': "✏ जाँच संपादित करें",
        'dlg_new_exam': "📋 नई जाँच",
        'lbl_date_req': "जाँच तिथि *",
        'lbl_mod_req': "मोडलिटी *",
        'lbl_ind': "संकेत (Indication)",
        'lbl_etab': "संस्थान",
        'lbl_lang': "रिपोर्ट भाषा",
        'btn_create_exam': "✔ जाँच बनाएँ",
        'dlg_crs': "📄 रिपोर्ट — ",
        'lbl_info_none': "   कोई अतिरिक्त जानकारी नहीं",
        'lbl_versions': "  संस्करण (Versions)",
        'col_version': "संस्करण",
        'lbl_cr_content': "  रिपोर्ट की सामग्री",
        'btn_copy': "📋 कॉपी",
        'btn_open_main': "📖 एडिटर में खोलें",
        'btn_close': "✕ बंद करें",
        'lbl_prescripteur': "रेफरिंग डॉक्टर",
        'lbl_radiologue': "रेडियोलॉजिस्ट",
        'lbl_indication': "संकेत",
        'stats_patients': "मरीज़",
        'stats_examens': "जाँच",
        'stats_today': "आज",
        'confirm_del_patient': "इस फ़ाइल और सभी जाँचों को हटाएँ?\nयह क्रिया वापस नहीं की जा सकती।",
        'confirm_del_patient_title': "पुष्टि करें",
        'confirm_del_exam': "इस जाँच और सभी रिपोर्टों को हटाएँ?",
        'confirm_del_exam_title': "पुष्टि करें",
        'no_exam_selected': "सूची से एक जाँच चुनें।",
        'no_exam_title': "कोई चयन नहीं",
        'use_cr_ok_title': "रिपोर्ट में लोड किया गया",
        'statut_en_cours': "जारी है",
        'statut_finalise': "पूर्ण",
        'statut_archive': "संग्रहीत",
        'sexe_M':     "पुरुष",
        'sexe_F':     "महिला",
        'sexe_Autre': "अन्य",
        'modalites': ["MRI", "CT स्कैन", "अल्ट्रासाउंड", "पारंपरिक रेडियोग्राफी",
                      "इंटरवेंशनल रेडियोलॉजी", "स्तन इमेजिंग", "परामर्श", "अन्य"],
    },
    'Svenska': {
        'pacs_title': "🏥 PACS/RIS — Radiologiska journaler",
        'search_hint': "🔍 Sök patient…",
        'btn_new_pat': "➕ Patient",
        'col_dos': "Journalnr",
        'col_nom': "Efternamn",
        'col_prenom': "Förnamn",
        'col_ddn': "Födelsedatum",
        'col_pays': "Land",
        'col_exam': "Undersökningar",
        'lbl_select_pat': "Välj en patient",
        'btn_edit': "✏ Redigera",
        'btn_del': "🗑 Ta bort",
        'btn_new_exam': "📋 Ny undersökning",
        'lbl_hist': "  📁 Historik (dubbelklicka = visa svar)",
        'col_acc': "Accessionsnr",
        'col_date': "Datum",
        'col_mod': "Modalitet",
        'col_type': "Typ",
        'col_presc': "Remitterande",
        'col_rad': "Radiolog",
        'col_statut': "Status",
        'lbl_cr_edit': "  📝 Svar (snabbredigering)",
        'btn_use_cr': "📤 Använd i svar",
        'btn_save_cr': "💾 Spara svar",
        'btn_copy_cr': "📋 Kopiera svar",
        'btn_view_crs': "📄 Visa alla svar",
        'btn_edit_exam': "✏ Redigera undersökning",
        'btn_del_exam': "🗑 Ta bort undersökning",
        'dlg_edit_pat': "✏️ Redigera patient",
        'dlg_new_pat': "👤 Ny patient",
        'hdr_edit_pat': "  ✏️ Redigera patientjournal",
        'hdr_new_pat': "  👤 Ny patientjournal",
        'lbl_nom_req': "Efternamn *",
        'lbl_prenom_req': "Förnamn *",
        'lbl_ddn': "Födelsedatum",
        'lbl_sexe': "Kön",
        'lbl_cin': "ID / Pass",
        'lbl_tel': "Telefon",
        'lbl_adresse': "Adress",
        'lbl_rem': "Anmärkningar",
        'btn_cancel': "✕ Avbryt",
        'btn_save_mod': "✔ Spara ändringar",
        'btn_save': "✔ Spara",
        'dlg_edit_exam': "✏ Redigera undersökning",
        'dlg_new_exam': "📋 Ny undersökning",
        'lbl_date_req': "Undersökningsdatum *",
        'lbl_mod_req': "Modalitet *",
        'lbl_ind': "Indikation",
        'lbl_etab': "Inrättning",
        'lbl_lang': "Svarsspråk",
        'btn_create_exam': "✔ Skapa undersökning",
        'dlg_crs': "📄 Svar — ",
        'lbl_info_none': "   Ingen ytterligare info",
        'lbl_versions': "  Versioner",
        'col_version': "Version",
        'lbl_cr_content': "  Svarsinnehåll",
        'btn_copy': "📋 Kopiera",
        'btn_open_main': "📖 Öppna i redigerare",
        'btn_close': "✕ Stäng",
        'lbl_prescripteur': "Remitterande",
        'lbl_radiologue': "Radiolog",
        'lbl_indication': "Indikation",
        'stats_patients': "patienter",
        'stats_examens': "undersökningar",
        'stats_today': "idag",
        'confirm_del_patient': "Ta bort journalen och alla undersökningar?\nDenna åtgärd kan inte ångras.",
        'confirm_del_patient_title': "Bekräfta",
        'confirm_del_exam': "Ta bort denna undersökning och alla svar?",
        'confirm_del_exam_title': "Bekräfta",
        'no_exam_selected': "Välj en undersökning från listan.",
        'no_exam_title': "Inget val",
        'use_cr_ok_title': "Laddat i svar",
        'statut_en_cours': "Pågår",
        'statut_finalise': "Slutförd",
        'statut_archive': "Arkiverad",
        'sexe_M':     "M",
        'sexe_F':     "K",
        'sexe_Autre': "Annat",
        'modalites': ["MRI", "DT", "Ultraljud", "Konventionell röntgen",
                      "Interventionell radiologi", "Bröstdiagnostik", "Konsultationer", "Annat"],
    },
    'Norsk': {
        'pacs_title': "🏥 PACS/RIS — Radiologiske journaler",
        'search_hint': "🔍 Søk etter pasient…",
        'btn_new_pat': "➕ Pasient",
        'col_dos': "Journalnr",
        'col_nom': "Etternavn",
        'col_prenom': "Fornavn",
        'col_ddn': "Fødselsdato",
        'col_pays': "Land",
        'col_exam': "Undersøkelser",
        'lbl_select_pat': "Velg pasient",
        'btn_edit': "✏ Rediger",
        'btn_del': "🗑 Slett",
        'btn_new_exam': "📋 Ny undersøkelse",
        'lbl_hist': "  📁 Historikk (dobbeltklikk = se svar)",
        'col_acc': "Aksessjonsnr",
        'col_date': "Dato",
        'col_mod': "Modalitet",
        'col_type': "Type",
        'col_presc': "Henviser",
        'col_rad': "Radiolog",
        'col_statut': "Status",
        'lbl_cr_edit': "  📝 Svar (hurtigredigering)",
        'btn_use_cr': "📤 Bruk i svar",
        'btn_save_cr': "💾 Lagre svar",
        'btn_copy_cr': "📋 Kopier svar",
        'btn_view_crs': "📄 Se alle svar",
        'btn_edit_exam': "✏ Rediger undersøkelse",
        'btn_del_exam': "🗑 Slett undersøkelse",
        'dlg_edit_pat': "✏️ Rediger pasient",
        'dlg_new_pat': "👤 Ny pasient",
        'hdr_edit_pat': "  ✏️ Rediger pasientjournal",
        'hdr_new_pat': "  👤 Ny pasientjournal",
        'lbl_nom_req': "Etternavn *",
        'lbl_prenom_req': "Fornavn *",
        'lbl_ddn': "Fødselsdato",
        'lbl_sexe': "Kjønn",
        'lbl_cin': "ID / Pass",
        'lbl_tel': "Telefon",
        'lbl_adresse': "Adresse",
        'lbl_rem': "Merknader",
        'btn_cancel': "✕ Avbryt",
        'btn_save_mod': "✔ Lagre endringer",
        'btn_save': "✔ Lagre",
        'dlg_edit_exam': "✏ Rediger undersøkelse",
        'dlg_new_exam': "📋 Ny undersøkelse",
        'lbl_date_req': "Undersøkelsesdato *",
        'lbl_mod_req': "Modalitet *",
        'lbl_ind': "Indikasjon",
        'lbl_etab': "Institusjon",
        'lbl_lang': "Svarsspråk",
        'btn_create_exam': "✔ Opprett undersøkelse",
        'dlg_crs': "📄 Svar — ",
        'lbl_info_none': "   Ingen tilleggsinfo",
        'lbl_versions': "  Versjoner",
        'col_version': "Versjon",
        'lbl_cr_content': "  Svarsinnhold",
        'btn_copy': "📋 Kopier",
        'btn_open_main': "📖 Åpne i redigerer",
        'btn_close': "✕ Lukk",
        'lbl_prescripteur': "Henviser",
        'lbl_radiologue': "Radiolog",
        'lbl_indication': "Indikasjon",
        'stats_patients': "pasienter",
        'stats_examens': "undersøkelser",
        'stats_today': "i dag",
        'confirm_del_patient': "Slette journalen og alle undersøkelsene?\nDenne handlingen kan ikke angres.",
        'confirm_del_patient_title': "Bekreft",
        'confirm_del_exam': "Slette denne undersøkelsen og alle svarene?",
        'confirm_del_exam_title': "Bekreft",
        'no_exam_selected': "Velg en undersøkelse fra listen.",
        'no_exam_title': "Ingen valgt",
        'use_cr_ok_title': "Lastet inn i svar",
        'statut_en_cours': "Pågår",
        'statut_finalise': "Fullført",
        'statut_archive': "Arkivert",
        'sexe_M':     "M",
        'sexe_F':     "K",
        'sexe_Autre': "Annet",
        'modalites': ["MRI", "CT", "Ultralyd", "Konvensjonell røntgen",
                      "Intervensjonell radiologi", "Brystdiagnostikk", "Konsultasjoner", "Annet"],
    },
    'Dansk': {
        'pacs_title': "🏥 PACS/RIS — Radiologiske journaler",
        'search_hint': "🔍 Søg patient…",
        'btn_new_pat': "➕ Patient",
        'col_dos': "Journalnr",
        'col_nom': "Efternavn",
        'col_prenom': "Fornavn",
        'col_ddn': "Fødselsdato",
        'col_pays': "Land",
        'col_exam': "Undersøgelser",
        'lbl_select_pat': "Vælg patient",
        'btn_edit': "✏ Rediger",
        'btn_del': "🗑 Slet",
        'btn_new_exam': "📋 Ny undersøgelse",
        'lbl_hist': "  📁 Historik (dobbeltklik = se svar)",
        'col_acc': "Accessionsnr",
        'col_date': "Dato",
        'col_mod': "Modalitet",
        'col_type': "Type",
        'col_presc': "Henviser",
        'col_rad': "Radiolog",
        'col_statut': "Status",
        'lbl_cr_edit': "  📝 Svar (hurtigredigering)",
        'btn_use_cr': "📤 Brug i svar",
        'btn_save_cr': "💾 Gem svar",
        'btn_copy_cr': "📋 Kopier svar",
        'btn_view_crs': "📄 Se alle svar",
        'btn_edit_exam': "✏ Rediger undersøgelse",
        'btn_del_exam': "🗑 Slet undersøgelse",
        'dlg_edit_pat': "✏️ Rediger patient",
        'dlg_new_pat': "👤 Ny patient",
        'hdr_edit_pat': "  ✏️ Rediger patientjournal",
        'hdr_new_pat': "  👤 Ny patientjournal",
        'lbl_nom_req': "Efternavn *",
        'lbl_prenom_req': "Fornavn *",
        'lbl_ddn': "Fødselsdato",
        'lbl_sexe': "Køn",
        'lbl_cin': "ID / Pas",
        'lbl_tel': "Telefon",
        'lbl_adresse': "Adresse",
        'lbl_rem': "Notater",
        'btn_cancel': "✕ Annuller",
        'btn_save_mod': "✔ Gem ændringer",
        'btn_save': "✔ Gem",
        'dlg_edit_exam': "✏ Rediger undersøgelse",
        'dlg_new_exam': "📋 Ny undersøgelse",
        'lbl_date_req': "Undersøgelsesdato *",
        'lbl_mod_req': "Modalitet *",
        'lbl_ind': "Indikation",
        'lbl_etab': "Afdeling",
        'lbl_lang': "Svarsprog",
        'btn_create_exam': "✔ Opret undersøgelse",
        'dlg_crs': "📄 Svar — ",
        'lbl_info_none': "   Ingen yderligere info",
        'lbl_versions': "  Versioner",
        'col_version': "Version",
        'lbl_cr_content': "  Svarsindhold",
        'btn_copy': "📋 Kopier",
        'btn_open_main': "📖 Åbn i redigeringsværktøj",
        'btn_close': "✕ Luk",
        'lbl_prescripteur': "Henviser",
        'lbl_radiologue': "Radiolog",
        'lbl_indication': "Indikation",
        'stats_patients': "patienter",
        'stats_examens': "undersøgelser",
        'stats_today': "i dag",
        'confirm_del_patient': "Slette journalen og alle undersøgelserne?\nDenne handling kan ikke fortrydes.",
        'confirm_del_patient_title': "Bekræft",
        'confirm_del_exam': "Slette denne undersøgelse og alle svarene?",
        'confirm_del_exam_title': "Bekræft",
        'no_exam_selected': "Vælg en undersøgelse fra listen.",
        'no_exam_title': "Intet valgt",
        'use_cr_ok_title': "Indlæst i svar",
        'statut_en_cours': "I gang",
        'statut_finalise': "Afsluttet",
        'statut_archive': "Arkiveret",
        'sexe_M':     "M",
        'sexe_F':     "K",
        'sexe_Autre': "Andet",
        'modalites': ["MRI", "CT", "Ultralyd", "Konventionel røntgen",
                      "Interventionel radiologi", "Brystdiagnostik", "Konsultationer", "Andet"],
    },
    'Nederlands': {
        'pacs_title': "🏥 PACS/RIS — Radiologische dossiers",
        'search_hint': "🔍 Patiënt zoeken…",
        'btn_new_pat': "➕ Patiënt",
        'col_dos': "Dossiernr",
        'col_nom': "Achternaam",
        'col_prenom': "Voornaam",
        'col_ddn': "Geboortedatum",
        'col_pays': "Land",
        'col_exam': "Onderzoeken",
        'lbl_select_pat': "Selecteer patiënt",
        'btn_edit': "✏ Bewerken",
        'btn_del': "🗑 Verwijderen",
        'btn_new_exam': "📋 Nieuw onderzoek",
        'lbl_hist': "  📁 Historiek (dubbelklik = verslagen)",
        'col_acc': "Accessienr",
        'col_date': "Datum",
        'col_mod': "Modaliteit",
        'col_type': "Type",
        'col_presc': "Aanvrager",
        'col_rad': "Radioloog",
        'col_statut': "Status",
        'lbl_cr_edit': "  📝 Verslag (snel bewerken)",
        'btn_use_cr': "📤 Gebruik in verslag",
        'btn_save_cr': "💾 Verslag opslaan",
        'btn_copy_cr': "📋 Verslag kopiëren",
        'btn_view_crs': "📄 Bekijk verslagen",
        'btn_edit_exam': "✏ Bewerk onderzoek",
        'btn_del_exam': "🗑 Verwijder onderzoek",
        'dlg_edit_pat': "✏️ Patiënt bewerken",
        'dlg_new_pat': "👤 Nieuwe patiënt",
        'hdr_edit_pat': "  ✏️ Patiëntendossier bewerken",
        'hdr_new_pat': "  👤 Nieuw patiëntendossier",
        'lbl_nom_req': "Achternaam *",
        'lbl_prenom_req': "Voornaam *",
        'lbl_ddn': "Geboortedatum",
        'lbl_sexe': "Geslacht",
        'lbl_cin': "ID / Paspoort",
        'lbl_tel': "Telefoon",
        'lbl_adresse': "Adres",
        'lbl_rem': "Opmerkingen",
        'btn_cancel': "✕ Annuleren",
        'btn_save_mod': "✔ Wijzigingen opslaan",
        'btn_save': "✔ Opslaan",
        'dlg_edit_exam': "✏ Bewerk onderzoek",
        'dlg_new_exam': "📋 Nieuw onderzoek",
        'lbl_date_req': "Datum onderzoek *",
        'lbl_mod_req': "Modaliteit *",
        'lbl_ind': "Indicatie",
        'lbl_etab': "Instelling",
        'lbl_lang': "Taal verslag",
        'btn_create_exam': "✔ Onderzoek aanmaken",
        'dlg_crs': "📄 Verslagen — ",
        'lbl_info_none': "   Geen extra info",
        'lbl_versions': "  Versies",
        'col_version': "Versie",
        'lbl_cr_content': "  Inhoud verslag",
        'btn_copy': "📋 Kopiëren",
        'btn_open_main': "📖 Open in editor",
        'btn_close': "✕ Sluiten",
        'lbl_prescripteur': "Aanvrager",
        'lbl_radiologue': "Radioloog",
        'lbl_indication': "Indicatie",
        'stats_patients': "patiënten",
        'stats_examens': "onderzoeken",
        'stats_today': "vandaag",
        'confirm_del_patient': "Dossier en alle onderzoeken verwijderen?\nDeze actie kan niet ongedaan worden gemaakt.",
        'confirm_del_patient_title': "Bevestigen",
        'confirm_del_exam': "Dit onderzoek en alle verslagen verwijderen?",
        'confirm_del_exam_title': "Bevestigen",
        'no_exam_selected': "Selecteer een onderzoek uit de lijst.",
        'no_exam_title': "Geen selectie",
        'use_cr_ok_title': "Geladen in verslag",
        'statut_en_cours': "Bezig",
        'statut_finalise': "Voltooid",
        'statut_archive': "Gearchiveerd",
        'sexe_M':     "M",
        'sexe_F':     "V",
        'sexe_Autre': "Anders",
        'modalites': ["MRI", "CT", "Echografie", "Conventionele radiografie",
                      "Interventionele radiologie", "Borstdiagnostiek", "Consulten", "Anders"],
    },
    'Bahasa Indonesia': {
        'pacs_title': "🏥 PACS/RIS — Rekam Radiologi",
        'search_hint': "🔍 Cari pasien…",
        'btn_new_pat': "➕ Pasien",
        'col_dos': "No. Rekam Medis",
        'col_nom': "Nama Belakang",
        'col_prenom': "Nama Depan",
        'col_ddn': "Tanggal Lahir",
        'col_pays': "Negara",
        'col_exam': "Pemeriksaan",
        'lbl_select_pat': "Pilih pasien",
        'btn_edit': "✏ Edit",
        'btn_del': "🗑 Hapus",
        'btn_new_exam': "📋 Pemeriksaan Baru",
        'lbl_hist': "  📁 Riwayat (klik ganda = lihat laporan)",
        'col_acc': "No. Aksesi",
        'col_date': "Tanggal",
        'col_mod': "Modalitas",
        'col_type': "Tipe",
        'col_presc': "Dokter Pengirim",
        'col_rad': "Radiolog",
        'col_statut': "Status",
        'lbl_cr_edit': "  📝 Laporan (edit cepat)",
        'btn_use_cr': "📤 Gunakan di Laporan",
        'btn_save_cr': "💾 Simpan Laporan",
        'btn_copy_cr': "📋 Salin Laporan",
        'btn_view_crs': "📄 Lihat semua laporan",
        'btn_edit_exam': "✏ Edit pemeriksaan",
        'btn_del_exam': "🗑 Hapus pemeriksaan",
        'dlg_edit_pat': "✏️ Edit Pasien",
        'dlg_new_pat': "👤 Pasien Baru",
        'hdr_edit_pat': "  ✏️ Edit rekam pasien",
        'hdr_new_pat': "  👤 Rekam pasien baru",
        'lbl_nom_req': "Nama Belakang *",
        'lbl_prenom_req': "Nama Depan *",
        'lbl_ddn': "Tanggal Lahir",
        'lbl_sexe': "Jenis Kelamin",
        'lbl_cin': "NIK / Paspor",
        'lbl_tel': "Telepon",
        'lbl_adresse': "Alamat",
        'lbl_rem': "Catatan",
        'btn_cancel': "✕ Batal",
        'btn_save_mod': "✔ Simpan perubahan",
        'btn_save': "✔ Simpan",
        'dlg_edit_exam': "✏ Edit pemeriksaan",
        'dlg_new_exam': "📋 Pemeriksaan Baru",
        'lbl_date_req': "Tgl Pemeriksaan *",
        'lbl_mod_req': "Modalitas *",
        'lbl_ind': "Indikasi",
        'lbl_etab': "Fasilitas",
        'lbl_lang': "Bahasa Laporan",
        'btn_create_exam': "✔ Buat pemeriksaan",
        'dlg_crs': "📄 Laporan — ",
        'lbl_info_none': "   Tidak ada info tambahan",
        'lbl_versions': "  Versi",
        'col_version': "Versi",
        'lbl_cr_content': "  Isi Laporan",
        'btn_copy': "📋 Salin",
        'btn_open_main': "📖 Buka di Editor",
        'btn_close': "✕ Tutup",
        'lbl_prescripteur': "Dokter Pengirim",
        'lbl_radiologue': "Radiolog",
        'lbl_indication': "Indikasi",
        'stats_patients': "pasien",
        'stats_examens': "pemeriksaan",
        'stats_today': "hari ini",
        'confirm_del_patient': "Hapus rekam medis dan semua pemeriksaan?\nTindakan ini tidak dapat dibatalkan.",
        'confirm_del_patient_title': "Konfirmasi",
        'confirm_del_exam': "Hapus pemeriksaan ini dan semua laporannya?",
        'confirm_del_exam_title': "Konfirmasi",
        'no_exam_selected': "Pilih pemeriksaan dari daftar.",
        'no_exam_title': "Tidak ada pilihan",
        'use_cr_ok_title': "Dimuat ke Laporan",
        'statut_en_cours': "Berlangsung",
        'statut_finalise': "Selesai",
        'statut_archive': "Diarsipkan",
        'sexe_M':     "L",
        'sexe_F':     "P",
        'sexe_Autre': "Lainnya",
        'modalites': ["MRI", "CT Scan", "USG", "Radiografi Konvensional",
                      "Radiologi Intervensi", "Pencitraan Payudara", "Konsultasi", "Lainnya"],
    },
    'Polski': {
        'pacs_title': "🏥 PACS/RIS — Dokumentacja Radiologiczna",
        'search_hint': "🔍 Szukaj pacjenta…",
        'btn_new_pat': "➕ Pacjent",
        'col_dos': "Nr Dokumentacji",
        'col_nom': "Nazwisko",
        'col_prenom': "Imię",
        'col_ddn': "Data Urodzenia",
        'col_pays': "Kraj",
        'col_exam': "Badania",
        'lbl_select_pat': "Wybierz pacjenta",
        'btn_edit': "✏ Edytuj",
        'btn_del': "🗑 Usuń",
        'btn_new_exam': "📋 Nowe Badanie",
        'lbl_hist': "  📁 Historia (dwuklik = opis)",
        'col_acc': "Nr Accession",
        'col_date': "Data",
        'col_mod': "Modalność",
        'col_type': "Typ",
        'col_presc': "Kierujący",
        'col_rad': "Radiolog",
        'col_statut': "Status",
        'lbl_cr_edit': "  📝 Opis (szybka edycja)",
        'btn_use_cr': "📤 Użyj w opisie",
        'btn_save_cr': "💾 Zapisz opis",
        'btn_copy_cr': "📋 Kopiuj opis",
        'btn_view_crs': "📄 Zobacz wszystkie opisy",
        'btn_edit_exam': "✏ Edytuj badanie",
        'btn_del_exam': "🗑 Usuń badanie",
        'dlg_edit_pat': "✏️ Edytuj pacjenta",
        'dlg_new_pat': "👤 Nowy pacjent",
        'hdr_edit_pat': "  ✏️ Edytuj kartę pacjenta",
        'hdr_new_pat': "  👤 Nowa karta pacjenta",
        'lbl_nom_req': "Nazwisko *",
        'lbl_prenom_req': "Imię *",
        'lbl_ddn': "Data urodzenia",
        'lbl_sexe': "Płeć",
        'lbl_cin': "PESEL / Paszport",
        'lbl_tel': "Telefon",
        'lbl_adresse': "Adres",
        'lbl_rem': "Uwagi",
        'btn_cancel': "✕ Anuluj",
        'btn_save_mod': "✔ Zapisz zmiany",
        'btn_save': "✔ Zapisz",
        'dlg_edit_exam': "✏ Edytuj badanie",
        'dlg_new_exam': "📋 Nowe badanie",
        'lbl_date_req': "Data badania *",
        'lbl_mod_req': "Modalność *",
        'lbl_ind': "Wskazania",
        'lbl_etab': "Placówka",
        'lbl_lang': "Język opisu",
        'btn_create_exam': "✔ Utwórz badanie",
        'dlg_crs': "📄 Opisy — ",
        'lbl_info_none': "   Brak dodatkowych informacji",
        'lbl_versions': "  Wersje",
        'col_version': "Wersja",
        'lbl_cr_content': "  Treść opisu",
        'btn_copy': "📋 Kopiuj",
        'btn_open_main': "📖 Otwórz w edytorze",
        'btn_close': "✕ Zamknij",
        'lbl_prescripteur': "Kierujący",
        'lbl_radiologue': "Radiolog",
        'lbl_indication': "Wskazania",
        'stats_patients': "pacjentów",
        'stats_examens': "badań",
        'stats_today': "dziś",
        'confirm_del_patient': "Usunąć kartę i wszystkie badania?\nTej operacji nie można cofnąć.",
        'confirm_del_patient_title': "Potwierdź",
        'confirm_del_exam': "Usunąć to badanie i wszystkie opisy?",
        'confirm_del_exam_title': "Potwierdź",
        'no_exam_selected': "Wybierz badanie z listy.",
        'no_exam_title': "Brak wyboru",
        'use_cr_ok_title': "Załadowano do opisu",
        'statut_en_cours': "W toku",
        'statut_finalise': "Zakończone",
        'statut_archive': "Zarchiwizowane",
        'sexe_M':     "M",
        'sexe_F':     "K",
        'sexe_Autre': "Inne",
        'modalites': ["MRI", "TK", "USG", "RTG konwencjonalne",
                      "Radiologia interwencyjna", "Diagnostyka piersi", "Konsultacje", "Inne"],
    },
    'ไทย': {
        'pacs_title': "🏥 PACS/RIS — ประวัติทางรังสีวิทยา",
        'search_hint': "🔍 ค้นหาผู้ป่วย…",
        'btn_new_pat': "➕ ผู้ป่วยใหม่",
        'col_dos': "เลขที่แฟ้ม",
        'col_nom': "นามสกุล",
        'col_prenom': "ชื่อ",
        'col_ddn': "วันเกิด",
        'col_pays': "ประเทศ",
        'col_exam': "การตรวจ",
        'lbl_select_pat': "เลือกผู้ป่วย",
        'btn_edit': "✏ แก้ไข",
        'btn_del': "🗑 ลบ",
        'btn_new_exam': "📋 เพิ่มการตรวจ",
        'lbl_hist': "  📁 ประวัติการตรวจ (ดับเบิลคลิกเพื่อดูผล)",
        'col_acc': "เลขที่รับงาน",
        'col_date': "วันที่",
        'col_mod': "เครื่องมือ",
        'col_type': "ประเภท",
        'col_presc': "แพทย์ผู้ส่ง",
        'col_rad': "รังสีแพทย์",
        'col_statut': "สถานะ",
        'lbl_cr_edit': "  📝 รายงานผล (แก้ไขด่วน)",
        'btn_use_cr': "📤 ใช้ในรายงาน",
        'btn_save_cr': "💾 บันทึกรายงาน",
        'btn_copy_cr': "📋 คัดลอกรายงาน",
        'btn_view_crs': "📄 ดูรายงานทั้งหมด",
        'btn_edit_exam': "✏ แก้ไขการตรวจ",
        'btn_del_exam': "🗑 ลบการตรวจ",
        'dlg_edit_pat': "✏️ แก้ไขข้อมูลผู้ป่วย",
        'dlg_new_pat': "👤 ผู้ป่วยใหม่",
        'hdr_edit_pat': "  ✏️ แก้ไขเวชระเบียน",
        'hdr_new_pat': "  👤 สร้างเวชระเบียนใหม่",
        'lbl_nom_req': "นามสกุล *",
        'lbl_prenom_req': "ชื่อ *",
        'lbl_ddn': "วันเดือนปีเกิด",
        'lbl_sexe': "เพศ",
        'lbl_cin': "เลขบัตรประชาชน",
        'lbl_tel': "โทรศัพท์",
        'lbl_adresse': "ที่อยู่",
        'lbl_rem': "หมายเหตุ",
        'btn_cancel': "✕ ยกเลิก",
        'btn_save_mod': "✔ บันทึกการแก้ไข",
        'btn_save': "✔ บันทึก",
        'dlg_new_exam': "📋 เพิ่มการตรวจ",
        'lbl_date_req': "วันที่ตรวจ *",
        'lbl_mod_req': "เครื่องมือ *",
        'lbl_ind': "ข้อบ่งชี้",
        'lbl_etab': "สถานพยาบาล",
        'lbl_lang': "ภาษาของรายงาน",
        'btn_create_exam': "✔ สร้างการตรวจ",
        'dlg_crs': "📄 รายงาน — ",
        'lbl_info_none': "   ไม่มีข้อมูลเพิ่มเติม",
        'lbl_versions': "  รุ่นของไฟล์",
        'col_version': "เวอร์ชัน",
        'lbl_cr_content': "  เนื้อหาในรายงาน",
        'btn_copy': "📋 คัดลอก",
        'btn_open_main': "📖 เปิดในโปรแกรมแก้ไข",
        'btn_close': "✕ ปิด",
        'lbl_prescripteur': "แพทย์ผู้ส่ง",
        'lbl_radiologue': "รังสีแพทย์",
        'lbl_indication': "ข้อบ่งชี้",
        'stats_patients': "ผู้ป่วย",
        'stats_examens': "การตรวจ",
        'stats_today': "วันนี้",
        'confirm_del_patient': "ลบแฟ้มและการตรวจทั้งหมดหรือไม่?\nการกระทำนี้ไม่สามารถยกเลิกได้",
        'confirm_del_patient_title': "ยืนยัน",
        'confirm_del_exam': "ลบการตรวจนี้และรายงานทั้งหมดหรือไม่?",
        'confirm_del_exam_title': "ยืนยัน",
        'no_exam_selected': "กรุณาเลือกการตรวจจากรายการ",
        'no_exam_title': "ไม่ได้เลือก",
        'use_cr_ok_title': "โหลดลงในรายงานแล้ว",
        'statut_en_cours': "กำลังดำเนินการ",
        'statut_finalise': "เสร็จสิ้น",
        'statut_archive': "เก็บถาวร",
        'sexe_M':     "ชาย",
        'sexe_F':     "หญิง",
        'sexe_Autre': "อื่น",
        'modalites': ["MRI", "CT Scan", "อัลตราซาวด์", "เอกซเรย์ทั่วไป",
                      "รังสีวิทยาหัตถการ", "การถ่ายภาพเต้านม", "การปรึกษา", "อื่นๆ"],
    },
    'Română': {
        'pacs_title': "🏥 PACS/RIS — Dosare Radiologice",
        'search_hint': "🔍 Căutare pacient…",
        'btn_new_pat': "➕ Pacient",
        'col_dos': "Nr. Dosar",
        'col_nom': "Nume",
        'col_prenom': "Prenume",
        'col_ddn': "Data Nașterii",
        'col_pays': "Țară",
        'col_exam': "Examinări",
        'lbl_select_pat': "Selectați un pacient",
        'btn_edit': "✏ Editare",
        'btn_del': "🗑 Ștergere",
        'btn_new_exam': "📋 Examinare Nouă",
        'lbl_hist': "  📁 Istoric (dublu clic = rapoarte)",
        'col_acc': "Nr. Accesiune",
        'col_date': "Dată",
        'col_mod': "Modalitate",
        'col_type': "Tip",
        'col_presc': "Medic Trimitător",
        'col_rad': "Radiolog",
        'col_statut': "Status",
        'lbl_cr_edit': "  📝 Raport (editare rapidă)",
        'btn_use_cr': "📤 Utilizare în raport",
        'btn_save_cr': "💾 Salvare raport",
        'btn_copy_cr': "📋 Copiere raport",
        'btn_view_crs': "📄 Vezi toate rapoartele",
        'btn_edit_exam': "✏ Editare examinare",
        'btn_del_exam': "🗑 Ștergere examinare",
        'dlg_edit_pat': "✏️ Editare Pacient",
        'dlg_new_pat': "👤 Pacient Nou",
        'hdr_edit_pat': "  ✏️ Editare dosar pacient",
        'hdr_new_pat': "  👤 Dosar pacient nou",
        'lbl_nom_req': "Nume *",
        'lbl_prenom_req': "Prenume *",
        'lbl_ddn': "Data nașterii",
        'lbl_sexe': "Sex",
        'lbl_cin': "CNP / Pașaport",
        'lbl_tel': "Telefon",
        'lbl_adresse': "Adresă",
        'lbl_rem': "Observații",
        'btn_cancel': "✕ Anulare",
        'btn_save_mod': "✔ Salvare modificări",
        'btn_save': "✔ Salvare",
        'dlg_new_exam': "📋 Examinare Nouă",
        'lbl_date_req': "Data examinării *",
        'lbl_mod_req': "Modalitate *",
        'lbl_ind': "Indicație",
        'lbl_etab': "Unitate",
        'lbl_lang': "Limbă Raport",
        'btn_create_exam': "✔ Creare examinare",
        'dlg_crs': "📄 Rapoarte — ",
        'lbl_info_none': "   Fără informații suplimentare",
        'lbl_versions': "  Versiuni",
        'col_version': "Versiune",
        'lbl_cr_content': "  Conținut raport",
        'btn_copy': "📋 Copiere",
        'btn_open_main': "📖 Deschide în editor",
        'btn_close': "✕ Închide",
        'lbl_prescripteur': "Medic Trimitător",
        'lbl_radiologue': "Radiolog",
        'lbl_indication': "Indicație",
        'stats_patients': "pacienți",
        'stats_examens': "examinări",
        'stats_today': "azi",
        'confirm_del_patient': "Ștergeți dosarul și toate examinările?\nAceastă acțiune nu poate fi anulată.",
        'confirm_del_patient_title': "Confirmare",
        'confirm_del_exam': "Ștergeți această examinare și toate rapoartele?",
        'confirm_del_exam_title': "Confirmare",
        'no_exam_selected': "Selectați o examinare din listă.",
        'no_exam_title': "Nicio selecție",
        'use_cr_ok_title': "Încărcat în raport",
        'statut_en_cours': "În desfășurare",
        'statut_finalise': "Finalizat",
        'statut_archive': "Arhivat",
        'sexe_M':     "M",
        'sexe_F':     "F",
        'sexe_Autre': "Altul",
        'modalites': ["IRM", "CT", "Ecografie", "Radiografie convențională",
                      "Radiologie intervențională", "Imagistică mamară", "Consultații", "Altele"],
    },
    'Ελληνικά': {
        'pacs_title': "🏥 PACS/RIS — Ακτινολογικός Φάκελος",
        'search_hint': "🔍 Αναζήτηση ασθενή…",
        'btn_new_pat': "➕ Ασθενής",
        'col_dos': "Αρ. Φακέλου",
        'col_nom': "Επώνυμο",
        'col_prenom': "Όνομα",
        'col_ddn': "Ημ. Γέννησης",
        'col_pays': "Χώρα",
        'col_exam': "Εξετάσεις",
        'lbl_select_pat': "Επιλογή ασθενή",
        'btn_edit': "✏ Επεξεργασία",
        'btn_del': "🗑 Διαγραφή",
        'btn_new_exam': "📋 Νέα Εξέταση",
        'lbl_hist': "  📁 Ιστορικό (διπλό κλικ = γνωματεύσεις)",
        'col_acc': "Αρ. Εισαγωγής",
        'col_date': "Ημερομηνία",
        'col_mod': "Μέθοδος",
        'col_type': "Τύπος",
        'col_presc': "Παραπέμπων",
        'col_rad': "Ακτινολόγος",
        'col_statut': "Κατάσταση",
        'lbl_cr_edit': "  📝 Γνωμάτευση (γρήγορη επεξεργασία)",
        'btn_use_cr': "📤 Χρήση στη γνωμάτευση",
        'btn_save_cr': "💾 Αποθήκευση",
        'btn_copy_cr': "📋 Αντιγραφή",
        'btn_view_crs': "📄 Προβολή όλων",
        'btn_edit_exam': "✏ Επεξεργασία εξέτασης",
        'btn_del_exam': "🗑 Διαγραφή εξέτασης",
        'dlg_edit_pat': "✏️ Επεξεργασία Ασθενή",
        'dlg_new_pat': "👤 Νέος Ασθενής",
        'hdr_edit_pat': "  ✏️ Επεξεργασία φακέλου",
        'hdr_new_pat': "  👤 Νέος φάκελος",
        'lbl_nom_req': "Επώνυμο *",
        'lbl_prenom_req': "Όνομα *",
        'lbl_ddn': "Ημερομηνία γέννησης",
        'lbl_sexe': "Φύλο",
        'lbl_cin': "Ταυτότητα / Διαβατήριο",
        'lbl_tel': "Τηλέφωνο",
        'lbl_adresse': "Διεύθυνση",
        'lbl_rem': "Σημειώσεις",
        'btn_cancel': "✕ Ακύρωση",
        'btn_save_mod': "✔ Αποθήκευση αλλαγών",
        'btn_save': "✔ Αποθήκευση",
        'dlg_new_exam': "📋 Νέα Εξέταση",
        'lbl_date_req': "Ημ. Εξέτασης *",
        'lbl_mod_req': "Μέθοδος *",
        'lbl_ind': "Ένδειξη",
        'lbl_etab': "Ίδρυμα",
        'lbl_lang': "Γλώσσα",
        'btn_create_exam': "✔ Δημιουργία",
        'dlg_crs': "📄 Γνωματεύσεις — ",
        'lbl_info_none': "   Καμία πρόσθετη πληροφορία",
        'lbl_versions': "  Εκδόσεις",
        'col_version': "Έκδοση",
        'lbl_cr_content': "  Περιεχόμενο",
        'btn_copy': "📋 Αντιγραφή",
        'btn_open_main': "📖 Άνοιγμα στον επεξεργαστή",
        'btn_close': "✕ Κλείσιμο",
        'lbl_prescripteur': "Παραπέμπων",
        'lbl_radiologue': "Ακτινολόγος",
        'lbl_indication': "Ένδειξη",
        'stats_patients': "ασθενείς",
        'stats_examens': "εξετάσεις",
        'stats_today': "σήμερα",
        'confirm_del_patient': "Διαγραφή φακέλου και όλων των εξετάσεων;\nΗ ενέργεια αυτή δεν μπορεί να αναιρεθεί.",
        'confirm_del_patient_title': "Επιβεβαίωση",
        'confirm_del_exam': "Διαγραφή αυτής της εξέτασης και όλων των γνωματεύσεων;",
        'confirm_del_exam_title': "Επιβεβαίωση",
        'no_exam_selected': "Επιλέξτε μια εξέταση από τη λίστα.",
        'no_exam_title': "Χωρίς επιλογή",
        'use_cr_ok_title': "Φορτώθηκε στη γνωμάτευση",
        'statut_en_cours': "Σε εξέλιξη",
        'statut_finalise': "Ολοκληρωμένο",
        'statut_archive': "Αρχειοθετημένο",
        'sexe_M':     "Α",
        'sexe_F':     "Θ",
        'sexe_Autre': "Άλλο",
        'modalites': ["MRI", "CT", "Υπερηχογράφημα", "Συμβατική Ακτινογραφία",
                      "Επεμβατική Ακτινολογία", "Μαστογραφία", "Συμβουλευτική", "Άλλο"],
    },
}


def translate_modalite(value: str, target_lang: str) -> str:
    if not value:
        return value
    target_list = PACS_TRANSLATIONS.get(target_lang, {}).get('modalites', [])
    if not target_list:
        return value
    for lang_data in PACS_TRANSLATIONS.values():
        lang_list = lang_data.get('modalites', [])
        if not lang_list:
            continue
        for idx, item in enumerate(lang_list):
            if item.strip().lower() == value.strip().lower():
                if idx < len(target_list):
                    return target_list[idx]
                return value
    return value


def translate_statut(value: str, t: dict) -> str:
    statut_map = {
        "En cours":  t.get("statut_en_cours",  value),
        "Finalisé":  t.get("statut_finalise",  value),
        "Archivé":   t.get("statut_archive",   value),
    }
    return statut_map.get(value, value)


def translate_sexe(value: str, t: dict) -> str:
    if not value:
        return ""
    sexe_map = {
        "M":     t.get("sexe_M",     "M"),
        "F":     t.get("sexe_F",     "F"),
        "Autre": t.get("sexe_Autre", value),
    }
    return sexe_map.get(value, value)


# ══════════════════════════════════════════════════════════════════════════════
#  CONSTANTES
# ══════════════════════════════════════════════════════════════════════════════

DB_FOLDER   = Path.home() / ".pisum_data"
DB_PATH     = DB_FOLDER / "pacs_ris.db"

# RGPD Art.5(1)(e) — limitation de la conservation
# Les entrées du journal d'audit sont supprimées après RGPD_AUDIT_RETENTION_DAYS jours.
# Valeur par défaut : 5 ans (1825 jours). Modifiez selon votre DPA/contrat.
RGPD_AUDIT_RETENTION_DAYS = 1825  # 5 years

C_PRIMARY       = wx.Colour(14,  110, 110)
C_PRIMARY_DARK  = wx.Colour(10,   80,  80)
C_PRIMARY_LIGHT = wx.Colour(224, 242, 242)
C_ACCENT        = wx.Colour(200, 151,  58)
C_BG            = wx.Colour(250, 248, 244)
C_CARD          = wx.Colour(255, 255, 255)
C_TEXT          = wx.Colour(13,   17,  23)
C_TEXT2         = wx.Colour(113, 128, 150)
C_BORDER        = wx.Colour(220, 215, 205)
C_RED           = wx.Colour(200,  60,  60)
C_GREEN         = wx.Colour(40,  160,  80)


# ══════════════════════════════════════════════════════════════════════════════
#  SCALING DPI — base 96 DPI, adapté à l'écran courant
# ══════════════════════════════════════════════════════════════════════════════

_DPI_FACTOR: float | None = None

def _S(px: int) -> int:
    """Scale *px* depuis la base 96 DPI vers le DPI réel de l'écran principal.

    Appelé à l'exécution (après wx.App), jamais à l'import.
    Le facteur est mis en cache après le premier appel.
    """
    global _DPI_FACTOR
    if _DPI_FACTOR is None:
        try:
            ppi = wx.Display(0).GetPPI()
            _DPI_FACTOR = max(ppi.x, ppi.y) / 96.0
        except Exception:
            _DPI_FACTOR = 1.0
        _DPI_FACTOR = max(1.0, _DPI_FACTOR)
    return max(1, round(px * _DPI_FACTOR))


# ══════════════════════════════════════════════════════════════════════════════
#  MOTEUR DE CHIFFREMENT — AES-256-GCM + PBKDF2-HMAC-SHA256
# ══════════════════════════════════════════════════════════════════════════════

class PacsEncryptor:
    """
    Chiffrement symétrique AES-256-GCM de tous les champs PII.

    Dérivation de clé :
      • Matériau = hardware_id (UUID machine) + sel persistant (16 octets)
      • KDF      = PBKDF2-HMAC-SHA256, 600 000 itérations (recommandation NIST 2023)
      • Clé      = 32 octets → AES-256

    Format d'un champ chiffré stocké en base :
      b64url( version(1) | sel(16) | nonce(12) | tag(16) | ciphertext )
      ──────────────────────────────────────────────────────────────────
      • version  : 0x01  (réservé pour migrations futures)
      • sel      : 16 octets aléatoires propres à ce champ (diversification)
      • nonce    : 12 octets aléatoires GCM  (IV unique par écriture)
      • tag      : 16 octets GHASH intégrés par AESGCM
      • ct       : données chiffrées (longueur = longueur plaintext)

    Détection : toute valeur commençant par le préfixe ASCII  ENC:  est
    considérée chiffrée.  Les valeurs vides ou NULL restent telles quelles.
    """

    _PREFIX   = "ENC:"
    _VERSION  = b"\x01"
    _KDF_ITER = 600_000
    _SALT_LEN = 16
    _NONCE_LEN = 12

    def __init__(self, db_folder: Path):
        if not _CRYPTO_OK:
            raise RuntimeError("Module 'cryptography' requis pour le chiffrement PACS.")
        self._db_folder  = db_folder
        self._master_key = self._load_or_create_master_key()

    # ── Dérivation matériau hardware ─────────────────────────────────────────

    @staticmethod
    def _get_hardware_material() -> bytes:
        """
        Identifiant machine stable, résistant aux changements de carte réseau,
        VPN, VM et renommages mineurs.
        Utilise un UUID persistant stocké dans ~/.pisum_data/.machine_id
        """
        id_file = Path.home() / ".pisum_data" / ".machine_id"
        id_file.parent.mkdir(parents=True, exist_ok=True)

        if id_file.exists():
            machine_id = id_file.read_text(encoding="utf-8").strip()
        else:
            machine_id = str(uuid.uuid4())
            # Write with restricted permissions from the start (HIPAA §164.312(a)(2)(iv))
            try:
                fd = os.open(str(id_file), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(machine_id)
            except (AttributeError, OSError):
                # Fallback for Windows (no os.O_CREAT mode support)
                id_file.write_text(machine_id, encoding="utf-8")
                try:
                    os.chmod(id_file, 0o600)
                except Exception:
                    pass

        raw = f"{machine_id}|{platform.node()}"
        return hashlib.sha256(raw.encode("utf-8")).digest()

    # ── Clé maître (persistée dans ~/.pisum_data/.enc_master) ────────────────

    def _load_or_create_master_key(self) -> bytes:
        """
        Charge ou génère la clé maître AES-256.

        Le fichier .enc_master contient :
          sel_kdf(16) | sel_verif(16) | tag_verif(32) | key_enc(32)
          ────────────────────────────────────────────────────────
          • sel_kdf    : sel PBKDF2 fixe pour cette installation
          • sel_verif  : sel HMAC-SHA256 de vérification
          • tag_verif  : HMAC-SHA256(hardware_material, key_enc) pour détecter
                         toute modification du fichier ou migration matérielle
          • key_enc    : la clé AES-256 en clair (protégée par les deux éléments
                         ci-dessus ; le fichier est chmod 600)
        """
        key_file = self._db_folder / ".enc_master"
        hw_mat   = self._get_hardware_material()

        if key_file.exists():
            try:
                raw = key_file.read_bytes()
                if len(raw) != 96:
                    raise ValueError("Fichier clé corrompu (taille invalide).")
                sal_kdf   = raw[0:16]
                sal_verif = raw[16:32]
                tag_verif = raw[32:64]
                key_enc   = raw[64:96]

                # Vérification intégrité HMAC
                expected_tag = hmac.new(
                    hw_mat + sal_verif, key_enc, "sha256"
                ).digest()
                if not hmac.compare_digest(tag_verif, expected_tag):
                    raise ValueError(
                        "Clé de chiffrement invalide : matériel changé ou fichier altéré."
                    )
                return key_enc

            except Exception as e:
                logger.critical(f"❌ Impossible de charger la clé de chiffrement : {e}")
                raise

        # Première installation → génération
        sal_kdf   = os.urandom(16)
        sal_verif = os.urandom(16)
        key_enc   = os.urandom(32)           # AES-256 key, full entropy
        tag_verif = hmac.new(
            hw_mat + sal_verif, key_enc, "sha256"
        ).digest()

        payload = sal_kdf + sal_verif + tag_verif + key_enc
        # Write atomically with restricted permissions (HIPAA §164.312(a)(2)(iv))
        tmp_key = key_file.with_suffix(".tmp")
        try:
            fd = os.open(str(tmp_key), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(payload)
        except (AttributeError, OSError):
            tmp_key.write_bytes(payload)
            try:
                os.chmod(tmp_key, 0o600)
            except Exception:
                pass
        tmp_key.replace(key_file)
        logger.info("🔑 Clé de chiffrement PACS générée et persistée.")
        return key_enc

    # ── Dérivation de sous-clé par contexte (diversification) ────────────────

    def _derive_field_key(self, field_salt: bytes) -> bytes:
        """Dérive une sous-clé AES-256 spécifique à ce champ via PBKDF2."""
        kdf = PBKDF2HMAC(
            algorithm=_crypto_hashes.SHA256(),
            length=32,
            salt=field_salt,
            iterations=self._KDF_ITER,
        )
        return kdf.derive(self._master_key)

    # ── Chiffrement / Déchiffrement ───────────────────────────────────────────

    def encrypt(self, plaintext: str) -> str:
        """
        Chiffre une chaîne et retourne  ENC:<base64url>.
        Si plaintext est vide/None, retourne la valeur telle quelle.
        """
        if not plaintext:
            return plaintext or ""
        if plaintext.startswith(self._PREFIX):
            return plaintext  # déjà chiffré

        pt_bytes   = plaintext.encode("utf-8")
        field_salt = os.urandom(self._SALT_LEN)
        nonce      = os.urandom(self._NONCE_LEN)
        key        = self._derive_field_key(field_salt)

        aesgcm     = AESGCM(key)
        ct_tag     = aesgcm.encrypt(nonce, pt_bytes, None)  # tag intégré (16 B fin)

        blob  = self._VERSION + field_salt + nonce + ct_tag
        return self._PREFIX + base64.urlsafe_b64encode(blob).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        """
        Déchiffre une valeur  ENC:<base64url>.
        Si la valeur n'est pas chiffrée, la retourne telle quelle.
        """
        if not ciphertext or not ciphertext.startswith(self._PREFIX):
            return ciphertext or ""
        try:
            blob       = base64.urlsafe_b64decode(ciphertext[len(self._PREFIX):])
            # version(1) | sel(16) | nonce(12) | ct+tag(reste)
            if len(blob) < 1 + self._SALT_LEN + self._NONCE_LEN + 16:
                raise ValueError("Blob chiffré trop court.")
            _ver       = blob[0:1]
            field_salt = blob[1:1 + self._SALT_LEN]
            nonce      = blob[1 + self._SALT_LEN:1 + self._SALT_LEN + self._NONCE_LEN]
            ct_tag     = blob[1 + self._SALT_LEN + self._NONCE_LEN:]

            key    = self._derive_field_key(field_salt)
            aesgcm = AESGCM(key)
            pt     = aesgcm.decrypt(nonce, ct_tag, None)
            return pt.decode("utf-8")
        except Exception as e:
            logger.error(f"❌ Déchiffrement échoué : {e}")
            return ""

    def encrypt_dict(self, d: dict, fields: tuple) -> dict:
        """Chiffre les champs `fields` dans le dict `d`, retourne un nouveau dict."""
        out = dict(d)
        for f in fields:
            if f in out and out[f]:
                out[f] = self.encrypt(str(out[f]))
        return out

    def decrypt_dict(self, d: dict, fields: tuple) -> dict:
        """Déchiffre les champs `fields` dans le dict `d`, retourne un nouveau dict."""
        out = dict(d)
        for f in fields:
            if f in out and out[f]:
                out[f] = self.decrypt(str(out[f]))
        return out


# Champs PII à chiffrer par table
_PAT_ENC_FIELDS  = ("nom", "prenom", "date_naissance", "cin",
                    "telephone", "adresse", "remarques")
_EXAM_ENC_FIELDS = ("indication", "medecin_prescripteur", "medecin", "etablissement")
_CR_ENC_FIELDS   = ("contenu",)

# Singleton encrypteur — initialisé avec PacsRisDB
_pacs_enc: PacsEncryptor | None = None

def get_pacs_enc() -> PacsEncryptor:
    global _pacs_enc
    if _pacs_enc is None:
        DB_FOLDER.mkdir(exist_ok=True)
        _pacs_enc = PacsEncryptor(DB_FOLDER)
    return _pacs_enc


# ══════════════════════════════════════════════════════════════════════════════
#  BASE DE DONNÉES
# ══════════════════════════════════════════════════════════════════════════════

class PacsRisDB:
    """Gestionnaire SQLite local PACS/RIS."""

    def __init__(self, db_path: Path = DB_PATH):
        DB_FOLDER.mkdir(exist_ok=True)
        self.db_path = db_path
        self.enc = get_pacs_enc()          # ← moteur de chiffrement AES-256-GCM
        self._init_database()
        # Restrict DB file permissions after creation (HIPAA §164.312(a)(2)(iv))
        try:
            os.chmod(self.db_path, 0o600)
        except Exception:
            pass  # Windows
        self._migrate_database()
        self._encrypt_existing_plaintext()  # ← chiffre les données legacy au démarrage

    def _connect(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA secure_delete=ON")   # SOC2 CC7.2: overwrite deleted pages
        return conn

    def _init_database(self):
        try:
            conn = self._connect()
            c = conn.cursor()

            c.execute("""
                CREATE TABLE IF NOT EXISTS patients (
                    patient_uuid    TEXT PRIMARY KEY,
                    bio_key         TEXT UNIQUE NOT NULL,
                    nom             TEXT NOT NULL,
                    prenom          TEXT NOT NULL,
                    date_naissance  TEXT,
                    sexe            TEXT CHECK(sexe IN ('M','F','Autre','')),
                    num_dossier     TEXT UNIQUE,
                    cin             TEXT,
                    telephone       TEXT,
                    pays            TEXT DEFAULT '',
                    adresse         TEXT,
                    remarques       TEXT,
                    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            c.execute("""
                CREATE TABLE IF NOT EXISTS examens (
                    examen_uuid         TEXT PRIMARY KEY,
                    patient_uuid        TEXT NOT NULL,
                    num_accession       TEXT UNIQUE,
                    date_examen         TEXT NOT NULL,
                    modalite            TEXT NOT NULL,
                    type_examen         TEXT,
                    formula_name        TEXT DEFAULT '',
                    indication          TEXT,
                    medecin_prescripteur TEXT DEFAULT '',
                    medecin             TEXT,
                    etablissement       TEXT,
                    langue              TEXT,
                    statut              TEXT DEFAULT 'En attente'
                                            CHECK(statut IN ('En attente','En cours','Finalisé','Archivé')),
                    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(patient_uuid) REFERENCES patients(patient_uuid)
                        ON DELETE CASCADE
                )
            """)

            c.execute("""
                CREATE TABLE IF NOT EXISTS compte_rendus (
                    cr_uuid         TEXT PRIMARY KEY,
                    examen_uuid     TEXT NOT NULL,
                    contenu         TEXT NOT NULL,
                    version         INTEGER DEFAULT 1,
                    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(examen_uuid) REFERENCES examens(examen_uuid)
                        ON DELETE CASCADE
                )
            """)

            # ── Table audit log (RGPD/INPDP Art. 32) ─────────────────────────
            c.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    log_id          TEXT PRIMARY KEY,
                    ts              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    utilisateur     TEXT NOT NULL,
                    action          TEXT NOT NULL,
                    patient_uuid    TEXT,
                    examen_uuid     TEXT,
                    details         TEXT
                )
            """)
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_ts  ON audit_log(ts)"
            )
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_pat ON audit_log(patient_uuid)"
            )

            # ── Table mot de passe accès PACS (RGPD Art. 32) ─────────────────
            c.execute("""
                CREATE TABLE IF NOT EXISTS pacs_auth (
                    id              INTEGER PRIMARY KEY CHECK(id=1),
                    pw_hash         TEXT NOT NULL,
                    pw_salt         TEXT NOT NULL,
                    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            for idx_sql in [
                "CREATE INDEX IF NOT EXISTS idx_pat_nom    ON patients(nom, prenom)",
                "CREATE INDEX IF NOT EXISTS idx_pat_ddn    ON patients(date_naissance)",
                "CREATE INDEX IF NOT EXISTS idx_pat_ndos   ON patients(num_dossier)",
                "CREATE INDEX IF NOT EXISTS idx_exam_pat   ON examens(patient_uuid)",
                "CREATE INDEX IF NOT EXISTS idx_exam_date  ON examens(date_examen)",
                "CREATE INDEX IF NOT EXISTS idx_exam_mod   ON examens(modalite)",
                "CREATE INDEX IF NOT EXISTS idx_cr_exam    ON compte_rendus(examen_uuid)",
            ]:
                c.execute(idx_sql)

            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"❌ Erreur init PACS/RIS DB : {e}", exc_info=True)

    def _migrate_database(self):
        try:
            conn = self._connect()
            c = conn.cursor()

            # ── Column additions ──────────────────────────────────────────────
            for table, col, col_def in [
                ("patients", "pays",                 "TEXT DEFAULT ''"),
                ("examens",  "medecin_prescripteur", "TEXT DEFAULT ''"),
                ("examens",  "formula_name",         "TEXT DEFAULT ''"),
            ]:
                try:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}")
                except sqlite3.OperationalError:
                    pass

            # ── Expand statut CHECK to include 'En attente' ───────────────────
            # Check current schema; if 'En attente' absent → recreate table.
            # First, clean up any stranded backup from a previously failed migration.
            bak_exists = c.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='_examens_bak'"
            ).fetchone()
            if bak_exists:
                main_exists = c.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='examens'"
                ).fetchone()
                if main_exists:
                    # Both exist — previous migration succeeded partially; drop the bak
                    c.execute("DROP TABLE _examens_bak")
                    logger.info("Cleaned up stranded _examens_bak table")
                else:
                    # examens missing — restore from backup
                    c.execute("ALTER TABLE _examens_bak RENAME TO examens")
                    logger.info("Restored examens from _examens_bak")

            # ── Repair broken FK: compte_rendus may reference _examens_bak ────
            # SQLite ≥ 3.26 rewrites FK references on RENAME; if a previous
            # migration renamed examens→_examens_bak without legacy_alter_table,
            # compte_rendus.sql now says REFERENCES _examens_bak.  Fix it.
            cr_schema = c.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='compte_rendus'"
            ).fetchone()
            if cr_schema and "_examens_bak" in (cr_schema["sql"] or ""):
                try:
                    logger.warning("Repairing compte_rendus FK (references _examens_bak)")
                    # Recreate compte_rendus with correct FK (data preserved via rename)
                    c.execute("PRAGMA legacy_alter_table=ON")
                    c.execute("ALTER TABLE compte_rendus RENAME TO _cr_bak")
                    c.execute("PRAGMA legacy_alter_table=OFF")
                    c.execute("""
                        CREATE TABLE compte_rendus (
                            cr_uuid     TEXT PRIMARY KEY,
                            examen_uuid TEXT NOT NULL,
                            contenu     TEXT NOT NULL,
                            version     INTEGER DEFAULT 1,
                            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            FOREIGN KEY(examen_uuid) REFERENCES examens(examen_uuid)
                                ON DELETE CASCADE
                        )
                    """)
                    c.execute("INSERT INTO compte_rendus SELECT * FROM _cr_bak")
                    c.execute("DROP TABLE _cr_bak")
                    c.execute("CREATE INDEX IF NOT EXISTS idx_cr_exam ON compte_rendus(examen_uuid)")
                    logger.info("✅ compte_rendus FK repaired successfully")
                except Exception as repair_err:
                    logger.error(f"❌ FK repair failed: {repair_err}", exc_info=True)
                    try:
                        c.execute("ALTER TABLE _cr_bak RENAME TO compte_rendus")
                    except Exception:
                        pass

            schema_row = c.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='examens'"
            ).fetchone()
            if schema_row and "'En attente'" not in (schema_row["sql"] or ""):
                try:
                    # PRAGMA legacy_alter_table=ON prevents SQLite ≥ 3.26 from
                    # rewriting FK references in compte_rendus from "examens" to
                    # "_examens_bak", which would break FK enforcement after the
                    # backup table is dropped.
                    c.execute("PRAGMA legacy_alter_table=ON")
                    c.execute("ALTER TABLE examens RENAME TO _examens_bak")
                    c.execute("PRAGMA legacy_alter_table=OFF")
                    c.execute("""
                        CREATE TABLE examens (
                            examen_uuid          TEXT PRIMARY KEY,
                            patient_uuid         TEXT NOT NULL,
                            num_accession        TEXT UNIQUE,
                            date_examen          TEXT NOT NULL,
                            modalite             TEXT NOT NULL,
                            type_examen          TEXT,
                            formula_name         TEXT DEFAULT '',
                            indication           TEXT,
                            medecin_prescripteur TEXT DEFAULT '',
                            medecin              TEXT,
                            etablissement        TEXT,
                            langue               TEXT,
                            statut               TEXT DEFAULT 'En attente'
                                                     CHECK(statut IN
                                                       ('En attente','En cours','Finalisé','Archivé')),
                            created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            FOREIGN KEY(patient_uuid) REFERENCES patients(patient_uuid)
                                ON DELETE CASCADE
                        )
                    """)
                    c.execute("""
                        INSERT INTO examens
                        SELECT examen_uuid, patient_uuid, num_accession, date_examen,
                               modalite, type_examen, formula_name, indication,
                               medecin_prescripteur, medecin, etablissement, langue,
                               statut, created_at, updated_at
                        FROM _examens_bak
                    """)
                    c.execute("DROP TABLE _examens_bak")
                    for idx in [
                        "CREATE INDEX IF NOT EXISTS idx_exam_pat  ON examens(patient_uuid)",
                        "CREATE INDEX IF NOT EXISTS idx_exam_date ON examens(date_examen)",
                        "CREATE INDEX IF NOT EXISTS idx_exam_mod  ON examens(modalite)",
                    ]:
                        c.execute(idx)
                    logger.info("✅ Migration: 'En attente' ajouté à examens.statut")
                except Exception as me:
                    logger.error(f"❌ Migration statut: {me}", exc_info=True)
                    try:
                        c.execute("ALTER TABLE _examens_bak RENAME TO examens")
                    except Exception:
                        pass

            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"❌ Erreur migration DB : {e}", exc_info=True)

    def _encrypt_existing_plaintext(self):
        """
        Migration one-shot : chiffre tous les enregistrements en clair.
        Ignoré si tous les champs sont déjà chiffrés.
        Exécuté dans une transaction unique pour atomicité.
        """
        enc = self.enc
        try:
            conn = self._connect()
            c    = conn.cursor()

            # ── Patients ──────────────────────────────────────────────────────
            rows = c.execute("SELECT * FROM patients").fetchall()
            updated = 0
            for row in rows:
                r = dict(row)
                needs = any(
                    r.get(f) and not str(r[f]).startswith("ENC:")
                    for f in _PAT_ENC_FIELDS
                )
                if needs:
                    enc_r = enc.encrypt_dict(r, _PAT_ENC_FIELDS)
                    c.execute("""
                        UPDATE patients SET
                            nom=?, prenom=?, date_naissance=?, cin=?,
                            telephone=?, adresse=?, remarques=?
                        WHERE patient_uuid=?
                    """, (enc_r["nom"], enc_r["prenom"], enc_r["date_naissance"],
                          enc_r["cin"], enc_r["telephone"], enc_r["adresse"],
                          enc_r["remarques"], r["patient_uuid"]))
                    updated += 1
            if updated:
                logger.info(f"🔐 Migration chiffrement patients : {updated} enreg.")

            # ── Examens ───────────────────────────────────────────────────────
            rows = c.execute("SELECT * FROM examens").fetchall()
            updated = 0
            for row in rows:
                r = dict(row)
                needs = any(
                    r.get(f) and not str(r[f]).startswith("ENC:")
                    for f in _EXAM_ENC_FIELDS
                )
                if needs:
                    enc_r = enc.encrypt_dict(r, _EXAM_ENC_FIELDS)
                    c.execute("""
                        UPDATE examens SET
                            indication=?, medecin_prescripteur=?,
                            medecin=?, etablissement=?
                        WHERE examen_uuid=?
                    """, (enc_r["indication"], enc_r["medecin_prescripteur"],
                          enc_r["medecin"], enc_r["etablissement"],
                          r["examen_uuid"]))
                    updated += 1
            if updated:
                logger.info(f"🔐 Migration chiffrement examens : {updated} enreg.")

            # ── Comptes rendus ────────────────────────────────────────────────
            rows = c.execute("SELECT * FROM compte_rendus").fetchall()
            updated = 0
            for row in rows:
                r = dict(row)
                if r.get("contenu") and not str(r["contenu"]).startswith("ENC:"):
                    c.execute(
                        "UPDATE compte_rendus SET contenu=? WHERE cr_uuid=?",
                        (enc.encrypt(r["contenu"]), r["cr_uuid"])
                    )
                    updated += 1
            if updated:
                logger.info(f"🔐 Migration chiffrement CR : {updated} enreg.")

            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"❌ _encrypt_existing_plaintext : {e}", exc_info=True)

    @staticmethod
    def _make_bio_key(nom: str, prenom: str, date_naissance: str, sexe: str = "") -> str:
        # NOTE RGPD Art.4(5) — pseudonymisation : le bio_key est un hash SHA-256
        # de données biographiques, utilisé uniquement pour détecter les doublons.
        # Il ne permet pas de retrouver les données source sans les champs d'origine.
        raw = f"{nom.strip().upper()}|{prenom.strip().upper()}|{date_naissance.strip()}|{sexe.strip().upper()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def add_patient(self, nom, prenom, date_naissance="", sexe="",
                    num_dossier=None, cin="", telephone="", pays="",
                    adresse="", remarques="") -> str | None:
        bio_key      = self._make_bio_key(nom, prenom, date_naissance, sexe)
        patient_uuid = str(uuid.uuid4())
        num_dossier  = num_dossier or self._next_num_dossier()
        enc = self.enc
        try:
            conn = self._connect()
            conn.execute("""
                INSERT INTO patients
                    (patient_uuid, bio_key, nom, prenom, date_naissance, sexe,
                     num_dossier, cin, telephone, pays, adresse, remarques)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (patient_uuid, bio_key,
                  enc.encrypt(nom.strip()),
                  enc.encrypt(prenom.strip()),
                  enc.encrypt(date_naissance),
                  sexe,  # sexe non chiffré (utilisé pour bio_key)
                  num_dossier,
                  enc.encrypt(cin),
                  enc.encrypt(telephone),
                  pays,  # pays non chiffré (utilisé pour filtres UI)
                  enc.encrypt(adresse),
                  enc.encrypt(remarques)))
            conn.commit()
            conn.close()
            self.audit("CREATE_PATIENT", patient_uuid=patient_uuid,
                       details=f"Nouveau dossier {num_dossier}")
            return patient_uuid
        except sqlite3.IntegrityError:
            return None
        except Exception as e:
            logger.error(f"❌ Erreur add_patient : {e}", exc_info=True)
            return None

    def get_patient_by_uuid(self, patient_uuid: str) -> dict | None:
        try:
            conn = self._connect()
            row  = conn.execute("SELECT * FROM patients WHERE patient_uuid=?", (patient_uuid,)).fetchone()
            conn.close()
            if not row:
                return None
            result = self.enc.decrypt_dict(dict(row), _PAT_ENC_FIELDS)
            self.audit("VIEW_PATIENT", patient_uuid=patient_uuid)
            return result
        except Exception as e:
            logger.error(f"❌ get_patient_by_uuid : {e}")
            return None

    def get_patient_by_bio_key(self, nom, prenom, date_naissance, sexe="") -> dict | None:
        bio_key = self._make_bio_key(nom, prenom, date_naissance, sexe)
        try:
            conn = self._connect()
            row  = conn.execute("SELECT * FROM patients WHERE bio_key=?", (bio_key,)).fetchone()
            conn.close()
            if not row:
                return None
            return self.enc.decrypt_dict(dict(row), _PAT_ENC_FIELDS)
        except Exception as e:
            logger.error(f"❌ get_patient_by_bio_key : {e}")
            return None

    def search_patients(self, query: str, limit: int = 200) -> list[dict]:
        """
        Recherche patients.
        Les champs PII étant chiffrés, la recherche s'effectue en deux passes :
          1. Filtre SQL sur champs NON chiffrés (num_dossier, pays, sexe)
          2. Chargement de tous les enreg. déchiffrés + filtre Python sur
             nom / prenom / cin / telephone
        """
        q = query.strip().lower()
        if not q:
            return []
        enc = self.enc
        try:
            conn = self._connect()
            # Passe 1 : présélection sur colonnes non chiffrées
            sql_q = f"%{q}%"
            candidate_uuids = set()
            rows_nd = conn.execute(
                "SELECT patient_uuid FROM patients WHERE num_dossier LIKE ? OR pays LIKE ?",
                (sql_q, sql_q)
            ).fetchall()
            for r in rows_nd:
                candidate_uuids.add(r["patient_uuid"])

            # Passe 2 : chargement complet et filtre Python post-déchiffrement
            all_rows = conn.execute(
                "SELECT * FROM patients ORDER BY nom, prenom"
            ).fetchall()
            conn.close()

            results = []
            for row in all_rows:
                d = self.enc.decrypt_dict(dict(row), _PAT_ENC_FIELDS)
                # Chercher dans tous les champs textuels
                haystack = " ".join(str(d.get(f, "")).lower() for f in
                    ("nom", "prenom", "num_dossier", "cin", "telephone", "pays"))
                if q in haystack:
                    results.append(d)
                if len(results) >= limit:
                    break
            return results
        except Exception as e:
            logger.error(f"❌ search_patients : {e}")
            return []

    def update_patient(self, patient_uuid: str, **fields) -> bool:
        allowed = {"nom","prenom","date_naissance","sexe","cin",
                   "telephone","pays","adresse","remarques","num_dossier"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False

        bio_fields = {"nom","prenom","date_naissance","sexe"}
        if bio_fields & set(updates.keys()):
            current = self.get_patient_by_uuid(patient_uuid) or {}
            nom    = updates.get("nom",            current.get("nom", ""))
            prenom = updates.get("prenom",         current.get("prenom", ""))
            ddn    = updates.get("date_naissance", current.get("date_naissance", ""))
            sexe   = updates.get("sexe",           current.get("sexe", ""))
            new_bio_key = self._make_bio_key(nom, prenom, ddn, sexe)
            updates["bio_key"] = new_bio_key

        # Chiffrer les champs PII avant écriture
        enc = self.enc
        _enc_fields_set = set(_PAT_ENC_FIELDS)
        for k in list(updates.keys()):
            if k in _enc_fields_set and updates[k]:
                updates[k] = enc.encrypt(str(updates[k]))

        set_clause = ", ".join(f"{k}=?" for k in updates)
        set_clause += ", updated_at=CURRENT_TIMESTAMP"
        vals = list(updates.values()) + [patient_uuid]
        try:
            conn = self._connect()
            if "bio_key" in updates:
                existing = conn.execute(
                    "SELECT patient_uuid FROM patients WHERE bio_key=? AND patient_uuid!=?",
                    (updates["bio_key"], patient_uuid)
                ).fetchone()
                if existing:
                    conn.close()
                    return False
            conn.execute(f"UPDATE patients SET {set_clause} WHERE patient_uuid=?", vals)
            conn.commit()
            conn.close()
            self.audit("UPDATE_PATIENT", patient_uuid=patient_uuid,
                       details=f"Champs modifiés : {', '.join(k for k in updates if k != 'bio_key')}")
            return True
        except Exception as e:
            logger.error(f"❌ update_patient : {e}", exc_info=True)
            return False

    def delete_patient(self, patient_uuid: str) -> bool:
        try:
            conn = self._connect()
            conn.execute("DELETE FROM patients WHERE patient_uuid=?", (patient_uuid,))
            conn.commit()
            conn.close()
            self.audit("DELETE_PATIENT", patient_uuid=patient_uuid)
            return True
        except Exception as e:
            logger.error(f"❌ delete_patient : {e}")
            return False

    def _next_num_dossier(self) -> str:
        today = datetime.date.today().strftime("%Y%m%d")
        try:
            conn = self._connect()
            row  = conn.execute(
                "SELECT COUNT(*) as c FROM patients WHERE num_dossier LIKE ?",
                (f"PISUM-{today}%",)
            ).fetchone()
            conn.close()
            seq = (row["c"] if row else 0) + 1
        except Exception:
            seq = 1
        return f"PISUM-{today}-{seq}"

    def add_examen(self, patient_uuid: str, modalite: str, type_examen: str = "",
                   formula_name: str = "", date_examen: str = "", indication: str = "",
                   medecin_prescripteur: str = "",
                   medecin: str = "", etablissement: str = "",
                   langue: str = "") -> str | None:
        examen_uuid   = str(uuid.uuid4())
        num_accession = self._next_num_accession()
        date_examen   = date_examen or datetime.date.today().strftime("%d-%m-%Y")
        enc = self.enc
        try:
            conn = self._connect()
            conn.execute("""
                INSERT INTO examens
                    (examen_uuid, patient_uuid, num_accession, date_examen,
                     modalite, type_examen, formula_name, indication,
                     medecin_prescripteur, medecin,
                     etablissement, langue)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (examen_uuid, patient_uuid, num_accession, date_examen,
                  modalite, type_examen, formula_name,
                  enc.encrypt(indication),
                  enc.encrypt(medecin_prescripteur),
                  enc.encrypt(medecin),
                  enc.encrypt(etablissement),
                  langue))
            conn.commit()
            conn.close()
            self.audit("CREATE_EXAMEN", patient_uuid=patient_uuid,
                       examen_uuid=examen_uuid,
                       details=f"{modalite} {type_examen} — {num_accession}")
            return examen_uuid
        except Exception as e:
            logger.error(f"❌ add_examen : {e}", exc_info=True)
            return None

    def get_examens_patient(self, patient_uuid: str) -> list[dict]:
        try:
            conn = self._connect()
            rows = conn.execute("""
                SELECT * FROM examens WHERE patient_uuid=?
                ORDER BY created_at DESC
            """, (patient_uuid,)).fetchall()
            conn.close()
            return [self.enc.decrypt_dict(dict(r), _EXAM_ENC_FIELDS) for r in rows]
        except Exception as e:
            logger.error(f"❌ get_examens_patient : {e}")
            return []

    def get_examen_by_uuid(self, examen_uuid: str) -> dict | None:
        try:
            conn = self._connect()
            row  = conn.execute("SELECT * FROM examens WHERE examen_uuid=?", (examen_uuid,)).fetchone()
            conn.close()
            if not row:
                return None
            return self.enc.decrypt_dict(dict(row), _EXAM_ENC_FIELDS)
        except Exception as e:
            logger.error(f"❌ get_examen : {e}")
            return None

    def update_examen_statut(self, examen_uuid: str, statut: str) -> bool:
        try:
            conn = self._connect()
            conn.execute("UPDATE examens SET statut=?, updated_at=CURRENT_TIMESTAMP WHERE examen_uuid=?",
                         (statut, examen_uuid))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"❌ update_examen_statut : {e}")
            return False

    def update_examen(self, examen_uuid: str, modalite: str, type_examen: str,
                      formula_name: str, date_examen: str, indication: str, medecin_prescripteur: str,
                      medecin: str, etablissement: str, langue: str) -> bool:
        enc = self.enc
        try:
            conn = self._connect()
            conn.execute("""
                UPDATE examens SET
                    modalite=?, type_examen=?, formula_name=?, date_examen=?, indication=?,
                    medecin_prescripteur=?, medecin=?, etablissement=?, langue=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE examen_uuid=?
            """, (modalite, type_examen, formula_name, date_examen,
                  enc.encrypt(indication),
                  enc.encrypt(medecin_prescripteur),
                  enc.encrypt(medecin),
                  enc.encrypt(etablissement),
                  langue,
                  examen_uuid))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"❌ update_examen : {e}")
            return False

    def delete_examen(self, examen_uuid: str) -> bool:
        try:
            conn = self._connect()
            conn.execute("DELETE FROM examens WHERE examen_uuid=?", (examen_uuid,))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"❌ delete_examen : {e}")
            return False

    def _next_num_accession(self) -> str:
        today = datetime.date.today().strftime("%Y%m%d")
        try:
            conn = self._connect()
            row  = conn.execute(
                "SELECT COUNT(*) as c FROM examens WHERE num_accession LIKE ?",
                (f"ACC-{today}%",)
            ).fetchone()
            conn.close()
            seq = (row["c"] if row else 0) + 1
        except Exception:
            seq = 1
        return f"ACC-{today}-{seq}"

    def save_compte_rendu(self, examen_uuid: str, contenu: str) -> str | None:
        cr_uuid = str(uuid.uuid4())
        enc = self.enc
        try:
            conn = self._connect()
            row  = conn.execute(
                "SELECT MAX(version) as v FROM compte_rendus WHERE examen_uuid=?",
                (examen_uuid,)
            ).fetchone()
            version = (row["v"] or 0) + 1
            conn.execute("""
                INSERT INTO compte_rendus (cr_uuid, examen_uuid, contenu, version)
                VALUES (?,?,?,?)
            """, (cr_uuid, examen_uuid, enc.encrypt(contenu), version))
            conn.execute("""
                UPDATE examens SET statut='Finalisé', updated_at=CURRENT_TIMESTAMP
                WHERE examen_uuid=?
            """, (examen_uuid,))
            conn.commit()
            conn.close()
            self.audit("SAVE_CR", examen_uuid=examen_uuid,
                       details=f"Version {version} — {len(contenu)} caractères")
            return cr_uuid
        except Exception as e:
            logger.error(f"❌ save_compte_rendu : {e}", exc_info=True)
            return None

    def get_compte_rendus(self, examen_uuid: str) -> list[dict]:
        try:
            conn = self._connect()
            rows = conn.execute("""
                SELECT * FROM compte_rendus WHERE examen_uuid=?
                ORDER BY version DESC
            """, (examen_uuid,)).fetchall()
            conn.close()
            return [self.enc.decrypt_dict(dict(r), _CR_ENC_FIELDS) for r in rows]
        except Exception as e:
            logger.error(f"❌ get_compte_rendus : {e}")
            return []

    def get_last_compte_rendu(self, examen_uuid: str) -> dict | None:
        crs = self.get_compte_rendus(examen_uuid)
        return crs[0] if crs else None

    def get_stats(self) -> dict:
        try:
            conn = self._connect()
            stats = {
                "total_patients":  conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0],
                "total_examens":   conn.execute("SELECT COUNT(*) FROM examens").fetchone()[0],
                "total_crs":       conn.execute("SELECT COUNT(*) FROM compte_rendus").fetchone()[0],
                "examens_today":   conn.execute(
                    "SELECT COUNT(*) FROM examens WHERE date_examen=?",
                    (datetime.date.today().strftime("%d-%m-%Y"),)
                ).fetchone()[0],
            }
            conn.close()
            return stats
        except Exception as e:
            logger.error(f"❌ get_stats : {e}")
            return {}

    def get_worklist(self, statut_filter: str = None,
                     date_range: str = "today", limit: int = 500) -> list:
        """
        Returns exams joined with patient info — for the worklist view.
        date_range : 'today' | 'week' | 'month' | 'all'
        Order      : En attente → En cours → Finalisé, then created_at DESC.
        """
        import datetime as _dt
        today = _dt.date.today()

        # Build date clause — date_examen stored as DD-MM-YYYY
        date_clause = ""
        date_params: list = []
        if date_range == "today":
            date_clause = "AND e.date_examen = ?"
            date_params = [today.strftime("%d-%m-%Y")]
        elif date_range == "week":
            dates = [(today - _dt.timedelta(days=i)).strftime("%d-%m-%Y")
                     for i in range(7)]
            placeholders = ",".join("?" * len(dates))
            date_clause = f"AND e.date_examen IN ({placeholders})"
            date_params = dates
        elif date_range == "month":
            # Use created_at for month filter (more reliable than text date)
            date_clause = "AND e.created_at >= datetime('now', '-30 days')"

        status_clause = "AND e.statut = ?" if statut_filter else ""
        status_params = [statut_filter] if statut_filter else []

        sql = f"""
            SELECT e.*, p.nom, p.prenom, p.date_naissance, p.sexe, p.num_dossier
              FROM examens e
              JOIN patients p ON e.patient_uuid = p.patient_uuid
             WHERE 1=1 {date_clause} {status_clause}
             ORDER BY
                CASE e.statut
                    WHEN 'En attente' THEN 0
                    WHEN 'En cours'   THEN 1
                    WHEN 'Finalisé'   THEN 2
                    ELSE 3
                END,
                e.created_at DESC
             LIMIT ?
        """
        params = date_params + status_params + [limit]
        try:
            conn = self._connect()
            rows = conn.execute(sql, params).fetchall()
            conn.close()

            results = []
            enc = self.enc
            for row in rows:
                d = dict(row)
                for f in _PAT_ENC_FIELDS:
                    if f in d and d[f]:
                        d[f] = enc.decrypt(str(d[f]))
                for f in _EXAM_ENC_FIELDS:
                    if f in d and d[f]:
                        d[f] = enc.decrypt(str(d[f]))
                results.append(d)
            return results
        except Exception as e:
            logger.error(f"❌ get_worklist : {e}", exc_info=True)
            return []

    def mark_in_progress(self, examen_uuid: str) -> bool:
        """Transition En attente → En cours (no-op if already En cours/Finalisé)."""
        try:
            conn = self._connect()
            conn.execute(
                "UPDATE examens SET statut='En cours', updated_at=CURRENT_TIMESTAMP"
                " WHERE examen_uuid=? AND statut='En attente'",
                (examen_uuid,),
            )
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"❌ mark_in_progress : {e}")
            return False

    # ══════════════════════════════════════════════════════════════════════════
    #  AUDIT LOG (RGPD Art. 32 / INPDP)
    # ══════════════════════════════════════════════════════════════════════════

    def audit(self, action: str, patient_uuid: str = None,
              examen_uuid: str = None, details: str = None):
        """
        Enregistre une entrée dans le journal d'audit en arrière-plan.
        Utilisateur = login OS courant.
        """
        utilisateur = getpass.getuser()
        log_id      = str(uuid.uuid4())
        def _write():
            try:
                conn = self._connect()
                conn.execute("""
                    INSERT INTO audit_log
                        (log_id, utilisateur, action, patient_uuid, examen_uuid, details)
                    VALUES (?,?,?,?,?,?)
                """, (log_id, utilisateur, action, patient_uuid, examen_uuid, details))
                conn.commit()
                conn.close()
            except Exception as e:
                logger.warning(f"[audit] {e}")
        threading.Thread(target=_write, daemon=True).start()

    def get_audit_log(self, patient_uuid: str = None,
                      limit: int = 500) -> list[dict]:
        """Retourne les entrées du journal, optionnellement filtrées par patient."""
        try:
            conn = self._connect()
            if patient_uuid:
                rows = conn.execute("""
                    SELECT * FROM audit_log WHERE patient_uuid=?
                    ORDER BY ts DESC LIMIT ?
                """, (patient_uuid, limit)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT * FROM audit_log
                    ORDER BY ts DESC LIMIT ?
                """, (limit,)).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"❌ get_audit_log : {e}")
            return []

    def purge_audit_log(self, days: int = RGPD_AUDIT_RETENTION_DAYS):
        """Supprime les entrées d'audit vieilles de plus de `days` jours."""
        try:
            conn = self._connect()
            conn.execute(
                "DELETE FROM audit_log WHERE ts < datetime('now', ?)",
                (f"-{days} days",)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"❌ purge_audit_log : {e}")

    # ══════════════════════════════════════════════════════════════════════════
    #  AUTHENTIFICATION (RGPD Art. 32 / INPDP)
    #  PBKDF2-HMAC-SHA256 × 600 000 itérations
    # ══════════════════════════════════════════════════════════════════════════

    _AUTH_ITER = 600_000

    @staticmethod
    def _hash_password(password: str, salt: bytes) -> str:
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, PacsRisDB._AUTH_ITER
        )
        return base64.b64encode(dk).decode("ascii")

    def has_password(self) -> bool:
        try:
            conn = self._connect()
            row  = conn.execute("SELECT id FROM pacs_auth WHERE id=1").fetchone()
            conn.close()
            return row is not None
        except Exception:
            return False

    def set_password(self, password: str) -> bool:
        """Définit ou remplace le mot de passe d'accès au PACS."""
        if not password or len(password) < 6:
            logger.warning("[PACS] set_password: mot de passe trop court (min 6 car.)")
            return False
        salt    = os.urandom(32)
        pw_hash = self._hash_password(password, salt)
        try:
            conn = self._connect()
            conn.execute("""
                INSERT INTO pacs_auth (id, pw_hash, pw_salt, updated_at)
                VALUES (1, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    pw_hash=excluded.pw_hash,
                    pw_salt=excluded.pw_salt,
                    updated_at=excluded.updated_at
            """, (pw_hash, base64.b64encode(salt).decode("ascii")))
            conn.commit()
            conn.close()
            self.audit("SET_PASSWORD", details="Mot de passe PACS modifié")
            return True
        except Exception as e:
            logger.error(f"❌ set_password : {e}")
            return False

    def verify_password(self, password: str) -> bool:
        """Vérifie le mot de passe. Retourne True si correct ou si aucun mot de passe défini."""
        import time
        utilisateur = getpass.getuser()

        # ── Brute-force lockout check ─────────────────────────────────────────
        fail_info = _AUTH_FAILURES.get(utilisateur)
        if fail_info:
            count, first_ts = fail_info
            if count >= _AUTH_MAX_TRIES:
                elapsed = time.time() - first_ts
                if elapsed < _AUTH_LOCKOUT_S:
                    remaining = int(_AUTH_LOCKOUT_S - elapsed)
                    self.audit("LOGIN_LOCKED",
                               details=f"Compte verrouillé — réessayez dans {remaining}s")
                    logger.warning("[PACS] verify_password: locked out for %ds", remaining)
                    return False
                else:
                    # Lockout expired — reset counter
                    del _AUTH_FAILURES[utilisateur]

        try:
            conn = self._connect()
            row  = conn.execute(
                "SELECT pw_hash, pw_salt FROM pacs_auth WHERE id=1"
            ).fetchone()
            conn.close()
            if not row:
                return True  # Pas encore de mot de passe → accès libre
            salt    = base64.b64decode(row["pw_salt"])
            expected = self._hash_password(password, salt)
            ok = hmac.compare_digest(expected, row["pw_hash"])
            action = "LOGIN_OK" if ok else "LOGIN_FAIL"
            self.audit(action, details=f"Tentative connexion PACS ({'succès' if ok else 'échec'})")

            # ── Update failure counter ────────────────────────────────────────
            if ok:
                _AUTH_FAILURES.pop(utilisateur, None)
            else:
                count = (_AUTH_FAILURES.get(utilisateur, (0, time.time()))[0]) + 1
                _AUTH_FAILURES[utilisateur] = (count, _AUTH_FAILURES.get(utilisateur, (0, time.time()))[1])

            return ok
        except Exception as e:
            logger.error(f"❌ verify_password : {e}")
            return False

    # ══════════════════════════════════════════════════════════════════════════
    #  EXPORT DONNÉES PATIENT (RGPD Art. 20 — Portabilité)
    # ══════════════════════════════════════════════════════════════════════════

    def export_patient_data(self, patient_uuid: str) -> dict | None:
        """
        Exporte toutes les données d'un patient en un dict structuré
        (données déchiffrées), conforme au droit de portabilité RGPD.
        """
        patient = self.get_patient_by_uuid(patient_uuid)
        if not patient:
            return None
        examens = self.get_examens_patient(patient_uuid)
        export  = {
            "export_date":      datetime.datetime.now().isoformat(),
            "export_version":   "1.0",
            "patient":          patient,
            "examens":          [],
        }
        for ex in examens:
            crs = self.get_compte_rendus(ex["examen_uuid"])
            export["examens"].append({
                "examen":         ex,
                "comptes_rendus": crs,
            })
        self.audit("EXPORT_PATIENT", patient_uuid=patient_uuid,
                   details="Export RGPD données patient")
        return export

    def export_patient_json(self, patient_uuid: str, dest_path: str) -> bool:
        """Exporte le dossier complet d'un patient en JSON (UTF-8)."""
        data = self.export_patient_data(patient_uuid)
        if not data:
            return False
        try:
            with open(dest_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2,
                          default=str)
            return True
        except Exception as e:
            logger.error(f"❌ export_patient_json : {e}")
            return False

    def export_patient_txt(self, patient_uuid: str, dest_path: str) -> bool:
        """Exporte le dossier complet d'un patient en texte lisible (.txt)."""
        data = self.export_patient_data(patient_uuid)
        if not data:
            return False
        try:
            p   = data["patient"]
            sep = "═" * 60
            lines = [
                sep,
                "  PISUM — EXPORT DOSSIER PATIENT",
                f"  Date d'export : {data['export_date'][:19]}",
                sep,
                "",
                "IDENTITÉ",
                f"  Nom            : {p.get('nom','')}",
                f"  Prénom         : {p.get('prenom','')}",
                f"  Date naissance : {p.get('date_naissance','')}",
                f"  Sexe           : {p.get('sexe','')}",
                f"  N° Dossier     : {p.get('num_dossier','')}",
                f"  CIN            : {p.get('cin','')}",
                f"  Téléphone      : {p.get('telephone','')}",
                f"  Pays           : {p.get('pays','')}",
                f"  Adresse        : {p.get('adresse','')}",
                f"  Remarques      : {p.get('remarques','')}",
                "",
            ]
            for i, ex_block in enumerate(data["examens"], 1):
                ex = ex_block["examen"]
                lines += [
                    f"EXAMEN {i} — {ex.get('modalite','')} {ex.get('type_examen','')}",
                    f"  N° Accession   : {ex.get('num_accession','')}",
                    f"  Date           : {ex.get('date_examen','')}",
                    f"  Indication     : {ex.get('indication','')}",
                    f"  Prescripteur   : {ex.get('medecin_prescripteur','')}",
                    f"  Radiologue     : {ex.get('medecin','')}",
                    f"  Établissement  : {ex.get('etablissement','')}",
                    f"  Statut         : {ex.get('statut','')}",
                    "",
                ]
                for j, cr in enumerate(ex_block["comptes_rendus"], 1):
                    lines += [
                        f"  COMPTE RENDU v{cr.get('version', j)} — {cr.get('created_at','')[:19]}",
                        "  " + "─" * 50,
                    ]
                    for crline in cr.get("contenu", "").splitlines():
                        lines.append("  " + crline)
                    lines.append("")
            lines.append(sep)
            with open(dest_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            return True
        except Exception as e:
            logger.error(f"❌ export_patient_txt : {e}")
            return False

    # ══════════════════════════════════════════════════════════════════════════
    #  SUPPRESSION DÉFINITIVE (RGPD Art. 17 — Droit à l'effacement)
    # ══════════════════════════════════════════════════════════════════════════

    def delete_patient_gdpr(self, patient_uuid: str,
                             confirmed_nom: str = "") -> bool:
        """
        Suppression définitive RGPD :
        - Vérifie la correspondance du nom (double confirmation)
        - Supprime le patient + tous examens/CRs (CASCADE)
        - Purge les entrées audit liées
        - Écrit une entrée de suppression dans l'audit
        """
        patient = self.get_patient_by_uuid(patient_uuid)
        if not patient:
            return False
        # Double confirmation : le nom saisi doit correspondre
        if confirmed_nom.strip().upper() != patient.get("nom", "").strip().upper():
            return False
        nom_for_log = patient.get("nom", "?") + " " + patient.get("prenom", "?")
        try:
            conn = self._connect()
            conn.execute("DELETE FROM patients WHERE patient_uuid=?", (patient_uuid,))
            # RGPD Art.17 vs. Art.5(2) accountability balance :
            # On anonymise les entrées d'audit liées (patient_uuid → NULL)
            # plutôt que de les supprimer, pour conserver la traçabilité
            # des accès sans conserver de PII patient. (EDPB Guidelines 01/2020)
            conn.execute(
                "UPDATE audit_log SET patient_uuid=NULL, details='[SUPPRIMÉ RGPD]' "
                "WHERE patient_uuid=? AND action != 'DELETE_PATIENT_GDPR'",
                (patient_uuid,)
            )
            conn.commit()
            conn.close()
            self.audit("DELETE_PATIENT_GDPR",
                       details=f"Suppression RGPD : {nom_for_log}")
            return True
        except Exception as e:
            logger.error(f"❌ delete_patient_gdpr : {e}")
            return False


# ── Brute-force lockout state (SOC2 CC6.1 / HIPAA §164.312(d)) ──────────────
_AUTH_FAILURES: dict = {}   # {username: (count, first_fail_ts)}
_AUTH_MAX_TRIES  = 5
_AUTH_LOCKOUT_S  = 30       # seconds

_pacs_db: PacsRisDB | None = None

# ── Fichier de configuration réseau ──────────────────────────────────────────
# Le logiciel cherche automatiquement "pisum_network.cfg" dans cet ordre :
#   1. Même dossier que le .exe (ou le script)
#   2. Bureau de l'utilisateur
#   3. Dossier home (~/.pisum_data/)
#
# Si le fichier est absent → base locale uniquement (comportement original).
# Pas besoin de recompiler le .exe pour changer la configuration réseau !
# ─────────────────────────────────────────────────────────────────────────────

def _load_network_config() -> dict:
    """
    Cherche et lit le fichier pisum_network.cfg.
    Retourne un dict vide si absent (→ mode local).
    """
    import sys

    # Emplacements à chercher dans l'ordre
    candidates = [
        # 1. Même dossier que le .exe / script
        Path(getattr(sys, "_MEIPASS", "") or "").parent / "pisum_network.cfg"
        if getattr(sys, "frozen", False)
        else Path(sys.argv[0]).parent / "pisum_network.cfg",
        # 2. Bureau Windows/Linux
        Path.home() / "Desktop"  / "pisum_network.cfg",
        Path.home() / "Bureau"   / "pisum_network.cfg",
        # 3. Dossier de données PISUM
        Path.home() / ".pisum_data" / "pisum_network.cfg",
    ]

    for cfg_path in candidates:
        try:
            if cfg_path.exists():
                data = json.loads(cfg_path.read_text(encoding="utf-8"))
                logger.info("📡 Config réseau trouvée : %s", cfg_path)
                # SOC2 CC6.7: warn if plaintext credential found in config
                if data.get("network_password"):
                    logger.warning(
                        "⚠️  pisum_network.cfg contient un mot de passe en clair. "
                        "Considérez un mécanisme de stockage sécurisé des identifiants."
                    )
                return data
        except Exception as e:
            logger.warning("pisum_network.cfg illisible (%s) : %s", cfg_path, e)

    return {}   # pas de config → mode local


def get_pacs_db() -> PacsRisDB:
    """
    Retourne la base de données active.
    - Si pisum_network.cfg existe et contient network_path → base réseau.
    - Sinon → base locale (comportement original, rien ne change).
    """
    global _pacs_db
    if _pacs_db is not None:
        return _pacs_db

    cfg = _load_network_config()
    network_path = cfg.get("network_path", "").strip()

    if network_path:
        try:
            from pacs_network_sync import NetworkSyncConfig, init_network_sync
            sync_cfg = NetworkSyncConfig(
                mode             = cfg.get("mode", "shared"),
                network_path     = network_path,
                clinic_name      = cfg.get("clinic_name", "Clinique"),
                workstation_id   = cfg.get("workstation_id", getpass.getuser()),
                network_password = cfg.get("network_password", ""),
                sync_interval_seconds = int(cfg.get("sync_interval_seconds", 30)),
            )
            sync     = init_network_sync(sync_cfg)
            _pacs_db = sync.get_db()
            logger.info("✅ Base réseau active : %s", network_path)
        except Exception as e:
            logger.error(
                "❌ Réseau indisponible (%s) — basculement base locale.\n   %s",
                network_path, e
            )
            _pacs_db = PacsRisDB()
    else:
        _pacs_db = PacsRisDB()

    return _pacs_db


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS UI
# ══════════════════════════════════════════════════════════════════════════════

def _label(parent, text, bold=False, size=10, color=None):
    lbl = wx.StaticText(parent, label=text)
    font = lbl.GetFont()
    font.SetPointSize(size)
    if bold:
        font.SetWeight(wx.FONTWEIGHT_BOLD)
    lbl.SetFont(font)
    if color:
        lbl.SetForegroundColour(color)
    return lbl

def _field(parent, value="", placeholder="", width=200, multiline=False):
    style = wx.TE_MULTILINE if multiline else 0
    tf = wx.TextCtrl(parent, value=value, style=style,
                     size=(width, _S(90) if multiline else -1))
    tf.SetHint(placeholder)
    tf.SetBackgroundColour(C_CARD)
    return tf

def _btn(parent, label, color=None, handler=None):
    b = wx.Button(parent, label=label)
    b.SetBackgroundColour(color or C_PRIMARY)
    b.SetForegroundColour(wx.WHITE)
    b.SetFont(b.GetFont().Bold())
    if handler:
        b.Bind(wx.EVT_BUTTON, handler)
    return b


# ══════════════════════════════════════════════════════════════════════════════
#  DIALOG — Nouveau / Édition patient
# ══════════════════════════════════════════════════════════════════════════════

class NewPatientDialog(wx.Dialog):
    def __init__(self, parent, db: PacsRisDB, t: dict,
                 edit_mode: bool = False, patient_uuid_to_edit: str = None):
        self.t = t or {}
        title = self.t.get('dlg_edit_pat', "✏️ Modifier le patient") if edit_mode else self.t.get('dlg_new_pat', "👤 Nouveau Patient")
        super().__init__(parent, title=title,
                         size=(_S(680), _S(720)),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.SetMinSize((_S(500), _S(550)))
        self.db = db
        self.edit_mode           = edit_mode
        self.patient_uuid_to_edit = patient_uuid_to_edit
        self.patient_uuid = patient_uuid_to_edit
        self._build()
        self.Centre()

    def _build(self):
        self.SetBackgroundColour(C_BG)
        root = wx.BoxSizer(wx.VERTICAL)

        hdr = wx.Panel(self, size=(-1, _S(58)))
        hdr.SetBackgroundColour(C_PRIMARY)
        hdr_s = wx.BoxSizer(wx.HORIZONTAL)
        header_text = self.t.get('hdr_edit_pat', "  ✏️ Modifier le dossier patient") if self.edit_mode else self.t.get('hdr_new_pat', "  👤 Nouveau dossier patient")
        hdr_s.Add(_label(hdr, header_text, bold=True, size=13, color=wx.WHITE),
                  1, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 12)
        hdr.SetSizer(hdr_s)
        root.Add(hdr, 0, wx.EXPAND)

        form = scrolled.ScrolledPanel(self)
        form.SetBackgroundColour(C_BG)
        gs = wx.FlexGridSizer(rows=0, cols=2, hgap=14, vgap=12)
        gs.AddGrowableCol(1, 1)

        def row(label, widget):
            gs.Add(_label(form, label, bold=True, size=11), 0, wx.ALIGN_CENTER_VERTICAL)
            gs.Add(widget, 1, wx.EXPAND)

        self.f_nom      = _field(form, placeholder="", width=_S(320))
        self.f_prenom   = _field(form, placeholder="", width=_S(320))
        # ── Date de naissance en DD-MM-YYYY ──────────────────────────────────
        self.f_ddn      = _field(form, placeholder="DD-MM-YYYY", width=_S(220))
        _sexe_labels = ["",
                       self.t.get('sexe_M',     'M'),
                       self.t.get('sexe_F',     'F'),
                       self.t.get('sexe_Autre', 'Autre')]
        self._sexe_raw  = ['', 'M', 'F', 'Autre']
        self._sexe_labels = _sexe_labels
        self.f_sexe     = wx.Choice(form, choices=_sexe_labels)
        self.f_num      = _field(form, placeholder="", width=_S(280))
        self.f_cin      = _field(form, placeholder="", width=_S(280))
        self.f_tel      = _field(form, placeholder="", width=_S(280))
        self.f_pays     = _field(form, placeholder="", width=_S(280))
        self.f_adresse  = _field(form, multiline=True, width=_S(320))
        self.f_rem      = _field(form, multiline=True, width=_S(320))

        row(self.t.get('lbl_nom_req', "Nom *"), self.f_nom)
        row(self.t.get('lbl_prenom_req', "Prénom *"), self.f_prenom)
        row(self.t.get('lbl_ddn', "Date naissance"), self.f_ddn)
        row(self.t.get('lbl_sexe', "Sexe"), self.f_sexe)
        row(self.t.get('col_dos', "N° dossier"), self.f_num)
        row(self.t.get('lbl_cin', "CIN / Pièce ID"), self.f_cin)
        row(self.t.get('lbl_tel', "Téléphone"), self.f_tel)
        row(self.t.get('col_pays', "Pays"), self.f_pays)
        row(self.t.get('lbl_adresse', "Adresse"), self.f_adresse)
        row(self.t.get('lbl_rem', "Remarques"), self.f_rem)

        pad = wx.BoxSizer(wx.VERTICAL)
        pad.Add(gs, 1, wx.ALL | wx.EXPAND, 18)
        form.SetSizer(pad)
        form.SetupScrolling(scroll_x=False)
        root.Add(form, 1, wx.EXPAND)

        save_label = self.t.get('btn_save_mod', "✔ Enregistrer les modifications") if self.edit_mode else self.t.get('btn_save', "✔ Enregistrer")
        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        btn_row.AddStretchSpacer()
        btn_row.Add(_btn(self, self.t.get('btn_cancel', "✕ Annuler"), C_TEXT2,
                         lambda e: self.EndModal(wx.ID_CANCEL)), 0, wx.RIGHT, 10)
        btn_row.Add(_btn(self, save_label, C_GREEN, self._on_save), 0)
        root.Add(btn_row, 0, wx.ALL, 14)
        self.SetSizer(root)

    def _on_save(self, _):
        nom    = self.f_nom.GetValue().strip()
        prenom = self.f_prenom.GetValue().strip()
        if not nom or not prenom:
            return

        # ── Date stockée telle quelle en DD-MM-YYYY ──────────────────────────
        ddn    = self.f_ddn.GetValue().strip()
        _sexe_idx = self.f_sexe.GetSelection()
        sexe      = self._sexe_raw[_sexe_idx] if _sexe_idx >= 0 else ''
        num    = self.f_num.GetValue().strip() or None
        cin    = self.f_cin.GetValue().strip()
        tel    = self.f_tel.GetValue().strip()
        pays   = self.f_pays.GetValue().strip()
        adr    = self.f_adresse.GetValue().strip()
        rem    = self.f_rem.GetValue().strip()

        if self.edit_mode and self.patient_uuid_to_edit:
            ok = self.db.update_patient(
                self.patient_uuid_to_edit,
                nom=nom, prenom=prenom, date_naissance=ddn, sexe=sexe,
                cin=cin, telephone=tel, pays=pays,
                adresse=adr, remarques=rem
            )
            if num:
                self.db.update_patient(self.patient_uuid_to_edit, num_dossier=num)
            if ok:
                self.EndModal(wx.ID_OK)
        else:
            existing = self.db.get_patient_by_bio_key(nom, prenom, ddn, sexe)
            if existing:
                self.patient_uuid = existing["patient_uuid"]
                self.EndModal(wx.ID_OK)
                return

            uid = self.db.add_patient(nom, prenom, ddn, sexe, num, cin, tel, pays, adr, rem)
            if uid:
                self.patient_uuid = uid
                self.EndModal(wx.ID_OK)


# ══════════════════════════════════════════════════════════════════════════════
#  DIALOG — Nouvel examen
# ══════════════════════════════════════════════════════════════════════════════

class NewExamenDialog(wx.Dialog):
    MODALITES = ["IRM", "Scanner", "Echographie", "Radiographie conventionnelle",
                 "Radiologie Interventionnelle", "Sénologie", "Consultations", "Autre"]

    def __init__(self, parent, db: PacsRisDB, patient: dict, prefill: dict = None, t: dict = None):
        self.t = t or {}
        self.MODALITES = self.t.get('modalites', self.__class__.MODALITES)
        super().__init__(parent, title=self.t.get('dlg_new_exam', "📋 Nouvel Examen"),
                         size=(_S(660), _S(620)),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.SetMinSize((_S(500), _S(480)))
        self.db          = db
        self.patient     = patient
        self.prefill     = prefill or {}
        self.examen_uuid = None
        self._build()
        self.Centre()

    def _build(self):
        self.SetBackgroundColour(C_BG)
        root = wx.BoxSizer(wx.VERTICAL)

        hdr = wx.Panel(self, size=(-1, _S(58)))
        hdr.SetBackgroundColour(C_PRIMARY)
        hdr_s = wx.BoxSizer(wx.HORIZONTAL)
        nom_complet = f"{self.patient.get('prenom','')} {self.patient.get('nom','')}".strip()
        prefix = self.t.get('dlg_new_exam', "📋 Nouvel Examen")
        hdr_s.Add(_label(hdr, f"  {prefix} — {nom_complet}", bold=True, size=13,
                         color=wx.WHITE), 1, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 12)
        hdr.SetSizer(hdr_s)
        root.Add(hdr, 0, wx.EXPAND)

        form = scrolled.ScrolledPanel(self)
        form.SetBackgroundColour(C_BG)
        gs = wx.FlexGridSizer(rows=0, cols=2, hgap=14, vgap=12)
        gs.AddGrowableCol(1, 1)

        def row(label, widget):
            gs.Add(_label(form, label, bold=True, size=11), 0, wx.ALIGN_CENTER_VERTICAL)
            gs.Add(widget, 1, wx.EXPAND)

        # ── Date examen en DD-MM-YYYY ─────────────────────────────────────────
        self.f_date = _field(form, value=datetime.date.today().strftime("%d-%m-%Y"), width=_S(180))

        self.f_mod = wx.Choice(form, choices=self.MODALITES)
        pref_mod = self.prefill.get("modalite", "")
        if pref_mod in self.MODALITES:
            self.f_mod.SetSelection(self.MODALITES.index(pref_mod))

        self.f_type = _field(form, value=self.prefill.get("type_examen", ""), width=_S(320))
        self.f_ind = _field(form, width=_S(320), multiline=True)
        self.f_prescripteur = _field(form, value=self.prefill.get("medecin_prescripteur", ""), width=_S(280))
        self.f_med = _field(form, value=self.prefill.get("medecin", ""), width=_S(280))
        self.f_etab = _field(form, value=self.prefill.get("etablissement", ""), width=_S(280))
        self.f_lang = _field(form, value=self.prefill.get("langue", ""), width=_S(180))

        row(self.t.get('lbl_date_req', "Date examen *"), self.f_date)
        row(self.t.get('lbl_mod_req', "Modalité *"), self.f_mod)
        row(self.t.get('col_type', "Type examen"), self.f_type)
        row(self.t.get('lbl_ind', "Indication"), self.f_ind)
        row(self.t.get('col_presc', "Médecin prescripteur"), self.f_prescripteur)
        row(self.t.get('col_rad', "Radiologue"), self.f_med)
        row(self.t.get('lbl_etab', "Établissement"), self.f_etab)
        row(self.t.get('lbl_lang', "Langue CR"), self.f_lang)

        pad = wx.BoxSizer(wx.VERTICAL)
        pad.Add(gs, 1, wx.ALL | wx.EXPAND, 18)
        form.SetSizer(pad)
        form.SetupScrolling(scroll_x=False)
        root.Add(form, 1, wx.EXPAND)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        btn_row.AddStretchSpacer()
        btn_row.Add(_btn(self, self.t.get('btn_cancel', "✕ Annuler"), C_TEXT2,
                         lambda e: self.EndModal(wx.ID_CANCEL)), 0, wx.RIGHT, 10)
        btn_row.Add(_btn(self, self.t.get('btn_create_exam', "✔ Créer l'examen"), C_GREEN, self._on_save), 0)
        root.Add(btn_row, 0, wx.ALL, 14)
        self.SetSizer(root)

    def _on_save(self, _):
        date  = self.f_date.GetValue().strip()
        mod   = self.f_mod.GetStringSelection()
        if not date or not mod:
            return
        fr_modalites = PACS_TRANSLATIONS.get('Français', {}).get('modalites', self.__class__.MODALITES)
        mod_fr = mod
        for idx, item in enumerate(self.MODALITES):
            if item.strip().lower() == mod.strip().lower():
                if idx < len(fr_modalites):
                    mod_fr = fr_modalites[idx]
                break
        uid = self.db.add_examen(
            patient_uuid         = self.patient["patient_uuid"],
            modalite             = mod_fr,
            type_examen          = self.f_type.GetValue().strip(),
            formula_name         = "",
            date_examen          = date,
            indication           = self.f_ind.GetValue().strip(),
            medecin_prescripteur = self.f_prescripteur.GetValue().strip(),
            medecin              = self.f_med.GetValue().strip(),
            etablissement        = self.f_etab.GetValue().strip(),
            langue               = self.f_lang.GetValue().strip(),
        )
        if uid:
            self.examen_uuid = uid
            self.EndModal(wx.ID_OK)


# ══════════════════════════════════════════════════════════════════════════════
#  DIALOG — Consultation rapide des CRs
# ══════════════════════════════════════════════════════════════════════════════

class CRViewerDialog(wx.Dialog):
    def __init__(self, parent, db: PacsRisDB, examen: dict, patient: dict,
                 on_open_in_pisum=None, t: dict = None):
        self.t = t or {}
        nom_complet = f"{patient.get('prenom','')} {patient.get('nom','')}".strip()
        _parent_lang = getattr(parent, 'lang', None) or "Français"
        mod   = translate_modalite(examen.get("modalite", ""), _parent_lang)
        typex = examen.get("type_examen", "")
        date  = examen.get("date_examen", "")
        title_prefix = self.t.get('dlg_crs', "📄 CRs — ")
        super().__init__(parent,
                         title=f"{title_prefix}{nom_complet}  |  {mod} {typex}  |  {date}",
                         size=(_S(920), _S(680)),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX)
        self.SetMinSize((_S(700), _S(520)))
        self.db               = db
        self.examen           = examen
        self.patient          = patient
        self.on_open_in_pisum = on_open_in_pisum
        self._crs             = []
        self._build()
        self._load()
        self.Centre()

    def _build(self):
        self.SetBackgroundColour(C_BG)
        root = wx.BoxSizer(wx.VERTICAL)

        hdr = wx.Panel(self, size=(-1, _S(62)))
        hdr.SetBackgroundColour(C_PRIMARY)
        hdr_s = wx.BoxSizer(wx.HORIZONTAL)

        nom   = f"{self.patient.get('prenom','')} {self.patient.get('nom','')}".strip()
        ndos  = self.patient.get("num_dossier", "")
        mod   = self.examen.get("modalite", "")
        typex = self.examen.get("type_examen", "")
        date  = self.examen.get("date_examen", "")
        acc   = self.examen.get("num_accession", "")

        txt_hdr = f"  📄  {nom}   —   N° {ndos}   |   {mod}  {typex}   |   {date}   |   {acc}"
        hdr_s.Add(_label(hdr, txt_hdr, bold=True, size=12, color=wx.WHITE),
                  1, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 8)
        hdr.SetSizer(hdr_s)
        root.Add(hdr, 0, wx.EXPAND)

        info_panel = wx.Panel(self)
        info_panel.SetBackgroundColour(C_PRIMARY_LIGHT)
        info_s = wx.BoxSizer(wx.HORIZONTAL)
        prescripteur = self.examen.get("medecin_prescripteur", "") or ""
        indication   = self.examen.get("indication", "") or ""
        radiologue   = self.examen.get("medecin", "") or ""
        infos = []
        if prescripteur: infos.append(f"{self.t.get('lbl_prescripteur', 'Prescripteur')} : {prescripteur}")
        if radiologue:   infos.append(f"{self.t.get('lbl_radiologue', 'Radiologue')} : {radiologue}")
        if indication:   infos.append(f"{self.t.get('lbl_indication', 'Indication')} : {indication}")

        fallback_info = self.t.get('lbl_info_none', "   Aucune information complémentaire")
        lbl_info = _label(info_panel,
                          "   " + "    •    ".join(infos) if infos else fallback_info,
                          size=10, color=C_TEXT2)
        info_s.Add(lbl_info, 1, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 8)
        info_panel.SetSizer(info_s)
        root.Add(info_panel, 0, wx.EXPAND)

        splitter = wx.SplitterWindow(self, style=wx.SP_LIVE_UPDATE)
        splitter.SetMinimumPaneSize(140)

        left_panel = wx.Panel(splitter)
        left_panel.SetBackgroundColour(C_BG)
        left_s = wx.BoxSizer(wx.VERTICAL)
        left_s.Add(_label(left_panel, self.t.get('lbl_versions', "  Versions"), bold=True, size=11, color=C_PRIMARY),
                   0, wx.ALL, 10)

        self.version_list = wx.ListCtrl(left_panel,
                                        style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SIMPLE)
        self.version_list.SetBackgroundColour(C_CARD)
        self.version_list.InsertColumn(0, self.t.get('col_version', "Version"), width=_S(80))
        self.version_list.InsertColumn(1, self.t.get('col_date', "Date"), width=_S(150))
        self.version_list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_version_selected)
        left_s.Add(self.version_list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        left_panel.SetSizer(left_s)

        right_panel = wx.Panel(splitter)
        right_panel.SetBackgroundColour(C_BG)
        right_s = wx.BoxSizer(wx.VERTICAL)
        right_s.Add(_label(right_panel, self.t.get('lbl_cr_content', "  Contenu du compte rendu"), bold=True, size=11, color=C_PRIMARY),
                    0, wx.ALL, 10)
        self.cr_view = wx.TextCtrl(right_panel,
                                   style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP | wx.BORDER_SIMPLE)
        self.cr_view.SetBackgroundColour(C_CARD)
        self.cr_view.SetFont(wx.Font(12, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                     wx.FONTWEIGHT_NORMAL, faceName="Segoe UI"))
        right_s.Add(self.cr_view, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        rb = wx.BoxSizer(wx.HORIZONTAL)
        rb.Add(_btn(right_panel, self.t.get('btn_copy', "📋 Copier"), C_PRIMARY, self._on_copy), 0, wx.RIGHT, 10)

        self.btn_open_pisum = _btn(right_panel, self.t.get('btn_open_main', "📖 Ouvrir dans l'éditeur"), C_ACCENT,
                                   self._on_open_in_pisum)
        self.btn_open_pisum.Enable(self.on_open_in_pisum is not None)
        rb.Add(self.btn_open_pisum, 0)

        right_s.Add(rb, 0, wx.LEFT | wx.BOTTOM, 10)
        right_panel.SetSizer(right_s)

        splitter.SplitVertically(left_panel, right_panel, _S(260))
        root.Add(splitter, 1, wx.EXPAND | wx.ALL, 8)

        close_row = wx.BoxSizer(wx.HORIZONTAL)
        close_row.AddStretchSpacer()
        close_row.Add(_btn(self, self.t.get('btn_close', "✕ Fermer"), C_TEXT2,
                           lambda e: self.EndModal(wx.ID_CANCEL)), 0, wx.RIGHT, 12)
        root.Add(close_row, 0, wx.BOTTOM, 12)

        self.SetSizer(root)

    def _load(self):
        self._crs = self.db.get_compte_rendus(self.examen["examen_uuid"])
        self.version_list.DeleteAllItems()
        if not self._crs:
            self.cr_view.SetValue("")
            self.btn_open_pisum.Enable(False)
            return
        for cr in self._crs:
            idx = self.version_list.InsertItem(self.version_list.GetItemCount(),
                                               f"v{cr['version']}")
            self.version_list.SetItem(idx, 1, cr.get("created_at", "")[:16])
        self.version_list.Select(0)
        self.cr_view.SetValue(self._crs[0]["contenu"])
        self.btn_open_pisum.Enable(self.on_open_in_pisum is not None)

    def _on_version_selected(self, event):
        idx = event.GetIndex()
        if 0 <= idx < len(self._crs):
            self.cr_view.SetValue(self._crs[idx]["contenu"])

    def _on_copy(self, _):
        txt = self.cr_view.GetValue()
        if txt and wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(txt))
            wx.TheClipboard.Close()

    def _on_open_in_pisum(self, _):
        contenu = self.cr_view.GetValue().strip()
        if not contenu:
            return
        _parent_lang = getattr(self.GetParent(), 'lang', None) or "Français"
        cr_lang = self.examen.get("langue") or _parent_lang
        examen_payload = dict(self.examen)
        examen_payload["modalite_display"]    = translate_modalite(self.examen.get("modalite", ""), cr_lang)
        examen_payload["referring_physician"] = self.examen.get("medecin_prescripteur", "") or ""
        examen_payload["radiologist"]         = self.examen.get("medecin", "") or ""
        try:
            # Cherche le callback sur la fenêtre actuelle ou son parent
            callback = getattr(self, 'on_cr_open_in_pisum', None)
            if not callback and hasattr(self.GetParent(), 'on_cr_open_in_pisum'):
                callback = self.GetParent().on_cr_open_in_pisum
            
            if callback:
                callback(dict(self.patient), examen_payload, contenu)
                logger.info(f"[PACS] _on_open_in_pisum success contenu={len(contenu)}c")
            else:
                logger.warning("[PACS] Callback on_cr_open_in_pisum introuvable.")
        except Exception as _e:
            logger.error("[PACS] _on_open_in_pisum: %s", _e)
        self.EndModal(wx.ID_OK)


# ══════════════════════════════════════════════════════════════════════════════
#  DIALOG — Modifier un examen existant
# ══════════════════════════════════════════════════════════════════════════════

class EditExamenDialog(wx.Dialog):
    """Dialog pour modifier les données d'un examen existant."""

    def __init__(self, parent, db: PacsRisDB, examen: dict, t: dict = None):
        self.t = t or {}
        self.db      = db
        self.examen  = examen
        self.MODALITES = self.t.get('modalites',
            ["IRM", "Scanner", "Echographie", "Radiographie conventionnelle",
             "Radiologie Interventionnelle", "Sénologie", "Consultations", "Autre"])
        title = self.t.get('dlg_edit_exam', "✏ Modifier l'examen")
        super().__init__(parent, title=title, size=(_S(660), _S(580)),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.SetMinSize((_S(500), _S(460)))
        self._build()
        self._fill(examen)
        self.Centre()

    def _build(self):
        self.SetBackgroundColour(C_BG)
        root = wx.BoxSizer(wx.VERTICAL)

        hdr = wx.Panel(self, size=(-1, _S(52)))
        hdr.SetBackgroundColour(C_PRIMARY)
        hdr_s = wx.BoxSizer(wx.HORIZONTAL)
        hdr_s.Add(_label(hdr, f"  {self.t.get('dlg_edit_exam', '✏ Modifier l examen')}",
                         bold=True, size=13, color=wx.WHITE), 1, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 12)
        hdr.SetSizer(hdr_s)
        root.Add(hdr, 0, wx.EXPAND)

        form = scrolled.ScrolledPanel(self)
        form.SetBackgroundColour(C_BG)
        gs = wx.FlexGridSizer(rows=0, cols=2, hgap=14, vgap=12)
        gs.AddGrowableCol(1, 1)

        def row(label, widget):
            gs.Add(_label(form, label, bold=True, size=11), 0, wx.ALIGN_CENTER_VERTICAL)
            gs.Add(widget, 1, wx.EXPAND)

        self.f_date         = _field(form, width=_S(180))
        self.f_mod          = wx.Choice(form, choices=self.MODALITES)
        self.f_type         = _field(form, width=_S(320))
        self.f_ind          = _field(form, width=_S(320), multiline=True)
        self.f_prescripteur = _field(form, width=_S(280))
        self.f_med          = _field(form, width=_S(280))
        self.f_etab         = _field(form, width=_S(280))
        self.f_lang         = _field(form, width=_S(180))

        row(self.t.get('lbl_date_req', "Date examen *"), self.f_date)
        row(self.t.get('lbl_mod_req',  "Modalité *"),    self.f_mod)
        row(self.t.get('col_type',     "Type examen"),   self.f_type)
        row(self.t.get('lbl_ind',      "Indication"),    self.f_ind)
        row(self.t.get('col_presc',    "Médecin prescripteur"), self.f_prescripteur)
        row(self.t.get('col_rad',      "Radiologue"),    self.f_med)
        row(self.t.get('lbl_etab',     "Établissement"), self.f_etab)
        row(self.t.get('lbl_lang',     "Langue CR"),     self.f_lang)

        pad = wx.BoxSizer(wx.VERTICAL)
        pad.Add(gs, 1, wx.ALL | wx.EXPAND, 18)
        form.SetSizer(pad)
        form.SetupScrolling(scroll_x=False)
        root.Add(form, 1, wx.EXPAND)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        btn_row.AddStretchSpacer()
        btn_row.Add(_btn(self, self.t.get('btn_cancel', "✕ Annuler"), C_TEXT2,
                         lambda e: self.EndModal(wx.ID_CANCEL)), 0, wx.RIGHT, 10)
        btn_row.Add(_btn(self, self.t.get('btn_save_mod', "✔ Sauvegarder"), C_GREEN,
                         self._on_save), 0)
        root.Add(btn_row, 0, wx.ALL, 14)
        self.SetSizer(root)

    def _fill(self, examen):
        self.f_date.SetValue(examen.get("date_examen", ""))
        self.f_type.SetValue(examen.get("type_examen", ""))
        self.f_ind.SetValue(examen.get("indication", ""))
        self.f_prescripteur.SetValue(examen.get("medecin_prescripteur", ""))
        self.f_med.SetValue(examen.get("medecin", ""))
        self.f_etab.SetValue(examen.get("etablissement", ""))
        self.f_lang.SetValue(examen.get("langue", ""))
        stored_mod = examen.get("modalite", "")
        fr_list = PACS_TRANSLATIONS.get('Français', {}).get('modalites', [])
        idx_found = -1
        for idx, item in enumerate(fr_list):
            if item.strip().lower() == stored_mod.strip().lower():
                idx_found = idx
                break
        if idx_found >= 0 and idx_found < len(self.MODALITES):
            self.f_mod.SetSelection(idx_found)
        elif stored_mod in self.MODALITES:
            self.f_mod.SetSelection(self.MODALITES.index(stored_mod))

    def _on_save(self, _):
        date = self.f_date.GetValue().strip()
        mod  = self.f_mod.GetStringSelection()
        if not date or not mod:
            return
        fr_list = PACS_TRANSLATIONS.get('Français', {}).get('modalites', self.MODALITES)
        mod_fr = mod
        for idx, item in enumerate(self.MODALITES):
            if item.strip().lower() == mod.strip().lower():
                if idx < len(fr_list):
                    mod_fr = fr_list[idx]
                break
        ok = self.db.update_examen(
            examen_uuid          = self.examen["examen_uuid"],
            modalite             = mod_fr,
            type_examen          = self.f_type.GetValue().strip(),
            formula_name         = self.examen.get("formula_name", ""),
            date_examen          = date,
            indication           = self.f_ind.GetValue().strip(),
            medecin_prescripteur = self.f_prescripteur.GetValue().strip(),
            medecin              = self.f_med.GetValue().strip(),
            etablissement        = self.f_etab.GetValue().strip(),
            langue               = self.f_lang.GetValue().strip(),
        )
        if ok:
            self.EndModal(wx.ID_OK)


# ══════════════════════════════════════════════════════════════════════════════
#  DIALOG — Authentification PACS (RGPD Art. 32)
# ══════════════════════════════════════════════════════════════════════════════

class PacsAuthDialog(wx.Dialog):
    """
    Dialog de connexion au PACS.
    - Si aucun mot de passe n'est défini, propose d'en créer un.
    - Sinon, demande le mot de passe existant.
    """
    def __init__(self, parent, db: "PacsRisDB", t: dict):
        self.t  = t or {}
        self.db = db
        self._setup_mode = not db.has_password()
        title = (self.t.get("auth_setup_title", "🔐 Créer un mot de passe PACS")
                 if self._setup_mode else
                 self.t.get("auth_title", "🔒 Accès sécurisé PACS/RIS"))
        super().__init__(parent, title=title, size=(_S(420), _S(320)),
                         style=wx.DEFAULT_DIALOG_STYLE)
        self._build()
        self.Centre()

    def _build(self):
        p   = wx.Panel(self)
        vsz = wx.BoxSizer(wx.VERTICAL)

        if self._setup_mode:
            msg = self.t.get("auth_setup_msg",
                "Première utilisation : définissez un mot de passe\n"
                "pour protéger l'accès aux dossiers patients.")
        else:
            msg = self.t.get("auth_msg",
                "Entrez votre mot de passe pour accéder\naux dossiers radiologiques.")

        vsz.Add(wx.StaticText(p, label=msg), 0, wx.ALL, 16)

        grid = wx.FlexGridSizer(cols=2, vgap=10, hgap=10)
        grid.AddGrowableCol(1)

        lbl_pw = wx.StaticText(p, label=self.t.get("auth_pw", "Mot de passe :"))
        self.f_pw = wx.TextCtrl(p, style=wx.TE_PASSWORD | wx.TE_PROCESS_ENTER)
        grid.Add(lbl_pw, 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self.f_pw, 1, wx.EXPAND)

        if self._setup_mode:
            lbl_pw2 = wx.StaticText(p, label=self.t.get("auth_pw2", "Confirmer :"))
            self.f_pw2 = wx.TextCtrl(p, style=wx.TE_PASSWORD | wx.TE_PROCESS_ENTER)
            grid.Add(lbl_pw2, 0, wx.ALIGN_CENTER_VERTICAL)
            grid.Add(self.f_pw2, 1, wx.EXPAND)

        vsz.Add(grid, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 20)

        self.lbl_err = wx.StaticText(p, label="")
        self.lbl_err.SetForegroundColour(C_RED)
        vsz.Add(self.lbl_err, 0, wx.LEFT | wx.TOP, 20)

        btn_sizer = wx.StdDialogButtonSizer()
        btn_ok = wx.Button(p, wx.ID_OK, self.t.get("auth_btn_ok", "Connexion"))
        btn_ok.SetDefault()
        btn_sizer.AddButton(btn_ok)
        if not self._setup_mode:
            btn_cancel = wx.Button(p, wx.ID_CANCEL, self.t.get("btn_cancel", "Annuler"))
            btn_sizer.AddButton(btn_cancel)
        btn_sizer.Realize()
        vsz.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 16)

        p.SetSizer(vsz)
        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)
        self.f_pw.Bind(wx.EVT_TEXT_ENTER, self._on_ok)

    def _on_ok(self, _):
        pw = self.f_pw.GetValue()
        if self._setup_mode:
            pw2 = self.f_pw2.GetValue()
            if len(pw) < 6:
                self.lbl_err.SetLabel(
                    self.t.get("auth_err_short", "❌ Minimum 6 caractères."))
                return
            if pw != pw2:
                self.lbl_err.SetLabel(
                    self.t.get("auth_err_mismatch", "❌ Les mots de passe ne correspondent pas."))
                return
            self.db.set_password(pw)
            self.EndModal(wx.ID_OK)
        else:
            if self.db.verify_password(pw):
                self.EndModal(wx.ID_OK)
            else:
                self.lbl_err.SetLabel(
                    self.t.get("auth_err_wrong", "❌ Mot de passe incorrect."))
                self.f_pw.SetValue("")
                self.f_pw.SetFocus()


# ══════════════════════════════════════════════════════════════════════════════
#  DIALOG — Modification du mot de passe (confirmation obligatoire)
# ══════════════════════════════════════════════════════════════════════════════

class PacsChangePasswordDialog(wx.Dialog):
    """
    Dialog de modification du mot de passe.
    Le nouveau mot de passe doit être saisi deux fois pour confirmation.
    """
    def __init__(self, parent, t: dict):
        self.t            = t or {}
        self.new_password = ""
        super().__init__(parent,
                         title=self.t.get("chpw_title", "🔑 Modifier le mot de passe"),
                         size=(_S(420), _S(300)),
                         style=wx.DEFAULT_DIALOG_STYLE)
        self._build()
        self.Centre()

    def _build(self):
        p   = wx.Panel(self)
        vsz = wx.BoxSizer(wx.VERTICAL)

        msg = self.t.get("chpw_msg", "Nouveau mot de passe (min. 6 caractères) :")
        vsz.Add(wx.StaticText(p, label=msg), 0, wx.ALL, 16)

        grid = wx.FlexGridSizer(cols=2, vgap=10, hgap=10)
        grid.AddGrowableCol(1)

        grid.Add(wx.StaticText(p, label=self.t.get("auth_pw", "Nouveau mot de passe :")),
                 0, wx.ALIGN_CENTER_VERTICAL)
        self.f_pw1 = wx.TextCtrl(p, style=wx.TE_PASSWORD | wx.TE_PROCESS_ENTER)
        grid.Add(self.f_pw1, 1, wx.EXPAND)

        grid.Add(wx.StaticText(p, label=self.t.get("auth_pw2", "Confirmer :")),
                 0, wx.ALIGN_CENTER_VERTICAL)
        self.f_pw2 = wx.TextCtrl(p, style=wx.TE_PASSWORD | wx.TE_PROCESS_ENTER)
        grid.Add(self.f_pw2, 1, wx.EXPAND)

        vsz.Add(grid, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 20)

        self.lbl_err = wx.StaticText(p, label="")
        self.lbl_err.SetForegroundColour(wx.Colour(200, 0, 0))
        vsz.Add(self.lbl_err, 0, wx.LEFT | wx.TOP, 20)

        btn_sizer = wx.StdDialogButtonSizer()
        btn_ok     = wx.Button(p, wx.ID_OK,     self.t.get("auth_btn_ok", "Valider"))
        btn_cancel = wx.Button(p, wx.ID_CANCEL, self.t.get("btn_cancel",  "Annuler"))
        btn_ok.SetDefault()
        btn_sizer.AddButton(btn_ok)
        btn_sizer.AddButton(btn_cancel)
        btn_sizer.Realize()
        vsz.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 16)

        p.SetSizer(vsz)
        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)
        self.f_pw1.Bind(wx.EVT_TEXT_ENTER, self._on_ok)
        self.f_pw2.Bind(wx.EVT_TEXT_ENTER, self._on_ok)

    def _on_ok(self, _):
        pw1 = self.f_pw1.GetValue()
        pw2 = self.f_pw2.GetValue()
        if len(pw1) < 6:
            self.lbl_err.SetLabel(
                self.t.get("auth_err_short", "❌ Minimum 6 caractères."))
            return
        if pw1 != pw2:
            self.lbl_err.SetLabel(
                self.t.get("auth_err_mismatch", "❌ Les mots de passe ne correspondent pas."))
            self.f_pw1.SetValue("")
            self.f_pw2.SetValue("")
            self.f_pw1.SetFocus()
            return
        self.new_password = pw1
        self.EndModal(wx.ID_OK)


# ══════════════════════════════════════════════════════════════════════════════
#  DIALOG — Suppression RGPD (Art. 17 — Droit à l'effacement)
# ══════════════════════════════════════════════════════════════════════════════

class PacsGdprDeleteDialog(wx.Dialog):
    """
    Double confirmation de suppression définitive.
    L'utilisateur doit saisir le nom du patient pour confirmer.
    """
    def __init__(self, parent, patient: dict, t: dict):
        self.t       = t or {}
        self.patient = patient
        super().__init__(parent,
                         title=self.t.get("gdpr_del_title", "⚠️ Suppression définitive RGPD"),
                         size=(_S(480), _S(340)),
                         style=wx.DEFAULT_DIALOG_STYLE)
        self.confirmed_nom = ""
        self._build()
        self.Centre()

    def _build(self):
        p   = wx.Panel(self)
        vsz = wx.BoxSizer(wx.VERTICAL)

        nom_complet = f"{self.patient.get('prenom','')} {self.patient.get('nom','')}"
        msg = (self.t.get("gdpr_del_msg",
            "⚠️  Cette action supprime DÉFINITIVEMENT le dossier de\n"
            "{nom}\net tous ses examens et comptes rendus.\n\n"
            "Cette opération est IRRÉVERSIBLE (RGPD Art. 17).\n"
            "Saisissez le NOM du patient pour confirmer :"
        ).replace("{nom}", nom_complet))

        lbl = wx.StaticText(p, label=msg)
        lbl.SetForegroundColour(C_RED)
        vsz.Add(lbl, 0, wx.ALL, 16)

        self.f_nom = wx.TextCtrl(p)
        self.f_nom.SetHint(self.t.get("gdpr_del_hint",
                           "Saisir le NOM (en majuscules)…"))
        vsz.Add(self.f_nom, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 16)

        btn_sizer = wx.StdDialogButtonSizer()
        btn_del = wx.Button(p, wx.ID_OK,
                            self.t.get("gdpr_del_btn", "🗑 Supprimer définitivement"))
        btn_del.SetBackgroundColour(C_RED)
        btn_del.SetForegroundColour(wx.WHITE)
        btn_cancel = wx.Button(p, wx.ID_CANCEL,
                               self.t.get("btn_cancel", "Annuler"))
        btn_sizer.AddButton(btn_del)
        btn_sizer.AddButton(btn_cancel)
        btn_sizer.Realize()
        vsz.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 12)

        p.SetSizer(vsz)
        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)

    def _on_ok(self, _):
        self.confirmed_nom = self.f_nom.GetValue().strip()
        self.EndModal(wx.ID_OK)


# ══════════════════════════════════════════════════════════════════════════════
#  DIALOG — Visualiseur journal d'audit
# ══════════════════════════════════════════════════════════════════════════════

class PacsAuditDialog(wx.Dialog):
    def __init__(self, parent, db: "PacsRisDB", patient: dict = None, t: dict = None):
        self.t       = t or {}
        self.db      = db
        self.patient = patient
        title_suffix = (f" — {patient.get('prenom','')} {patient.get('nom','')}"
                        if patient else "")
        super().__init__(parent,
                         title=self.t.get("audit_title", "📋 Journal d'audit") + title_suffix,
                         size=(_S(860), _S(520)),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.SetMinSize((_S(600), _S(400)))
        self._build()
        self.Centre()

    def _build(self):
        vsz = wx.BoxSizer(wx.VERTICAL)
        self.list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.BORDER_SIMPLE)
        for i, (col, w) in enumerate([
            ("Horodatage",    _S(150)),
            ("Utilisateur",   _S(110)),
            ("Action",        _S(160)),
            ("Détails",       _S(380)),
        ]):
            self.list.InsertColumn(i, col, width=w)

        pid = self.patient["patient_uuid"] if self.patient else None
        for entry in self.db.get_audit_log(patient_uuid=pid, limit=500):
            idx = self.list.InsertItem(
                self.list.GetItemCount(),
                str(entry.get("ts", ""))[:19]
            )
            self.list.SetItem(idx, 1, entry.get("utilisateur", ""))
            self.list.SetItem(idx, 2, entry.get("action", ""))
            self.list.SetItem(idx, 3, entry.get("details", "") or "")

        vsz.Add(self.list, 1, wx.EXPAND | wx.ALL, 10)
        btn = wx.Button(self, wx.ID_CLOSE,
                        self.t.get("btn_close", "Fermer"))
        vsz.Add(btn, 0, wx.ALIGN_RIGHT | wx.RIGHT | wx.BOTTOM, 12)
        self.SetSizer(vsz)
        self.Bind(wx.EVT_BUTTON, lambda _: self.EndModal(wx.ID_OK), id=wx.ID_CLOSE)


# ══════════════════════════════════════════════════════════════════════════════
#  FENÊTRE PRINCIPALE PACS/RIS
# ══════════════════════════════════════════════════════════════════════════════

class PacsRisFrame(wx.Frame):
    def __init__(self, parent, prefill: dict = None, on_exam_loaded=None,
                 on_cr_open_in_pisum=None):

        self.prefill = prefill or {}
        self.lang = self.prefill.get("langue", "Français")
        self.t = PACS_TRANSLATIONS.get(self.lang, PACS_TRANSLATIONS['Français'])

        _sw, _sh = wx.GetDisplaySize()
        _fw = min(int(_sw * 0.90), _S(1800))
        _fh = min(int(_sh * 0.88), _S(980))
        super().__init__(parent,
                         title=self.t.get('pacs_title', "🏥 PACS/RIS — Dossiers Radiologiques"),
                         size=(_fw, _fh),
                         style=wx.DEFAULT_FRAME_STYLE)
        self.SetMinSize((_S(1100), _S(680)))

        self.db                  = get_pacs_db()
        self.on_exam_loaded      = on_exam_loaded
        self.on_cr_open_in_pisum = on_cr_open_in_pisum

        self.lm = LicenseManager()
        self._current_patient = None
        self._current_examen  = None

        self.SetBackgroundColour(C_BG)
        self._build_ui()
        self._refresh_stats()
        self._load_recent_patients()
        self.Centre()
        self.Show()

        # ── Authentification RGPD ─────────────────────────────────────────────
        wx.CallAfter(self._check_auth)

    def _build_ui(self):
        root = wx.BoxSizer(wx.HORIZONTAL)

        left = wx.BoxSizer(wx.VERTICAL)
        search_row = wx.BoxSizer(wx.HORIZONTAL)
        self.search_ctrl = wx.SearchCtrl(self, style=wx.TE_PROCESS_ENTER)
        self.search_ctrl.SetHint(self.t.get('search_hint', "🔍 Rechercher un patient…"))
        self.search_ctrl.Bind(wx.EVT_TEXT, self._on_search)
        self.search_ctrl.Bind(wx.EVT_SEARCHCTRL_SEARCH_BTN, self._on_search)
        search_row.Add(self.search_ctrl, 1, wx.EXPAND | wx.RIGHT, 8)
        search_row.Add(_btn(self, self.t.get('btn_new_pat', "➕ Patient"), C_GREEN, self._on_new_patient), 0)
        left.Add(search_row, 0, wx.EXPAND | wx.ALL, 12)

        self.stats_lbl = _label(self, "", size=10, color=C_TEXT2)
        left.Add(self.stats_lbl, 0, wx.LEFT | wx.BOTTOM, 12)

        self.patient_list = wx.ListCtrl(self,
                                        style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SIMPLE)
        self.patient_list.SetBackgroundColour(C_CARD)

        for i, (col, w) in enumerate([
            (self.t.get('col_dos', "N° Dossier"), _S(140)),
            (self.t.get('col_nom', "Nom"), _S(130)),
            (self.t.get('col_prenom', "Prénom"), _S(110)),
            (self.t.get('col_ddn', "DDN"), _S(100)),
            (self.t.get('col_pays', "Pays"), _S(90)),
            (self.t.get('col_exam', "Examens"), _S(60))
        ]):
            self.patient_list.InsertColumn(i, col, width=w)

        self.patient_list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_patient_selected)
        left.Add(self.patient_list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        root.Add(left, 2, wx.EXPAND)
        root.Add(wx.StaticLine(self, style=wx.LI_VERTICAL), 0, wx.EXPAND | wx.TOP | wx.BOTTOM, 6)

        right = wx.BoxSizer(wx.VERTICAL)

        self.patient_card = wx.Panel(self)
        self.patient_card.SetBackgroundColour(C_CARD)
        pc_s = wx.BoxSizer(wx.VERTICAL)
        self.lbl_patient_name = _label(self.patient_card, self.t.get('lbl_select_pat', "Sélectionner un patient"),
                                       bold=True, size=14, color=C_PRIMARY)
        self.lbl_patient_info = _label(self.patient_card, "", size=10, color=C_TEXT2)
        btn_row_pat = wx.BoxSizer(wx.HORIZONTAL)

        self.btn_edit_patient   = _btn(self.patient_card, self.t.get('btn_edit', "✏ Modifier"),      C_ACCENT, self._on_edit_patient)
        self.btn_delete_patient = _btn(self.patient_card, self.t.get('btn_del', "🗑 Supprimer"),     C_RED,    self._on_delete_patient)
        self.btn_new_exam       = _btn(self.patient_card, self.t.get('btn_new_exam', "📋 Nouvel examen"), C_PRIMARY, self._on_new_exam)
        self.btn_export         = _btn(self.patient_card, self.t.get('btn_export', "📤 Exporter"),   C_PRIMARY_DARK, self._on_export_patient)
        self.btn_gdpr_del       = _btn(self.patient_card, self.t.get('btn_gdpr_del', "🔴 Eff. RGPD"), C_RED,    self._on_gdpr_delete)

        btn_row_pat.Add(self.btn_edit_patient,   0, wx.RIGHT, 8)
        btn_row_pat.Add(self.btn_delete_patient, 0, wx.RIGHT, 8)
        btn_row_pat.Add(self.btn_export,         0, wx.RIGHT, 8)
        btn_row_pat.Add(self.btn_gdpr_del,       0, wx.RIGHT, 8)
        btn_row_pat.AddStretchSpacer()
        btn_row_pat.Add(self.btn_new_exam, 0)

        pc_s.Add(self.lbl_patient_name, 0, wx.ALL, 12)
        pc_s.Add(self.lbl_patient_info, 0, wx.LEFT | wx.BOTTOM, 12)
        pc_s.Add(btn_row_pat, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        self.patient_card.SetSizer(pc_s)
        right.Add(self.patient_card, 0, wx.EXPAND | wx.ALL, 10)

        right.Add(_label(self, self.t.get('lbl_hist', "  📁 Historique des examens  (double-clic = voir CRs)"), bold=True,
                         size=11, color=C_TEXT), 0, wx.LEFT | wx.BOTTOM, 8)

        self.exam_list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SIMPLE)
        self.exam_list.SetBackgroundColour(C_CARD)

        for i, (col, w) in enumerate([
            (self.t.get('col_acc', "N° Accession"), _S(130)),
            (self.t.get('col_date', "Date"), _S(100)),
            (self.t.get('col_mod', "Modalité"), _S(110)),
            (self.t.get('col_type', "Type"), _S(170)),
            (self.t.get('col_presc', "Prescripteur"), _S(130)),
            (self.t.get('col_rad', "Radiologue"), _S(130)),
            (self.t.get('col_statut', "Statut"), _S(90))
        ]):
            self.exam_list.InsertColumn(i, col, width=w)

        self.exam_list.Bind(wx.EVT_LIST_ITEM_SELECTED,   self._on_exam_selected)
        self.exam_list.Bind(wx.EVT_LIST_ITEM_ACTIVATED,  self._on_exam_dblclick)
        right.Add(self.exam_list, 2, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        right.Add(_label(self, self.t.get('lbl_cr_edit', "  📝 Compte rendu (édition rapide)"), bold=True,
                         size=11, color=C_TEXT), 0, wx.LEFT | wx.TOP, 10)
        self.cr_text = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_RICH2 | wx.BORDER_SIMPLE,
                                   size=(-1, _S(150)))
        self.cr_text.SetBackgroundColour(C_CARD)
        self.cr_text.SetFont(wx.Font(11, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                     wx.FONTWEIGHT_NORMAL, faceName="Segoe UI"))
        right.Add(self.cr_text, 1, wx.EXPAND | wx.ALL, 10)

        cr_btns = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_use_in_cr = _btn(self, self.t.get('btn_use_cr', "📤 Utiliser dans CR"), C_ACCENT, self._on_use_in_cr)
        cr_btns.Add(self.btn_use_in_cr, 0, wx.RIGHT, 10)
        cr_btns.AddStretchSpacer()
        cr_btns.Add(_btn(self, self.t.get('btn_save_cr', "💾 Sauvegarder CR"),   C_GREEN,   self._on_save_cr),    0, wx.RIGHT, 10)
        cr_btns.Add(_btn(self, self.t.get('btn_copy_cr', "📋 Copier CR"),        C_PRIMARY,  self._on_copy_cr),    0, wx.RIGHT, 10)
        cr_btns.Add(_btn(self, self.t.get('btn_view_crs', "📄 Voir tous les CRs"), C_ACCENT,  self._on_view_crs),   0, wx.RIGHT, 10)
        cr_btns.Add(_btn(self, self.t.get('btn_edit_exam', "✏ Modifier examen"), C_ACCENT,   self._on_edit_exam),  0, wx.RIGHT, 10)
        cr_btns.Add(_btn(self, self.t.get('btn_del_exam', "🗑 Supprimer examen"), C_RED,      self._on_delete_exam), 0, wx.RIGHT, 10)
        cr_btns.Add(_btn(self, self.t.get('btn_audit', "🔍 Audit"),              C_TEXT2,    self._on_view_audit), 0, wx.RIGHT, 10)
        cr_btns.Add(_btn(self, self.t.get('btn_chpw', "🔑 MDP"),                C_TEXT2,    self._on_change_password), 0)
        right.Add(cr_btns, 0, wx.RIGHT | wx.BOTTOM, 12)

        root.Add(right, 3, wx.EXPAND)
        self.SetSizer(root)
        self._set_patient_buttons_state(False)

    def _refresh_stats(self):
        s = self.db.get_stats()
        today_str = self.t.get('stats_today', "aujourd'hui")
        self.stats_lbl.SetLabel(
            f"  👥 {s.get('total_patients',0)} {self.t.get('stats_patients','patients')}  |  "
            f"📋 {s.get('total_examens',0)} {self.t.get('stats_examens','examens')}  |  "
            f"📅 {s.get('examens_today',0)} {today_str}"
        )

    def _load_recent_patients(self, query=""):
        self.patient_list.DeleteAllItems()
        self._pat_uuid_map = {}
        patients = self.db.search_patients(query) if query else self._get_all_patients()
        for p in patients:
            nb_ex = len(self.db.get_examens_patient(p["patient_uuid"]))
            idx = self.patient_list.InsertItem(self.patient_list.GetItemCount(),
                                               p.get("num_dossier", ""))
            self.patient_list.SetItem(idx, 1, p.get("nom", ""))
            self.patient_list.SetItem(idx, 2, p.get("prenom", ""))
            # ── DDN stockée en DD-MM-YYYY, affichée directement ──────────────
            self.patient_list.SetItem(idx, 3, p.get("date_naissance", ""))
            self.patient_list.SetItem(idx, 4, p.get("pays", ""))
            self.patient_list.SetItem(idx, 5, str(nb_ex))
            self._pat_uuid_map[idx] = p["patient_uuid"]

    def _get_all_patients(self, limit=500):
        """Charge tous les patients via PacsRisDB pour garantir le déchiffrement."""
        try:
            conn = self.db._connect()
            rows = conn.execute(
                "SELECT patient_uuid FROM patients ORDER BY updated_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
            conn.close()
            result = []
            for row in rows:
                p = self.db.get_patient_by_uuid(row["patient_uuid"])
                if p:
                    result.append(p)
            return result
        except Exception:
            return []

    def _load_examens(self, patient_uuid: str):
        self.exam_list.DeleteAllItems()
        self.cr_text.SetValue("")
        self._current_examen = None
        _PACS_STATE["examen"] = None
        self._exam_uuid_map = {}
        
        history_limit = self.lm.get_limit("history_days")
        limit_date = None
        if history_limit > 0:
            limit_date = datetime.date.today() - datetime.timedelta(days=history_limit)
            
        for ex in self.db.get_examens_patient(patient_uuid):
            if limit_date:
                date_str = ex.get("date_examen", "")
                try:
                    d, m, y = map(int, date_str.split('-'))
                    if datetime.date(y, m, d) < limit_date:
                        continue
                except Exception:
                    pass
            idx = self.exam_list.InsertItem(self.exam_list.GetItemCount(),
                                            ex.get("num_accession", ""))
            self.exam_list.SetItem(idx, 1, ex.get("date_examen", ""))
            self.exam_list.SetItem(idx, 2, translate_modalite(ex.get("modalite", ""), self.lang))
            self.exam_list.SetItem(idx, 3, ex.get("type_examen", ""))
            self.exam_list.SetItem(idx, 4, ex.get("medecin_prescripteur", ""))
            self.exam_list.SetItem(idx, 5, ex.get("medecin", ""))
            self.exam_list.SetItem(idx, 6, translate_statut(ex.get("statut", ""), self.t))
            self._exam_uuid_map[idx] = ex["examen_uuid"]

    def _on_search(self, _):
        self._load_recent_patients(self.search_ctrl.GetValue())

    def _on_patient_selected(self, event):
        idx = event.GetIndex()
        uid = self._pat_uuid_map.get(idx)
        if not uid:
            return
        self._current_patient = self.db.get_patient_by_uuid(uid)
        _PACS_STATE["patient"] = self._current_patient
        _PACS_STATE["examen"]  = None
        if self._current_patient:
            p = self._current_patient
            self.lbl_patient_name.SetLabel(
                f"👤 {p.get('prenom','')} {p.get('nom','')}  —  N° {p.get('num_dossier','')}"
            )
            infos = []
            # ── DDN affichée directement en DD-MM-YYYY ───────────────────────
            if p.get("date_naissance"): infos.append(f"DDN: {p['date_naissance']}")
            if p.get("sexe"):           infos.append(translate_sexe(p["sexe"], self.t))
            if p.get("pays"):           infos.append(f"🌍 {p['pays']}")
            if p.get("cin"):            infos.append(f"CIN: {p['cin']}")
            if p.get("telephone"):      infos.append(f"☎ {p['telephone']}")
            self.lbl_patient_info.SetLabel("   " + "  |  ".join(infos))
            self.patient_card.Layout()
            self._set_patient_buttons_state(True)
            self._load_examens(uid)

    def _on_exam_selected(self, event):
        idx = event.GetIndex()
        uid = self._exam_uuid_map.get(idx)
        if not uid:
            return
        self._current_examen = self.db.get_examen_by_uuid(uid)
        _PACS_STATE["examen"] = self._current_examen
        cr = self.db.get_last_compte_rendu(uid)
        self.cr_text.SetValue(cr["contenu"] if cr else "")

    def _on_exam_dblclick(self, event):
        idx = event.GetIndex()
        uid = self._exam_uuid_map.get(idx)
        if not uid or not self._current_patient:
            return
        examen = self.db.get_examen_by_uuid(uid)
        if examen:
            dlg = CRViewerDialog(
                self, self.db, examen, self._current_patient,
                on_open_in_pisum=self.on_cr_open_in_pisum, t=self.t
            )
            dlg.ShowModal()
            dlg.Destroy()

    def _on_new_patient(self, _):
        can_add, msg = self.lm.can_add_patient()
        if not can_add:
            wx.MessageBox(msg, "Limite atteinte", wx.OK | wx.ICON_WARNING, self)
            return
            
        dlg = NewPatientDialog(self, self.db, self.t)
        if dlg.ShowModal() == wx.ID_OK and dlg.patient_uuid:
            self.lm.increment_patient_count()
            self._load_recent_patients(self.search_ctrl.GetValue())
            self._refresh_stats()
        dlg.Destroy()

    def _on_edit_patient(self, _):
        if not self._current_patient:
            return
        p = self._current_patient
        dlg = NewPatientDialog(self, self.db, self.t,
                               edit_mode=True,
                               patient_uuid_to_edit=p["patient_uuid"])
        dlg.f_nom.SetValue(p.get("nom", ""))
        dlg.f_prenom.SetValue(p.get("prenom", ""))
        # ── DDN affichée directement en DD-MM-YYYY ───────────────────────────
        dlg.f_ddn.SetValue(p.get("date_naissance", ""))

        try:
            _raw = dlg._sexe_raw
        except AttributeError:
            _raw = ['', 'M', 'F', 'Autre']

        if p.get("sexe") in _raw:
            dlg.f_sexe.SetSelection(_raw.index(p["sexe"]))

        dlg.f_num.SetValue(p.get("num_dossier", ""))
        dlg.f_cin.SetValue(p.get("cin", ""))
        dlg.f_tel.SetValue(p.get("telephone", ""))
        dlg.f_pays.SetValue(p.get("pays", ""))
        dlg.f_adresse.SetValue(p.get("adresse", ""))
        dlg.f_rem.SetValue(p.get("remarques", ""))

        if dlg.ShowModal() == wx.ID_OK:
            self._current_patient = self.db.get_patient_by_uuid(p["patient_uuid"])
            self._load_recent_patients(self.search_ctrl.GetValue())
            if self._current_patient:
                up = self._current_patient
                self.lbl_patient_name.SetLabel(
                    f"👤 {up.get('prenom','')} {up.get('nom','')}  —  N° {up.get('num_dossier','')}"
                )
        dlg.Destroy()

    def _on_delete_patient(self, _):
        if not self._current_patient:
            return
        p = self._current_patient
        ans = wx.MessageBox(
            self.t.get('confirm_del_patient', "Supprimer le dossier et tous ses examens ?\nCette action est irréversible."),
            self.t.get('confirm_del_patient_title', "Confirmer"), wx.YES_NO | wx.ICON_WARNING, self
        )
        if ans == wx.YES:
            self.db.delete_patient(p["patient_uuid"])
            self._current_patient = None
            self._current_examen  = None
            self.exam_list.DeleteAllItems()
            self.cr_text.SetValue("")
            self.lbl_patient_name.SetLabel(self.t.get('lbl_select_pat', "Sélectionner un patient"))
            self.lbl_patient_info.SetLabel("")
            self._set_patient_buttons_state(False)
            self._load_recent_patients(self.search_ctrl.GetValue())
            self._refresh_stats()

    def _on_new_exam(self, _):
        if not self._current_patient:
            return
        dlg = NewExamenDialog(self, self.db, self._current_patient, self.prefill, self.t)
        if dlg.ShowModal() == wx.ID_OK and dlg.examen_uuid:
            self._current_examen = self.db.get_examen_by_uuid(dlg.examen_uuid)
            self._load_examens(self._current_patient["patient_uuid"])
            self._refresh_stats()
        dlg.Destroy()

    def _on_save_cr(self, _):
        if not self._current_examen:
            return
        contenu = self.cr_text.GetValue().strip()
        if not contenu:
            return
        cr_uuid = self.db.save_compte_rendu(self._current_examen["examen_uuid"], contenu)
        if cr_uuid:
            self._load_examens(self._current_patient["patient_uuid"])

    def _on_copy_cr(self, _):
        contenu = self.cr_text.GetValue()
        if contenu and wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(contenu))
            wx.TheClipboard.Close()

    def _on_view_crs(self, _):
        if not self._current_examen or not self._current_patient:
            return
        dlg = CRViewerDialog(
            self, self.db, self._current_examen, self._current_patient,
            on_open_in_pisum=self.on_cr_open_in_pisum, t=self.t
        )
        dlg.ShowModal()
        dlg.Destroy()

    def _on_delete_exam(self, _):
        if not self._current_examen:
            return
        ans = wx.MessageBox(self.t.get('confirm_del_exam', "Supprimer cet examen et tous ses comptes rendus ?"),
                            self.t.get('confirm_del_exam_title', "Confirmer"), wx.YES_NO | wx.ICON_WARNING, self)
        if ans == wx.YES:
            self.db.delete_examen(self._current_examen["examen_uuid"])
            self._current_examen = None
            self._load_examens(self._current_patient["patient_uuid"])
            self._refresh_stats()

    def _on_edit_exam(self, _):
        if not self._current_examen:
            wx.MessageBox(
                self.t.get("no_exam_selected", "Sélectionnez un examen dans la liste."),
                self.t.get("no_exam_title", "Sélection manquante"),
                wx.OK | wx.ICON_WARNING, self
            )
            return
        dlg = EditExamenDialog(self, self.db, self._current_examen, self.t)
        if dlg.ShowModal() == wx.ID_OK:
            self._current_examen = self.db.get_examen_by_uuid(
                self._current_examen["examen_uuid"])
            self._load_examens(self._current_patient["patient_uuid"])
        dlg.Destroy()

    def _on_use_in_cr(self, _):
        patient = _PACS_STATE.get("patient") or getattr(self, '_current_patient', None)
        examen  = _PACS_STATE.get("examen")  or getattr(self, '_current_examen',  None)

        if not patient or not examen:
            wx.MessageBox(
                self.t.get("no_exam_selected", "Sélectionnez un patient et un examen dans la liste."),
                self.t.get("no_exam_title", "Sélection manquante"),
                wx.OK | wx.ICON_WARNING, self
            )
            return

        contenu_cr = ""
        try:
            cr = self.db.get_last_compte_rendu(examen.get("examen_uuid", ""))
            if cr:
                contenu_cr = cr.get("contenu", "") or ""
        except Exception as _e:
            logger.warning(f"[PACS] get_last_compte_rendu: {_e}")
            
        if not contenu_cr:
            try:
                txt = getattr(self, 'cr_text', None)
                if txt:
                    contenu_cr = txt.GetValue().strip()
            except Exception:
                pass

        cr_lang = examen.get("langue") or self.lang
        examen_payload = dict(examen)
        examen_payload["modalite_display"]    = translate_modalite(examen.get("modalite", ""), cr_lang)
        examen_payload["referring_physician"] = examen.get("medecin_prescripteur", "") or ""
        examen_payload["radiologist"]         = examen.get("medecin", "") or ""

        # --- NOUVEAU BLOC SANS pacs_signal_write ---
        try:
            callback = getattr(self, 'on_cr_open_in_pisum', None)
            if callback:
                callback(dict(patient), examen_payload, contenu_cr)
                logger.info(f"[PACS] _on_use_in_cr success cr={len(contenu_cr)} chars")
            else:
                logger.warning("[PACS] Callback on_cr_open_in_pisum non défini.")
        except Exception as _e:
            logger.error("[PACS] _on_use_in_cr FAILED: %s", _e)
            return
        # -------------------------------------------

        prenom = patient.get("prenom", "")
        nom    = patient.get("nom", "")
        mod    = translate_modalite(examen.get("modalite", ""), self.lang)
        typex  = examen.get("type_examen", "")
        wx.MessageBox(
            f"{self.t.get('use_cr_ok_title', 'Chargé dans CR')} — {prenom} {nom}  |  {mod} {typex}",
            self.t.get("use_cr_ok_title", "Chargé dans CR"),
            wx.OK | wx.ICON_INFORMATION, self
        )

    def _check_auth(self):
        """Affiche le dialog d'authentification. Ferme le PACS si annulé."""
        dlg = PacsAuthDialog(self, self.db, self.t)
        result = dlg.ShowModal()
        dlg.Destroy()
        if result != wx.ID_OK:
            self.Close()

    def _on_export_patient(self, _):
        if not self._current_patient:
            return
        p = self._current_patient
        nom = p.get("nom", "patient").replace(" ", "_")
        with wx.FileDialog(
            self, self.t.get("export_title", "Exporter le dossier patient"),
            wildcard="Fichier texte (*.txt)|*.txt|JSON (*.json)|*.json",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT
        ) as dlg:
            dlg.SetFilename(f"PISUM_{nom}_{datetime.date.today()}")
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = dlg.GetPath()
            idx  = dlg.GetFilterIndex()
        ok = (self.db.export_patient_txt(p["patient_uuid"], path)
              if idx == 0 else
              self.db.export_patient_json(p["patient_uuid"], path))
        if ok:
            wx.MessageBox(
                self.t.get("export_ok", f"✅ Dossier exporté :\n{path}"),
                self.t.get("export_ok_title", "Export réussi"),
                wx.OK | wx.ICON_INFORMATION, self
            )
        else:
            wx.MessageBox(
                self.t.get("export_err", "❌ Erreur lors de l'export."),
                "Erreur", wx.OK | wx.ICON_ERROR, self
            )

    def _on_gdpr_delete(self, _):
        if not self._current_patient:
            return
        dlg = PacsGdprDeleteDialog(self, self._current_patient, self.t)
        if dlg.ShowModal() == wx.ID_OK:
            ok = self.db.delete_patient_gdpr(
                self._current_patient["patient_uuid"],
                confirmed_nom=dlg.confirmed_nom
            )
            if ok:
                self._current_patient = None
                self._current_examen  = None
                self.exam_list.DeleteAllItems()
                self.cr_text.SetValue("")
                self.lbl_patient_name.SetLabel(
                    self.t.get('lbl_select_pat', "Sélectionner un patient"))
                self.lbl_patient_info.SetLabel("")
                self._set_patient_buttons_state(False)
                self._load_recent_patients(self.search_ctrl.GetValue())
                self._refresh_stats()
                wx.MessageBox(
                    self.t.get("gdpr_del_ok", "✅ Dossier supprimé définitivement."),
                    "RGPD", wx.OK | wx.ICON_INFORMATION, self
                )
            else:
                wx.MessageBox(
                    self.t.get("gdpr_del_err",
                               "❌ Suppression annulée.\nLe nom saisi ne correspond pas."),
                    "Erreur", wx.OK | wx.ICON_ERROR, self
                )
        dlg.Destroy()

    def _on_view_audit(self, _):
        dlg = PacsAuditDialog(self, self.db, self._current_patient, self.t)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_change_password(self, _):
        dlg = PacsChangePasswordDialog(self, self.t)
        if dlg.ShowModal() == wx.ID_OK:
            pw = dlg.new_password
            self.db.set_password(pw)
            wx.MessageBox(
                self.t.get("chpw_ok", "✅ Mot de passe modifié."),
                "OK", wx.OK | wx.ICON_INFORMATION, self
            )
        dlg.Destroy()

    def _set_patient_buttons_state(self, enabled: bool):
        for btn in [self.btn_edit_patient, self.btn_delete_patient,
                    self.btn_new_exam, self.btn_export, self.btn_gdpr_del]:
            btn.Enable(enabled)
#  FONCTIONS D'INTÉGRATION publiques
# ══════════════════════════════════════════════════════════════════════════════
def open_pacs_ris(parent, prefill=None, on_exam_loaded=None, on_cr_open_in_pisum=None):
    """Ouvre PacsRisFrame et garde la référence (anti garbage-collector)."""
    frame = PacsRisFrame(parent, prefill,
                         on_exam_loaded=on_exam_loaded,
                         on_cr_open_in_pisum=on_cr_open_in_pisum)
    if parent is not None:
        parent._pacs_frame_ref = frame
    return frame


def save_cr_for_current_exam(examen_uuid, contenu, modalite=None, type_examen=None, formula_name=None):
    db = get_pacs_db()
    if modalite is not None or type_examen is not None or formula_name is not None:
        ex = db.get_examen_by_uuid(examen_uuid)
        if ex:
            db.update_examen(
                examen_uuid=examen_uuid,
                modalite=modalite if modalite is not None else ex.get("modalite", ""),
                type_examen=type_examen if type_examen is not None else ex.get("type_examen", ""),
                formula_name=formula_name if formula_name is not None else ex.get("formula_name", ""),
                date_examen=ex.get("date_examen", ""),
                indication=ex.get("indication", ""),
                medecin_prescripteur=ex.get("medecin_prescripteur", ""),
                medecin=ex.get("medecin", ""),
                etablissement=ex.get("etablissement", ""),
                langue=ex.get("langue", "")
            )
    return db.save_compte_rendu(examen_uuid, contenu) is not None

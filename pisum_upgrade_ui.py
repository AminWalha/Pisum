# -*- coding: utf-8 -*-
"""
PISUM — Conversion-Optimized Upgrade UI  v2.0
wxPython components:
  - LicenseTopBar      → always-visible bar (user + plan + upgrade button)
  - UsageDashboard     → live usage stats with progress bars
  - LockedButton       → greyed button that triggers upgrade popup
  - UpgradePopup       → contextual upgrade dialog
  - PlanComparisonDlg  → full plan comparison with "Recommended" highlight
  - ActivationDialog   → license key entry screen
  - SmartNotifier      → banner notification widget

Usage:
    from pisum_upgrade_ui import LicenseTopBar, LockedButton, UpgradePopup
    bar = LicenseTopBar(parent_panel, license_manager)
"""

import wx
import wx.lib.agw.gradientbutton as GB
import webbrowser
from pisum_license_manager import LicenseManager

# ── Brand colours ──────────────────────────────────────
COLOR_BG_DARK    = "#1A1F2E"
COLOR_BG_PANEL   = "#252B3B"
COLOR_ACCENT     = "#4F8EF7"    # blue
COLOR_UPGRADE    = "#F5A623"    # amber — draws the eye
COLOR_DANGER     = "#E53E3E"
COLOR_SUCCESS    = "#38A169"
COLOR_TEXT       = "#FFFFFF"
COLOR_SUBTEXT    = "#A0AEC0"
COLOR_LOCKED     = "#4A5568"
COLOR_FREE_BADGE = "#718096"
COLOR_SOLO_BADGE = "#3182CE"
COLOR_PRO_BADGE  = "#805AD5"
COLOR_CLINIC_BADGE = "#00B5D8"

PLAN_BADGE_COLORS = {
    "free":   COLOR_FREE_BADGE,
    "solo":   COLOR_SOLO_BADGE,
    "pro":    COLOR_PRO_BADGE,
    "clinic": COLOR_CLINIC_BADGE,
}

# Marketing configuration for comparison UI (decoupled from the real licensing logic)
MARKETING_PLANS = {
    "free": {
        "display_name": "FREE", "price_label": "Gratuit",
        "max_reports_per_day": 10, "max_patients_per_day": 20, "ai_dictation_minutes_per_day": 10,
        "custom_templates": False, "export_word_quality": "basic", "pacs_ris": False, "printing": False
    },
    "solo": {
        "display_name": "SOLO", "price_label": "29 €/mois",
        "max_reports_per_day": -1, "max_patients_per_day": 200, "ai_dictation_minutes_per_day": 60,
        "custom_templates": True, "export_word_quality": "premium", "pacs_ris": True, "printing": True
    },
    "pro": {
        "display_name": "PRO", "price_label": "59 €/mois",
        "max_reports_per_day": -1, "max_patients_per_day": -1, "ai_dictation_minutes_per_day": -1,
        "custom_templates": True, "export_word_quality": "premium", "pacs_ris": True, "printing": True
    },
    "clinic": {
        "display_name": "CLINIC", "price_label": "149 €/mois",
        "max_reports_per_day": -1, "max_patients_per_day": -1, "ai_dictation_minutes_per_day": -1,
        "custom_templates": True, "export_word_quality": "premium", "pacs_ris": True, "printing": True
    },
}

# Replace with your real upgrade URL
UPGRADE_URL = "https://pisum.app/upgrade"


def _hex(color_str: str) -> wx.Colour:
    c = color_str.lstrip("#")
    return wx.Colour(int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))


def _bold(size: int = 10) -> wx.Font:
    return wx.Font(size, wx.FONTFAMILY_DEFAULT,
                   wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)


def _regular(size: int = 10) -> wx.Font:
    return wx.Font(size, wx.FONTFAMILY_DEFAULT,
                   wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)


# ════════════════════════════════════════════════════════════
#  LICENSE TOP BAR
# ════════════════════════════════════════════════════════════
class LicenseTopBar(wx.Panel):
    """
    Slim bar to embed at the top of your main window.
    Shows:  [👤 Dr. Ahmed]  [PRO]  [Rapports: 0 / ∞]  [⬆ Upgrade]
    """

    def __init__(self, parent: wx.Window, lm: LicenseManager):
        super().__init__(parent, style=wx.BORDER_NONE)
        self.lm = lm
        self.SetBackgroundColour(_hex(COLOR_BG_DARK))
        self._build()
        self._refresh_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_timer, self._refresh_timer)
        self._refresh_timer.Start(60_000)   # refresh every minute

    def _build(self) -> None:
        sizer = wx.BoxSizer(wx.HORIZONTAL)
        sizer.AddSpacer(12)

        # ── User name ────────────────────────────────────
        self._lbl_user = wx.StaticText(self, label="")
        self._lbl_user.SetFont(_bold(10))
        self._lbl_user.SetForegroundColour(_hex(COLOR_TEXT))
        sizer.Add(self._lbl_user, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)

        # ── Plan badge ───────────────────────────────────
        self._badge = wx.StaticText(self, label="")
        self._badge.SetFont(_bold(9))
        sizer.Add(self._badge, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 16)

        # ── Usage label ──────────────────────────────────
        self._lbl_usage = wx.StaticText(self, label="")
        self._lbl_usage.SetFont(_regular(9))
        self._lbl_usage.SetForegroundColour(_hex(COLOR_SUBTEXT))
        sizer.Add(self._lbl_usage, 0, wx.ALIGN_CENTER_VERTICAL)

        sizer.AddStretchSpacer()

        # ── Days until expiry ────────────────────────────
        self._lbl_expiry = wx.StaticText(self, label="")
        self._lbl_expiry.SetFont(_regular(9))
        self._lbl_expiry.SetForegroundColour(_hex(COLOR_SUBTEXT))
        sizer.Add(self._lbl_expiry, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 16)

        # ── Upgrade button (hidden for clinic/pro) ───────
        self._btn_upgrade = wx.Button(self, label="⬆ Passer à PRO", size=(140, 28))
        self._btn_upgrade.SetFont(_bold(9))
        self._btn_upgrade.SetBackgroundColour(_hex(COLOR_UPGRADE))
        self._btn_upgrade.SetForegroundColour(_hex("#1A1F2E"))
        self._btn_upgrade.Bind(wx.EVT_BUTTON, self._on_upgrade)
        sizer.Add(self._btn_upgrade, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)

        self.SetSizer(sizer)
        self.SetMinSize((-1, 42))
        self.refresh()

    def refresh(self) -> None:
        """Call after any license change to update all labels."""
        lm   = self.lm
        plan_name = lm.get_plan_name()
        display_name = MARKETING_PLANS.get(plan_name, MARKETING_PLANS["free"])["display_name"]

        # User + plan
        self._lbl_user.SetLabel(f"👤  {lm.user_name}")
        self._badge.SetLabel(f" {display_name} ")
        badge_color = PLAN_BADGE_COLORS.get(plan_name, COLOR_FREE_BADGE)
        self._badge.SetBackgroundColour(_hex(badge_color))
        self._badge.SetForegroundColour(_hex(COLOR_TEXT))

        # Usage
        rl = lm.get_limit("max_reports_per_day")
        if rl == -1:
            usage_txt = "Rapports : ∞"
        else:
            usage_txt = f"Rapports aujourd'hui : {lm.reports_used} / {rl}"
        self._lbl_usage.SetLabel(usage_txt)

        # Expiry
        days = lm.days_until_expiry()
        if days is not None:
            if days <= 7:
                self._lbl_expiry.SetLabel(f"⚠ Expire dans {days}j")
                self._lbl_expiry.SetForegroundColour(_hex(COLOR_DANGER))
            else:
                self._lbl_expiry.SetLabel(f"Expire dans {days}j")
                self._lbl_expiry.SetForegroundColour(_hex(COLOR_SUBTEXT))
        else:
            self._lbl_expiry.SetLabel("")

        # Show upgrade button only for free / solo
        show_upgrade = plan_name in ("free", "solo")
        self._btn_upgrade.Show(show_upgrade)
        if plan_name == "solo":
            self._btn_upgrade.SetLabel("⬆ Passer à PRO")
        elif plan_name == "free":
            self._btn_upgrade.SetLabel("⬆ Upgrade")

        self.Layout()

    def _on_timer(self, _evt) -> None:
        self.refresh()

    def _on_upgrade(self, _evt) -> None:
        dlg = PlanComparisonDlg(self, self.lm)
        dlg.ShowModal()
        dlg.Destroy()
        self.refresh()


# ════════════════════════════════════════════════════════════
#  USAGE DASHBOARD PANEL
# ════════════════════════════════════════════════════════════
class UsageDashboard(wx.Panel):
    """
    Embeddable panel showing usage bars for reports, patients, dictation.
    Designed to create mild frustration on FREE plan.
    """

    def __init__(self, parent: wx.Window, lm: LicenseManager):
        super().__init__(parent, style=wx.BORDER_NONE)
        self.lm = lm
        self.SetBackgroundColour(_hex(COLOR_BG_PANEL))
        self._build()

    def _build(self) -> None:
        main = wx.BoxSizer(wx.VERTICAL)

        title = wx.StaticText(self, label="Utilisation du compte")
        title.SetFont(_bold(11))
        title.SetForegroundColour(_hex(COLOR_TEXT))
        main.Add(title, 0, wx.ALL, 12)

        self._rows: list[tuple] = []   # (label_widget, bar_widget, pct_label)

        for field, label in [
            ("reports",   "Comptes rendus aujourd'hui"),
            ("patients",  "Patients aujourd'hui"),
            ("dictation", "Dictée aujourd'hui"),
        ]:
            row = self._make_stat_row(label, field)
            main.Add(row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        # Upgrade CTA if on free plan
        self._cta_panel = self._make_cta()
        main.Add(self._cta_panel, 0, wx.EXPAND | wx.ALL, 10)

        self.SetSizer(main)
        self.refresh()

    def _make_stat_row(self, label: str, field: str) -> wx.Panel:
        panel = wx.Panel(self, style=wx.BORDER_NONE)
        panel.SetBackgroundColour(_hex(COLOR_BG_PANEL))
        vs = wx.BoxSizer(wx.VERTICAL)

        hs = wx.BoxSizer(wx.HORIZONTAL)
        lbl = wx.StaticText(panel, label=label)
        lbl.SetFont(_regular(9))
        lbl.SetForegroundColour(_hex(COLOR_TEXT))
        hs.Add(lbl, 1)
        pct = wx.StaticText(panel, label="—")
        pct.SetFont(_bold(9))
        pct.SetForegroundColour(_hex(COLOR_SUBTEXT))
        hs.Add(pct, 0)
        vs.Add(hs, 0, wx.EXPAND | wx.BOTTOM, 3)

        bar = wx.Gauge(panel, range=100, size=(-1, 8),
                       style=wx.GA_HORIZONTAL | wx.GA_SMOOTH)
        bar.SetBackgroundColour(_hex(COLOR_BG_DARK))
        vs.Add(bar, 0, wx.EXPAND)

        panel.SetSizer(vs)
        self._rows.append((field, lbl, bar, pct))
        return panel

    def _make_cta(self) -> wx.Panel:
        panel = wx.Panel(self, style=wx.BORDER_NONE)
        panel.SetBackgroundColour(_hex(COLOR_BG_DARK))
        hs = wx.BoxSizer(wx.HORIZONTAL)

        icon = wx.StaticText(panel, label="⚡")
        icon.SetFont(_bold(13))
        icon.SetForegroundColour(_hex(COLOR_UPGRADE))
        hs.Add(icon, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)

        txt = wx.StaticText(panel,
            label="Passez à PRO — Comptes rendus illimités, 0 filigrane, PDF professionnel.")
        txt.SetFont(_regular(9))
        txt.SetForegroundColour(_hex(COLOR_SUBTEXT))
        txt.Wrap(300)
        hs.Add(txt, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)

        btn = wx.Button(panel, label="Voir les offres", size=(120, 28))
        btn.SetFont(_bold(9))
        btn.SetBackgroundColour(_hex(COLOR_UPGRADE))
        btn.SetForegroundColour(_hex("#1A1F2E"))
        btn.Bind(wx.EVT_BUTTON, self._on_see_plans)
        hs.Add(btn, 0, wx.ALIGN_CENTER_VERTICAL)

        panel.SetSizer(hs)
        return panel

    def refresh(self) -> None:
        lm   = self.lm

        data = {
            "reports":   (lm.reports_used,             lm.get_limit("max_reports_per_day"),           "CRs"),
            "patients":  (lm.patients_count,           lm.get_limit("max_patients_per_day"),          "patients"),
            "dictation": (lm._dictation_used_today,    lm.get_limit("ai_dictation_minutes_per_day"),  "min"),
        }

        for (field, lbl_widget, bar, pct_lbl) in self._rows:
            used, limit, unit = data.get(field, (0, 1, ""))
            if limit == -1:
                bar.SetValue(0)
                pct_lbl.SetLabel("∞")
                bar.SetForegroundColour(_hex(COLOR_SUCCESS))
            elif limit == 0:
                bar.SetValue(0)
                pct_lbl.SetLabel("Non disponible")
                bar.SetForegroundColour(_hex(COLOR_LOCKED))
            else:
                pct = min(100, int(used / limit * 100))
                bar.SetValue(pct)
                pct_lbl.SetLabel(f"{used} / {limit} {unit}")
                if pct >= 90:
                    bar.SetForegroundColour(_hex(COLOR_DANGER))
                elif pct >= 70:
                    bar.SetForegroundColour(_hex(COLOR_UPGRADE))
                else:
                    bar.SetForegroundColour(_hex(COLOR_ACCENT))

        # Show CTA only for free/solo
        self._cta_panel.Show(lm.get_plan_name() in ("free", "solo"))
        self.Layout()

    def _on_see_plans(self, _evt) -> None:
        parent = wx.GetTopLevelParent(self)
        dlg = PlanComparisonDlg(parent, self.lm)
        dlg.ShowModal()
        dlg.Destroy()


# ════════════════════════════════════════════════════════════
#  LOCKED BUTTON
# ════════════════════════════════════════════════════════════
class LockedButton(wx.Button):
    """
    A button that appears disabled with a lock icon.
    Clicking it shows an upgrade popup instead of performing the action.

    Usage:
        btn = LockedButton(parent, lm, label="Modèles avancés",
                           target_plan="pro",
                           feature_description="Accès à tous les modèles de radiologie")
    """

    def __init__(self, parent, lm: LicenseManager,
                 label: str = "",
                 feature_name: str = "",
                 target_plan: str = "pro",
                 feature_description: str = "",
                 **kwargs):
        super().__init__(parent, label=f"🔒 {label}", **kwargs)
        self.lm                  = lm
        self.feature_name        = feature_name
        self.target_plan         = target_plan
        self.feature_description = feature_description or label

        self.SetFont(_regular(9))
        self.SetBackgroundColour(_hex(COLOR_LOCKED))
        self.SetForegroundColour(_hex(COLOR_SUBTEXT))

        self.Bind(wx.EVT_BUTTON, self._on_click)

    def _on_click(self, _evt) -> None:
        show_upgrade_popup(
            parent=wx.GetTopLevelParent(self),
            lm=self.lm,
            feature=self.feature_description,
            target_plan=self.target_plan,
        )


# ════════════════════════════════════════════════════════════
#  UPGRADE POPUP  (standalone function + dialog)
# ════════════════════════════════════════════════════════════
def show_upgrade_popup(parent: wx.Window,
                       lm: LicenseManager,
                       feature: str = "",
                       target_plan: str = "pro",
                       message: str = "") -> None:
    """
    Show a contextual upgrade popup.
    Call this anywhere a limit is reached or a locked feature is clicked.

    Example:
        ok, msg = lm.can_create_report()
        if not ok:
            show_upgrade_popup(self, lm, feature="Comptes rendus", message=msg)
            return
    """
    dlg = UpgradePopup(parent, lm, feature, target_plan, message)
    dlg.ShowModal()
    dlg.Destroy()


class UpgradePopup(wx.Dialog):
    """Contextual upgrade dialog — conversion-optimised."""

    def __init__(self, parent: wx.Window, lm: LicenseManager,
                 feature: str = "",
                 target_plan: str = "pro",
                 message: str = ""):
        super().__init__(parent, title="", style=wx.BORDER_NONE | wx.FRAME_SHAPED,
                         size=(460, 320))
        self.lm = lm
        self.CenterOnParent()
        self.SetBackgroundColour(_hex(COLOR_BG_PANEL))
        self._build(feature, target_plan, message)

    def _build(self, feature: str, target_plan: str, message: str) -> None:
        plan_data  = MARKETING_PLANS.get(target_plan, MARKETING_PLANS["pro"])
        plan_color = PLAN_BADGE_COLORS.get(target_plan, COLOR_PRO_BADGE)

        vs = wx.BoxSizer(wx.VERTICAL)

        # ── Header strip ─────────────────────────────────
        header = wx.Panel(self, style=wx.BORDER_NONE, size=(-1, 48))
        header.SetBackgroundColour(_hex(plan_color))
        hs = wx.BoxSizer(wx.HORIZONTAL)
        hs.AddSpacer(16)
        lbl = wx.StaticText(header,
            label=f"Fonctionnalité {plan_data['display_name']}")
        lbl.SetFont(_bold(12))
        lbl.SetForegroundColour(_hex(COLOR_TEXT))
        hs.Add(lbl, 0, wx.ALIGN_CENTER_VERTICAL)
        header.SetSizer(hs)
        vs.Add(header, 0, wx.EXPAND)

        inner = wx.BoxSizer(wx.VERTICAL)
        inner.AddSpacer(16)

        # ── Lock icon + feature name ──────────────────────
        if feature:
            feat_lbl = wx.StaticText(self, label=f"🔒  {feature}")
            feat_lbl.SetFont(_bold(11))
            feat_lbl.SetForegroundColour(_hex(COLOR_TEXT))
            inner.Add(feat_lbl, 0, wx.LEFT | wx.RIGHT, 16)
            inner.AddSpacer(8)

        # ── Message ───────────────────────────────────────
        body = message or (
            f"Cette fonctionnalité est disponible à partir du plan "
            f"«\u202f{plan_data['display_name']}\u202f».\n"
            "Passez à la version supérieure pour en profiter maintenant."
        )
        msg_lbl = wx.StaticText(self, label=body)
        msg_lbl.SetFont(_regular(10))
        msg_lbl.SetForegroundColour(_hex(COLOR_SUBTEXT))
        msg_lbl.Wrap(420)
        inner.Add(msg_lbl, 0, wx.LEFT | wx.RIGHT, 16)
        inner.AddSpacer(12)

        # ── Price anchor ──────────────────────────────────
        price_txt = f"{plan_data['display_name']}  —  {plan_data['price_label']}"
        price_lbl = wx.StaticText(self, label=price_txt)
        price_lbl.SetFont(_bold(11))
        price_lbl.SetForegroundColour(_hex(plan_color))
        inner.Add(price_lbl, 0, wx.LEFT, 16)
        inner.AddSpacer(16)

        # ── Buttons ───────────────────────────────────────
        hs2 = wx.BoxSizer(wx.HORIZONTAL)
        hs2.AddSpacer(16)

        btn_close = wx.Button(self, label="Plus tard", size=(110, 34))
        btn_close.SetFont(_regular(9))
        btn_close.SetBackgroundColour(_hex(COLOR_BG_DARK))
        btn_close.SetForegroundColour(_hex(COLOR_SUBTEXT))
        btn_close.Bind(wx.EVT_BUTTON, lambda _: self.EndModal(wx.ID_CANCEL))
        hs2.Add(btn_close, 0, wx.RIGHT, 8)

        btn_compare = wx.Button(self, label="Comparer les offres", size=(160, 34))
        btn_compare.SetFont(_regular(9))
        btn_compare.SetBackgroundColour(_hex(COLOR_BG_DARK))
        btn_compare.SetForegroundColour(_hex(COLOR_ACCENT))
        btn_compare.Bind(wx.EVT_BUTTON, self._on_compare)
        hs2.Add(btn_compare, 0, wx.RIGHT, 8)

        btn_upgrade = wx.Button(self,
            label=f"⬆ Passer à {plan_data['display_name']}  →", size=(190, 34))
        btn_upgrade.SetFont(_bold(10))
        btn_upgrade.SetBackgroundColour(_hex(COLOR_UPGRADE))
        btn_upgrade.SetForegroundColour(_hex("#1A1F2E"))
        btn_upgrade.Bind(wx.EVT_BUTTON, self._on_upgrade)
        hs2.Add(btn_upgrade, 0)

        inner.Add(hs2, 0, wx.EXPAND)
        vs.Add(inner, 1, wx.EXPAND)

        self.SetSizer(vs)
        self._target_plan = target_plan

    def _on_upgrade(self, _evt) -> None:
        webbrowser.open(UPGRADE_URL)
        self.EndModal(wx.ID_OK)

    def _on_compare(self, _evt) -> None:
        self.EndModal(wx.ID_CANCEL)
        dlg = PlanComparisonDlg(wx.GetTopLevelParent(self), self.lm)
        dlg.ShowModal()
        dlg.Destroy()


# ════════════════════════════════════════════════════════════
#  PLAN COMPARISON DIALOG
# ════════════════════════════════════════════════════════════
class PlanComparisonDlg(wx.Dialog):
    """
    Full plan comparison table with PRO highlighted as "Recommended".
    Price anchoring included.
    """

    _FEATURES = [
        ("Comptes rendus / jour",        "max_reports_per_day"),
        ("Patients / jour",              "max_patients_per_day"),
        ("Dictée vocale / jour",         "ai_dictation_minutes_per_day"),
        ("Modèles personnalisés",        "custom_templates"),
        ("Qualité Word Export",          "export_word_quality"),
        ("PACS / RIS local",             "pacs_ris"),
        ("Impression directe",           "printing"),
    ]

    def __init__(self, parent: wx.Window, lm: LicenseManager):
        super().__init__(parent, title="Choisir votre plan PISUM",
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         size=(760, 540))
        self.lm = lm
        self.CenterOnParent()
        self.SetBackgroundColour(_hex(COLOR_BG_DARK))
        self._build()

    def _build(self) -> None:
        vs = wx.BoxSizer(wx.VERTICAL)

        # ── Title ─────────────────────────────────────────
        title = wx.StaticText(self, label="Comparez les offres PISUM")
        title.SetFont(_bold(14))
        title.SetForegroundColour(_hex(COLOR_TEXT))
        vs.Add(title, 0, wx.ALL | wx.ALIGN_CENTER, 14)

        sub = wx.StaticText(self,
            label="Choisissez le plan qui correspond à votre pratique.")
        sub.SetFont(_regular(10))
        sub.SetForegroundColour(_hex(COLOR_SUBTEXT))
        vs.Add(sub, 0, wx.BOTTOM | wx.ALIGN_CENTER, 10)

        # ── Grid ──────────────────────────────────────────
        grid = wx.GridSizer(rows=len(self._FEATURES) + 2, cols=5, hgap=2, vgap=2)

        plans = ["free", "solo", "pro", "clinic"]
        headers = ["Fonctionnalité"] + [p.upper() for p in plans]

        for h in headers:
            lbl = wx.StaticText(self, label=h)
            lbl.SetFont(_bold(10))
            lbl.SetForegroundColour(_hex(COLOR_TEXT))
            bg = wx.Panel(self)
            bg.SetBackgroundColour(_hex(
                PLAN_BADGE_COLORS.get(h.lower(), COLOR_BG_PANEL)
                if h != "Fonctionnalité" else COLOR_BG_PANEL
            ))
            sizer = wx.BoxSizer(wx.HORIZONTAL)
            sizer.Add(lbl, 1, wx.ALIGN_CENTER | wx.ALL, 6)
            # Add "Recommandé" badge under PRO
            if h == "PRO":
                inner = wx.BoxSizer(wx.VERTICAL)
                inner.Add(lbl, 0, wx.ALIGN_CENTER)
                rec = wx.StaticText(self, label="⭐ Recommandé")
                rec.SetFont(_bold(8))
                rec.SetForegroundColour(_hex(COLOR_UPGRADE))
                inner.Add(rec, 0, wx.ALIGN_CENTER)
                bg.SetSizer(inner)
            else:
                bg.SetSizer(sizer)
            grid.Add(bg, 0, wx.EXPAND)

        # Price row
        price_labels = ["Prix"] + [
            MARKETING_PLANS[p]["price_label"] for p in plans
        ]
        for i, pl in enumerate(price_labels):
            lbl = wx.StaticText(self, label=pl)
            lbl.SetFont(_bold(9) if i > 0 else _regular(9))
            lbl.SetForegroundColour(_hex(COLOR_UPGRADE if i > 0 else COLOR_TEXT))
            bg = wx.Panel(self)
            bg.SetBackgroundColour(_hex(COLOR_BG_PANEL))
            s = wx.BoxSizer(wx.HORIZONTAL)
            s.Add(lbl, 1, wx.ALIGN_CENTER | wx.ALL, 6)
            bg.SetSizer(s)
            grid.Add(bg, 0, wx.EXPAND)

        # Feature rows
        for feat_label, feat_key in self._FEATURES:
            lbl = wx.StaticText(self, label=feat_label)
            lbl.SetFont(_regular(9))
            lbl.SetForegroundColour(_hex(COLOR_TEXT))
            cell = wx.Panel(self)
            cell.SetBackgroundColour(_hex(COLOR_BG_PANEL))
            s = wx.BoxSizer(wx.HORIZONTAL)
            s.Add(lbl, 1, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 8)
            cell.SetSizer(s)
            grid.Add(cell, 0, wx.EXPAND)

            for plan_key in plans:
                cfg = MARKETING_PLANS[plan_key]
                text, color = self._format_cell(cfg, feat_key)
                cell2 = wx.Panel(self)
                cell2.SetBackgroundColour(_hex(
                    "#2D3451" if plan_key == "pro" else COLOR_BG_PANEL
                ))
                lbl2 = wx.StaticText(cell2, label=text)
                lbl2.SetFont(_regular(9))
                lbl2.SetForegroundColour(_hex(color))
                s2 = wx.BoxSizer(wx.HORIZONTAL)
                s2.Add(lbl2, 1, wx.ALIGN_CENTER | wx.ALL, 5)
                cell2.SetSizer(s2)
                grid.Add(cell2, 0, wx.EXPAND)

        vs.Add(grid, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        # ── Buttons ───────────────────────────────────────
        hs = wx.BoxSizer(wx.HORIZONTAL)
        for plan_key in plans:
            cfg = MARKETING_PLANS[plan_key]
            is_current = (plan_key == self.lm.get_plan_name())
            label = (f"✓ Plan actuel" if is_current
                     else f"Choisir {cfg['display_name']}")
            btn = wx.Button(self, label=label, size=(170, 36))
            btn.SetFont(_bold(9))
            if is_current:
                btn.SetBackgroundColour(_hex(COLOR_SUCCESS))
                btn.SetForegroundColour(_hex(COLOR_TEXT))
                btn.Disable()
            elif plan_key == "pro":
                btn.SetBackgroundColour(_hex(COLOR_UPGRADE))
                btn.SetForegroundColour(_hex("#1A1F2E"))
            else:
                btn.SetBackgroundColour(_hex(PLAN_BADGE_COLORS[plan_key]))
                btn.SetForegroundColour(_hex(COLOR_TEXT))
            btn.Bind(wx.EVT_BUTTON, lambda e, p=plan_key: self._on_choose(p))
            hs.Add(btn, 0, wx.ALL, 5)

        vs.Add(hs, 0, wx.ALIGN_CENTER | wx.ALL, 10)
        self.SetSizer(vs)

    @staticmethod
    def _format_cell(cfg: dict, feat_key: str) -> tuple[str, str]:
        """Return (display text, hex color)."""
        negate = feat_key.startswith("!")
        key    = feat_key.lstrip("!")
        val    = cfg.get(key)

        if feat_key == "export_word_quality":
            return ("Premium", COLOR_SUCCESS) if val == "premium" else ("Standard", COLOR_TEXT)

        if isinstance(val, bool):
            actual = not val if negate else val
            return ("✓", COLOR_SUCCESS) if actual else ("✗", COLOR_DANGER)

        if isinstance(val, int):
            if val == -1:
                return ("Illimité", COLOR_SUCCESS)
            if val == 0:
                return ("—", COLOR_LOCKED)
            return (str(val), COLOR_TEXT)

        return (str(val) if val is not None else "—", COLOR_TEXT)

    def _on_choose(self, plan_key: str) -> None:
        # 🔥 ouvrir page paiement
        webbrowser.open(f"{UPGRADE_URL}?plan={plan_key}")

        # 🔥 ensuite proposer activation
        dlg = ActivationDialog(self, self.lm)
        result = dlg.ShowModal()
        dlg.Destroy()

        if result == wx.ID_OK:
            print("Licence activée")
        else:
            print("Activation annulée")

        self.EndModal(wx.ID_OK)


# ════════════════════════════════════════════════════════════
#  ACTIVATION DIALOG
# ════════════════════════════════════════════════════════════
class ActivationDialog(wx.Dialog):
    """
    License key entry dialog.
    Call this when lm.is_active is False on startup.

    Returns (success, license_key) via EndModal / GetValue.
    """

    def __init__(self, parent: wx.Window, lm: LicenseManager):
        super().__init__(parent, title="Activation PISUM",
                         style=wx.DEFAULT_DIALOG_STYLE | wx.STAY_ON_TOP,
                         size=(480, 340))
        self.lm = lm
        self.CenterOnParent()
        self.SetBackgroundColour(_hex(COLOR_BG_DARK))
        self._build()

    def _build(self) -> None:
        vs = wx.BoxSizer(wx.VERTICAL)

        # ── Logo/title strip ─────────────────────────────
        header = wx.Panel(self, size=(-1, 50))
        header.SetBackgroundColour(_hex(COLOR_ACCENT))
        hs = wx.BoxSizer(wx.HORIZONTAL)
        hs.AddSpacer(16)
        brand = wx.StaticText(header, label="PISUM  —  Activation de la licence")
        brand.SetFont(_bold(13))
        brand.SetForegroundColour(_hex(COLOR_TEXT))
        hs.Add(brand, 0, wx.ALIGN_CENTER_VERTICAL)
        header.SetSizer(hs)
        vs.Add(header, 0, wx.EXPAND)
        vs.AddSpacer(20)

        sub = wx.StaticText(self,
            label="Entrez votre clé de licence pour activer PISUM.\n"
                  "Format : PISUM-XXXX-XXXX-XXXX")
        sub.SetFont(_regular(10))
        sub.SetForegroundColour(_hex(COLOR_SUBTEXT))
        vs.Add(sub, 0, wx.LEFT | wx.BOTTOM, 20)

        # Key field
        lbl = wx.StaticText(self, label="Clé de licence :")
        lbl.SetFont(_bold(10))
        lbl.SetForegroundColour(_hex(COLOR_TEXT))
        vs.Add(lbl, 0, wx.LEFT, 20)
        vs.AddSpacer(4)
        self._key_field = wx.TextCtrl(self, size=(-1, 34),
                                      style=wx.TE_PROCESS_ENTER)
        self._key_field.SetFont(_bold(11))
        self._key_field.SetBackgroundColour(_hex(COLOR_BG_PANEL))
        self._key_field.SetForegroundColour(_hex(COLOR_TEXT))
        self._key_field.SetHint("PISUM-XXXX-XXXX-XXXX")
        self._key_field.Bind(wx.EVT_TEXT_ENTER, self._on_activate)
        vs.Add(self._key_field, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 20)

        # Status label
        self._status = wx.StaticText(self, label="")
        self._status.SetFont(_regular(9))
        self._status.SetForegroundColour(_hex(COLOR_SUBTEXT))
        vs.Add(self._status, 0, wx.LEFT, 20)
        vs.AddSpacer(8)

        # Buttons
        hs2 = wx.BoxSizer(wx.HORIZONTAL)
        hs2.AddSpacer(20)

        btn_free = wx.Button(self, label="Continuer en FREE", size=(160, 34))
        btn_free.SetFont(_regular(9))
        btn_free.SetBackgroundColour(_hex(COLOR_BG_PANEL))
        btn_free.SetForegroundColour(_hex(COLOR_SUBTEXT))
        btn_free.Bind(wx.EVT_BUTTON, lambda _: self.EndModal(wx.ID_CANCEL))
        hs2.Add(btn_free, 0, wx.RIGHT, 8)

        btn_buy = wx.Button(self, label="Obtenir une licence", size=(160, 34))
        btn_buy.SetFont(_regular(9))
        btn_buy.SetBackgroundColour(_hex(COLOR_BG_DARK))
        btn_buy.SetForegroundColour(_hex(COLOR_ACCENT))
        btn_buy.Bind(wx.EVT_BUTTON, lambda _: webbrowser.open(UPGRADE_URL))
        hs2.Add(btn_buy, 0, wx.RIGHT, 8)

        self._btn_activate = wx.Button(self,
            label="🔓 Activer", size=(120, 34))
        self._btn_activate.SetFont(_bold(10))
        self._btn_activate.SetBackgroundColour(_hex(COLOR_UPGRADE))
        self._btn_activate.SetForegroundColour(_hex("#1A1F2E"))
        self._btn_activate.Bind(wx.EVT_BUTTON, self._on_activate)
        hs2.Add(self._btn_activate, 0)

        vs.Add(hs2, 0)
        vs.AddSpacer(16)
        self.SetSizer(vs)

    def _on_activate(self, _evt) -> None:
        key = self._key_field.GetValue().strip()
        if not key:
            self._set_status("⚠ Veuillez entrer une clé.", COLOR_DANGER)
            return

        self._set_status("🔄 Vérification en cours…", COLOR_SUBTEXT)
        self._btn_activate.Disable()
        wx.Yield()

        success, msg = self.lm.activate(key)

        self._btn_activate.Enable()
        if success:
            self._set_status(f"✓ {msg}", COLOR_SUCCESS)
            wx.CallLater(800, self.EndModal, wx.ID_OK)
        else:
            self._set_status(f"✗ {msg}", COLOR_DANGER)

    def _set_status(self, text: str, color: str) -> None:
        self._status.SetLabel(text)
        self._status.SetForegroundColour(_hex(color))
        self.Layout()


# ════════════════════════════════════════════════════════════
#  SMART NOTIFICATION BANNER
# ════════════════════════════════════════════════════════════
class SmartNotificationBanner(wx.Panel):
    """
    Dismissable banner panel.  Call show_if_needed() after each action.

    Usage:
        banner = SmartNotificationBanner(parent, lm)
        main_sizer.Add(banner, 0, wx.EXPAND)
        # after user creates a report:
        banner.show_if_needed()
    """

    def __init__(self, parent: wx.Window, lm: LicenseManager):
        super().__init__(parent, style=wx.BORDER_NONE)
        self.lm = lm
        self.SetBackgroundColour(_hex(COLOR_BG_DARK))
        self._build()
        self.Hide()

    def _build(self) -> None:
        hs = wx.BoxSizer(wx.HORIZONTAL)

        self._icon = wx.StaticText(self, label="💡")
        self._icon.SetFont(_bold(11))
        self._icon.SetForegroundColour(_hex(COLOR_UPGRADE))
        hs.Add(self._icon, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 10)

        self._msg = wx.StaticText(self, label="")
        self._msg.SetFont(_regular(9))
        self._msg.SetForegroundColour(_hex(COLOR_TEXT))
        hs.Add(self._msg, 1, wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.RIGHT, 8)

        btn_upgrade = wx.Button(self, label="Upgrade →", size=(100, 26))
        btn_upgrade.SetFont(_bold(9))
        btn_upgrade.SetBackgroundColour(_hex(COLOR_UPGRADE))
        btn_upgrade.SetForegroundColour(_hex("#1A1F2E"))
        btn_upgrade.Bind(wx.EVT_BUTTON, self._on_upgrade)
        hs.Add(btn_upgrade, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)

        btn_dismiss = wx.Button(self, label="×", size=(26, 26))
        btn_dismiss.SetFont(_bold(11))
        btn_dismiss.SetBackgroundColour(_hex(COLOR_BG_DARK))
        btn_dismiss.SetForegroundColour(_hex(COLOR_SUBTEXT))
        btn_dismiss.Bind(wx.EVT_BUTTON, lambda _: self.Hide())
        hs.Add(btn_dismiss, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)

        self.SetSizer(hs)
        self.SetMinSize((-1, 36))

    def show_if_needed(self) -> None:
        msg = self.lm.smart_notification()
        if msg:
            # Show only first line in banner; full text in tooltip
            first_line = msg.split("\n")[0]
            self._msg.SetLabel(first_line)
            self._msg.SetToolTip(msg)
            self.Show()
            self.GetParent().Layout()

    def _on_upgrade(self, _evt) -> None:
        show_upgrade_popup(wx.GetTopLevelParent(self), self.lm)
        self.Hide()


# ════════════════════════════════════════════════════════════
#  WATERMARK HELPER  (for FREE plan PDFs / reports)
# ════════════════════════════════════════════════════════════
def add_watermark_to_document(doc, lm: LicenseManager) -> None:
    """
    If the plan requires a watermark, add a diagonal text watermark
    to a python-docx Document object.
    Only called when lm.has_watermark() is True.
    """
    if not lm.has_watermark():
        return
    try:
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement
        # Add a paragraph at the top of the document with watermark text
        para = doc.add_paragraph()
        run = para.add_run("PISUM FREE — Mettez à niveau pour supprimer ce filigrane")
        run.font.size = __import__("docx.shared", fromlist=["Pt"]).Pt(8)
        run.font.color.rgb = __import__("docx.shared", fromlist=["RGBColor"]).RGBColor(0x99, 0x99, 0x99)
        para.alignment = 1  # center
        # Move this paragraph to position 0
        doc.element.body.insert(0, para._element)
    except Exception:
        pass


# ════════════════════════════════════════════════════════════
#  INTEGRATION HELPER  (convenience entry points)
# ════════════════════════════════════════════════════════════
def guard_feature(parent: wx.Window, lm: LicenseManager,
                  check_fn_name: str, *args) -> bool:
    """
    Generic guard. Returns True if allowed, False + popup if blocked.

    Usage:
        if not guard_feature(self, lm, "can_create_report"):
            return
        lm.increment_report_count()
        # ... create report ...
    """
    check_fn = getattr(lm, check_fn_name, None)
    if check_fn is None:
        return True
    ok, msg = check_fn(*args)
    if not ok:
        show_upgrade_popup(parent, lm, message=msg)
    return ok

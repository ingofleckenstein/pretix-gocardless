"""Links to GoCardless records for event administrators."""
import re

from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _


def payment_status(reference):
    """Use only reconciled, independently verified status, never a mandate alone."""
    status = getattr(reference, "last_status", "")
    if not getattr(reference, "remote_payment_id", None):
        return None
    if getattr(reference, "manual_review_required", False):
        return "danger", _("Rücklastschrift oder Zahlungsausfall nach Bestätigung – manuelle Prüfung erforderlich")
    if status in {"pending_submission", "submitted"}:
        return "warning", _("Lastschrift beantragt – Zahlung steht noch aus")
    if status in {"confirmed", "paid_out"}:
        return "success", _("Zahlung bestätigt")
    if status in {"failed", "cancelled", "customer_approval_denied", "charged_back"}:
        return "danger", _("Zahlung fehlgeschlagen oder rückbelastet – bitte prüfen")
    return None


def order_status(order):
    rows = []
    for payment in order.payments.filter(provider="gocardless").select_related("gocardless_reference"):
        reference = getattr(payment, "gocardless_reference", None)
        state = payment_status(reference) if reference else None
        if state:
            rows.append((state[0], payment.full_id, state[1]))
    return format_html_join("", '<div class="alert alert-{}" role="status"><strong>{}</strong>: {}</div>', rows)


def payment_links(order):
    rows = []
    for payment in order.payments.filter(provider="gocardless").select_related("gocardless_reference"):
        reference = getattr(payment, "gocardless_reference", None)
        if not reference:
            continue
        payment_id = reference.remote_payment_id
        hosts = {"sandbox": "manage-sandbox.gocardless.com", "live": "manage.gocardless.com"}
        host = hosts.get(reference.environment)
        if not host or not isinstance(payment_id, str) or not re.fullmatch(r"PM[A-Za-z0-9]+", payment_id):
            continue
        status = getattr(reference, "last_status", "")
        needs_review = (getattr(reference, "manual_review_required", False) or
                        (getattr(payment, "state", None) == "confirmed" and status in
                         {"failed", "cancelled", "customer_approval_denied", "charged_back"}))
        rows.append((payment.full_id, f"https://{host}/payments/{payment_id}",
                     _("Sandbox") if reference.environment == "sandbox" else _("Live"),
                     _("Reversed after confirmation: payment and order were not changed. Manual review required.")
                     if needs_review else ""))
    if not rows:
        return ""
    links = format_html_join("", '<li>{}: <a href="{}" target="_blank" rel="noopener noreferrer">{} – {}</a> <strong>{}</strong></li>',
                             ((local_id, url, _("Open payment in GoCardless"), environment, warning)
                              for local_id, url, environment, warning in rows))
    return format_html('<div class="panel panel-default"><div class="panel-heading">{}</div>'
                       '<div class="panel-body"><ul>{}</ul></div></div>', _("GoCardless payments"), links)

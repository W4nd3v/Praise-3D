from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import RequestReminder
from .activity import log_event, current_actor

PENDING = ["scheduled", "due", "snoozed"]


def visible_reminders(request):
    qs = RequestReminder.objects.filter(company=request.company, request__active=True).exclude(request__status="cancelled")
    role = getattr(getattr(request, "membership", None), "role", "viewer")
    if not request.user.is_superuser and role != "admin":
        qs = qs.filter(Q(assignee__isnull=True) | Q(assignee=request.user))
    return qs.select_related("request__customer")


def refresh_due(company, now=None):
    now = now or timezone.now()
    ids = RequestReminder.objects.filter(company=company, status__in=["scheduled", "snoozed"], scheduled_at__lte=now).values_list("pk", flat=True)
    for pk in list(ids):
        with transaction.atomic():
            reminder = RequestReminder.objects.select_for_update().select_related("request").get(pk=pk)
            if reminder.status not in PENDING or reminder.scheduled_at > now:
                continue
            reminder.status = "due"
            reminder.save(update_fields=["status"])
            token = current_actor.set(None)
            try:
                log_event(reminder.request, "reminder.fired", "Lembrete vencido", key=f"reminder:{pk}:fired:{reminder.version}", details={"scheduled_at": reminder.scheduled_at.isoformat()}, at=now)
            finally:
                current_actor.reset(token)


@transaction.atomic
def snooze_reminder(reminder, until, version, user=None):
    reminder = RequestReminder.objects.select_for_update().select_related("request").get(pk=reminder.pk)
    if reminder.status not in PENDING:
        raise ValidationError("Este lembrete já foi concluído ou cancelado.")
    if str(reminder.version) != str(version):
        raise ValidationError("Outro usuário atualizou o lembrete. Atualize a página.")
    if until <= timezone.now():
        raise ValidationError("Escolha um horário futuro.")
    before = reminder.scheduled_at
    reminder.scheduled_at = until
    reminder.status = "snoozed"
    reminder.version += 1
    reminder.save(update_fields=["scheduled_at", "status", "version"])
    log_event(reminder.request, "reminder.snoozed", "Lembrete adiado", user=user,
        key=f"reminder:{reminder.pk}:snoozed:{reminder.version}", details={"before": before.isoformat(), "until": until.isoformat()})
    return reminder


@transaction.atomic
def finish_reminder(reminder, user=None, mode="manual"):
    reminder = RequestReminder.objects.select_for_update().select_related("request").get(pk=reminder.pk)
    if reminder.status not in PENDING:
        return reminder
    reminder.status = "cancelled" if mode == "cancelled" else "completed"
    reminder.completed_at = timezone.now()
    reminder.completed_by = user or current_actor.get()
    reminder.completion_mode = mode
    reminder.version += 1
    reminder.save(update_fields=["status", "completed_at", "completed_by", "completion_mode", "version"])
    log_event(reminder.request, f"reminder.{reminder.status}", "Lembrete cancelado com a solicitação" if mode == "cancelled" else "Tarefa do lembrete realizada", user=user, key=f"reminder:{reminder.pk}:finished", details={"mode": mode})
    return reminder


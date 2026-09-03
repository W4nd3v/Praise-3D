from .models import Alert, ProductionDemand, QuoteRequest


def erp_context(request):
    company = getattr(request, "company", None)
    if not company:
        return {"current_company": None, "nav_badges": {}}
    from .reminders import refresh_due, visible_reminders
    from .operations import operating_demands
    from django.utils import timezone
    refresh_due(company)
    due = list(visible_reminders(request).filter(status="due", scheduled_at__lte=timezone.now()))
    role = "admin" if request.user.is_superuser else getattr(getattr(request, "membership", None), "role", "viewer")
    return {
        "current_company": company,
        "due_reminders": due,
        "can_operate_reminders": role in {"admin", "operator", "finance"},
        "can_operate": role in {"admin", "operator"},
        "can_finance": role in {"admin", "finance"},
        "is_admin": role == "admin",
        "nav_badges": {
            "requests": QuoteRequest.objects.filter(company=company, active=True, status__in=["new", "analysis", "waiting"]).count(),
            "production": operating_demands(company).exclude(stage="ready").count(),
            "alerts": Alert.objects.filter(company=company, active=True, resolved_at__isnull=True).count(),
        },
    }

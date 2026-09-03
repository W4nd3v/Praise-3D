from .models import Alert, ProductionDemand, QuoteRequest


def erp_context(request):
    company = getattr(request, "company", None)
    if not company:
        return {"current_company": None, "nav_badges": {}}
    return {
        "current_company": company,
        "nav_badges": {
            "requests": QuoteRequest.objects.filter(company=company, active=True, status__in=["new", "analysis", "waiting"]).count(),
            "production": ProductionDemand.objects.filter(company=company, active=True).exclude(stage="ready").count(),
            "alerts": Alert.objects.filter(company=company, active=True, resolved_at__isnull=True).count(),
        },
    }


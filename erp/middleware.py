from .models import Company, Membership


class CurrentCompanyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.company = None
        request.membership = None
        if request.user.is_authenticated:
            requested = request.session.get("company_id")
            memberships = Membership.objects.select_related("company").filter(user=request.user, active=True, company__active=True)
            membership = memberships.filter(company_id=requested).first() if requested else memberships.first()
            if membership:
                request.company = membership.company
                request.membership = membership
                request.session["company_id"] = membership.company_id
            elif request.user.is_superuser:
                request.company = Company.objects.filter(active=True).first()
        from .activity import current_actor
        token = current_actor.set(request.user if request.user.is_authenticated else None)
        try:
            return self.get_response(request)
        finally:
            current_actor.reset(token)

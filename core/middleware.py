from django.shortcuts import redirect

class CurrentCompanyMiddleware:
    def __init__(self, get_response): self.get_response = get_response
    def __call__(self, request):
        request.company = None
        if request.user.is_authenticated:
            membership = request.user.company_memberships.filter(active=True, company__active=True).select_related('company').first()
            request.company = membership.company if membership else None
            if not request.company and request.path not in ['/admin/', '/accounts/logout/'] and not request.path.startswith('/admin/'):
                return redirect('/admin/')
        return self.get_response(request)

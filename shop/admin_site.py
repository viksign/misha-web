from django.conf import settings
from django.contrib.admin import AdminSite
from django.contrib.auth.views import redirect_to_login


class StaffAdminSite(AdminSite):
    def has_permission(self, request):
        return super().has_permission(request) and (
            not settings.STAFF_MFA_REQUIRED or request.user.is_verified()
        )

    def login(self, request, extra_context=None):
        return redirect_to_login(
            request.GET.get('next', '/admin/'), login_url='two_factor:login',
        )

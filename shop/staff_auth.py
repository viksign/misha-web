from django.conf import settings
from django.contrib import messages
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.urls import reverse
from two_factor.forms import AuthenticationTokenForm, BackupTokenForm
from two_factor.utils import default_device
from two_factor.views import LoginView

class StaffAuthenticationForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].label = 'Username or email address'

    def clean(self):
        username = self.cleaned_data.get('username', '')
        if '@' in username:
            users = list(User.objects.filter(email__iexact=username).values_list('username', flat=True)[:2])
            if len(users) == 1:
                self.cleaned_data['username'] = users[0]
        return super().clean()

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not user.is_staff:
            raise ValidationError('This sign-in is for active staff accounts only.', code='not_staff')


class StaffLoginView(LoginView):
    form_list = (
        ('auth', StaffAuthenticationForm),
        ('token', AuthenticationTokenForm),
        ('backup', BackupTokenForm),
    )

    def get_success_url(self):
        return self.get_redirect_url() or reverse('control_panel')


class StaffMFAMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        protected = any(path == prefix.rstrip('/') or path.startswith(prefix) for prefix in ['/admin/', '/controlpanel/'])
        security_page = path.startswith('/staff-auth/') and path != reverse('two_factor:login')
        if not settings.STAFF_MFA_REQUIRED or not (protected or security_page):
            return self.get_response(request)
        user = request.user
        if not user.is_authenticated:
            return redirect_to_login(request.get_full_path(), login_url='two_factor:login')
        if not user.is_active or not user.is_staff:
            return self.get_response(request)
        if user.is_verified():
            return self.get_response(request)
        if default_device(user):
            return redirect_to_login(request.get_full_path(), login_url='two_factor:login')
        if path in {reverse('two_factor:setup'), reverse('two_factor:qr')}:
            return self.get_response(request)
        messages.info(request, 'Set up an authenticator app before accessing staff tools. Save your recovery codes after setup.')
        return redirect('two_factor:setup')

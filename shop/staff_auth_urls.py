from django.contrib.admin.views.decorators import staff_member_required
from django.urls import path
from two_factor.views import BackupTokensView, ProfileView, QRGeneratorView, SetupCompleteView, SetupView

from .staff_auth import StaffLoginView


app_name = 'two_factor'


def staff(view):
    return staff_member_required(view, login_url='two_factor:login')


urlpatterns = [
    path('login/', StaffLoginView.as_view(), name='login'),
    path('setup/', staff(SetupView.as_view()), name='setup'),
    path('qrcode/', staff(QRGeneratorView.as_view()), name='qr'),
    path('setup/complete/', staff(SetupCompleteView.as_view(template_name='two_factor/staff_setup_complete.html')), name='setup_complete'),
    path('recovery-codes/', staff(BackupTokensView.as_view()), name='backup_tokens'),
    path('', staff(ProfileView.as_view(template_name='two_factor/staff_profile.html')), name='profile'),
]

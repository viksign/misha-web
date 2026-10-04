from django.contrib.admin.apps import AdminConfig


class StaffAdminConfig(AdminConfig):
    default_site = 'shop.admin_site.StaffAdminSite'

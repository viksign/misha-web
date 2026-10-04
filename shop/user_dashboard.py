from django.contrib.auth.models import User
from django.db.models import Count, IntegerField, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce, Lower

from .dashboard import DashboardChartView
from .models import AnalyticsEvent, CustomDesignRequest, CustomerReview, Enquiry, InterestSignup, Order


def _related_count(model, **filters):
    query = model.objects.filter(user_id=OuterRef('pk'), **filters).order_by().values('user_id').annotate(total=Count('pk')).values('total')
    return Coalesce(Subquery(query, output_field=IntegerField()), 0)


def _email_count(model):
    query = model.objects.exclude(email='').annotate(email_key=Lower('email')).filter(email_key=Lower(OuterRef('email'))).order_by().values('email_key').annotate(total=Count('pk')).values('total')
    return Coalesce(Subquery(query, output_field=IntegerField()), 0)


def user_filters(params):
    role = params.get('role', 'all')
    state = params.get('state', 'all')
    sort = params.get('sort', 'newest')
    return {
        'q': params.get('q', '').strip(),
        'role': role if role in {'all', 'staff', 'customer'} else 'all',
        'state': state if state in {'all', 'active', 'disabled'} else 'all',
        'sort': sort if sort in {'newest', 'oldest', 'name', 'orders', 'preorders'} else 'newest',
    }


def filtered_users(filters):
    users = User.objects.annotate(
        order_count=_related_count(Order),
        completed_order_count=_related_count(Order, status='completed'),
        preorder_count=_related_count(InterestSignup, interest_type='preorder'),
        custom_request_count=_related_count(CustomDesignRequest),
        enquiry_count=_email_count(Enquiry), feedback_count=_email_count(CustomerReview),
        activity_count=_related_count(AnalyticsEvent),
    )
    if filters['q']:
        query = filters['q']
        users = users.filter(Q(email__icontains=query) | Q(username__icontains=query) | Q(first_name__icontains=query) | Q(last_name__icontains=query))
    if filters['role'] != 'all':
        users = users.filter(is_staff=filters['role'] == 'staff')
    if filters['state'] != 'all':
        users = users.filter(is_active=filters['state'] == 'active')
    sorting = {'newest': ('-date_joined', '-pk'), 'oldest': ('date_joined', 'pk'), 'name': ('first_name', 'last_name', 'pk'), 'orders': ('-order_count', 'pk'), 'preorders': ('-preorder_count', 'pk')}
    return users.order_by(*sorting[filters['sort']])


def user_summary(users):
    return users.aggregate(
        accounts=Count('pk'), staff=Count('pk', filter=Q(is_staff=True)),
        customers=Count('pk', filter=Q(is_staff=False)), active=Count('pk', filter=Q(is_active=True)),
        disabled=Count('pk', filter=Q(is_active=False)), orders=Sum('order_count', default=0),
        preorders=Sum('preorder_count', default=0), custom_requests=Sum('custom_request_count', default=0),
        enquiries=Sum('enquiry_count', default=0), feedback=Sum('feedback_count', default=0),
    )


class UserRoleChartView(DashboardChartView):
    def build_chart(self):
        summary = user_summary(filtered_users(user_filters(self.request.GET)))
        return {'labels': ['Staff', 'Customers'], 'data': [[summary['staff'], summary['customers']]], 'providers': ['Accounts'], 'colors': [['#367c96', '#286b56']]}


class UserEngagementChartView(DashboardChartView):
    def build_chart(self):
        summary = user_summary(filtered_users(user_filters(self.request.GET)))
        return {'labels': ['Orders', 'Pre-order requests', 'Custom requests', 'Enquiries', 'Reviews / feedback'], 'data': [[summary[field] for field in ['orders', 'preorders', 'custom_requests', 'enquiries', 'feedback']]], 'providers': ['Records linked to accounts'], 'colors': [['#286b56', '#bf9547', '#367c96', '#77857b', '#a96983']]}
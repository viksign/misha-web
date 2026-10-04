from chartjs.views.lines import BaseLineChartView
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count, F, Q
from django.utils.decorators import method_decorator

from .models import InterestSignup, Order, Product


@method_decorator(staff_member_required(login_url='login'), name='dispatch')
class DashboardChartView(BaseLineChartView):
    def get_context_data(self, **kwargs):
        self.chart = self.build_chart()
        context = super().get_context_data(**kwargs)
        context['empty'] = not any(sum(series) for series in self.chart['data'])
        return context

    def get_labels(self):
        return self.chart['labels']

    def get_data(self):
        return self.chart['data']

    def get_providers(self):
        return self.chart['providers']

    def get_dataset_options(self, index, color):
        return {'backgroundColor': self.chart['colors'][index], 'borderWidth': 0, 'borderRadius': 3}

    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        response['Cache-Control'] = 'no-store, private'
        return response


class InventoryChartView(DashboardChartView):
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['preorder_interest_count'] = self.chart['preorder_interest_count']
        return context

    def build_chart(self):
        counts = Product.objects.filter(active=True).aggregate(
            healthy=Count('pk', filter=Q(stock_quantity__gt=F('reorder_level'))),
            low=Count('pk', filter=Q(stock_quantity__gt=0, stock_quantity__lte=F('reorder_level'))),
            empty=Count('pk', filter=Q(stock_quantity=0)),
        )
        preorder_interest_count = InterestSignup.objects.filter(
            interest_type='preorder', product__active=True, product__stock_quantity=0,
        ).count()
        return {
            'labels': ['Healthy stock', 'Low stock', ['Out of stock', f'Pre-order interest: {preorder_interest_count}']],
            'data': [[counts['healthy'], counts['low'], counts['empty']]],
            'providers': ['Active products'],
            'colors': [['#286b56', '#bf9547', '#b75353']],
            'preorder_interest_count': preorder_interest_count,
        }


class FulfilmentChartView(DashboardChartView):
    def build_chart(self):
        stages = [('pending', 'Pending'), ('processing', 'Processing'), ('shipped', 'Shipped'), ('completed', 'Completed')]
        counts = dict(Order.objects.filter(payment_status='paid').values('status').annotate(total=Count('pk')).values_list('status', 'total'))
        return {
            'labels': [label for status, label in stages],
            'data': [[counts.get(status, 0) for status, label in stages]],
            'providers': ['Paid orders'],
            'colors': [['#bf9547', '#367c96', '#286b56', '#77857b']],
        }


class TeamWorkloadChartView(DashboardChartView):
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        counts = context['datasets'][0]['data']
        context['total'] = sum(counts)
        context['percentages'] = [round(count * 100 / context['total'], 1) for count in counts] if context['total'] else []
        return context

    def build_chart(self):
        members = Order.objects.filter(payment_status='paid', status='processing').values(
            'assigned_to_id', 'assigned_to__first_name', 'assigned_to__last_name',
        ).annotate(total=Count('pk')).order_by('-total', 'assigned_to_id')
        labels, counts, colors = [], [], []
        palette = ['#286b56', '#367c96', '#bf9547', '#b75353', '#557b88', '#a96983', '#64864a', '#a56e43']
        for member in members:
            name = ' '.join(filter(None, [member['assigned_to__first_name'], member['assigned_to__last_name']])).strip()
            labels.append(name or (f"Staff #{member['assigned_to_id']} (name not set)" if member['assigned_to_id'] else 'Unassigned'))
            counts.append(member['total'])
            colors.append(palette[member['assigned_to_id'] % len(palette)] if member['assigned_to_id'] else '#77857b')
        return {'labels': labels, 'data': [counts], 'providers': ['Processing orders'], 'colors': [colors]}
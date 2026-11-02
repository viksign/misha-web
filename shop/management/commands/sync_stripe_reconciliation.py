from django.core.cache import cache
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from shop.sales import sales_window, sync_stripe_transactions


class Command(BaseCommand):
    help = 'Refresh Stripe reconciliation for the current and previous calendar years.'

    def handle(self, *args, **options):
        lock = 'sales:stripe-sync'
        if not cache.add(lock, True, 120):
            raise CommandError('Another Stripe refresh is running. Try again later.')
        try:
            year = timezone.localdate().year
            failures = []
            for selected_year in (year - 1, year):
                run = sync_stripe_transactions(
                    sales_window({'year': str(selected_year)}), None,
                )
                self.stdout.write(
                    f'{timezone.now().isoformat()}: {selected_year} Stripe refresh '
                    f'{run.status}; {run.transaction_count} transactions; '
                    f'error={run.error_code or "none"}.'
                )
                if run.status != 'complete':
                    failures.append(f'{selected_year}: {run.status} ({run.error_code or "scan limit"})')
            if failures:
                raise CommandError('Stripe reconciliation incomplete: ' + '; '.join(failures))
        finally:
            cache.delete(lock)

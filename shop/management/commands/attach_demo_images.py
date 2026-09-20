from django.core.management.base import BaseCommand
from shop.models import Product, ProductImage
from pathlib import Path
from django.conf import settings

class Command(BaseCommand):
    help = 'Attach the three supplied necklace photos to the Plumeria Pendant demo product.'
    def handle(self, *args, **kwargs):
        p = Product.objects.filter(slug='plumeria-pendant').first()
        if not p:
            self.stderr.write('Run seed_shop first.'); return
        files = ['plumeria-pendant-neck1.jpeg','plumeria-pendant-neck2.jpeg','plumeria-pendant-neck3.jpeg']
        for i, name in enumerate(files):
            path = Path(settings.MEDIA_ROOT) / 'products' / name
            if not path.exists(): continue
            if not ProductImage.objects.filter(product=p, image=f'products/{name}').exists():
                ProductImage.objects.create(product=p, image=f'products/{name}', alt_text=f'{p.name} — necklace photograph', is_primary=(i==0), sort_order=i)
        self.stdout.write(self.style.SUCCESS('Demo necklace images attached.'))

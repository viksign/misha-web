from django.core.management.base import BaseCommand
from shop.models import Collection, Product, ProductImage
from pathlib import Path
from django.conf import settings

class Command(BaseCommand):
    help = 'Create initial Misha Island Heritage collections and sample products.'
    def handle(self, *args, **kwargs):
        signature, _ = Collection.objects.get_or_create(slug='signature', defaults={'name':'Signature Collection','description':'Timeless pieces inspired by island nature.'})
        heritage, _ = Collection.objects.get_or_create(slug='heritage', defaults={'name':'Heritage Collection','description':'Pieces inspired by Mauritius and generations of island heritage.'})
        products = [
            ('Plumeria Pendant','plumeria-pendant','MIH-PEN-001','185.00',signature),
            ('Plumeria Earrings','plumeria-earrings','MIH-EAR-001','139.00',signature),
            ('Heritage Ring','heritage-ring','MIH-RNG-001','165.00',heritage),
            ('Island Bangle','island-bangle','MIH-BNG-001','220.00',heritage),
        ]
        for name, slug, sku, price, collection in products:
            p, created = Product.objects.get_or_create(slug=slug, defaults={'name':name,'sku':sku,'price':price,'collection':collection,'description':f'{name}, part of the Misha Island Heritage collection.','short_description':'Inspired by island heritage.','material':'Gold plated','stock_quantity':10,'featured':True,'active':True})
            if created:
                self.stdout.write(self.style.SUCCESS(f'Created {name}'))
        self.stdout.write(self.style.SUCCESS('Shop seed complete.'))

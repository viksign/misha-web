from decimal import Decimal
from .models import Product

class Cart:
    SESSION_KEY = 'misha_cart'
    def __init__(self, request):
        self.session = request.session
        self.data = self.session.get(self.SESSION_KEY, {})

    def add(self, product, quantity=1):
        key = str(product.id)
        current = int(self.data.get(key, 0))
        self.data[key] = min(current + int(quantity), product.stock_quantity)
        self.save()

    def set(self, product, quantity):
        key = str(product.id)
        quantity = int(quantity)
        if quantity <= 0: self.data.pop(key, None)
        else: self.data[key] = min(quantity, product.stock_quantity)
        self.save()

    def remove(self, product):
        self.data.pop(str(product.id), None); self.save()

    def clear(self): self.data = {}; self.save()

    def save(self):
        self.session[self.SESSION_KEY] = self.data
        self.session.modified = True

    def items(self):
        ids = [int(x) for x in self.data]
        products = {p.id:p for p in Product.objects.filter(id__in=ids, active=True)}
        for pid, qty in list(self.data.items()):
            product = products.get(int(pid))
            if not product or not product.in_stock:
                continue
            yield {'product': product, 'quantity': int(qty), 'total': product.price * int(qty)}

    @property
    def count(self): return sum(int(v) for v in self.data.values())
    @property
    def subtotal(self): return sum((x['total'] for x in self.items()), Decimal('0'))

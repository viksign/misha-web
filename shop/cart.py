from decimal import Decimal
from .models import CartItem, Product

class Cart:
    SESSION_KEY = 'misha_cart'
    def __init__(self, request):
        self.request = request
        self.session = request.session
        self.user = request.user if request.user.is_authenticated else None
        self.data = self._load()

    def _load(self):
        session_data = {
            str(product_id): int(quantity)
            for product_id, quantity in self.session.get(self.SESSION_KEY, {}).items()
            if int(quantity) > 0
        }
        if self.user is None:
            return session_data

        database_data = {
            str(item.product_id): item.quantity
            for item in CartItem.objects.filter(user=self.user)
        }
        if session_data:
            for product_id, quantity in session_data.items():
                database_data[product_id] = database_data.get(product_id, 0) + quantity
            self.session.pop(self.SESSION_KEY, None)
            self.session.modified = True
            self._write_database(database_data)
        return database_data

    def add(self, product, quantity=1):
        key = str(product.id)
        current = int(self.data.get(key, 0))
        self.data[key] = current + int(quantity)
        self.save()

    def set(self, product, quantity):
        key = str(product.id)
        quantity = int(quantity)
        if quantity <= 0: self.data.pop(key, None)
        else: self.data[key] = quantity
        self.save()

    def remove(self, product):
        self.data.pop(str(product.id), None); self.save()

    def clear(self): self.data = {}; self.save()

    def save(self):
        if self.user is None:
            self.session[self.SESSION_KEY] = self.data
            self.session.modified = True
        else:
            self._write_database(self.data)

    def _write_database(self, data):
        existing = {item.product_id: item for item in CartItem.objects.filter(user=self.user)}
        product_ids = {int(product_id) for product_id in data}
        for product_id, item in existing.items():
            if product_id not in product_ids:
                item.delete()
        for product_id, quantity in data.items():
            CartItem.objects.update_or_create(
                user=self.user,
                product_id=int(product_id),
                defaults={'quantity': int(quantity)},
            )

    def items(self):
        ids = [int(x) for x in self.data]
        products = {p.id:p for p in Product.objects.filter(id__in=ids, active=True)}
        for pid, qty in list(self.data.items()):
            product = products.get(int(pid))
            if not product:
                continue
            yield {'product': product, 'quantity': int(qty), 'total': product.price * int(qty)}

    @property
    def count(self): return sum(int(v) for v in self.data.values())
    @property
    def subtotal(self): return sum((x['total'] for x in self.items()), Decimal('0'))

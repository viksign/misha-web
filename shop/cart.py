from decimal import Decimal

from django.core.exceptions import ValidationError

from .models import CartItem, Product


class Cart:
    SESSION_KEY = 'misha_cart'

    def __init__(self, request):
        self.request = request
        self.session = request.session
        self.user = request.user if request.user.is_authenticated else None
        self.data = self._load()

    @staticmethod
    def selection_key(option_ids):
        ids = sorted({int(value) for value in option_ids if value not in (None, '')})
        return '-'.join(str(value) for value in ids)

    @classmethod
    def key(cls, product, option_ids=()):
        selection = cls.selection_key(option_ids)
        return f'{product.pk}:{selection}' if selection else str(product.pk)

    @staticmethod
    def split_key(key):
        """Return (product_id, option_ids); accepts legacy 'product:chain:size' keys."""
        try:
            product_id, _, rest = str(key).partition(':')
            if ':' in rest:
                parts = rest.split(':')
                if len(parts) != 2:
                    raise ValueError
            else:
                parts = rest.split('-')
            return int(product_id), [int(part) for part in parts if part]
        except ValueError:
            raise ValidationError('This bag selection is invalid. Please remove it and choose the product again.')

    @classmethod
    def normalise_key(cls, key):
        try:
            product_id, option_ids = cls.split_key(key)
        except ValidationError:
            return str(key)
        selection = cls.selection_key(option_ids)
        return f'{product_id}:{selection}' if selection else str(product_id)

    def _load(self):
        session_data = {}
        for key, quantity in self.session.get(self.SESSION_KEY, {}).items():
            if int(quantity) > 0:
                key = self.normalise_key(key)
                session_data[key] = session_data.get(key, 0) + int(quantity)
        if self.user is None:
            return session_data

        database_data = {}
        for item in CartItem.objects.filter(user=self.user):
            key = self.normalise_key(f'{item.product_id}:{item.selection_key}' if item.selection_key else str(item.product_id))
            database_data[key] = database_data.get(key, 0) + item.quantity
        if session_data:
            for key, quantity in session_data.items():
                database_data[key] = database_data.get(key, 0) + quantity
            self.session.pop(self.SESSION_KEY, None)
            self.session.modified = True
            self._write_database(database_data)
        return database_data

    def add(self, product, quantity=1, option_ids=()):
        selection = product.purchase_selection(option_ids)
        key = self.key(product, [option.pk for option in selection['options']])
        self.data[key] = self.data.get(key, 0) + int(quantity)
        self.save()

    def set(self, product, quantity, key=None):
        key = self._product_key(product, key)
        quantity = int(quantity)
        if quantity <= 0:
            self.data.pop(key, None)
        elif key in self.data:
            self.data[key] = quantity
        else:
            raise ValidationError('This selection is no longer in your bag.')
        self.save()

    def _product_key(self, product, key):
        key = key or str(product.pk)
        product_id, _ = self.split_key(key)
        if product_id != product.pk:
            raise ValidationError('This selection does not belong to this product.')
        return key

    def remove(self, product, key=None):
        self.data.pop(self._product_key(product, key), None)
        self.save()

    def clear(self):
        self.data = {}
        self.save()

    def save(self):
        if self.user is None:
            self.session[self.SESSION_KEY] = self.data
            self.session.modified = True
        else:
            self._write_database(self.data)

    def _write_database(self, data):
        existing = {
            f'{item.product_id}:{item.selection_key}' if item.selection_key else str(item.product_id): item
            for item in CartItem.objects.filter(user=self.user)
        }
        for key, item in existing.items():
            if key not in data:
                item.delete()
        for key, quantity in data.items():
            product_id, option_ids = self.split_key(key)
            selection_key = self.selection_key(option_ids)
            CartItem.objects.update_or_create(
                user=self.user, product_id=product_id, selection_key=selection_key,
                defaults={'quantity': int(quantity)},
            )

    def items(self):
        ids = []
        for key in self.data:
            try:
                ids.append(self.split_key(key)[0])
            except ValidationError:
                continue
        products = {
            product.pk: product
            for product in Product.objects.filter(pk__in=ids, active=True).prefetch_related('options')
        }
        for key, quantity in self.data.items():
            try:
                product_id, option_ids = self.split_key(key)
            except ValidationError:
                continue
            product = products.get(product_id)
            if product is None:
                continue
            row = {'key': key, 'product': product, 'quantity': quantity, 'error': ''}
            try:
                selection = product.purchase_selection(option_ids)
            except ValidationError as error:
                row.update(unit_price=None, total=None, error=' '.join(error.messages))
            else:
                row.update(
                    options=selection['options'],
                    unit_price=selection['unit_price'], total=selection['unit_price'] * quantity,
                )
            yield row

    @property
    def count(self):
        return sum(self.data.values())

    @property
    def subtotal(self):
        return sum((row['total'] for row in self.items() if not row['error']), Decimal('0.00'))

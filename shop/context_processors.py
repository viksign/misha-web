from .cart import Cart

def shop_context(request):
    cart = Cart(request)
    return {'cart_count': cart.count, 'cart_subtotal': cart.subtotal}

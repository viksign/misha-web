from django import forms
from .models import Enquiry, Order

class EnquiryForm(forms.ModelForm):
    class Meta:
        model = Enquiry
        fields = ['name','email','phone','subject','message']
        widgets = {'message': forms.Textarea(attrs={'rows': 6})}

class CheckoutForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ['email','shipping_name','shipping_address1','shipping_address2','shipping_city','shipping_postcode','shipping_country']

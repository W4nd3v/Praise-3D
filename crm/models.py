from django.db import models
from core.models import CompanyModel

class Customer(CompanyModel):
    name = models.CharField(max_length=180)
    trading_name = models.CharField(max_length=180, blank=True)
    tax_id = models.CharField(max_length=20, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    whatsapp = models.CharField(max_length=30, blank=True)
    instagram = models.CharField(max_length=80, blank=True)
    email = models.EmailField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=2, blank=True)
    notes = models.TextField(blank=True)
    is_system = models.BooleanField(default=False)
    def __str__(self): return self.name

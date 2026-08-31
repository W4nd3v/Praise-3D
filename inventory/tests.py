from django.core.exceptions import ValidationError
from django.test import TestCase
from core.models import Company
from .models import Material
from .services import open_roll, finish_roll

class RollTests(TestCase):
    def setUp(self):
        self.c=Company.objects.create(name='Praise',slug='praise')
        self.m=Material.objects.create(company=self.c,type='filament',name='PLA',closed_rolls=1)
    def test_open_and_finish(self):
        open_roll(self.m,'open-1'); self.m.refresh_from_db(); self.assertEqual((self.m.closed_rolls,self.m.open_rolls),(0,1))
        finish_roll(self.m,'finish-1'); self.m.refresh_from_db(); self.assertEqual(self.m.open_rolls,0)
    def test_cannot_open_without_stock(self):
        self.m.closed_rolls=0; self.m.save()
        with self.assertRaises(ValidationError): open_roll(self.m,'open-2')

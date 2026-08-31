from django.contrib.auth import get_user_model
from django.test import TestCase
from .models import Company, Membership, Sequence

class CoreTests(TestCase):
    def setUp(self):
        self.a=Company.objects.create(name='A',slug='a'); self.b=Company.objects.create(name='B',slug='b')
    def test_sequences_are_per_company(self):
        self.assertEqual(Sequence.next(self.a,'PED'),'PED-000001'); self.assertEqual(Sequence.next(self.b,'PED'),'PED-000001')

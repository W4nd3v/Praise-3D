import uuid
from django.conf import settings
from django.db import models, transaction

MONEY = dict(max_digits=14, decimal_places=2, default=0)
QTY = dict(max_digits=14, decimal_places=3, default=0)
PERCENT = dict(max_digits=8, decimal_places=4, default=0)

class UUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta: abstract = True

class Company(UUIDModel):
    name = models.CharField(max_length=160)
    slug = models.SlugField(unique=True)
    trading_name = models.CharField(max_length=160, blank=True)
    slogan = models.CharField(max_length=200, blank=True)
    logo = models.ImageField(upload_to='companies/', blank=True)
    timezone = models.CharField(max_length=50, default='America/Sao_Paulo')
    primary_color = models.CharField(max_length=7, default='#1E40AF')
    secondary_color = models.CharField(max_length=7, default='#F59E0B')
    accent_color = models.CharField(max_length=7, default='#10B981')
    default_filament_minimum = models.PositiveIntegerField(default=2)
    energy_rate = models.DecimalField(**MONEY)
    active = models.BooleanField(default=True)
    def __str__(self): return self.trading_name or self.name

class Membership(UUIDModel):
    ROLES = [('admin','Administrador'),('operator','Operador'),('finance','Financeiro'),('viewer','Leitura')]
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='company_memberships')
    role = models.CharField(max_length=16, choices=ROLES, default='operator')
    active = models.BooleanField(default=True)
    class Meta: constraints = [models.UniqueConstraint(fields=['company','user'], name='unique_company_user')]

class Sequence(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    prefix = models.CharField(max_length=4)
    value = models.PositiveBigIntegerField(default=0)
    class Meta: constraints = [models.UniqueConstraint(fields=['company','prefix'], name='unique_company_sequence')]
    @classmethod
    def next(cls, company, prefix):
        with transaction.atomic():
            obj, _ = cls.objects.select_for_update().get_or_create(company=company, prefix=prefix)
            obj.value += 1; obj.save(update_fields=['value'])
            return f'{prefix}-{obj.value:06d}'

class CompanyModel(UUIDModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT)
    active = models.BooleanField(default=True)
    class Meta: abstract = True

class AuditLog(UUIDModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    entity = models.CharField(max_length=80)
    entity_id = models.CharField(max_length=40)
    action = models.CharField(max_length=50)
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

class IdempotencyKey(UUIDModel):
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    key = models.CharField(max_length=100)
    operation = models.CharField(max_length=80)
    result_id = models.CharField(max_length=40, blank=True)
    class Meta: constraints = [models.UniqueConstraint(fields=['company','key','operation'], name='unique_operation_key')]

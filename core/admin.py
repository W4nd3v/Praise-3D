from django.contrib import admin
from .models import Company, Membership, AuditLog
admin.site.register([Company, Membership, AuditLog])

# Register your models here.

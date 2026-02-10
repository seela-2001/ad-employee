# employees/admin.py
from django.contrib import admin
from .models import Employee, OutTransferLog

admin.site.register(Employee)
admin.site.register(OutTransferLog)


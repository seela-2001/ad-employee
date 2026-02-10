from django.db import models

# Create your models here.
class Employee(models.Model):
    employee_id = models.CharField(max_length=100, primary_key=True)
    full_ar_name = models.CharField(max_length=250, verbose_name="الاسم بالكامل")
    full_en_name = models.CharField(max_length=250, verbose_name="Full Name")
    job_title = models.CharField(max_length=150)
    department = models.CharField(max_length=150)
    national_id = models.CharField(max_length=14, unique=True)
    hiring_date = models.DateField()
    ad_username = models.CharField(max_length=250, unique=True)
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'employees'
        verbose_name = 'Employee'
        verbose_name_plural = 'Employees'

        indexes = [
            models.Index(fields=['employee_id']),
            models.Index(fields=['ad_username']),
        ]
    
    def __str__(self):
        return f"{self.employee_id} - {self.full_en_name}"
    
class OutTransferLog(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='out_transfers')
    from_ou = models.CharField(max_length=150)
    to_ou = models.CharField(max_length=150)
    tranferred_by = models.CharField(max_length=150)
    transfer_date = models.DateField(auto_now_add=True)
    note = models.TextField(blank=True, null=True)
    
   
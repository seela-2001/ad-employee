from rest_framework import serializers
from .models import Employee, OutTransferLog
from .ad_service import ADService
from drf_yasg.utils import swagger_serializer_method


class EmployeeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Employee
        fields = ["employee_id", "full_ar_name", "full_en_name", "job_title", "department",
                  "national_id", "hiring_date", "ad_username", "is_active", "created_at", "updated_at",
        ]


class EmployeeDetailSerializer(serializers.ModelSerializer):
    """Detailed employee serializer with AD info"""
    ad_info = serializers.SerializerMethodField()
    
    class Meta:
        model = Employee
        fields = [
            'employee_id', 'full_ar_name', 'full_en_name',
            'job_title', 'department', 'hiring_date', 'national_id',
            'ad_username', 'is_active', 'created_at', 'updated_at',
            'ad_info'
        ]

    def get_ad_info(self, obj):
        try:
            ad_service = ADService()
            ad_data = ad_service.get_user_info(obj.ad_username)
            return ad_data if ad_data else {}
        except Exception as e:
            return {'error': str(e)}


class OutTransferLogSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.full_en_name', read_only=True)
    class Meta:
        model = OutTransferLog
        fields = ["employee", "employee_name", "from_ou", "to_ou", "tranferred_by", "transfer_date", "note"]


class LoginSerializer(serializers.Serializer):

    username = serializers.CharField(required=True)
    password = serializers.CharField(required=True, write_only=True)

class TransferOURequestSerializer(serializers.Serializer):
    new_ou = serializers.ChoiceField(choices=[
        'Accountant', 'Administrative Affairs', 'Camera', 'Exhibit',
        'HR', 'IT', 'Audit', 'Out Work', 'Projects', 'Sales',
        'Supplies', 'Secretarial'
    ])
    admin_password = serializers.CharField(write_only=True, required=True)
    notes = serializers.CharField(required=False, allow_blank=True)


class EmployeeProfileSerializer(serializers.Serializer):

    database_info = EmployeeSerializer()
    ad_info = serializers.DictField()
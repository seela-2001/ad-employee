from rest_framework import status, generics, viewsets
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated, IsAdminUser
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import authenticate
from django.contrib.auth.models import User

from .models import Employee, OutTransferLog
from .serializers import (
    EmployeeSerializer, EmployeeDetailSerializer, LoginSerializer,
    OutTransferLogSerializer, TransferOURequestSerializer, EmployeeProfileSerializer
)
from .ad_service import ADService
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi


class LoginView(APIView):

    permission_classes = [AllowAny]
    
    @swagger_auto_schema(
        operation_description="Login to obtain JWT tokens.",
        request_body=LoginSerializer,
        responses={
            200: openapi.Response(
                description="Successful Login",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'access': openapi.Schema(type=openapi.TYPE_STRING, description='Access Token'),
                        'refresh': openapi.Schema(type=openapi.TYPE_STRING, description='Refresh Token'),
                        'user': openapi.Schema(
                            type=openapi.TYPE_OBJECT,
                            properties={
                                'username': openapi.Schema(type=openapi.TYPE_STRING),
                                'employee_id': openapi.Schema(type=openapi.TYPE_STRING),
                                'full_name': openapi.Schema(type=openapi.TYPE_STRING),
                            }
                        )
                    }
                )
            ),
            401: "Invalid credentials",
            404: "Employee record not found"
        }
    )
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        username = serializer.validated_data['username']
        password = serializer.validated_data['password']
        
        ad_service = ADService()
        if not ad_service.authenticate_user(username, password):
            return Response(
                {'error': 'Invalid credentials'},
                status=status.HTTP_401_UNAUTHORIZED
            )
        
        try:
            employee = Employee.objects.get(ad_username=username)
        except Employee.DoesNotExist:
            return Response(
                {'error': 'Employee record not found in database'},
                status=status.HTTP_404_NOT_FOUND
            )

        user, created = User.objects.get_or_create(
            username=username,
            defaults={'is_staff': False}
        )

        refresh = RefreshToken.for_user(user)
        
        return Response({
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'user': {
                'username': username,
                'employee_id': employee.employee_id,
                'full_name': employee.full_name_en
            }
        })

class LogoutView(APIView):

    permission_classes = [IsAuthenticated]
    
    @swagger_auto_schema(
        operation_description="Logout by blacklisting the refresh token.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['refresh'],
            properties={
                'refresh': openapi.Schema(type=openapi.TYPE_STRING, description='Refresh Token')
            }
        ),
        responses={
            200: "Successfully logged out",
            400: "Bad Request"
        }
    )
    def post(self, request):
        try:
            refresh_token = request.data.get('refresh')
            token = RefreshToken(refresh_token)
            token.blacklist()
            return Response({'message': 'Successfully logged out'})
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )


class EmployeeProfileView(APIView):

    permission_classes = [IsAuthenticated]
    
    @swagger_auto_schema(
        operation_description="Get current user's profile information.",
        responses={
            200: openapi.Response(
                description="User Profile",
                schema=EmployeeProfileSerializer
            ),
            404: "Employee record not found"
        }
    )
    def get(self, request):
        username = request.user.username
        
        try:
            employee = Employee.objects.get(ad_username=username)
        except Employee.DoesNotExist:
            return Response(
                {'error': 'Employee record not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Get AD information
        ad_service = ADService()
        ad_info = ad_service.get_user_info(username)
        
        response_data = {
            'database_info': EmployeeSerializer(employee).data,
            'ad_info': ad_info if ad_info else {}
        }
        
        return Response(response_data)


class EmployeeViewSet(viewsets.ModelViewSet):

    queryset = Employee.objects.all()
    permission_classes = [IsAuthenticated]
    
    def get_serializer_class(self):
        if self.action == 'retrieve':
            return EmployeeDetailSerializer
        return EmployeeSerializer
    
    def get_permissions(self):
        # Only admin can create, update, delete
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [IsAdminUser()]
        return [IsAuthenticated()]
    
    @swagger_auto_schema(
        method='get',
        operation_description="Get AD information for a specific employee.",
        responses={
            200: openapi.Response(
                description="AD Information",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'cn': openapi.Schema(type=openapi.TYPE_STRING),
                        'email': openapi.Schema(type=openapi.TYPE_STRING),
                        'phone': openapi.Schema(type=openapi.TYPE_STRING),
                        'ou': openapi.Schema(type=openapi.TYPE_STRING),
                        'distinguished_name': openapi.Schema(type=openapi.TYPE_STRING),
                        'department': openapi.Schema(type=openapi.TYPE_STRING),
                        'title': openapi.Schema(type=openapi.TYPE_STRING),
                    }
                )
            ),
            500: "Could not fetch AD information"
        }
    )
    @action(detail=True, methods=['get'])
    def ad_info(self, request, pk=None):

        employee = self.get_object()
        ad_service = ADService()
        ad_info = ad_service.get_user_info(employee.ad_username)
        
        if ad_info:
            return Response(ad_info)
        return Response(
            {'error': 'Could not fetch AD information'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
    
    @swagger_auto_schema(
        method='get',
        operation_description="Sync all employees with AD (Admin only).",
        responses={
            200: openapi.Response(
                description="Sync Results",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'total_employees': openapi.Schema(type=openapi.TYPE_INTEGER),
                        'sync_results': openapi.Schema(
                            type=openapi.TYPE_ARRAY,
                            items=openapi.Schema(
                                type=openapi.TYPE_OBJECT,
                                properties={
                                    'employee_id': openapi.Schema(type=openapi.TYPE_STRING),
                                    'name': openapi.Schema(type=openapi.TYPE_STRING),
                                    'ad_synced': openapi.Schema(type=openapi.TYPE_BOOLEAN),
                                    'ad_ou': openapi.Schema(type=openapi.TYPE_STRING, x_nullable=True),
                                }
                            )
                        )
                    }
                )
            ),
            403: "Admin access required"
        }
    )
    @action(detail=False, methods=['get'])
    def sync_ad(self, request):
        """
        GET /api/employees/sync_ad/
        Sync all employees with AD (admin only)
        """
        if not request.user.is_staff:
            return Response(
                {'error': 'Admin access required'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        employees = Employee.objects.all()
        ad_service = ADService()
        sync_results = []
        
        for employee in employees:
            ad_info = ad_service.get_user_info(employee.ad_username)
            sync_results.append({
                'employee_id': employee.employee_id,
                'name': employee.full_name_en,
                'ad_synced': ad_info is not None,
                'ad_ou': ad_info.get('ou') if ad_info else None
            })
        
        return Response({
            'total_employees': len(employees),
            'sync_results': sync_results
        })

# OU TRANSFER VIEWS (Phase 2)
class OUTransferView(APIView):

    permission_classes = [IsAdminUser]
    
    @swagger_auto_schema(
        operation_description="Transfer an employee to a different Organizational Unit (OU).",
        request_body=TransferOURequestSerializer,
        responses={
            200: openapi.Response(
                description="Transfer Successful",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'message': openapi.Schema(type=openapi.TYPE_STRING),
                        'transfer': openapi.Schema(
                            type=openapi.TYPE_OBJECT,
                            description="Transfer Log Data (OutTransferLogSerializer)"
                        )
                    }
                )
            ),
            400: "Bad Request",
            404: "Employee not found",
            500: "Internal Server Error (AD fetch failed)"
        }
    )
    def post(self, request, employee_id):
        try:
            employee = Employee.objects.get(employee_id=employee_id)
        except Employee.DoesNotExist:
            return Response(
                {'error': 'Employee not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        serializer = TransferOURequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        new_ou = serializer.validated_data['new_ou']
        admin_password = serializer.validated_data['admin_password']
        notes = serializer.validated_data.get('notes', '')
        
        # Get current OU
        ad_service = ADService()
        current_info = ad_service.get_user_info(employee.ad_username)
        
        if not current_info:
            return Response(
                {'error': 'Could not fetch current AD information'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        
        current_ou = current_info['ou']

        success, message = ad_service.move_user_to_ou(
            employee.ad_username,
            new_ou,
            request.user.username,
            admin_password
        )
                       
        transfer_log = OutTransferLog.objects.create(
            employee=employee,
            from_ou=current_ou,
            to_ou=new_ou,
            transferred_by=request.user.username,
            notes=notes,
            success=success
        )
        
        if success:
            return Response({
                'message': message,
                'transfer': OutTransferLogSerializer(transfer_log).data
            })
        else:
            return Response(
                {'error': message},
                status=status.HTTP_400_BAD_REQUEST
            )


class OUTransferLogViewSet(viewsets.ReadOnlyModelViewSet):

    queryset = OutTransferLog.objects.all()
    serializer_class = OutTransferLogSerializer
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                'employee_id',
                openapi.IN_QUERY,
                description="Filter by Employee ID",
                type=openapi.TYPE_STRING
            )
        ]
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)
    
    def get_queryset(self):
        queryset = super().get_queryset()
        employee_id = self.request.query_params.get('employee_id')
        
        if employee_id:
            queryset = queryset.filter(employee__employee_id=employee_id)
        
        return queryset



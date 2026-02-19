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


# ─────────────────────────────────────────────
# Reusable schema components
# ─────────────────────────────────────────────

_token_pair_schema = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    properties={
        'access': openapi.Schema(
            type=openapi.TYPE_STRING,
            description='Short-lived JWT access token. Include in requests as: Authorization: Bearer <token>',
            example='eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...'
        ),
        'refresh': openapi.Schema(
            type=openapi.TYPE_STRING,
            description='Long-lived JWT refresh token. Use /api/token/refresh/ to get a new access token.',
            example='eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...'
        ),
    }
)

_user_summary_schema = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    properties={
        'username': openapi.Schema(type=openapi.TYPE_STRING, example='john.doe'),
        'employee_id': openapi.Schema(type=openapi.TYPE_STRING, example='EMP-0042'),
        'full_name': openapi.Schema(type=openapi.TYPE_STRING, example='John Doe'),
    }
)

_ad_info_schema = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    description='User attributes fetched live from Active Directory.',
    properties={
        'cn': openapi.Schema(type=openapi.TYPE_STRING, description='Common Name (display name in AD)', example='John Doe'),
        'email': openapi.Schema(type=openapi.TYPE_STRING, description='Mail attribute from AD', example='john.doe@example.local'),
        'phone': openapi.Schema(type=openapi.TYPE_STRING, description='telephoneNumber attribute from AD', example='+1-555-0100'),
        'ou': openapi.Schema(type=openapi.TYPE_STRING, description='Organizational Unit the user belongs to', example='Engineering'),
        'distinguished_name': openapi.Schema(
            type=openapi.TYPE_STRING,
            description='Full LDAP distinguished name',
            example='CN=John Doe,OU=Engineering,DC=example,DC=local'
        ),
        'department': openapi.Schema(type=openapi.TYPE_STRING, description='department attribute from AD', example='Software'),
        'title': openapi.Schema(type=openapi.TYPE_STRING, description='title attribute from AD', example='Senior Developer'),
    }
)

_error_schema = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    properties={
        'error': openapi.Schema(type=openapi.TYPE_STRING, description='Human-readable error message')
    }
)

_transfer_log_schema = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    description='Audit log entry created after every OU transfer attempt.',
    properties={
        'id': openapi.Schema(type=openapi.TYPE_INTEGER, example=1),
        'employee': openapi.Schema(type=openapi.TYPE_STRING, example='EMP-0042'),
        'from_ou': openapi.Schema(type=openapi.TYPE_STRING, example='Engineering'),
        'to_ou': openapi.Schema(type=openapi.TYPE_STRING, example='Management'),
        'transferred_by': openapi.Schema(type=openapi.TYPE_STRING, example='admin'),
        'notes': openapi.Schema(type=openapi.TYPE_STRING, example='Promoted to team lead'),
        'success': openapi.Schema(type=openapi.TYPE_BOOLEAN, example=True),
        'timestamp': openapi.Schema(type=openapi.TYPE_STRING, format='date-time', example='2024-01-15T10:30:00Z'),
    }
)

# ─────────────────────────────────────────────
# Auth Views
# ─────────────────────────────────────────────

class LoginView(APIView):
    """
    Authenticate a user against Active Directory and return JWT tokens.

    The username and password are verified directly against the configured
    AD/LDAP server. On success, the employee record is looked up in the
    local database and a JWT access + refresh token pair is returned.
    """

    permission_classes = [AllowAny]

    @swagger_auto_schema(
        tags=['Authentication'],
        operation_id='auth_login',
        operation_summary='Login with AD credentials',
        operation_description=(
            'Validates the supplied credentials against Active Directory.\n\n'
            '**Flow:**\n'
            '1. Credentials are checked against the AD/LDAP server.\n'
            '2. The employee record is looked up in the local database by `ad_username`.\n'
            '3. A Django `User` object is created on first login (if not already present).\n'
            '4. A JWT token pair is returned.\n\n'
            '**Use the `access` token** as a `Bearer` header on all subsequent requests.\n'
            'When the access token expires, call `/api/token/refresh/` with the `refresh` token.'
        ),
        request_body=LoginSerializer,
        responses={
            200: openapi.Response(
                description='Login successful — returns JWT token pair and basic user info.',
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        **_token_pair_schema.properties,
                        'user': _user_summary_schema,
                    }
                ),
                examples={
                    'application/json': {
                        'access': 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...',
                        'refresh': 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...',
                        'user': {
                            'username': 'john.doe',
                            'employee_id': 'EMP-0042',
                            'full_name': 'John Doe'
                        }
                    }
                }
            ),
            400: openapi.Response(description='Validation error — missing or malformed fields.', schema=_error_schema),
            401: openapi.Response(description='Invalid credentials — AD authentication failed.', schema=_error_schema),
            404: openapi.Response(description='No employee record found for this AD username in the local database.', schema=_error_schema),
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
                'full_name': employee.full_en_name
            }
        })


class LogoutView(APIView):
    """
    Invalidate a refresh token, effectively logging the user out.

    The supplied refresh token is added to the JWT blacklist so it
    can no longer be used to obtain new access tokens. Existing
    access tokens remain valid until they naturally expire.
    """

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        tags=['Authentication'],
        operation_id='auth_logout',
        operation_summary='Logout and blacklist refresh token',
        operation_description=(
            'Blacklists the provided refresh token so it cannot be used again.\n\n'
            '> **Note:** The access token is **not** immediately revoked. '
            'It will remain valid until its natural expiry (default: 2 hours). '
            'For immediate invalidation, implement token rotation on the client side.\n\n'
            '**Required header:** `Authorization: Bearer <access_token>`'
        ),
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['refresh'],
            properties={
                'refresh': openapi.Schema(
                    type=openapi.TYPE_STRING,
                    description='The refresh token received at login.',
                    example='eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...'
                )
            }
        ),
        responses={
            200: openapi.Response(
                description='Successfully logged out.',
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'message': openapi.Schema(type=openapi.TYPE_STRING, example='Successfully logged out')
                    }
                )
            ),
            400: openapi.Response(description='Token is invalid, expired, or already blacklisted.', schema=_error_schema),
            401: openapi.Response(description='Access token missing or invalid.', schema=_error_schema),
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


# ─────────────────────────────────────────────
# Employee Profile
# ─────────────────────────────────────────────

class EmployeeProfileView(APIView):
    """
    Returns the authenticated user's own employee profile.

    Combines data from the local database with live attributes
    fetched from Active Directory.
    """

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        tags=['Profile'],
        operation_id='profile_me',
        operation_summary='Get my profile',
        operation_description=(
            'Returns the profile of the currently authenticated employee.\n\n'
            'The response merges two data sources:\n'
            '- **`database_info`** — data stored in the local PostgreSQL database.\n'
            '- **`ad_info`** — live attributes fetched from Active Directory '
            '(e.g. email, phone, OU, department). This field is empty `{}` if '
            'AD is unreachable or the user is not found in AD.\n\n'
            '**Required header:** `Authorization: Bearer <access_token>`'
        ),
        responses={
            200: openapi.Response(
                description='Profile data returned successfully.',
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'database_info': openapi.Schema(
                            type=openapi.TYPE_OBJECT,
                            description='Employee fields stored in the local database (see EmployeeSerializer).'
                        ),
                        'ad_info': openapi.Schema(
                            type=openapi.TYPE_OBJECT,
                            description='Live AD attributes. Empty object `{}` if AD is unavailable.',
                            properties=_ad_info_schema.properties
                        ),
                    }
                )
            ),
            401: openapi.Response(description='Access token missing or invalid.', schema=_error_schema),
            404: openapi.Response(description='The authenticated user has no matching employee record in the database.', schema=_error_schema),
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

        ad_service = ADService()
        ad_info = ad_service.get_user_info(username)

        response_data = {
            'database_info': EmployeeSerializer(employee).data,
            'ad_info': ad_info if ad_info else {}
        }

        return Response(response_data)


# ─────────────────────────────────────────────
# Employee ViewSet
# ─────────────────────────────────────────────

class EmployeeViewSet(viewsets.ModelViewSet):
    """
    CRUD operations for Employee records.

    - **List / Retrieve** — available to any authenticated user.
    - **Create / Update / Delete** — restricted to admin users only.
    - **ad_info** — fetch live AD attributes for a specific employee.
    - **sync_ad** — bulk-sync all employees against AD (admin only).
    """

    queryset = Employee.objects.all()
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return EmployeeDetailSerializer
        return EmployeeSerializer

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [IsAdminUser()]
        return [IsAuthenticated()]

    @swagger_auto_schema(
        tags=['Employees'],
        operation_id='employees_list',
        operation_summary='List all employees',
        operation_description=(
            'Returns a paginated list of all employee records from the local database.\n\n'
            'Default page size: **10**. Use `?page=2` to navigate pages.'
        ),
        responses={
            200: openapi.Response(description='Paginated list of employees.', schema=EmployeeSerializer(many=True)),
            401: openapi.Response(description='Authentication required.', schema=_error_schema),
        }
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @swagger_auto_schema(
        tags=['Employees'],
        operation_id='employees_retrieve',
        operation_summary='Get employee details',
        operation_description='Returns full details for a single employee record identified by their database `id`.',
        responses={
            200: openapi.Response(description='Employee detail.', schema=EmployeeDetailSerializer()),
            401: openapi.Response(description='Authentication required.', schema=_error_schema),
            404: openapi.Response(description='Employee not found.', schema=_error_schema),
        }
    )
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @swagger_auto_schema(
        tags=['Employees'],
        operation_id='employees_create',
        operation_summary='Create employee record (Admin)',
        operation_description=(
            'Creates a new employee record in the local database.\n\n'
            '> **Admin only.** Requires `is_staff=True`.'
        ),
        responses={
            201: openapi.Response(description='Employee created successfully.', schema=EmployeeSerializer()),
            400: openapi.Response(description='Validation error.', schema=_error_schema),
            401: openapi.Response(description='Authentication required.', schema=_error_schema),
            403: openapi.Response(description='Admin privileges required.', schema=_error_schema),
        }
    )
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @swagger_auto_schema(
        tags=['Employees'],
        operation_id='employees_update',
        operation_summary='Update employee record (Admin)',
        operation_description='Replaces all fields of an employee record. Use PATCH for partial updates.\n\n> **Admin only.**',
        responses={
            200: openapi.Response(description='Employee updated.', schema=EmployeeSerializer()),
            400: openapi.Response(description='Validation error.', schema=_error_schema),
            401: openapi.Response(description='Authentication required.', schema=_error_schema),
            403: openapi.Response(description='Admin privileges required.', schema=_error_schema),
            404: openapi.Response(description='Employee not found.', schema=_error_schema),
        }
    )
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @swagger_auto_schema(
        tags=['Employees'],
        operation_id='employees_partial_update',
        operation_summary='Partially update employee record (Admin)',
        operation_description='Updates one or more fields of an employee record without requiring all fields.\n\n> **Admin only.**',
        responses={
            200: openapi.Response(description='Employee updated.', schema=EmployeeSerializer()),
            400: openapi.Response(description='Validation error.', schema=_error_schema),
            401: openapi.Response(description='Authentication required.', schema=_error_schema),
            403: openapi.Response(description='Admin privileges required.', schema=_error_schema),
            404: openapi.Response(description='Employee not found.', schema=_error_schema),
        }
    )
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    @swagger_auto_schema(
        tags=['Employees'],
        operation_id='employees_delete',
        operation_summary='Delete employee record (Admin)',
        operation_description=(
            'Permanently deletes an employee record from the local database.\n\n'
            '> **Admin only.** This does **not** remove the user from Active Directory.'
        ),
        responses={
            204: openapi.Response(description='Employee deleted successfully.'),
            401: openapi.Response(description='Authentication required.', schema=_error_schema),
            403: openapi.Response(description='Admin privileges required.', schema=_error_schema),
            404: openapi.Response(description='Employee not found.', schema=_error_schema),
        }
    )
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

    @swagger_auto_schema(
        tags=['Employees — AD'],
        operation_id='employees_ad_info',
        operation_summary='Get live AD attributes for an employee',
        operation_description=(
            'Fetches the employee\'s attributes directly from Active Directory in real time.\n\n'
            'This is a live lookup — the data is **not** cached and reflects the current state in AD.\n\n'
            '**When this fails (500):** The AD server is unreachable, or the employee\'s '
            '`ad_username` does not exist in the directory.'
        ),
        responses={
            200: openapi.Response(description='Live AD attributes returned successfully.', schema=_ad_info_schema),
            401: openapi.Response(description='Authentication required.', schema=_error_schema),
            404: openapi.Response(description='Employee not found in local database.', schema=_error_schema),
            500: openapi.Response(description='Could not fetch data from AD — server unreachable or user not in directory.', schema=_error_schema),
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
        tags=['Employees — AD'],
        operation_id='employees_sync_ad',
        operation_summary='Sync all employees with AD (Admin)',
        operation_description=(
            'Checks every employee record in the database against Active Directory '
            'and reports whether each one was found.\n\n'
            '**What "synced" means:** The employee\'s `ad_username` was successfully '
            'resolved in AD and their OU was retrieved. No data is written — this is a '
            'read-only status check.\n\n'
            '> **Admin only.** Large organizations may experience slower response times '
            'proportional to the number of employees.'
        ),
        responses={
            200: openapi.Response(
                description='Sync report returned.',
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'total_employees': openapi.Schema(
                            type=openapi.TYPE_INTEGER,
                            description='Total number of employee records checked.',
                            example=42
                        ),
                        'sync_results': openapi.Schema(
                            type=openapi.TYPE_ARRAY,
                            items=openapi.Schema(
                                type=openapi.TYPE_OBJECT,
                                properties={
                                    'employee_id': openapi.Schema(type=openapi.TYPE_STRING, example='EMP-0042'),
                                    'name': openapi.Schema(type=openapi.TYPE_STRING, example='John Doe'),
                                    'ad_synced': openapi.Schema(
                                        type=openapi.TYPE_BOOLEAN,
                                        description='True if the user was found in AD.',
                                        example=True
                                    ),
                                    'ad_ou': openapi.Schema(
                                        type=openapi.TYPE_STRING,
                                        description='Current OU in AD. Null if not found.',
                                        example='Engineering',
                                        x_nullable=True
                                    ),
                                }
                            )
                        )
                    }
                )
            ),
            401: openapi.Response(description='Authentication required.', schema=_error_schema),
            403: openapi.Response(description='Admin privileges required.', schema=_error_schema),
        }
    )
    @action(detail=False, methods=['get'])
    def sync_ad(self, request):
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
                'name': employee.full_en_name,
                'ad_synced': ad_info is not None,
                'ad_ou': ad_info.get('ou') if ad_info else None
            })

        return Response({
            'total_employees': len(employees),
            'sync_results': sync_results
        })


# ─────────────────────────────────────────────
# OU Transfer Views
# ─────────────────────────────────────────────

class OUTransferView(APIView):
    """
    Move an employee from their current OU to a new one in Active Directory.

    Requires admin credentials (the request user's password is used to
    authenticate the LDAP modify operation). Every attempt — success or
    failure — is recorded in the OutTransferLog audit table.
    """

    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        tags=['OU Transfers'],
        operation_id='transfers_create',
        operation_summary='Transfer employee to a new OU (Admin)',
        operation_description=(
            'Moves the specified employee to a different Organizational Unit in Active Directory.\n\n'
            '**Steps performed:**\n'
            '1. Fetches the employee\'s current OU from AD.\n'
            '2. Issues an LDAP `modifyDN` operation to move the user.\n'
            '3. Records the transfer attempt in the audit log regardless of outcome.\n\n'
            '**`admin_password`** — the password of the currently authenticated admin user, '
            'used to authorize the LDAP write operation.\n\n'
            '> **Admin only.** The audit log entry is always created, even on failure, '
            'so you can track all transfer attempts.'
        ),
        request_body=TransferOURequestSerializer,
        responses={
            200: openapi.Response(
                description='Transfer completed successfully.',
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'message': openapi.Schema(type=openapi.TYPE_STRING, example='User moved successfully'),
                        'transfer': _transfer_log_schema,
                    }
                )
            ),
            400: openapi.Response(
                description='Transfer failed — LDAP operation rejected, or invalid request body.',
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        'error': openapi.Schema(type=openapi.TYPE_STRING, example='Failed to move user')
                    }
                )
            ),
            401: openapi.Response(description='Authentication required.', schema=_error_schema),
            403: openapi.Response(description='Admin privileges required.', schema=_error_schema),
            404: openapi.Response(description='Employee not found in local database.', schema=_error_schema),
            500: openapi.Response(description='Could not fetch current AD information for the employee.', schema=_error_schema),
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
    """
    Read-only access to the OU transfer audit log.

    Lists all historical transfer attempts. Supports filtering by
    employee ID via query parameter.
    """

    queryset = OutTransferLog.objects.all()
    serializer_class = OutTransferLogSerializer
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        tags=['OU Transfers'],
        operation_id='transfer_logs_list',
        operation_summary='List OU transfer audit logs (Admin)',
        operation_description=(
            'Returns a paginated list of all OU transfer attempts, ordered by most recent first.\n\n'
            'Use the `employee_id` query parameter to filter logs for a specific employee.\n\n'
            '> **Admin only.**'
        ),
        manual_parameters=[
            openapi.Parameter(
                name='employee_id',
                in_=openapi.IN_QUERY,
                description='Filter logs by employee ID (e.g. `EMP-0042`).',
                type=openapi.TYPE_STRING,
                required=False,
                example='EMP-0042'
            )
        ],
        responses={
            200: openapi.Response(description='Paginated transfer log list.', schema=OutTransferLogSerializer(many=True)),
            401: openapi.Response(description='Authentication required.', schema=_error_schema),
            403: openapi.Response(description='Admin privileges required.', schema=_error_schema),
        }
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @swagger_auto_schema(
        tags=['OU Transfers'],
        operation_id='transfer_logs_retrieve',
        operation_summary='Get a single transfer log entry (Admin)',
        operation_description='Returns the full details of a single OU transfer log entry by its ID.\n\n> **Admin only.**',
        responses={
            200: openapi.Response(description='Transfer log entry.', schema=OutTransferLogSerializer()),
            401: openapi.Response(description='Authentication required.', schema=_error_schema),
            403: openapi.Response(description='Admin privileges required.', schema=_error_schema),
            404: openapi.Response(description='Log entry not found.', schema=_error_schema),
        }
    )
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    def get_queryset(self):
        queryset = super().get_queryset()
        employee_id = self.request.query_params.get('employee_id')
        if employee_id:
            queryset = queryset.filter(employee__employee_id=employee_id)
        return queryset
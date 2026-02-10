# employees/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from . import views

router = DefaultRouter()
router.register(r'employees', views.EmployeeViewSet, basename='employee')
router.register(r'ou-transfers', views.OUTransferLogViewSet, basename='ou-transfer')

urlpatterns = [
    # API Root
    # path('', views.api_root, name='api-root'),
    
    # Authentication
    path('auth/login/', views.LoginView.as_view(), name='login'),
    path('auth/logout/', views.LogoutView.as_view(), name='logout'),
    path('auth/token/refresh/', TokenRefreshView.as_view(), name='token-refresh'),
    
    # Employee Profile
    path('employee/profile/', views.EmployeeProfileView.as_view(), name='employee-profile'),
    
    # OU Transfer
    path('employees/<str:employee_id>/transfer-ou/', views.OUTransferView.as_view(), name='transfer-ou'),
    
    # Router URLs
    path('', include(router.urls)),
]
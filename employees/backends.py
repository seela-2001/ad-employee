# employees/backends.py
from django.contrib.auth.backends import BaseBackend
from django.contrib.auth.models import User
from .ad_service import ADService
from .models import Employee

class ADAuthenticationBackend(BaseBackend):
    def authenticate(self, request, username=None, password=None):
        if not username or not password:
            return None
        
        ad_service = ADService()
        
        # Authenticate against AD
        if ad_service.authenticate_user(username, password):
            # Get or create Django user
            user, created = User.objects.get_or_create(
                username=username,
                defaults={'is_staff': False}
            )
            return user
        
        return None
    
    def get_user(self, user_id):
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None

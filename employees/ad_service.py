from ldap3 import Server, Connection, ALL, SUBTREE
from django.core.cache import cache
from dotenv import load_dotenv
import os

load_dotenv()

class ADService:
    def __init__(self):
        self.server_address = os.getenv('AD_SERVER', default='ldap://ad-dc')
        self.domain = os.getenv('AD_DOMAIN', default='EXAMPLE')
        self.base_dn = 'DC=example,DC=local'
        
    def get_connection(self, username, password):
        try:
            server = Server(self.server_address, port=389, get_info=ALL)
            
            # Try UPN format instead of DOMAIN\username
            user_dn = f'{username}@{self.domain.lower()}.local'
            conn = Connection(server, user=user_dn, password=password)
            
            if conn.bind():
                return conn
            return None
        except Exception as e:
            print(f"AD Connection Error: {e}")
            return None
    
    def authenticate_user(self, username, password):
        """Authenticate user against AD"""
        conn = self.get_connection(username, password)
        if conn:
            conn.unbind()
            return True
        return False
    
    def get_user_info(self, username, admin_username=None, admin_password=None):
        """Get user information from AD"""
        # Use admin credentials or service account
        if admin_username and admin_password:
            conn = self.get_connection(admin_username, admin_password)
        else:
            # For production, use a service account
            conn = self.get_connection(
                os.getenv('AD_SERVICE_USER'),
                os.getenv('AD_SERVICE_PASSWORD')
            )
        
        if not conn:
            return None
        
        try:
            search_filter = f'(sAMAccountName={username})'
            conn.search(
                search_base=self.base_dn,
                search_filter=search_filter,
                search_scope=SUBTREE,
                attributes=['cn', 'mail', 'telephoneNumber', 
                           'distinguishedName', 'department', 'title']
            )
            
            if conn.entries:
                entry = conn.entries[0]
                # Extract OU from distinguishedName
                dn = str(entry.distinguishedName)
                ou = self._extract_ou(dn)
                
                return {
                    'cn': str(entry.cn) if entry.cn else '',
                    'email': str(entry.mail) if entry.mail else '',
                    'phone': str(entry.telephoneNumber) if entry.telephoneNumber else '',
                    'ou': ou,
                    'distinguished_name': dn,
                    'department': str(entry.department) if entry.department else '',
                    'title': str(entry.title) if entry.title else '',
                }
            return None
        except Exception as e:
            print(f"Error fetching user info: {e}")
            return None
        finally:
            conn.unbind()
    
    def _extract_ou(self, distinguished_name):
        """Extract OU from DN"""
        parts = distinguished_name.split(',')
        ous = [p.replace('OU=', '') for p in parts if p.startswith('OU=')]
        return ous[0] if ous else 'Unknown'
    
    def move_user_to_ou(self, username, new_ou, admin_username, admin_password):
        """Move user to different OU (Phase 2)"""
        conn = self.get_connection(admin_username, admin_password)
        if not conn:
            return False, "Failed to connect to AD"
        
        try:
            # Get current DN
            user_info = self.get_user_info(username, admin_username, admin_password)
            if not user_info:
                return False, "User not found"
            
            old_dn = user_info['distinguished_name']
            cn = user_info['cn']
            
            # Construct new superior DN
            new_superior = f'OU={new_ou},OU=New,{self.base_dn}'
            
            # Move user
            success = conn.modify_dn(old_dn, f'CN={cn}', new_superior=new_superior)
            
            if success:
                return True, "User moved successfully"
            return False, "Failed to move user"
        except Exception as e:
            return False, f"Error: {str(e)}"
        finally:
            conn.unbind()
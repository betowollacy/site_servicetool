from django.shortcuts import render
from django.utils.deprecation import MiddlewareMixin

from .models import SystemSetting


def _on(value):
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


class MaintenanceModeMiddleware(MiddlewareMixin):
    """Bloqueia o site publico quando o modo manutencao esta ativo.

    O painel administrativo, o admin do Django, arquivos de midia/static e os
    webhooks de pagamento continuam acessiveis. Usuarios staff tambem podem
    navegar no site publico (para testar as mudancas) durante a manutencao.
    """

    ALWAYS_OK_PREFIXES = (
        '/admin-panel/',
        '/django-admin/',
        '/media/',
        '/static/',
        '/payment/',
    )

    def process_request(self, request):
        if not _on(SystemSetting.get('siteMaintenanceMode', 'off')):
            return None
        path = request.path or ''
        if path.startswith(self.ALWAYS_OK_PREFIXES):
            return None
        user = getattr(request, 'user', None)
        if user is not None and getattr(user, 'is_authenticated', False) and getattr(user, 'is_staff', False):
            return None
        response = render(request, 'maintenance.html', {
            'maintenance_msg': SystemSetting.get('siteMaintenanceMsg', ''),
        }, status=503)
        response['Retry-After'] = '3600'
        return response
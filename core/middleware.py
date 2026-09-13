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

    Vale para TODOS os visitantes (staff incluido). O painel administrativo,
    o admin do Django, arquivos de midia/static e os webhooks de pagamento
    continuam acessiveis.
    """

    ALWAYS_OK_PREFIXES = (
        '/admin/',
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
        response = render(request, 'maintenance.html', {
            'maintenance_msg': SystemSetting.get('siteMaintenanceMsg', ''),
        }, status=503)
        response['Retry-After'] = '3600'
        return response
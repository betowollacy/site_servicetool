"""servicetool URL Configuration"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path('admin/', RedirectView.as_view(url='/admin-panel/', permanent=False)),
    path('django-admin/', admin.site.urls),
    path('', include('core.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
else:
    from django.views.static import serve as media_serve
    urlpatterns += [
        path('media/<path:file_path>', media_serve, {'document_root': settings.MEDIA_ROOT}),
    ]

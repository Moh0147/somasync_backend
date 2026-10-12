"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from config.views import health_check

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/health/', health_check, name='health-check'),
    path('api/auth/', include('apps.accounts.urls')),
    path('api/schools/', include('apps.schools.urls')),
    path('api/academics/', include('apps.academics.urls')),
    path('api/classes/', include('apps.classes.urls')),
    path('api/lessons/', include('apps.lessons.urls')),
    path('api/quizzes/', include('apps.quizzes.urls')),
    path('api/progress/', include('apps.progress.urls')),
    path('api/sync/', include('apps.sync.urls')),
    path('api/ai/', include('apps.ai.urls')),
    path('api/exams/', include('apps.exams.urls')),
    path('api/analytics/', include('apps.analytics.urls')),
    path('api/admin/', include('apps.accounts.admin_urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

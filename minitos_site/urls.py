"""
URL configuration for minitos_site project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
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
from django.contrib import admin
from django.urls import include, path
from rest_framework.urlpatterns import format_suffix_patterns
from minitos_core.views import landing_page, location_select_view, terminal_editor_view


urlpatterns = [
    path('', landing_page, name='landing'),
    path('terminal-editor/', location_select_view, name='location_select'),
    path('terminal-editor/<int:location_id>/', terminal_editor_view, name='terminal_editor'),
    path('api_auth/', include('rest_framework.urls')),
    path('minitos_core/', include('minitos_core.urls')),
    path('admin/', admin.site.urls),
]

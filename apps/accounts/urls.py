from django.urls import path

from .views import LoginView, LogoutView, MeView, TokenRefreshView, assign_examiner_role, school_teachers

urlpatterns = [
    path('login/', LoginView.as_view(), name='auth-login'),
    path('refresh/', TokenRefreshView.as_view(), name='auth-refresh'),
    path('logout/', LogoutView.as_view(), name='auth-logout'),
    path('me/', MeView.as_view(), name='auth-me'),
    path('school-teachers/', school_teachers, name='auth-school-teachers'),
    path('school-teachers/<int:pk>/examiner/', assign_examiner_role, name='auth-assign-examiner'),
]

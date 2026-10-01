from django.contrib.auth.views import LoginView, LogoutView

from .forms import SignInForm


class SignInView(LoginView):
    template_name = "registration/login.html"
    authentication_form = SignInForm


class SignOutView(LogoutView):
    http_method_names = ["post", "options"]

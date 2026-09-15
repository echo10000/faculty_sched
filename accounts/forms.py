from django import forms
from django.contrib.auth.forms import AuthenticationForm

from .permissions import profile_for


class SignInForm(AuthenticationForm):
    username = forms.CharField(widget=forms.TextInput(attrs={"class": "form-control form-control-lg", "autocomplete": "username", "autofocus": True}))
    password = forms.CharField(strip=False, widget=forms.PasswordInput(attrs={"class": "form-control form-control-lg", "autocomplete": "current-password"}))

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not user.is_superuser and profile_for(user) is None:
            raise self.get_invalid_login_error()

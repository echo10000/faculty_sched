from django import forms


class TeachingLoadInput(forms.NumberInput):
    """Progressive range helper; only the exact DecimalField input is submitted."""

    template_name = "workloads/widgets/teaching_load_input.html"

    def __init__(self, label, attrs=None):
        self.label = label
        super().__init__({"min": "0", "max": "9999.99", "step": "0.01", **(attrs or {})})

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context["widget"]["load_label"] = self.label
        return context

    class Media:
        js = ("js/teaching-load-controls.js",)

from django.db import models


class College(models.Model):
    name = models.CharField(max_length=255, unique=True)
    code = models.CharField(max_length=10, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.code


class Department(models.Model):
    college = models.ForeignKey(College, on_delete=models.CASCADE, related_name="departments")
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=10)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["college", "code"], name="unique_department_code_per_college")]

    def __str__(self):
        return self.code


class Program(models.Model):
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name="programs")
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=10)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["department", "code"], name="unique_program_code_per_department")]

    def __str__(self):
        return self.code

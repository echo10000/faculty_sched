from django.db import models

from core.base import TrackedModel


class RoomType(TrackedModel):
    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Building(TrackedModel):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

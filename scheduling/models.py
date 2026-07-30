from django.db import models

from academics.models import Curriculum
from core.models import Department


class Room(models.Model):
    class RoomType(models.TextChoices):
        LECTURE = "lecture", "Lecture"
        LABORATORY = "laboratory", "Laboratory"
        SPECIALIZED = "specialized", "Specialized"

    name = models.CharField(max_length=100, unique=True)
    room_type = models.CharField(max_length=12, choices=RoomType.choices)
    capacity = models.PositiveIntegerField()
    restricted_to_department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.SET_NULL, related_name="restricted_rooms")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Term(models.Model):
    class TermName(models.TextChoices):
        FIRST = "1st", "1st"
        SECOND = "2nd", "2nd"
        SUMMER = "summer", "Summer"

    academic_year = models.CharField(max_length=9)
    term_name = models.CharField(max_length=6, choices=TermName.choices)
    is_active = models.BooleanField(default=False)

    class Meta:
        ordering = ["-academic_year", "term_name"]
        constraints = [models.UniqueConstraint(fields=["academic_year", "term_name"], name="unique_academic_term")]

    def __str__(self):
        return f"{self.academic_year} {self.term_name}"


class Block(models.Model):
    curriculum = models.ForeignKey(Curriculum, on_delete=models.CASCADE, related_name="blocks")
    term = models.ForeignKey(Term, on_delete=models.CASCADE, related_name="blocks")
    section_code = models.CharField(max_length=10)
    year_level = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ["curriculum", "year_level", "section_code"]
        constraints = [models.UniqueConstraint(fields=["curriculum", "term", "section_code", "year_level"], name="unique_block_section_per_term")]

    @property
    def enrolled_count(self):
        return self.students.count()

    def __str__(self):
        return f"{self.curriculum.program.code}-{self.year_level}{self.section_code}"

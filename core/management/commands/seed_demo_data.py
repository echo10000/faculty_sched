from decimal import Decimal

from django.core.management.base import BaseCommand

from academics.models import Curriculum, CurriculumSubject, IrregularEnrollment, Student, Subject
from core.models import College, Department, Program
from faculty.models import Designation, Faculty, FacultyQualification
from scheduling.models import Block, Room, Term


class Command(BaseCommand):
    help = "Populate CampusLoad with idempotent demo data."

    colleges = {
        "CBA": ("College of Business Administration", {"BAM": "Business Administration", "HTM": "Hospitality Management"}),
        "CICS": ("College of Informatics and Computing", {"CS": "Computer Science", "IT": "Information Technology"}),
    }
    program_names = {
        "BAM": (("BSBA", "Bachelor of Science in Business Administration"), ("BSEM", "Bachelor of Science in Entrepreneurship")),
        "HTM": (("BSHM", "Bachelor of Science in Hospitality Management"), ("BSTM", "Bachelor of Science in Tourism Management")),
        "CS": (("BSCS", "Bachelor of Science in Computer Science"), ("BSDS", "Bachelor of Science in Data Science")),
        "IT": (("BSIT", "Bachelor of Science in Information Technology"), ("BSIS", "Bachelor of Science in Information Systems")),
    }
    names = [("Maria", "Santos"), ("Jose", "Reyes"), ("Angela", "Cruz"), ("Carlo", "Garcia"), ("Mikaela", "Ramos"), ("Paolo", "Dela Cruz"), ("Rina", "Flores"), ("Mark", "Bautista")]

    def handle(self, *args, **options):
        departments = {}
        for college_code, (college_name, department_data) in self.colleges.items():
            college, _ = College.objects.get_or_create(code=college_code, defaults={"name": college_name})
            for department_code, department_name in department_data.items():
                department, _ = Department.objects.get_or_create(college=college, code=department_code, defaults={"name": department_name})
                departments[department_code] = department

        ge_subjects = []
        for code, title, units in (("GE101", "Understanding the Self", "3.0"), ("GE102", "Readings in Philippine History", "3.0"), ("PE101", "Physical Fitness", "2.0")):
            subject, _ = Subject.objects.get_or_create(code=code, defaults={"title": title, "units": Decimal(units), "is_general_education": True})
            ge_subjects.append(subject)

        curricula = []
        subjects_by_department = {}
        for department_code, department in departments.items():
            department_subjects = []
            for number in range(1, 18):
                code = f"{department_code}{number:03d}"
                subject, _ = Subject.objects.get_or_create(code=code, defaults={"title": f"{department.name} Studies {number}", "units": Decimal("3.0"), "owning_department": department})
                department_subjects.append(subject)
            subjects_by_department[department_code] = department_subjects
            for program_code, program_name in self.program_names[department_code]:
                program, _ = Program.objects.get_or_create(department=department, code=program_code, defaults={"name": program_name})
                curriculum, _ = Curriculum.objects.get_or_create(program=program, version_year=2026, defaults={"is_active": True})
                curricula.append(curriculum)
                placements = department_subjects + ge_subjects
                for index, subject in enumerate(placements):
                    year_level = min(4, index // 5 + 1)
                    term = "1st" if index % 2 == 0 else "2nd"
                    CurriculumSubject.objects.get_or_create(curriculum=curriculum, subject=subject, year_level=year_level, term=term)

        designations = {}
        for name, released in (("Dean", "12.0"), ("Program Chair", "6.0"), ("Coordinator", "3.0")):
            designations[name], _ = Designation.objects.get_or_create(name=name, defaults={"units_released": Decimal(released)})

        faculty_by_department = {}
        faculty_number = 1
        for department_code, department in departments.items():
            members = []
            for index in range(4):
                first_name, last_name = self.names[(faculty_number - 1) % len(self.names)]
                defaults = {"first_name": first_name, "last_name": last_name, "home_department": department, "employment_type": "full_time" if index < 3 else "part_time", "base_load_units": Decimal("24.0") if index < 3 else Decimal("9.0"), "designation": designations["Dean"] if index == 0 and department_code in ("BAM", "CS") else (designations["Program Chair"] if index == 1 else None)}
                faculty, _ = Faculty.objects.get_or_create(employee_id=f"FAC-{faculty_number:03d}", defaults=defaults)
                members.append(faculty)
                for subject in subjects_by_department[department_code][index * 4:index * 4 + 4]:
                    FacultyQualification.objects.get_or_create(faculty=faculty, subject=subject)
                faculty_number += 1
            faculty_by_department[department_code] = members

        for name, room_type, capacity, restricted in (("CBA-201", "lecture", 40, None), ("CBA-202", "lecture", 45, None), ("CICS-301", "lecture", 40, None), ("CICS-302", "lecture", 50, None), ("Computer Lab 1", "laboratory", 35, "IT"), ("Computer Lab 2", "laboratory", 35, "CS"), ("Hotel Lab", "specialized", 30, "HTM"), ("Business Lab", "specialized", 30, "BAM"), ("Auditorium", "lecture", 50, None)):
            Room.objects.get_or_create(name=name, defaults={"room_type": room_type, "capacity": capacity, "restricted_to_department": departments.get(restricted)})

        Term.objects.filter(is_active=True).exclude(academic_year="2026-2027", term_name="1st").update(is_active=False)
        term, _ = Term.objects.get_or_create(academic_year="2026-2027", term_name="1st", defaults={"is_active": True})
        if not term.is_active:
            term.is_active = True
            term.save(update_fields=["is_active"])

        student_number = 1
        for curriculum in curricula:
            for year_level in range(1, 5):
                for section_code in ("A", "B"):
                    block, _ = Block.objects.get_or_create(curriculum=curriculum, term=term, section_code=section_code, year_level=year_level)
                    for position in range(20):
                        first_name, last_name = self.names[(student_number + position) % len(self.names)]
                        Student.objects.get_or_create(student_number=f"2026-{student_number:05d}", defaults={"first_name": first_name, "last_name": last_name, "program": curriculum.program, "curriculum": curriculum, "block": block, "year_level": year_level})
                        student_number += 1
            for irregular_index in range(2):
                first_name, last_name = self.names[(student_number + irregular_index) % len(self.names)]
                student, _ = Student.objects.get_or_create(student_number=f"2026-{student_number:05d}", defaults={"first_name": first_name, "last_name": last_name, "program": curriculum.program, "curriculum": curriculum, "year_level": 2, "status": "irregular"})
                IrregularEnrollment.objects.get_or_create(student=student, subject=subjects_by_department[curriculum.program.department.code][irregular_index], term=term)
                student_number += 1

        models = (College, Department, Program, Curriculum, Subject, Faculty, Room, Term, Block, Student)
        self.stdout.write(self.style.SUCCESS("Demo data ready: " + ", ".join(f"{model.__name__}={model.objects.count()}" for model in models)))

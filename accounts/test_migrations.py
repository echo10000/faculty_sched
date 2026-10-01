from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ProfileUpgradeTests(TransactionTestCase):
    def test_dean_and_chair_transition_preserves_identity_scope_and_disabled_status(self):
        executor = MigrationExecutor(connection)
        current = executor.loader.graph.leaf_nodes()
        target = [('accounts', '0004_loginfailurebucket')]
        executor.migrate(target)
        try:
            apps = executor.loader.project_state(target).apps
            User = apps.get_model('auth', 'User')
            College = apps.get_model('core', 'College')
            Department = apps.get_model('core', 'Department')
            Profile = apps.get_model('accounts', 'AdminProfile')
            college = College.objects.create(code='ROLE-MIG', name='Role migration college')
            department = Department.objects.create(college=college, code='ROLE-D', name='Role migration department')
            dean_user = User.objects.create(username='role-dean', is_active=True)
            chair_user = User.objects.create(username='role-chair', is_active=False)
            dean = Profile.objects.create(user=dean_user, role='dean', college=college)
            chair = Profile.objects.create(user=chair_user, role='dept_chair', department=department, is_enabled=False)
            MigrationExecutor(connection).migrate(current)
            from .models import AdminProfile
            from django.contrib.auth import get_user_model
            from audit.models import AuditLog
            self.assertEqual(AdminProfile.objects.get(pk=dean.pk).role, 'staff')
            self.assertEqual(AdminProfile.objects.get(pk=dean.pk).college_id, college.pk)
            upgraded = AdminProfile.objects.get(pk=chair.pk)
            self.assertEqual(upgraded.role, 'staff')
            self.assertEqual(upgraded.department_id, department.pk)
            self.assertFalse(upgraded.is_enabled)
            self.assertFalse(get_user_model().objects.get(pk=chair_user.pk).is_active)
            self.assertFalse(get_user_model().objects.get(pk=dean_user.pk).is_superuser)
            self.assertEqual(AuditLog.objects.filter(action='account.role_migrated').count(), 2)
        finally:
            MigrationExecutor(connection).migrate(current)

    def test_legacy_dean_is_disabled_and_department_admin_becomes_scoped_staff(self):
        executor = MigrationExecutor(connection)
        old_target = [("accounts", "0002_alter_adminprofile_role"),
                      ("core", "0002_alter_college_options_college_created_by_and_more"),
                      ("auth", "0012_alter_user_first_name_max_length")]
        current = executor.loader.graph.leaf_nodes()
        executor.migrate(old_target)
        try:
            apps = executor.loader.project_state(old_target).apps
            User = apps.get_model("auth", "User")
            College = apps.get_model("core", "College")
            Department = apps.get_model("core", "Department")
            Profile = apps.get_model("accounts", "AdminProfile")
            college = College.objects.create(code="MIG", name="Migration College")
            department = Department.objects.create(college=college, code="MD", name="Migration Department")
            dean = User.objects.create(username="legacy-dean")
            staff = User.objects.create(username="legacy-staff")
            Profile.objects.create(user=dean, role="dean")
            Profile.objects.create(user=staff, role="department_admin", department=department)
            executor = MigrationExecutor(connection)
            executor.migrate(current)
            from .models import AdminProfile
            self.assertFalse(AdminProfile.objects.get(user_id=dean.pk).is_enabled)
            upgraded = AdminProfile.objects.get(user_id=staff.pk)
            self.assertEqual(upgraded.role, "staff")
            self.assertEqual(upgraded.department_id, department.pk)
        finally:
            MigrationExecutor(connection).migrate(current)

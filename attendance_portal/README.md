# Attendance Portal

A Django project with one application, `portal`, for employee accounts, website-admin pages, attendance, and the audit trail.

## Run locally

From this directory in PowerShell:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

The superuser command creates the first website admin account. Give that account an employee ID before it can log in, for example:

```powershell
python manage.py shell -c "from portal.models import User, EmployeeProfile; user = User.objects.get(username='admin'); EmployeeProfile.objects.create(user=user, employee_id='A-0001')"
```

Sign in at `/login/` using employee ID and password; the app dashboard is at `/`. Website admins can manage employees, review attendance, and inspect audit events using the shared sidebar. Login has its own standalone template.

## App structure

- `portal/models.py`: custom user and role, employee profile, attendance, audit event, and authentication audit signals.
- `portal/views.py`: dashboard, employee-management pages, check-in/out, access checks, and request-ID middleware.
- `portal/admin.py`: Django's separate technical admin interface; normal website-admin accounts do not require Django staff or superuser status.
- `templates/base.html`: navbar and responsive sidebar for signed-in pages.
- `templates/portal/`: separate dashboard, employee, attendance, and audit pages.
- `templates/registration/login.html`: standalone login page.

## Audit design

The audit event records actor/target IDs, action, entity, timestamp, request ID, IP address, user agent, and limited metadata. It records login, logout, failed login, employee creation/updates, and attendance check-in/out. Passwords and failed-login credentials are not stored. Attendance updates and their audit records are saved in one transaction.

This is an application audit trail, not a record of every HTTP request or database query. Django model protections do not prevent a database owner from altering audit rows. For stronger production guarantees, use PostgreSQL and forward events through a transactional outbox to a separately permissioned log service or SIEM.

## Existing database

The project has been consolidated without resetting its local `db.sqlite3`. The consolidated models keep the existing table names so current users, profiles, attendance, and audit events remain available. On a fresh database the initial portal migration creates those tables. On the existing database `portal.0001_initial` is recorded as applied because those legacy tables already exist; do not delete the database or unapply that migration.

For production, use PostgreSQL, secure environment-provided settings, TLS, backups, monitoring, and an explicit retention policy. Deactivate former employees rather than deleting accounts with attendance history.

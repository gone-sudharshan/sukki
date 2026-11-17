from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import Attendance, AuditEvent, EmployeeProfile, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("username", "email", "first_name", "last_name", "role", "is_active", "is_staff")
    list_filter = ("role", "is_active", "is_staff")
    fieldsets = DjangoUserAdmin.fieldsets + (("Portal role", {"fields": ("role",)}),)
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (("Portal role", {"fields": ("role",)}),)


@admin.register(EmployeeProfile)
class EmployeeProfileAdmin(admin.ModelAdmin):
    list_display = ("employee_id", "user", "department", "status", "updated_at")
    list_filter = ("department", "status")
    search_fields = ("employee_id", "user__username", "user__email")
    readonly_fields = ("created_at", "created_by", "updated_at", "updated_by")


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ("user", "work_date", "check_in_at", "check_out_at")
    list_filter = ("work_date",)
    search_fields = ("user__username", "user__email", "user__first_name", "user__last_name")
    readonly_fields = (
        "user",
        "work_date",
        "check_in_at",
        "check_in_note",
        "check_out_at",
        "check_out_note",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("occurred_at", "action", "actor_identifier", "target_identifier", "entity_type", "entity_id")
    list_filter = ("action", "occurred_at")
    search_fields = ("actor__username", "actor_identifier", "target_user__username", "target_identifier")
    readonly_fields = tuple(field.name for field in AuditEvent._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

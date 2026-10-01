from django.contrib.auth import get_user_model

from .models import Notification


def create_notification(*, user, notification_type, title, message, data=None):
    return Notification.objects.create(
        user=user,
        type=notification_type,
        title=title,
        message=message,
        data=data or {},
    )


def notify_admins(*, notification_type, title, message, data=None):
    User = get_user_model()
    admin_ids = User.objects.filter(role=User.Role.ADMIN, status=User.Status.ACTIVE).values_list("pk", flat=True)
    notifications = [
        Notification(
            user_id=user_id,
            type=notification_type,
            title=title,
            message=message,
            data=data or {},
        )
        for user_id in admin_ids
    ]
    return Notification.objects.bulk_create(notifications)

from django.conf import settings
from django.contrib.auth.models import User
from django.db import models

from core.models import AdminModel


class Note(AdminModel):
    """
    Textual note for points
    """
    text = models.TextField()
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    point = models.ForeignKey('NamedPoint', on_delete=models.CASCADE,
                              related_name='notes')
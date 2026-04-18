from django.db import models
from django.urls import reverse


class AdminModel(models.Model):
    """
    Enhanced base model for use with admin. Provides shorthands to admin add, change and delete pages.
    More to come later on, if found to be sufficiently generalizable.
    """
    class Meta:
        abstract = True

    def admin_add_url(self):
        """
        Returns an admin URL for adding a model instance.
        """
        return reverse(f'admin:{self._meta.app_label}.{self._meta.model_name}_add')

    def admin_change_url(self):
        """
        Returns the admin change URL for this model instance.
        """
        return reverse(f'admin:{self._meta.app_label}_{self._meta.model_name}_change', args=[self.pk])

    def admin_delete_url(self):
        """
        Returns the admin URL for deleting this model instance.
        """
        return reverse(f'admin:{self._meta.app_label}_{self._meta.model_name}_delete', args=[self.pk])

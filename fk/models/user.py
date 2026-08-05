from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext as _
from phonenumber_field.modelfields import PhoneNumberField


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, date_of_birth=None, password=None):
        """
        Creates and saves a User with the given email, date of
        birth and password.
        """
        if not email:
            raise ValueError("Users must have an email address")

        user = self.model(
            email=self.normalize_email(email),
            date_of_birth=date_of_birth,
        )

        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password):
        """
        Creates and saves a superuser with the given email, date of
        birth and password.
        """
        user = self.create_user(
            email,
            password=password,
        )
        user.is_superuser = True
        user.save(using=self._db)
        return user


class User(AbstractBaseUser):
    email = models.EmailField(verbose_name="email address", max_length=254, unique=True)
    first_name = models.CharField(blank=True, max_length=30, verbose_name="first name")
    last_name = models.CharField(blank=True, max_length=30, verbose_name="last name")
    is_active = models.BooleanField(
        default=True,
        help_text="Designates whether this user should be treated as active. Unselect this instead of deleting accounts.",
        verbose_name="active",
    )
    is_superuser = models.BooleanField(
        default=False,
        help_text="Designates that this user has all permissions without explicitly assigning them.",
        verbose_name="admin status",
    )
    identity_confirmed = models.BooleanField(
        default=False,
        help_text="Whether the identity of this user has been confirmed by Frikanalen management.",
        verbose_name="identity confirmed",
    )

    phone_number = PhoneNumberField(
        blank=True,
        help_text="Phone number at which this user can be reached",
        verbose_name="phone number",
    )

    date_joined = models.DateTimeField(_("date joined"), default=timezone.now)
    date_of_birth = models.DateField(blank=True, null=True)

    objects = UserManager()

    USERNAME_FIELD = "email"

    def __str__(self):
        return self.email

    def has_perm(self, perm, obj=None):
        """Does the user have a specific permission?"""
        # Simplest possible answer: Yes, always
        return self.is_superuser

    def has_module_perms(self, app_label):
        """Does the user have permissions to view the app `app_label`?"""
        # Simplest possible answer: Yes, always
        return self.is_superuser

    def get_short_name(self):
        return self.email

    def anonymize(self):
        """
        Scrub the person, keep the row.

        Video.creator is PROTECT and the playout log refers to what was
        broadcast, so a departing user's content has to outlive their
        account. Deleting the row is therefore not an option; instead
        every identifying column is cleared and the login disabled, which
        satisfies an erasure request while leaving attribution intact -
        the videos of one departed uploader stay recognizably one
        uploader's, without naming them.

        Organization ties are released rather than kept: a departed user
        must not remain visible as a member or as an organization's
        editor.
        """
        # .invalid is reserved by RFC 2606 and can never be delivered to;
        # the pk keeps the address unique, as the column requires.
        self.email = f"deleted-{self.pk}@invalid"
        self.first_name = ""
        self.last_name = ""
        self.phone_number = ""
        self.date_of_birth = None
        # A confirmation of identity outlives neither the identity nor
        # the privileges attached to the account.
        self.identity_confirmed = False
        self.is_superuser = False
        self.is_active = False
        self.set_unusable_password()
        self.save()

        self.organization_set.clear()
        self.editor.update(editor=None)

    @property
    def is_staff(self):
        """Is the user a member of staff?"""
        # Simplest possible answer: All admins are staff
        return self.is_superuser

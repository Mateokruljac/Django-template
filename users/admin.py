from django.contrib import admin

from .models import User


# Register your models here.
class UserAdmin(admin.ModelAdmin):
    list_filter = ['last_login']
    list_display = ['email']
    list_per_page = 5


# admin.site.site_url = 'https://www.django-template.com'
admin.site.register(User, UserAdmin)

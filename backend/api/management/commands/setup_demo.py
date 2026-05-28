from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from core.models import Organization

class Command(BaseCommand):
    help = 'Create demo users and organization'

    def handle(self, *args, **kwargs):
        org, created = Organization.objects.get_or_create(name='Breathe ESG')
        self.stdout.write(f'Organization: {org.name}')

        users = [
            ('analyst', 'breathe-analyst-2024'),
            ('admin', 'breathe-admin-2024'),
            ('auditor', 'breathe-auditor-2024'),
        ]
        for username, password in users:
            if not User.objects.filter(username=username).exists():
                user = User.objects.create_user(username, password=password)
                user.memberships.create(organization=org)
                self.stdout.write(f'Created user: {username}')
            else:
                self.stdout.write(f'User already exists: {username}')

        self.stdout.write('Done!')
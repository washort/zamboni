import csv
import os.path

from django.core.management.base import BaseCommand, CommandError

from mkt.webapps.fakedata import generate_apps_from_specs


class Command(BaseCommand):
    """
    Usage:

        python manage.py import_webapps_from_csv <csv file>

    """

    help = 'Load new webapps from CSV file'
    args = '<csv filename>'

    def handle(self, *args, **kwargs):
        if len(args) < 1:
            raise CommandError('Provide a CSV filename.')
        specs = []
        for row in csv.DictReader(open(args[0])):
            spec = {
                'type': 'hosted',
                'status': 'public',
                'device_types': ['firefoxos-tv']
            }
            specs.append(spec)
            spec['name'] = row['Name']
            spec['manifest_url'] = row['Manifest URL']
            spec['categories'] = [c.strip()
                                  for c in row['Categories'].split(',')]
            spec['preview_files'] = [os.path.join('screenshots', row['id']) +
                                     '.png']
            spec['icon'] = os.path.join('icons', row['id']) + '.png'
            spec['developer_name'] = row['Developer name']
            spec['description'] = row['Description']
            spec['tags'] = [t.strip() for t in row['Keywords'].split(',')]

        generate_apps_from_specs(
            specs, os.path.abspath(os.path.dirname(args[0])), 0, '')

from optparse import make_option
import os
from os.path import join
import shutil

from django.core.management.base import BaseCommand

from addons.models import Persona


class Command(BaseCommand):
    help = ('Copy static files for personas from getpersonas.com to the AMO '
            'static files directory. The directory name for each persona '
            'will be renamed from its persona ID to its addon ID.')
    option_list = BaseCommand.option_list + (
        make_option('--personas-dir', dest='personas_dir',
                    help='Root directory of getpersonas static files.'),
        make_option('--addons-dir', dest='amo_dir',
                    help='Directory of AMO static files.'))

    def handle(self, *args, **options):
        mapping = dict(Persona.objects.all().values_list('pk', 'addon_id'))
        for x in os.listdir(options['personas_dir']):
            if not x.isdigit():
                continue
            y_path = join(options['personas_dir'], x)
            for y in os.listdir(y_path):
                z_path = join(y_path, y)
                for z in os.listdir(z_path):
                    persona = join(z_path, z)
                    addon_id = str(mapping[int(z)])
                    target = join(options['amo_dir'], addon_id)
                    print "%s --> %s" % (persona, target)
                    shutil.copytree(persona, target)







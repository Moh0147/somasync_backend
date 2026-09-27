from django.db import migrations

FORMS = [
    {'name': 'Form I', 'code': 'F1', 'order': 1},
    {'name': 'Form II', 'code': 'F2', 'order': 2},
    {'name': 'Form III', 'code': 'F3', 'order': 3},
    {'name': 'Form IV', 'code': 'F4', 'order': 4},
]


def seed_class_levels(apps, schema_editor):
    ClassLevel = apps.get_model('academics', 'ClassLevel')
    for form in FORMS:
        ClassLevel.objects.get_or_create(code=form['code'], defaults=form)


def remove_class_levels(apps, schema_editor):
    ClassLevel = apps.get_model('academics', 'ClassLevel')
    ClassLevel.objects.filter(code__in=[f['code'] for f in FORMS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('academics', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_class_levels, remove_class_levels),
    ]

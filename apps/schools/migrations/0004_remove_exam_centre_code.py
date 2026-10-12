from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('schools', '0003_make_exam_centre_code_required'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='school',
            name='exam_centre_code',
        ),
    ]

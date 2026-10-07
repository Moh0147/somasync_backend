from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('exams', '0010_examquestion_is_compulsory')]

    operations = [
        migrations.AddField(
            model_name='exam',
            name='section_settings',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]

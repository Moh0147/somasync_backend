from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('exams', '0009_alter_studentexamanswer_recorded_answer')]

    operations = [
        migrations.AddField(
            model_name='examquestion',
            name='is_compulsory',
            field=models.BooleanField(default=True),
        ),
    ]

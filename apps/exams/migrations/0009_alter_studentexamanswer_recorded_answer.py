from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('exams', '0008_alter_examquestion_max_marks_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='studentexamanswer',
            name='recorded_answer',
            field=models.TextField(blank=True),
        ),
    ]

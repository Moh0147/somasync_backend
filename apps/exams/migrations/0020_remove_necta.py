from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('exams', '0019_remove_examexaminerpanel'),
        ('schools', '0004_remove_exam_centre_code'),
    ]

    operations = [
        migrations.DeleteModel(
            name='NectaCandidateRegistration',
        ),
        migrations.AlterField(
            model_name='examcandidate',
            name='candidate_number',
            field=models.CharField(max_length=20),
        ),
    ]

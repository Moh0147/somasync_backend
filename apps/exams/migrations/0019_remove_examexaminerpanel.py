from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('exams', '0018_examexaminerpanel'),
    ]

    operations = [
        migrations.DeleteModel(
            name='ExamExaminerPanel',
        ),
    ]

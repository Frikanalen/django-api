# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('fk', '0017_populate_created_time'),
    ]

    operations = [
        migrations.AlterField(
            model_name='video',
            name='created_time',
            field=models.DateTimeField(auto_now_add=True, help_text='Time the program record was created'),
        ),
        migrations.AlterField(
            model_name='video',
            name='updated_time',
            field=models.DateTimeField(auto_now=True, help_text='Time the program record has been updated'),
        ),
    ]

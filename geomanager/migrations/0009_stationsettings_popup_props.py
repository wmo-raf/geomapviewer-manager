# Generated by Django 4.1.10 on 2023-07-11 08:25

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('geomanager', '0008_stationsettings_name_column'),
    ]

    operations = [
        migrations.AddField(
            model_name='stationsettings',
            name='popup_props',
            field=models.JSONField(blank=True, null=True),
        ),
    ]

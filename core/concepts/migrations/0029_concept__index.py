# Generated by Django 3.2.8 on 2022-02-16 06:57

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('concepts', '0028_auto_20220203_0729'),
    ]

    operations = [
        migrations.AddField(
            model_name='concept',
            name='_index',
            field=models.BooleanField(default=True),
        ),
    ]

# Generated by Django 3.1.9 on 2021-07-09 12:37

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('concepts', '0012_auto_20210617_1231'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='concept',
            index=models.Index(fields=['-updated_at'], name='concepts_updated_c90a9b_idx'),
        ),
    ]

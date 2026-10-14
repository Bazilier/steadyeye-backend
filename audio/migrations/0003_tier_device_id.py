from django.db import migrations, models


def copy_trial_flag(apps, schema_editor):
    AudioJob = apps.get_model('audio', 'AudioJob')
    AudioJob.objects.filter(is_trial=True).update(tier='trial')


class Migration(migrations.Migration):

    dependencies = [
        ('audio', '0002_units_trial_ip'),
    ]

    operations = [
        migrations.AddField(
            model_name='audiojob',
            name='tier',
            field=models.CharField(
                choices=[('free', 'Free'), ('trial', 'Trial'), ('paid', 'Paid')],
                default='paid', max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='audiojob',
            name='device_id',
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
        migrations.RunPython(copy_trial_flag, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='audiojob',
            name='is_trial',
        ),
    ]

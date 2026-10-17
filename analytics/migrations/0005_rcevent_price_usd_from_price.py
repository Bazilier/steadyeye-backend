from decimal import Decimal, InvalidOperation

from django.db import migrations


def recompute_price_usd(apps, schema_editor):
    """price_usd held `price_in_purchased_currency` (the customer's currency,
    e.g. BRL or COP), inflating revenue. Re-read RC's USD `price` from the
    stored payload. Rows without it keep a value only if it was already USD."""
    RcEvent = apps.get_model('analytics', 'RcEvent')
    for row in RcEvent.objects.all().iterator():
        event = (row.raw_payload or {}).get('event', {})
        price = event.get('price')
        if price is not None:
            try:
                new = Decimal(str(price)).quantize(Decimal('0.01'))
            except InvalidOperation:
                new = None
        elif (row.currency or '').upper() == 'USD':
            new = row.price_usd
        else:
            new = None
        if new != row.price_usd:
            row.price_usd = new
            row.save(update_fields=['price_usd'])


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0004_duoreport'),
    ]

    operations = [
        migrations.RunPython(recompute_price_usd, migrations.RunPython.noop),
    ]

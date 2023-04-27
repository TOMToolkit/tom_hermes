[![pypi](https://img.shields.io/pypi/v/tom-hermes.svg)](https://pypi.python.org/pypi/tom-hermes)

# tom_hermes
This module adds [Hermes](https://hermes.lco.global) Broker
support to the TOM Toolkit. Using this module, TOMs can query non-localized events in the Hermes alert archive.
`tom_hermes` is different than other TOM Toolkit broker modules whose query results are alerts associated with specific Targets, and from which you can create Targets in your TOM). `tom_hermes` query results are non-localized events. In conjunction with [tom_nonlocalizedevents](https://pypi.python.org/pypi/tom-nonlocalizedevents), from your `tom_hermes` query results, you can create non-localized events in your TOM.

## Installation

Install the module into your TOM environment:

    pip install tom-hermes

Add `tom_hermes.hermes.HermesBroker` to the `TOM_ALERT_CLASSES` in your TOM's `settings.py`:

    TOM_ALERT_CLASSES = [
        'tom_alerts.brokers.antares.ANTARESBroker',
        ...
        'tom_hermes.hermes.HermesBroker',
    ]


Add `tom_hermes` to your `settings.INSTALLED_APPS`:

```python
    INSTALLED_APPS = [
        'django.contrib.admin',
        'django.contrib.auth',
        ...
        'tom_hermes'
    ]
```

Add `HERMES_API_URL` to your `settings.py` if you want to point to a hermes instance other than `https://hermes.lco.global`.
This is the same settings variable that `tom_nonlocalizedevents` uses to make queries to hermes as well.

`Hermes` is now available as a Broker from the TOM Toolkit Alerts page. You may configure and execute your queries as you would any Broker.

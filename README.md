[![pypi](https://img.shields.io/pypi/v/tom-hermes.svg)](https://pypi.python.org/pypi/tom-hermes)

# tom_hermes
This module adds [Hermes](https://hermes.lco.global) Broker
support to the TOM Toolkit. Using this module, TOMs can query non-localized events in the Hermes alert archive.

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
